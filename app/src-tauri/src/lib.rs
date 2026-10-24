//! Raphael's desktop shell: a tray app with a hidden main window that starts with Windows and
//! allows only one running copy.

mod agent;
mod avatar;
mod background;
mod commands;
mod schedule_awake;
mod computer;
mod core_process;
mod data_layout;
mod device_settings;
mod dictation;
mod first_run;
mod holdtap;
mod hotkey;
mod identity;
mod learn;
mod media;
mod member_notifications;
mod mic_access;
mod notices;
mod overlay;
mod paste;
mod rpc;
mod tray;

use std::path::PathBuf;
use tauri::{AppHandle, Manager, Runtime, WindowEvent, plugin::TauriPlugin};
use tauri_plugin_log::{RotationStrategy, Target, TargetKind, TimezoneStrategy};
use tauri_plugin_window_state::StateFlags;

/// Label of the main window, declared in `tauri.conf.json`.
const MAIN_WINDOW: &str = "main";
/// Label of the overlay window. It is created in Rust, never by the config.
const OVERLAY_WINDOW: &str = "overlay";

/// Remember only whether the window was maximized: it always opens, and un-maximizes, at the default
/// size from `tauri.conf.json`. Saved state never chooses the frame policy either.
fn main_window_state_flags() -> StateFlags {
    StateFlags::all()
        & !(StateFlags::VISIBLE | StateFlags::DECORATIONS | StateFlags::SIZE | StateFlags::POSITION)
}

/// Writes a panic to the log. The release build aborts on a panic (`panic = "abort"`) and has no
/// console, so without this a crash would leave no trace.
///
/// Only the thread and the place are logged, never the message: it can quote the text being
/// handled (a string-slice panic embeds up to 256 characters of its string), and that may be a
/// field the user was dictating into. For the same reason the release build does not hand the
/// panic to the default hook, which would print the message to stderr.
fn log_panics() {
    let default_hook = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        log::error!("{}", report_panic(info));
        log::logger().flush();
        if cfg!(debug_assertions) {
            default_hook(info);
        }
    }));
}

/// What the log says about the panic that `info` describes.
fn report_panic(info: &std::panic::PanicHookInfo<'_>) -> String {
    let thread = std::thread::current();
    panic_report(thread.name(), info.location())
}

/// What the log says about a panic: where it happened, and nothing it carried.
fn panic_report(thread: Option<&str>, location: Option<&std::panic::Location<'_>>) -> String {
    let thread = thread.unwrap_or("unnamed");
    match location {
        Some(at) => format!(
            "panic in thread {thread} at {}:{}:{}",
            at.file(),
            at.line(),
            at.column()
        ),
        None => format!("panic in thread {thread} at an unknown location"),
    }
}

