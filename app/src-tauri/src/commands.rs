//! The commands the main window calls, and the device settings behind some of them.
//!
//! - `core_call` passes a call on to the core, but only the methods the main window needs; the
//!   dictation methods are the controller's alone.
//! - `device_settings_get` and `device_settings_set` read and change `device.json`
//!   ([`crate::device_settings`]). A change takes effect at once: the hotkeys, the microphone,
//!   autostart, the overlay and the window's theme are updated, and only then is the file written.
//! - `keyboard_has_altgr`, `hotkeys_pause` and `main_events` are small helpers of the settings
//!   page.
//!
//! [`start`] is called once from `setup` in `lib.rs`. Like the other parts of the app it takes
//! what it needs from the app state with `try_state`: after a failure to start, the core, the
//! hotkeys or the overlay may not be there, and the settings page still has to work.
//!
//! Never log the params of a call: `secrets.set_openrouter_key` carries the OpenRouter key.

use std::future::Future;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, PoisonError};

use serde::Serialize;
use serde_json::{Value, json};
use tauri::async_runtime::JoinHandle;
use tauri::ipc::Channel;
use tauri::{AppHandle, Manager, State};
use tauri_plugin_autostart::ManagerExt;
use tokio::sync::broadcast::error::RecvError;

use crate::core_process::{CORE_RESTARTED, CoreApi, CoreProcess};
use crate::device_settings::{self, Appearance, DeviceSettings, DeviceStore, FILE_NAME, Subtitles};
use crate::hotkey::{Bindings, HotkeyService, PauseReason};
use crate::overlay::Overlay;
use crate::rpc::{CORE_EXITED, Notification, RpcError, TIMEOUT};

/// What `core_call` says when there is no core to call.
const CORE_NOT_RUNNING: &str = "Raphael's core is not running";

/// Whether the core is sending the microphone's loudness to the settings page: set by a
/// successful `audio.monitor` from the page. When the core restarts it is switched on again in
/// the new core ([`restore_core`]), so the page need not know about restarts.
static MONITORING: AtomicBool = AtomicBool::new(false);
/// The task that passes the core's notifications on to the page that last called `main_events`.
static FORWARDER: Mutex<Option<JoinHandle<()>>> = Mutex::new(None);

/// What the main window is told about the core.
#[derive(Clone, Debug, PartialEq, Serialize)]
#[serde(tag = "type")]
pub enum MainEvent {
    /// Loudness of the microphone, 0 to 1, while the microphone is being monitored.
    Level { value: f32 },
    /// A speech model is being downloaded.
    ModelProgress { name: String, done: u64, total: u64 },
}

// ---------- calls to the core ----------

const ALLOWED_PREFIXES: [&str; 5] = [
    "history.",
    "dictionary.",
    "settings.",
    "secrets.",
    "status.",
];
const ALLOWED_METHODS: [&str; 4] = [
    "audio.devices",
    "audio.monitor",
    "models.status",
    "models.ensure",
];

/// Whether the main window may call `m` on the core: the history, the dictionary, the brain's
/// settings, the key, the status, and the few device methods the settings page needs. Everything
/// else is refused, the dictation methods above all.
pub fn is_allowed_method(m: &str) -> bool {
    ALLOWED_METHODS.contains(&m)
        || ALLOWED_PREFIXES
            .iter()
            .any(|prefix| m.strip_prefix(prefix).is_some_and(|rest| !rest.is_empty()))
}

/// Checks the values the core would only type-check: a monthly limit of 0 or more, a history
/// retention of at least one day, a language the speech recognition knows, a model that is named.
/// `Err` says what is wrong. Calls other than `settings.set` pass.
pub fn validate_core_call(method: &str, params: &Value) -> Result<(), String> {
    if method != "settings.set" {
        return Ok(());
    }
    let (Some(key), Some(value)) = (
        params.get("key").and_then(Value::as_str),
        params.get("value"),
    ) else {
        return Ok(()); // the core refuses it
    };
    let fine = match key {
        "monthly_limit_usd" => value.as_f64().is_some_and(|limit| limit >= 0.0),
        "history_retention_days" => value.as_i64().is_some_and(|days| days >= 1),
        "language" => value
            .as_str()
            .is_some_and(|language| ["auto", "zh", "ja", "en"].contains(&language)),
        "cleanup_model" => value.as_str().is_some_and(|model| !model.trim().is_empty()),
        _ => true,
    };
    if fine {
        return Ok(());
    }
    Err(match key {
        "monthly_limit_usd" => "每月上限要是 0 以上的數字",
        "history_retention_days" => "歷史紀錄至少要保留 1 天",
        "language" => "語言只能是 auto、zh、ja 或 en",
        _ => "沒有指定整理用的模型",
    }
    .to_owned())
}

/// Passes `method` on to the core and returns its answer. Fails with the core's own error (code
/// and message), or with `Raphael's core is not running`.
#[tauri::command]
pub async fn core_call(app: AppHandle, method: String, params: Value) -> Result<Value, String> {
    if !is_allowed_method(&method) {
        return Err(format!("the main window may not call {method}"));
    }
    validate_core_call(&method, &params)?;
    let core = app
        .try_state::<Arc<CoreProcess>>()
        .ok_or(CORE_NOT_RUNNING)?;
    let monitor = (method == "audio.monitor")
        .then(|| params.get("on").and_then(Value::as_bool))
        .flatten();
    let result = core
        .call(&method, params)
        .await
        .map_err(|err| err.to_string())?;
    if let Some(on) = monitor {
        MONITORING.store(on, Ordering::Relaxed);
    }
    Ok(result)
}