pub fn run() {
    log_panics();
    let identity = identity::current();
    // Read before `use_own_data` sets it for a development build: only a folder the caller chose
    // keeps the local files (Raphael's picture) away from this build's own local folder.
    let caller_data_dir = std::env::var_os("RAPHAEL_DATA_DIR");
    // SAFETY: no other thread runs yet.
    unsafe { identity::use_own_data(identity) };
    let local_dir = identity.local_dir(caller_data_dir, std::env::var_os("LOCALAPPDATA"));
    let mut context = tauri::generate_context!();
    identity.apply(context.config_mut());

    let builder = startup_plugins(
        tauri::Builder::default(),
        |app, _args| {
            log::info!("another launch handed over: showing the main window");
            show_main_window(app);
        },
        data_layout::migrate_at_start,
    );

    builder
        .plugin(
            tauri_plugin_autostart::Builder::new()
                .arg("--autostart")
                .app_name(identity.autostart_name)
                .build(),
        )
        // After the data layout: it reads the window position from the folder the migration
        // moves it to.
        .plugin(
            tauri_plugin_window_state::Builder::new()
                .with_denylist(&[OVERLAY_WINDOW])
                .with_state_flags(main_window_state_flags())
                .build(),
        )
        .plugin(log_plugin())
        // The file dialog of 頭像 (avatar.rs). Used from Rust only: no page may open it.
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_notification::init())
        .on_window_event(|window, event| {
            // The app lives in the tray, so closing the main window only hides it.
            // "Quit" in the tray menu is the way out.
            if window.label() == MAIN_WINDOW
                && let WindowEvent::CloseRequested { api, .. } = event
            {
                api.prevent_close();
                if let Err(err) = window.hide() {
                    log::warn!("could not hide the main window: {err}");
                }
            }
        })
        // Tauri keeps only the last `invoke_handler` call, so every command of the app goes into
        // this one list.
        .invoke_handler(tauri::generate_handler![
            overlay::overlay_subscribe,
            overlay::overlay_log,
            overlay::overlay_confirm,
            commands::core_call,
            commands::save_usage_csv,
            commands::schedule_keep_awake_get,
            commands::schedule_keep_awake_set,
            commands::device_settings_get,
            commands::device_settings_set,
            commands::device_computer_app_remove,
            commands::keyboard_has_altgr,
            commands::open_link,
            commands::open_external_url,
            commands::open_attachment,
            commands::hotkeys_pause,
            commands::window_hotkey,
            commands::hotkeys_practice,
            commands::mic_access,
            commands::main_events,
            commands::agent_ask_text,
            commands::agent_retract,
            commands::agent_question_open,
            commands::agent_question_answer,
            commands::agent_page_shown,
            avatar::avatar_pick,
            avatar::avatar_save,
            avatar::avatar_get,
            avatar::avatar_clear,
        ])
        .setup(move |app| {
            if let Err(error) = schedule_awake::get() { log::warn!("could not restore scheduler awake setting: {error}"); }
            // Tauri keeps only the last `setup` call, so each part of the app starts in its own
            // section of this one closure, in dependency order: a part that needs another part's
            // handle comes after it.

            // ---- Data folders ----
            // What the move of files from earlier builds did (it ran before the log existed), and
            // the copy of the speech models when they could not be renamed.
            let migration = take_migration(app.handle());
            for (level, message) in &migration.notes {
                log::log!(*level, "data folders: {message}");
            }
            if let Some(job) = migration.copy {
                spawn_models_copy(job);
            }

            // ---- Main window ----
            // Created here rather than from the config so that it gets the webview data folder
            // like the overlay does, after the migration has put the old data there.
            create_main_window(app.handle())?;

            if let Some(window) = app.get_webview_window(MAIN_WINDOW) {
                match window.is_decorated() {
                    Ok(decorated) => log::info!("Main window native decorations: {decorated}"),
                    Err(error) => log::warn!("could not inspect the main window frame: {error}"),
                }
            }

            // ---- Raphael's picture ----
            // Kept in this build's own local data folder, which only Windows has (avatar.rs,
            // `Identity::local_dir`): a development build never changes the release's picture.
            if let Some(local) = &local_dir {
                app.manage(std::sync::Arc::new(avatar::AvatarStore::new(local)));
            }

            // ---- Tray ----
            // Its menu starts in the default language with no microphones: it names the one in
            // use and speaks the interface language, which the device settings (below) know.
            tray::build(app.handle())?;

            // ---- Overlay ----
            // Raphael's emblem in the bottom-right corner, 150 px with subtitles in Traditional
            // Chinese until the settings say otherwise (`Overlay::apply_settings`). The dictation
            // controller takes it from the app state as `Arc<Overlay>`. Without the overlay the
            // app still works, so a failure is only logged.
            match overlay::Overlay::create(app.handle(), 150, "on", "zh-TW") {
                Ok(overlay) => {
                    app.manage(overlay);
                }
                Err(err) => log::error!("could not create the overlay window: {err}"),
            }

            // ---- Python core ----
            // Started in the background and kept in Tauri state for the dictation controller. The
            // app does not need it to run (tray, settings), so a core that cannot be started is
            // logged and the app carries on without dictation.
            match core_process::resolve_app_core_command(app.handle())
                .map(|mut command| {
                    // Models that could not be moved yet stay usable where they are.
                    if let Some(dir) = &migration.models_dir {
                        command
                            .env
                            .push(("RAPHAEL_MODELS_DIR".to_owned(), dir.display().to_string()));
                    }
                    command
                })
                .and_then(core_process::CoreProcess::start)
            {
                Ok(core) => {
                    app.manage(core);
                }
                Err(err) => log::error!("the Python core is not running: {err}"),
            }

            // ---- Hotkeys ----
            // The hotkey service reports key events on this channel; the dictation controller
            // (below) takes the receiving end. The service sits in the app state as an `Arc` so
            // the controller can hold it too. If the keyboard hook cannot start, the app runs on
            // without hotkeys.
            let (hotkey_events, hotkey_event_rx) = tokio::sync::mpsc::unbounded_channel();
            // End-to-end runs press the keys with SendInput, which the hook ignores otherwise.
            // Development builds only.
            #[cfg(debug_assertions)]
            if std::env::var_os("RAPHAEL_TEST_INJECTED_KEYS").is_some_and(|value| value == "1") {
                log::warn!("hotkeys: injected key presses count (RAPHAEL_TEST_INJECTED_KEYS=1)");
                handy_keys::accept_injected_input(true);
            }
            let hotkey_event_rx =
                match hotkey::HotkeyService::start(&hotkey::Bindings::default(), hotkey_events) {
                    Ok(service) => {
                        app.manage(std::sync::Arc::new(service));
                        Some(hotkey_event_rx)
                    }
                    Err(err) => {
                        log::error!("global hotkeys are off: {err}");
                        None
                    }
                };

            // ---- Dictation controller ----
            // Ties the hotkeys, the core, the overlay and pasting together. It takes whatever of
            // them started; without the core, dictation is off.
            #[cfg(windows)]
            dictation::spawn(app.handle(), hotkey_event_rx);
            #[cfg(not(windows))]
            {
                log::error!("dictation needs Windows");
                drop(hotkey_event_rx);
            }

            // ---- Operating the desktop ----
            // The shell's half of a run on the owner's desktop: the core's requests answered with
            // UI Automation, the 「操作中」 overlay, the watch on the owner's own input. It takes
            // the core and the overlay from the app state; without the core there is no run.
            #[cfg(windows)]
            computer::spawn(app.handle());

            // ---- Device settings ----
            // `device.json`: loads it, puts it in force (hotkeys, overlay size and subtitles,
            // theme, autostart) and keeps the core told which microphone to use. It takes the
            // overlay, the core and the hotkeys from the app state, so it comes after them.
            commands::start(app.handle());

            // A member's desktop notices follow the core even while the main window is hidden.
            member_notifications::spawn(app.handle());

            // ---- First run ----
            // Until the first-run guide has been gone through, the main window opens by itself.
            // It reads the device settings, so it comes after them.
            first_run::start(app.handle());

            Ok(())
        })
        .run(context)
        .expect("error while running tauri application");
}