// ---------- events ----------

/// A whole number out of JSON, which may have written it as 380000000.0.
fn whole(value: &Value) -> Option<u64> {
    value.as_u64().or_else(|| {
        value
            .as_f64()
            .filter(|number| number.is_finite() && *number >= 0.0)
            .map(|number| number as u64)
    })
}

/// The event for a notification from the core, if the main window wants it: the loudness of the
/// microphone while it is monitored, and the progress of a model download. The loudness of a
/// dictation (it has a `session_id`) is the overlay's and not the meter's.
pub fn main_event_from(notification: &Notification, monitoring: bool) -> Option<MainEvent> {
    let params = &notification.params;
    match notification.method.as_str() {
        "audio.level" => {
            if !monitoring || !params.get("session_id").is_none_or(Value::is_null) {
                return None;
            }
            let level = params.get("level")?.as_f64()?;
            Some(MainEvent::Level {
                value: level.clamp(0.0, 1.0) as f32,
            })
        }
        "models.progress" => Some(MainEvent::ModelProgress {
            name: params.get("name")?.as_str()?.to_owned(),
            done: whole(params.get("done")?)?,
            total: whole(params.get("total")?)?,
        }),
        _ => None,
    }
}

/// The page calls this once and receives the events on `channel`. A page that calls it again (it
/// was reloaded) replaces the one before.
#[tauri::command]
pub async fn main_events(app: AppHandle, channel: Channel<MainEvent>) -> Result<(), String> {
    let core = app
        .try_state::<Arc<CoreProcess>>()
        .ok_or(CORE_NOT_RUNNING)?;
    let mut notifications = core.subscribe();
    let forwarder = tauri::async_runtime::spawn(async move {
        loop {
            match notifications.recv().await {
                Ok(notification) => {
                    let monitoring = MONITORING.load(Ordering::Relaxed);
                    if let Some(event) = main_event_from(&notification, monitoring)
                        && channel.send(event).is_err()
                    {
                        break; // the page is gone
                    }
                }
                // The core sends the loudness many times a second, and one of them is as good
                // as another.
                Err(RecvError::Lagged(_)) => {}
                Err(RecvError::Closed) => break,
            }
        }
    });
    let old = FORWARDER
        .lock()
        .unwrap_or_else(PoisonError::into_inner)
        .replace(forwarder);
    if let Some(old) = old {
        old.abort();
    }
    Ok(())
}

// ---------- the device settings ----------

/// What changing the device settings does outside the settings file.
pub trait Effects: Send + Sync {
    /// Registers new hotkeys. The hotkey thread answers within 2 s, so this must not hold up the
    /// async runtime.
    fn bindings(&self, bindings: &Bindings) -> impl Future<Output = Result<(), String>> + Send;
    /// Tells the core which microphone to use (`None`: the system's default).
    fn microphone(&self, name: Option<&str>) -> impl Future<Output = Result<(), String>> + Send;
    /// Starts Raphael with Windows, or no longer does.
    fn autostart(&self, on: bool) -> Result<(), String>;
    /// Resizes the overlay's emblem and changes its subtitles.
    fn overlay(&self, emblem_px: u32, subtitles: Subtitles);
    /// Changes the theme of the main window's frame.
    fn theme(&self, appearance: Appearance);
}

fn bindings_of(settings: &DeviceSettings) -> Bindings {
    Bindings {
        dictation: settings.dictation_keys.clone(),
        paste_last: settings.paste_last_keys.clone(),
    }
}

/// Makes `new` happen where it differs from `old`. The steps that can fail come first: if one
/// does, the ones before it are undone and nothing has changed. (The ones that cannot fail, the
/// overlay and the theme, come last.)
pub async fn apply_changes<E: Effects>(
    effects: &E,
    old: &DeviceSettings,
    new: &DeviceSettings,
) -> Result<(), String> {
    let bindings_changed =
        old.dictation_keys != new.dictation_keys || old.paste_last_keys != new.paste_last_keys;
    let microphone_changed = old.microphone != new.microphone;

    if bindings_changed {
        effects.bindings(&bindings_of(new)).await?;
    }
    let put_back_bindings = async || {
        if bindings_changed && let Err(err) = effects.bindings(&bindings_of(old)).await {
            log::error!("could not put the old hotkeys back: {err}");
        }
    };
    if microphone_changed && let Err(err) = effects.microphone(new.microphone.as_deref()).await {
        put_back_bindings().await;
        return Err(err);
    }
    if old.autostart != new.autostart
        && let Err(err) = effects.autostart(new.autostart)
    {
        if microphone_changed && let Err(err) = effects.microphone(old.microphone.as_deref()).await
        {
            log::error!("could not put the old microphone back: {err}");
        }
        put_back_bindings().await;
        return Err(err);
    }
    if old.emblem_size != new.emblem_size || old.subtitles != new.subtitles {
        effects.overlay(new.emblem_size, new.subtitles);
    }
    if old.appearance != new.appearance {
        effects.theme(new.appearance);
    }
    Ok(())
}