/// What the data migration did, from its plugin to `setup`, which logs it and starts the copy.
struct Migration(std::sync::Mutex<Option<data_layout::Outcome>>);

/// The first plugins, which decide whether this launch runs at all and prepare what everything
/// else opens. Plugins start in the order they are added, all of them before `setup`.
///
/// - Single instance: a second launch (from the Start menu, a shortcut, or Windows at sign-in
///   with `--autostart`) hands its arguments to the running copy, which calls `on_second_launch`,
///   and exits there: it makes no window, core, keyboard hook or tray icon, and migrates nothing.
/// - Data layout: `migrate` moves the files of earlier builds before any window, the window
///   position or the core opens them, and only in the copy that keeps running, since a running
///   app's webview and core hold those folders open.
fn startup_plugins<R: Runtime>(
    builder: tauri::Builder<R>,
    on_second_launch: impl Fn(&AppHandle<R>, Vec<String>) + Send + Sync + 'static,
    migrate: impl FnOnce() -> data_layout::Outcome + Send + 'static,
) -> tauri::Builder<R> {
    #[cfg(any(target_os = "macos", windows, target_os = "linux"))]
    let builder = builder.plugin(tauri_plugin_single_instance::init(
        move |app, args, _cwd| on_second_launch(app, args),
    ));
    #[cfg(not(any(target_os = "macos", windows, target_os = "linux")))]
    let _ = on_second_launch;
    builder.plugin(
        tauri::plugin::Builder::<R>::new("raphael-data-layout")
            .setup(move |app, _api| {
                app.manage(Migration(std::sync::Mutex::new(Some(migrate()))));
                Ok(())
            })
            .build(),
    )
}

/// [`startup_plugins`] with `on_migrate` in place of the data migration, for the tests that
/// launch the app twice (`tests/second_launch.rs`). Not part of the app.
#[doc(hidden)]
pub fn startup_plugins_for_tests<R: Runtime>(
    builder: tauri::Builder<R>,
    on_second_launch: impl Fn(&AppHandle<R>, Vec<String>) + Send + Sync + 'static,
    on_migrate: impl FnOnce() + Send + 'static,
) -> tauri::Builder<R> {
    startup_plugins(builder, on_second_launch, move || {
        on_migrate();
        data_layout::Outcome::default()
    })
}

/// Takes what the data migration did; a later call gets nothing to do.
fn take_migration<R: Runtime>(app: &AppHandle<R>) -> data_layout::Outcome {
    app.try_state::<Migration>()
        .and_then(|migration| migration.0.lock().ok()?.take())
        .unwrap_or_default()
}

/// The webview's data folder for every window: `WebView2` in Raphael's local folder.
pub(crate) fn webview_data_dir(app: &AppHandle) -> tauri::Result<PathBuf> {
    Ok(app
        .path()
        .app_local_data_dir()?
        .join(data_layout::WEBVIEW_DIR))
}

/// Builds the main window from its entry in the config, with the shared webview data folder.
fn create_main_window(app: &AppHandle) -> tauri::Result<()> {
    let Some(config) = app
        .config()
        .app
        .windows
        .iter()
        .find(|window| window.label == MAIN_WINDOW)
    else {
        log::error!("the config has no main window");
        return Ok(());
    };
    tauri::WebviewWindowBuilder::from_config(app, config)?
        .data_directory(webview_data_dir(app)?)
        .build()?;
    Ok(())
}

/// Copies the speech models to their new folder without holding anything up.
fn spawn_models_copy(job: data_layout::CopyJob) {
    let spawned = std::thread::Builder::new()
        .name("raphael-models-copy".into())
        .spawn(move || match data_layout::run_copy(&job) {
            Ok(()) => log::info!(
                "data folders: copied the models to {}; the old folder goes at the next start",
                job.to.display()
            ),
            Err(err) => log::warn!("data folders: could not copy the models: {err}"),
        });
    if let Err(err) = spawned {
        log::warn!("data folders: could not start the models copy: {err}");
    }
}

/// Shows the main window, restores it if it was minimized, and gives it focus.
fn show_main_window<R: Runtime>(app: &AppHandle<R>) {
    let Some(window) = app.get_webview_window(MAIN_WINDOW) else {
        log::warn!("the main window does not exist");
        return;
    };
    let shown = window
        .unminimize()
        .and_then(|()| window.show())
        .and_then(|()| window.set_focus());
    if let Err(err) = shown {
        log::warn!("could not show the main window: {err}");
    }
}