/// Changes the device settings by `patch`, applies what changed and saves. Nothing changes if the
/// patch is wrong or a step fails. The patches are applied one at a time.
pub async fn set_device_settings<E: Effects>(
    store: &DeviceStore,
    effects: &E,
    patch: &Value,
) -> Result<DeviceSettings, String> {
    let mut current = store.lock().await;
    let old = current.clone();
    let mut new = old.clone();
    new.apply_patch(patch)?;
    if new == old {
        return Ok(new);
    }
    apply_changes(effects, &old, &new).await?;
    *current = new.clone();
    // The settings are in force now; if the file cannot be written, they are lost at the next
    // start, and the page says so.
    new.save(store.path()).map_err(|err| {
        log::error!("could not save {}: {err}", store.path().display());
        format!("設定已經生效，但無法存檔：{err}")
    })?;
    Ok(new)
}

/// The device settings as they are now.
#[tauri::command]
pub async fn device_settings_get(store: State<'_, DeviceStore>) -> Result<DeviceSettings, String> {
    Ok(store.get().await)
}

/// Changes some of the device settings (`patch` is an object with the fields to change) and
/// returns all of them as they are afterwards.
#[tauri::command]
pub async fn device_settings_set(
    app: AppHandle,
    store: State<'_, DeviceStore>,
    patch: Value,
) -> Result<DeviceSettings, String> {
    set_device_settings(&store, &AppEffects { app }, &patch).await
}

/// The effects on the running app.
struct AppEffects {
    app: AppHandle,
}

impl AppEffects {
    /// The hotkey service, if the keyboard hook started.
    fn hotkeys(&self) -> Result<Arc<HotkeyService>, String> {
        self.app
            .try_state::<Arc<HotkeyService>>()
            .map(|hotkeys| Arc::clone(&hotkeys))
            .ok_or_else(|| "全域快捷鍵沒有啟動，現在無法變更快捷鍵".to_owned())
    }
}

impl Effects for AppEffects {
    async fn bindings(&self, bindings: &Bindings) -> Result<(), String> {
        let hotkeys = self.hotkeys()?;
        let bindings = bindings.clone();
        // `update_bindings` waits for the hotkey thread's answer, up to 2 s.
        run_blocking(move || hotkeys.update_bindings(&bindings)).await
    }

    async fn microphone(&self, name: Option<&str>) -> Result<(), String> {
        let Some(core) = self.app.try_state::<Arc<CoreProcess>>() else {
            // Nothing to tell: the choice is sent when the core is there.
            log::warn!("the microphone is saved, but the core is not running to be told");
            return Ok(());
        };
        microphone_outcome(core.call("audio.set_device", json!({ "name": name })).await)
    }

    fn autostart(&self, on: bool) -> Result<(), String> {
        let launcher = self.app.autolaunch();
        let result = if on {
            // Always: it writes the path of the exe that is running, which may have moved.
            launcher.enable()
        } else if launcher.is_enabled().unwrap_or(true) {
            launcher.disable()
        } else {
            Ok(())
        };
        result.map_err(|err| {
            format!(
                "無法{}開機自動啟動：{err}",
                if on { "開啟" } else { "關閉" }
            )
        })
    }

    fn overlay(&self, emblem_px: u32, subtitles: Subtitles) {
        match self.app.try_state::<Arc<Overlay>>() {
            Some(overlay) => overlay.apply_settings(emblem_px, subtitles.as_str()),
            None => log::warn!("there is no overlay to resize"),
        }
    }

    fn theme(&self, appearance: Appearance) {
        let Some(window) = self.app.get_webview_window(crate::MAIN_WINDOW) else {
            return;
        };
        let theme = match appearance {
            Appearance::System => None,
            Appearance::Light => Some(tauri::Theme::Light),
            Appearance::Dark => Some(tauri::Theme::Dark),
        };
        if let Err(err) = window.set_theme(theme) {
            log::warn!("could not set the theme of the main window: {err}");
        }
    }
}

/// What the core's answer to `audio.set_device` means for the settings change. A core that is
/// down is not a failure: the saved choice is sent when it is back (`watch_core`). A core that
/// does not answer is one: it may or may not have switched, so the change is refused and the page
/// says so.
fn microphone_outcome(reply: Result<Value, RpcError>) -> Result<(), String> {
    match reply {
        Ok(_) => Ok(()),
        Err(err) if err.code == CORE_EXITED => {
            log::warn!("the microphone is saved, but the core could not be told: {err}");
            Ok(())
        }
        Err(err) if err.code == TIMEOUT => {
            log::warn!("the core did not answer audio.set_device: {err}");
            Err("無法切換麥克風：核心沒有回應，請稍後再試".to_owned())
        }
        Err(err) => Err(format!("無法切換麥克風：{}", err.message)),
    }
}

/// Runs `work` on a thread meant for blocking work, so that waiting for it does not hold up the
/// async runtime.
async fn run_blocking<T, F>(work: F) -> Result<T, String>
where
    T: Send + 'static,
    F: FnOnce() -> Result<T, String> + Send + 'static,
{
    tauri::async_runtime::spawn_blocking(work)
        .await
        .map_err(|err| format!("a background task failed: {err}"))?
}

// ---------- hotkeys and the keyboard ----------

/// Pauses (`true`) or resumes (`false`) the dictation hotkeys, so that the settings page can
/// record a new one. It uses its own pause reason ([`PauseReason::KeyRecorder`]), so resuming
/// never ends the tray's 「暫停 1 小時」.
#[tauri::command]
pub fn hotkeys_pause(app: AppHandle, paused: bool) -> Result<(), String> {
    let hotkeys = app
        .try_state::<Arc<HotkeyService>>()
        .ok_or("全域快捷鍵沒有啟動")?;
    hotkeys.set_paused(PauseReason::KeyRecorder, paused);
    Ok(())
}