/// Logging to stdout (visible in `tauri dev`) and to `raphael.log` in the app log directory
/// (`%LOCALAPPDATA%\Raphael\logs`), with local timestamps. The file rotates at 1 MB
/// and the three newest rotated files are kept. `RAPHAEL_LOG=debug` (or `trace`) logs more, for
/// tracking a problem down.
fn log_plugin<R: Runtime>() -> TauriPlugin<R> {
    let level = std::env::var("RAPHAEL_LOG")
        .ok()
        .and_then(|value| value.parse().ok())
        .unwrap_or(log::LevelFilter::Info);
    tauri_plugin_log::Builder::new()
        .targets([
            Target::new(TargetKind::Stdout),
            Target::new(TargetKind::LogDir {
                file_name: Some("raphael".into()),
            }),
        ])
        .level(level)
        // Other crates (the webview, the HTTP stack) stay at Info even when Raphael logs more.
        .level_for("tao", log::LevelFilter::Info)
        .level_for("wry", log::LevelFilter::Info)
        // Until the "hotkey does nothing while Raphael's own window is in front" report is
        // explained, the keyboard hook and the hotkey service always log their decisions. They
        // log modifier keys only (Alt, Ctrl, Shift, Win), and a Space that went down just before
        // the modifier of its hotkey (Right Alt+Space); never what is typed.
        .level_for("handy_keys", log::LevelFilter::Debug)
        .level_for("app_lib::hotkey", log::LevelFilter::Debug)
        // Keep overlay event delivery and page diagnostics visible during normal dictations.
        .level_for("app_lib::overlay", log::LevelFilter::Debug)
        .max_file_size(1_000_000)
        .rotation_strategy(RotationStrategy::KeepSome(3))
        .timezone_strategy(TimezoneStrategy::UseLocal)
        .build()
}

#[cfg(test)]
mod panic_report_tests {
    use super::*;

    #[test]
    fn a_panic_is_reported_by_thread_and_place_only() {
        let here = std::panic::Location::caller();
        let report = panic_report(Some("raphael-learn"), Some(here));
        assert_eq!(
            report,
            format!(
                "panic in thread raphael-learn at {}:{}:{}",
                here.file(),
                here.line(),
                here.column()
            )
        );
        assert!(report.contains("lib.rs"));
    }

    #[test]
    fn an_unnamed_thread_or_unknown_place_is_still_reported() {
        assert_eq!(
            panic_report(None, None),
            "panic in thread unnamed at an unknown location"
        );
    }

    #[test]
    fn a_hook_built_from_the_report_never_sees_the_message() {
        // The report is built from the thread name and the location alone: whatever the panic
        // carried, including a quoted slice of dictated text, has no way in.
        let secret = "text the user dictated";
        let reports = std::sync::Arc::new(std::sync::Mutex::new(Vec::new()));
        let seen = reports.clone();
        let previous = std::panic::take_hook();
        // The report is built by the same function the real hook logs.
        std::panic::set_hook(Box::new(move |info| {
            seen.lock().unwrap().push(report_panic(info));
        }));
        let outcome = std::panic::catch_unwind(|| {
            let text = String::from(secret);
            // A slice error quotes the string it sliced in its message.
            let _ = &text[..1_000];
        });
        std::panic::set_hook(previous);
        assert!(outcome.is_err());
        let thread = std::thread::current();
        let mine = format!("panic in thread {} at ", thread.name().unwrap_or("unnamed"));
        let reports = reports.lock().unwrap();
        assert!(reports.iter().any(|report| report.starts_with(&mine)));
        assert!(reports.iter().all(|report| !report.contains(secret)));
    }
}

#[cfg(test)]
mod window_state_tests {
    use super::*;

    #[test]
    fn restoring_saved_geometry_cannot_restore_a_native_titlebar_or_show_the_window() {
        let flags = main_window_state_flags();
        assert!(!flags.intersects(StateFlags::VISIBLE | StateFlags::DECORATIONS));
        assert!(flags.contains(StateFlags::MAXIMIZED | StateFlags::FULLSCREEN));
    }

    #[test]
    fn the_window_always_opens_at_its_default_size() {
        // The owner wants opening and un-maximizing to land on the default size (2026-10-24),
        // so a size or place from an earlier session is never restored.
        let flags = main_window_state_flags();
        assert!(!flags.intersects(StateFlags::SIZE | StateFlags::POSITION));
    }
}