/// Whether the keyboard layout in use has an AltGr key, which is the Right Alt key. A hotkey on
/// Right Alt then gets in the way of typing some characters (spec §4). Always false off Windows.
#[tauri::command]
pub fn keyboard_has_altgr() -> bool {
    #[cfg(windows)]
    {
        layout_has_altgr(foreground_layout())
    }
    #[cfg(not(windows))]
    {
        false
    }
}

/// The keyboard layout of the window that has focus: the one the user is typing with.
#[cfg(windows)]
fn foreground_layout() -> windows::Win32::UI::Input::KeyboardAndMouse::HKL {
    use windows::Win32::UI::Input::KeyboardAndMouse::GetKeyboardLayout;
    use windows::Win32::UI::WindowsAndMessaging::{GetForegroundWindow, GetWindowThreadProcessId};

    // SAFETY: no pointers are passed; there may be no foreground window, and then the layout of
    // this thread is the answer.
    unsafe {
        let window = GetForegroundWindow();
        let thread = if window.0.is_null() {
            0
        } else {
            GetWindowThreadProcessId(window, None)
        };
        GetKeyboardLayout(thread)
    }
}

/// Whether the keyboard layout `hkl` has an AltGr key: whether some key gives a character with
/// Ctrl and Alt held, which is what Windows does for AltGr (it holds the left Ctrl for it). Layouts
/// without AltGr give nothing for that, or a control character.
#[cfg(windows)]
fn layout_has_altgr(hkl: windows::Win32::UI::Input::KeyboardAndMouse::HKL) -> bool {
    use windows::Win32::UI::Input::KeyboardAndMouse::{
        MAPVK_VK_TO_VSC, MapVirtualKeyExW, ToUnicodeEx, VK_CONTROL, VK_LCONTROL, VK_MENU, VK_RMENU,
    };

    let mut state = [0u8; 256];
    for key in [VK_CONTROL, VK_LCONTROL, VK_MENU, VK_RMENU] {
        state[usize::from(key.0)] = 0x80; // down
    }
    // The digits, the letters, and the keys for punctuation.
    let keys = (0x30u32..=0x39)
        .chain(0x41..=0x5A)
        .chain(0xBA..=0xC0)
        .chain(0xDB..=0xDF)
        .chain([0xE2]);
    for key in keys {
        // SAFETY: both functions only read their arguments; `buffer` is as long as it says.
        let produced = unsafe {
            let scan = MapVirtualKeyExW(key, MAPVK_VK_TO_VSC, Some(hkl));
            let mut buffer = [0u16; 8];
            // Flag 4: leave the keyboard state alone, which a dead key would change.
            let count = ToUnicodeEx(key, scan, &state, &mut buffer, 4, Some(hkl));
            match usize::try_from(count) {
                Ok(count) => buffer[..count.min(buffer.len())]
                    .iter()
                    .any(|&unit| unit >= 0x20 && unit != 0x7F),
                // Negative: a dead key, which is a character too.
                Err(_) => true,
            }
        };
        if produced {
            return true;
        }
    }
    false
}

// ---------- start ----------

/// Loads the device settings, puts them in force and keeps the core told about the microphone.
/// Called once from `setup`, after the overlay, the core and the hotkeys have been started. It
/// cannot fail: whatever does not work is logged, and the settings page still opens.
pub fn start(app: &AppHandle) {
    let path = device_settings::settings_path().unwrap_or_else(|err| {
        log::error!("{err}");
        app.path()
            .app_data_dir()
            .map_or_else(|_| std::env::temp_dir(), PathBuf::from)
            .join(FILE_NAME)
    });
    let settings = DeviceSettings::load(&path);
    app.manage(DeviceStore::new(path, settings.clone()));
    apply_saved(app, &settings);
    watch_core(app);
}

/// Puts the saved settings in force. Only what differs from how the app starts is applied.
fn apply_saved(app: &AppHandle, settings: &DeviceSettings) {
    let effects = AppEffects { app: app.clone() };
    let fresh = DeviceSettings::default();

    if (&settings.dictation_keys, &settings.paste_last_keys)
        != (&fresh.dictation_keys, &fresh.paste_last_keys)
        // Startup is not an async context and nothing else is waiting yet, so this blocks.
        && let Err(err) = effects
            .hotkeys()
            .and_then(|hotkeys| hotkeys.update_bindings(&bindings_of(settings)))
    {
        log::error!("could not use the saved hotkeys: {err}");
    }
    if (settings.emblem_size, settings.subtitles) != (fresh.emblem_size, fresh.subtitles) {
        effects.overlay(settings.emblem_size, settings.subtitles);
    }
    if settings.appearance != fresh.appearance {
        effects.theme(settings.appearance);
    }
    // A debug build runs from the target folder; it must not put itself in the registry.
    if !cfg!(debug_assertions)
        && let Err(err) = effects.autostart(settings.autostart)
    {
        log::warn!("{err}");
    }
}

/// Tells the core which microphone to use, and whether the settings page's meter is on: at
/// start, and again each time the core restarts (a new core uses the default microphone and does
/// not monitor). Missed notifications may have held a restart, so they count as one.
fn watch_core(app: &AppHandle) {
    let Some(core) = app.try_state::<Arc<CoreProcess>>() else {
        return;
    };
    let mut notifications = core.subscribe();
    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        restore_from(&app).await;
        loop {
            match notifications.recv().await {
                Ok(notification) if notification.method == CORE_RESTARTED => {
                    restore_from(&app).await;
                }
                Err(RecvError::Lagged(missed)) => {
                    log::warn!("missed {missed} notifications from the core; telling it again");
                    restore_from(&app).await;
                }
                Ok(_) => {}
                Err(RecvError::Closed) => break,
            }
        }
    });
}

/// [`restore_core`] with the saved microphone and [`MONITORING`].
async fn restore_from(app: &AppHandle) {
    let Some(core) = app.try_state::<Arc<CoreProcess>>() else {
        return;
    };
    let microphone = match app.try_state::<DeviceStore>() {
        Some(store) => store.get().await.microphone,
        None => None,
    };
    restore_core(core.inner().as_ref(), microphone.as_deref(), &MONITORING).await;
}

/// Puts back what a new core does not know. Sends `microphone` (nothing for the default one,
/// which is what a new core starts with), then `audio.monitor` on if the settings page has its
/// meter on (`monitoring`). `monitoring` stays the one record of whether the core monitors: it is
/// cleared if the core refuses, and if the page switched the meter off while the call was on its
/// way, the core is told to stop again.
async fn restore_core<C: CoreApi>(core: &C, microphone: Option<&str>, monitoring: &AtomicBool) {
    if let Some(name) = microphone
        && let Err(err) = core.call("audio.set_device", json!({ "name": name })).await
    {
        log::warn!("could not tell the core which microphone to use: {err}");
    }
    if !monitoring.load(Ordering::Relaxed) {
        return;
    }
    if let Err(err) = core.call("audio.monitor", json!({ "on": true })).await {
        log::warn!("could not switch the meter back on: {err}");
        monitoring.store(false, Ordering::Relaxed);
        return;
    }
    if !monitoring.load(Ordering::Relaxed)
        && let Err(err) = core.call("audio.monitor", json!({ "on": false })).await
    {
        log::warn!("could not switch the meter off: {err}");
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::sync::Mutex;

    // ---- which methods the window may call ----

    #[test]
    fn only_main_window_methods_are_allowed() {
        assert!(is_allowed_method("history.list") && is_allowed_method("audio.monitor"));
        assert!(
            !is_allowed_method("dictation.start") && !is_allowed_method("brain.finalize_dictation")
        );
    }

    #[test]
    fn the_whole_list_of_allowed_methods() {
        for method in [
            "history.list",
            "history.latest",
            "history.delete",
            "dictionary.list",
            "dictionary.add",
            "dictionary.delete",
            "dictionary.suggestions",
            "dictionary.dismiss",
            "settings.get",
            "settings.set",
            "secrets.status",
            "secrets.set_openrouter_key",
            "secrets.clear_openrouter_key",
            "status.get",
            "status.check_openrouter",
            "audio.devices",
            "audio.monitor",
            "models.status",
            "models.ensure",
        ] {
            assert!(is_allowed_method(method), "{method}");
        }
        for method in [
            "",
            "history",
            "history.",
            "dictation.start",
            "dictation.stop",
            "dictation.cancel",
            "brain.finalize_dictation",
            "audio.set_device", // goes through device_settings_set
            "audio.start",
            "core.hello",
            "xhistory.list",
            " history.list",
            "HISTORY.list",
        ] {
            assert!(!is_allowed_method(method), "{method:?}");
        }
    }

    // ---- values the core does not check ----

    fn set(key: &str, value: Value) -> Result<(), String> {
        validate_core_call("settings.set", &json!({"key": key, "value": value}))
    }

    #[test]
    fn the_monthly_limit_is_a_number_of_0_or_more() {
        assert!(set("monthly_limit_usd", json!(0)).is_ok());
        assert!(set("monthly_limit_usd", json!(10)).is_ok());
        assert!(set("monthly_limit_usd", json!(7.5)).is_ok());
        assert!(set("monthly_limit_usd", json!(-1)).is_err());
        assert!(set("monthly_limit_usd", json!(-0.01)).is_err());
        assert!(set("monthly_limit_usd", json!("10")).is_err());
        assert!(set("monthly_limit_usd", json!(null)).is_err());
        assert!(set("monthly_limit_usd", json!(true)).is_err());
    }

    #[test]
    fn the_history_retention_is_at_least_a_day() {
        assert!(set("history_retention_days", json!(1)).is_ok());
        assert!(set("history_retention_days", json!(30)).is_ok());
        assert!(set("history_retention_days", json!(36500)).is_ok());
        assert!(set("history_retention_days", json!(0)).is_err());
        assert!(set("history_retention_days", json!(-1)).is_err());
        assert!(set("history_retention_days", json!(7.5)).is_err());
        assert!(set("history_retention_days", json!("30")).is_err());
    }

    #[test]
    fn the_language_and_the_model_are_checked_too() {
        for language in ["auto", "zh", "ja", "en"] {
            assert!(set("language", json!(language)).is_ok(), "{language}");
        }
        assert!(set("language", json!("de")).is_err());
        assert!(set("language", json!(1)).is_err());
        assert!(set("cleanup_model", json!("google/gemini-3.1-flash-lite")).is_ok());
        assert!(set("cleanup_model", json!("")).is_err());
        assert!(set("cleanup_model", json!(3)).is_err());
    }

    #[test]
    fn other_calls_pass_through_to_the_core() {
        assert!(validate_core_call("history.list", &json!({"limit": 50})).is_ok());
        assert!(validate_core_call("settings.get", &json!({})).is_ok());
        // an unknown setting is the core's to refuse
        assert!(set("something_else", json!(1)).is_ok());
        // a settings.set without a key or value is the core's to refuse too
        assert!(validate_core_call("settings.set", &json!({})).is_ok());
    }

    // ---- what the window hears from the core ----

    fn notification(method: &str, params: Value) -> Notification {
        Notification {
            method: method.to_owned(),
            params,
        }
    }

    #[test]
    fn levels_are_forwarded_only_while_monitoring() {
        let level = notification("audio.level", json!({"session_id": null, "level": 0.25}));
        assert_eq!(
            main_event_from(&level, true),
            Some(MainEvent::Level { value: 0.25 })
        );
        assert_eq!(main_event_from(&level, false), None);
    }

    #[test]
    fn the_loudness_of_a_dictation_is_not_the_microphone_meter() {
        let during_dictation =
            notification("audio.level", json!({"session_id": "abc", "level": 0.5}));
        assert_eq!(main_event_from(&during_dictation, true), None);
        // a core that leaves the session out is talking about the monitor
        let plain = notification("audio.level", json!({"level": 0.5}));
        assert_eq!(
            main_event_from(&plain, true),
            Some(MainEvent::Level { value: 0.5 })
        );
    }

    #[test]
    fn levels_are_kept_between_0_and_1() {
        let level = |v: Value| notification("audio.level", json!({"session_id": null, "level": v}));
        assert_eq!(
            main_event_from(&level(json!(3.0)), true),
            Some(MainEvent::Level { value: 1.0 })
        );
        assert_eq!(
            main_event_from(&level(json!(-1.0)), true),
            Some(MainEvent::Level { value: 0.0 })
        );
        assert_eq!(main_event_from(&level(json!("loud")), true), None);
        assert_eq!(main_event_from(&level(json!(null)), true), None);
    }

    #[test]
    fn model_progress_is_always_forwarded() {
        let progress = notification(
            "models.progress",
            json!({"name": "qwen3-asr", "done": 380_000_000u64, "total": 900_000_000u64}),
        );
        let expected = Some(MainEvent::ModelProgress {
            name: "qwen3-asr".to_owned(),
            done: 380_000_000,
            total: 900_000_000,
        });
        assert_eq!(main_event_from(&progress, false), expected);
        assert_eq!(main_event_from(&progress, true), expected);
        let broken = notification("models.progress", json!({"name": "qwen3-asr"}));
        assert_eq!(main_event_from(&broken, true), None);
    }

    #[test]
    fn other_notifications_are_not_forwarded() {
        for method in ["core.restarted", "core.down", "dictation.partial"] {
            assert_eq!(
                main_event_from(&notification(method, json!({})), true),
                None
            );
        }
    }

    #[test]
    fn events_have_the_shape_the_page_expects() {
        assert_eq!(
            serde_json::to_string(&MainEvent::Level { value: 0.5 }).unwrap(),
            r#"{"type":"Level","value":0.5}"#
        );
        assert_eq!(
            serde_json::to_string(&MainEvent::ModelProgress {
                name: "silero-vad".to_owned(),
                done: 1,
                total: 2
            })
            .unwrap(),
            r#"{"type":"ModelProgress","name":"silero-vad","done":1,"total":2}"#
        );
    }

    // ---- after the core (re)starts ----

    /// A core that records its calls; `audio.monitor` fails if `monitor_fails`, and turns
    /// `page_turns_off` false while it runs (the page switching the meter off meanwhile).
    #[derive(Default)]
    struct RecordingCore {
        calls: Mutex<Vec<(String, Value)>>,
        monitor_fails: bool,
        page_turns_off: Option<Arc<AtomicBool>>,
    }

    impl RecordingCore {
        fn calls(&self) -> Vec<(String, Value)> {
            self.calls.lock().unwrap().clone()
        }
    }

    impl CoreApi for RecordingCore {
        async fn call(&self, method: &str, params: Value) -> Result<Value, RpcError> {
            self.calls.lock().unwrap().push((method.to_owned(), params));
            if method == "audio.monitor" {
                if let Some(flag) = &self.page_turns_off {
                    flag.store(false, Ordering::Relaxed);
                }
                if self.monitor_fails {
                    return Err(RpcError::new(-32603, "no"));
                }
            }
            Ok(json!({}))
        }
    }

    fn call(method: &str, params: Value) -> (String, Value) {
        (method.to_owned(), params)
    }

    #[tokio::test]
    async fn a_new_core_gets_the_microphone_and_the_meter_back() {
        let core = RecordingCore::default();
        let monitoring = AtomicBool::new(true);
        restore_core(&core, Some("USB Mic"), &monitoring).await;
        assert_eq!(
            core.calls(),
            vec![
                call("audio.set_device", json!({"name": "USB Mic"})),
                call("audio.monitor", json!({"on": true})),
            ]
        );
        assert!(monitoring.load(Ordering::Relaxed));
    }

    #[tokio::test]
    async fn a_new_core_is_not_asked_to_monitor_when_the_page_was_not() {
        let core = RecordingCore::default();
        let monitoring = AtomicBool::new(false);
        restore_core(&core, None, &monitoring).await;
        assert!(
            core.calls().is_empty(),
            "the default microphone needs no call"
        );
        assert!(!monitoring.load(Ordering::Relaxed));
    }

    #[tokio::test]
    async fn a_failed_monitor_is_not_counted_as_monitoring() {
        let core = RecordingCore {
            monitor_fails: true,
            ..RecordingCore::default()
        };
        let monitoring = AtomicBool::new(true);
        restore_core(&core, None, &monitoring).await;
        assert_eq!(
            core.calls(),
            vec![call("audio.monitor", json!({"on": true}))]
        );
        assert!(!monitoring.load(Ordering::Relaxed));
    }

    #[tokio::test]
    async fn the_meter_switched_off_meanwhile_is_switched_off_again() {
        let monitoring = Arc::new(AtomicBool::new(true));
        let core = RecordingCore {
            page_turns_off: Some(monitoring.clone()),
            ..RecordingCore::default()
        };
        restore_core(&core, None, &monitoring).await;
        assert_eq!(
            core.calls(),
            vec![
                call("audio.monitor", json!({"on": true})),
                call("audio.monitor", json!({"on": false})),
            ]
        );
        assert!(!monitoring.load(Ordering::Relaxed));
    }

    // ---- changing the device settings ----

    /// Records what is done, and fails at the step named in `fail`.
    #[derive(Default)]
    struct Fake {
        log: Mutex<Vec<String>>,
        fail: Option<&'static str>,
    }

    impl Fake {
        fn failing(step: &'static str) -> Self {
            Self {
                fail: Some(step),
                ..Self::default()
            }
        }

        fn done(&self) -> Vec<String> {
            self.log.lock().unwrap().clone()
        }

        fn record(&self, step: &str, what: String) -> Result<(), String> {
            self.log.lock().unwrap().push(format!("{step} {what}"));
            if self.fail == Some(step) {
                return Err(format!("{step} failed"));
            }
            Ok(())
        }
    }

    impl Effects for Fake {
        async fn bindings(&self, bindings: &Bindings) -> Result<(), String> {
            self.record("bindings", bindings.dictation.join(","))
        }

        async fn microphone(&self, name: Option<&str>) -> Result<(), String> {
            self.record("microphone", name.unwrap_or("default").to_owned())
        }

        fn autostart(&self, on: bool) -> Result<(), String> {
            self.record("autostart", on.to_string())
        }

        fn overlay(&self, emblem_px: u32, subtitles: Subtitles) {
            let _ = self.record("overlay", format!("{emblem_px} {}", subtitles.as_str()));
        }

        fn theme(&self, appearance: Appearance) {
            let _ = self.record("theme", format!("{appearance:?}"));
        }
    }

    // ---- the core's answer to audio.set_device ----

    #[test]
    fn a_microphone_the_core_accepted_is_a_success() {
        assert_eq!(microphone_outcome(Ok(json!({}))), Ok(()));
    }

    #[test]
    fn a_core_that_is_down_is_told_again_when_it_is_back() {
        let reply = Err(RpcError::new(CORE_EXITED, "the core is not running"));
        assert_eq!(microphone_outcome(reply), Ok(()));
    }

    #[test]
    fn a_core_that_does_not_answer_is_a_failure() {
        let reply = Err(RpcError::new(TIMEOUT, "no answer within 5 s"));
        let err = microphone_outcome(reply).unwrap_err();
        assert!(err.contains("麥克風"), "{err}");
    }

    #[test]
    fn a_core_that_refuses_the_microphone_is_a_failure() {
        let reply = Err(RpcError::new(1004, "no such device"));
        let err = microphone_outcome(reply).unwrap_err();
        assert!(err.contains("no such device"), "{err}");
    }

    // ---- blocking work ----

    #[tokio::test(flavor = "current_thread")]
    async fn blocking_work_does_not_hold_up_the_runtime() {
        use std::time::{Duration, Instant};
        // A task on this one-thread runtime. It can only run while this test awaits.
        let ran = Arc::new(AtomicBool::new(false));
        let flag = Arc::clone(&ran);
        let task = tokio::spawn(async move { flag.store(true, Ordering::SeqCst) });
        let seen = Arc::clone(&ran);
        let result = run_blocking(move || {
            let deadline = Instant::now() + Duration::from_secs(2);
            while !seen.load(Ordering::SeqCst) {
                if Instant::now() > deadline {
                    return Err("the runtime was held up".to_owned());
                }
                std::thread::sleep(Duration::from_millis(1));
            }
            Ok(7)
        })
        .await;
        task.await.unwrap();
        assert_eq!(result, Ok(7));
    }

    #[tokio::test]
    async fn blocking_work_passes_its_error_on() {
        let result: Result<(), String> = run_blocking(|| Err("nope".to_owned())).await;
        assert_eq!(result, Err("nope".to_owned()));
    }

    fn changed(patch: Value) -> (DeviceSettings, DeviceSettings) {
        let old = DeviceSettings::default();
        let mut new = old.clone();
        new.apply_patch(&patch).unwrap();
        (old, new)
    }

    #[tokio::test]
    async fn unchanged_settings_touch_nothing() {
        let fake = Fake::default();
        let same = DeviceSettings::default();
        apply_changes(&fake, &same, &same).await.unwrap();
        assert_eq!(fake.done(), Vec::<String>::new());
    }

    #[tokio::test]
    async fn only_what_changed_is_applied() {
        let fake = Fake::default();
        let (old, new) = changed(json!({"emblem_size": 194}));
        apply_changes(&fake, &old, &new).await.unwrap();
        assert_eq!(fake.done(), ["overlay 194 both"]);

        let fake = Fake::default();
        let (old, new) = changed(json!({"appearance": "dark", "autostart": false}));
        apply_changes(&fake, &old, &new).await.unwrap();
        assert_eq!(fake.done(), ["autostart false", "theme Dark"]);

        let fake = Fake::default();
        let (old, new) = changed(json!({"microphone": "耳機麥克風", "subtitles": "zh"}));
        apply_changes(&fake, &old, &new).await.unwrap();
        assert_eq!(fake.done(), ["microphone 耳機麥克風", "overlay 150 zh"]);

        // the answer to "onboarded" is the settings file's alone
        let fake = Fake::default();
        let (old, new) = changed(json!({"onboarded": true}));
        apply_changes(&fake, &old, &new).await.unwrap();
        assert_eq!(fake.done(), Vec::<String>::new());
    }

    #[tokio::test]
    async fn a_refused_binding_changes_nothing_else() {
        let fake = Fake::failing("bindings");
        let (old, new) = changed(json!({"dictation_keys": ["F9"], "emblem_size": 194}));
        let err = apply_changes(&fake, &old, &new).await.unwrap_err();
        assert_eq!(err, "bindings failed");
        assert_eq!(fake.done(), ["bindings F9"]);
    }

    #[tokio::test]
    async fn a_failed_microphone_puts_the_old_bindings_back() {
        let fake = Fake::failing("microphone");
        let (old, new) =
            changed(json!({"dictation_keys": ["F9"], "microphone": "x", "emblem_size": 194}));
        assert!(apply_changes(&fake, &old, &new).await.is_err());
        assert_eq!(
            fake.done(),
            ["bindings F9", "microphone x", "bindings AltRight"]
        );
    }

    #[tokio::test]
    async fn a_failed_autostart_puts_back_the_bindings_and_the_microphone() {
        let fake = Fake::failing("autostart");
        let (old, new) = changed(json!({
            "dictation_keys": ["F9"],
            "microphone": "x",
            "autostart": false,
            "emblem_size": 194,
        }));
        assert!(apply_changes(&fake, &old, &new).await.is_err());
        assert_eq!(
            fake.done(),
            [
                "bindings F9",
                "microphone x",
                "autostart false",
                "microphone default",
                "bindings AltRight",
            ]
        );
    }

    #[tokio::test]
    async fn changes_are_saved_and_returned() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("device.json");
        let store = DeviceStore::new(path.clone(), DeviceSettings::default());
        let fake = Fake::default();

        let saved = set_device_settings(&store, &fake, &json!({"emblem_size": 120}))
            .await
            .unwrap();
        assert_eq!(saved.emblem_size, 120);
        assert_eq!(store.get().await, saved);
        assert_eq!(DeviceSettings::load(&path), saved);
        assert_eq!(fake.done(), ["overlay 120 both"]);
    }

    #[tokio::test]
    async fn a_bad_patch_changes_nothing() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("device.json");
        let store = DeviceStore::new(path.clone(), DeviceSettings::default());
        let fake = Fake::default();

        assert!(
            set_device_settings(&store, &fake, &json!({"emblem_size": 151}))
                .await
                .is_err()
        );
        assert_eq!(store.get().await, DeviceSettings::default());
        assert!(!path.exists());
        assert_eq!(fake.done(), Vec::<String>::new());
    }

    #[tokio::test]
    async fn settings_that_could_not_be_applied_are_not_saved() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("device.json");
        let store = DeviceStore::new(path.clone(), DeviceSettings::default());
        let fake = Fake::failing("autostart");

        assert!(
            set_device_settings(&store, &fake, &json!({"autostart": false}))
                .await
                .is_err()
        );
        assert!(store.get().await.autostart);
        assert!(!path.exists());
    }

    #[tokio::test]
    async fn setting_what_is_already_set_does_nothing() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("device.json");
        let store = DeviceStore::new(path.clone(), DeviceSettings::default());
        let fake = Fake::default();
        set_device_settings(&store, &fake, &json!({"emblem_size": 150}))
            .await
            .unwrap();
        assert_eq!(fake.done(), Vec::<String>::new());
    }

    // ---- AltGr ----

    #[cfg(windows)]
    mod altgr {
        use super::super::*;
        use windows::Win32::UI::Input::KeyboardAndMouse::{
            KLF_NOTELLSHELL, LoadKeyboardLayoutW, UnloadKeyboardLayout,
        };
        use windows::core::w;

        fn with_layout(id: windows::core::PCWSTR) -> Option<bool> {
            // SAFETY: `id` is a NUL-terminated string; the layout is unloaded again below.
            let hkl = unsafe { LoadKeyboardLayoutW(id, KLF_NOTELLSHELL) }.ok()?;
            let has = layout_has_altgr(hkl);
            // SAFETY: `hkl` came from LoadKeyboardLayoutW above. It fails if the layout is
            // also one the user has installed, which is fine.
            let _ = unsafe { UnloadKeyboardLayout(hkl) };
            Some(has)
        }

        #[test]
        fn the_us_layout_has_no_altgr() {
            let Some(has) = with_layout(w!("00000409")) else {
                return; // this machine cannot load the layout
            };
            assert!(!has);
        }

        #[test]
        fn the_german_layout_has_altgr() {
            let Some(has) = with_layout(w!("00000407")) else {
                return;
            };
            assert!(has);
        }

        #[test]
        fn the_command_answers_for_the_current_layout() {
            // Whatever the answer is on this machine, asking must not fail.
            let _ = keyboard_has_altgr();
        }
    }
}
