//! The system tray icon and its menu.
//!
//! Left click shows the main window. The right-click menu holds what is used most (see
//! [`model`]): the pages of the main window that matter day to day, the microphone, the version
//! and "Quit". It speaks the interface language only, like the notices ([`Notice::text`]).
//!
//! The menu is plain data ([`model`]) until it is drawn, so what it holds is tested without a
//! tray. It is drawn again, whole, when something it shows has changed: the interface language or
//! the microphone ([`refresh`]), and the list of microphones ([`rescan`]). That list is read
//! from the core, which reads the devices again only when asked, so it is asked when the pointer
//! reaches the icon (the menu follows a moment later, long before a right click has finished),
//! when the core starts and when it restarts.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

use serde_json::{Value, json};
use tauri::{
    AppHandle, Manager, Wry,
    menu::{CheckMenuItem, IsMenuItem, Menu, MenuItem, PredefinedMenuItem, Submenu},
    tray::{MouseButton, MouseButtonState, TrayIcon, TrayIconBuilder, TrayIconEvent},
};

use crate::commands::{self, MainEvent, TrayAction, TrayPage};
use crate::core_process::CoreProcess;
use crate::device_settings::DeviceStore;
use crate::notices::{DEFAULT_LANGUAGE, LanguageCell, Notice};
use crate::rpc::{CoreApi, lock};

fn tray_icon() -> tauri::image::Image<'static> {
    tauri::include_image!("icons/tray-icon.png")
}

/// Id of the tray icon (`AppHandle::tray_by_id` finds it by this).
pub const TRAY_ID: &str = "raphael";

// ---------- the words ----------
// A [`Notice`] is a text in the three interface languages; the menu uses it for its labels.

const OPEN: Notice = Notice {
    zh: "打開 Raphael 首頁",
    en: "Open Raphael Home",
    ja: "Raphael のホームを開く",
};
const HISTORY: Notice = Notice {
    zh: "顯示歷史紀錄",
    en: "Show History",
    ja: "履歴を表示",
};
const ADD_WORD: Notice = Notice {
    zh: "將單字加入字典",
    en: "Add Word to Dictionary",
    ja: "辞書に単語を追加",
};
/// Shown only while a background task runs.
const STOP_BACKGROUND: Notice = Notice {
    zh: "中止背景任務",
    en: "Stop Background Task",
    ja: "バックグラウンドタスクを中止",
};
const SETTINGS: Notice = Notice {
    zh: "設定…",
    en: "Settings…",
    ja: "設定…",
};
const MICROPHONE: Notice = Notice {
    zh: "選擇麥克風",
    en: "Choose Microphone",
    ja: "マイクを選択",
};
/// The same name as the settings page gives the choice that follows the system (`null`).
const AUTOMATIC: Notice = Notice {
    zh: "自動偵測",
    en: "Auto-detect",
    ja: "自動検出",
};
/// A chosen microphone that is not plugged in, as in the settings page; `{name}` is its name.
const DISCONNECTED: Notice = Notice {
    zh: "{name}（未連接）",
    en: "{name} (disconnected)",
    ja: "{name}（未接続）",
};
/// `{version}` is the version of the app.
const VERSION: Notice = Notice {
    zh: "版本 {version}",
    en: "Version {version}",
    ja: "バージョン {version}",
};
const QUIT: Notice = Notice {
    zh: "結束 Raphael",
    en: "Quit Raphael",
    ja: "Raphael を終了",
};

// ---------- the menu as data ----------

/// What choosing a menu item does. An item's id is its action written out ([`Action::id`]), and
/// the menu event gives the id back ([`Action::from_id`]).
#[derive(Debug, Clone, PartialEq, Eq)]
enum Action {
    /// 「打開 Raphael 首頁」
    Open,
    /// 「顯示歷史紀錄」
    ShowHistory,
    /// 「將單字加入字典」
    AddWord,
    /// 「中止背景任務」, while a task of the background worker runs
    StopBackground,
    /// 「設定…」
    Settings,
    /// A microphone of 「選擇麥克風」; `None` is the automatic choice.
    Microphone(Option<String>),
    /// 「結束 Raphael」
    Quit,
}

/// The id of the version line, which does nothing.
const VERSION_ID: &str = "version";
/// Ids of the microphones: this one for the automatic choice, this and the name for the others.
const MICROPHONE_AUTOMATIC_ID: &str = "mic";
const MICROPHONE_ID_PREFIX: &str = "mic:";

impl Action {
    fn id(&self) -> String {
        match self {
            Self::Open => "open".to_owned(),
            Self::ShowHistory => "history".to_owned(),
            Self::AddWord => "add_word".to_owned(),
            Self::StopBackground => "stop_background".to_owned(),
            Self::Settings => "settings".to_owned(),
            Self::Microphone(None) => MICROPHONE_AUTOMATIC_ID.to_owned(),
            Self::Microphone(Some(name)) => format!("{MICROPHONE_ID_PREFIX}{name}"),
            Self::Quit => "quit".to_owned(),
        }
    }

    fn from_id(id: &str) -> Option<Self> {
        Some(match id {
            "open" => Self::Open,
            "history" => Self::ShowHistory,
            "add_word" => Self::AddWord,
            "stop_background" => Self::StopBackground,
            "settings" => Self::Settings,
            MICROPHONE_AUTOMATIC_ID => Self::Microphone(None),
            "quit" => Self::Quit,
            _ => match id.strip_prefix(MICROPHONE_ID_PREFIX) {
                Some(name) if !name.is_empty() => Self::Microphone(Some(name.to_owned())),
                _ => return None,
            },
        })
    }

    /// The page of the main window that this action opens, and what it asks there.
    fn destination(&self) -> Option<(TrayPage, Option<TrayAction>)> {
        match self {
            Self::Open => Some((TrayPage::Home, None)),
            Self::ShowHistory => Some((TrayPage::History, None)),
            Self::AddWord => Some((TrayPage::Dictionary, Some(TrayAction::AddWord))),
            Self::Settings => Some((TrayPage::Settings, None)),
            Self::StopBackground | Self::Microphone(_) | Self::Quit => None,
        }
    }
}

/// One entry of the menu.
#[derive(Debug, Clone, PartialEq, Eq)]
enum Entry {
    Item {
        id: String,
        label: String,
        enabled: bool,
    },
    /// An item with a tick, which is on for the one that is in use.
    Check {
        id: String,
        label: String,
        checked: bool,
    },
    Separator,
    Submenu {
        label: String,
        entries: Vec<Entry>,
    },
}

/// What the microphone submenu is drawn from.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
struct Microphones {
    /// The names the core listed; `None` until it has answered.
    found: Option<Vec<String>>,
    /// The microphone of the device settings; `None` follows the system.
    selected: Option<String>,
}

fn item(action: &Action, label: &str) -> Entry {
    Entry::Item {
        id: action.id(),
        label: label.to_owned(),
        enabled: true,
    }
}

fn check(action: &Action, label: &str, checked: bool) -> Entry {
    Entry::Check {
        id: action.id(),
        label: label.to_owned(),
        checked,
    }
}

/// The whole menu, in the interface `language` (`"zh-TW"`, `"en"` or `"ja"`; any other is
/// Chinese, as for the notices). `background_running` is whether a task of the background worker
/// is under way: only then does the menu offer to stop it.
fn model(
    language: &str,
    version: &str,
    microphones: &Microphones,
    background_running: bool,
) -> Vec<Entry> {
    let mut entries = vec![
        item(&Action::Open, OPEN.text(language)),
        item(&Action::ShowHistory, HISTORY.text(language)),
        item(&Action::AddWord, ADD_WORD.text(language)),
    ];
    if background_running {
        entries.push(item(
            &Action::StopBackground,
            STOP_BACKGROUND.text(language),
        ));
    }
    entries.extend([
        Entry::Separator,
        item(&Action::Settings, SETTINGS.text(language)),
        Entry::Submenu {
            label: MICROPHONE.text(language).to_owned(),
            entries: microphone_entries(language, microphones),
        },
        Entry::Separator,
        Entry::Item {
            id: VERSION_ID.to_owned(),
            label: VERSION.text(language).replace("{version}", version),
            enabled: false,
        },
        item(&Action::Quit, QUIT.text(language)),
    ]);
    entries
}

/// The automatic choice, then the microphones that were found, with a tick on the one in use. The
/// one in use is in the list even when it was not found, as in the settings page: it would be
/// wrong to show no tick at all, and unplugging a headset is no reason to forget the choice.
fn microphone_entries(language: &str, microphones: &Microphones) -> Vec<Entry> {
    let Microphones { found, selected } = microphones;
    let mut entries = vec![check(
        &Action::Microphone(None),
        AUTOMATIC.text(language),
        selected.is_none(),
    )];
    let listed = found.as_deref().unwrap_or_default();
    for name in listed {
        let in_use = selected.as_deref() == Some(name.as_str());
        entries.push(check(&Action::Microphone(Some(name.clone())), name, in_use));
    }
    if let Some(name) = selected
        && !listed.contains(name)
    {
        // Until the core has listed the microphones nothing says that this one is unplugged.
        let label = if found.is_some() {
            DISCONNECTED.text(language).replace("{name}", name)
        } else {
            name.clone()
        };
        entries.push(check(&Action::Microphone(Some(name.clone())), &label, true));
    }
    entries
}

/// The names in the core's reply to `audio.devices`.
fn microphone_names(reply: &Value) -> Vec<String> {
    reply
        .get("devices")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|device| device.get("name")?.as_str())
        .filter(|name| !name.is_empty())
        .map(str::to_owned)
        .collect()
}

/// The text of a label for a menu: Windows reads a lone `&` as the mark of a shortcut key, and
/// the name of a microphone may have one.
fn escape(label: &str) -> String {
    label.replace('&', "&&")
}

// ---------- drawing it ----------

/// What the tray keeps between two drawings of its menu. In the app state.
#[derive(Default)]
struct TrayState {
    /// The microphones the core listed last; `None` until it has listed them.
    microphones: Mutex<Option<Vec<String>>>,
    /// Held while the menu is drawn, so that an older reading never replaces a newer one.
    drawing: tokio::sync::Mutex<()>,
    /// A reading of the microphones is under way. The pointer can enter the icon often.
    scanning: AtomicBool,
    /// A task of the background worker is under way (`background`): the menu offers to stop it.
    background: AtomicBool,
}

fn version(app: &AppHandle) -> String {
    app.package_info().version.to_string()
}

fn menu_item(app: &AppHandle, entry: &Entry) -> tauri::Result<Box<dyn IsMenuItem<Wry>>> {
    Ok(match entry {
        Entry::Item { id, label, enabled } => Box::new(MenuItem::with_id(
            app,
            id,
            escape(label),
            *enabled,
            None::<&str>,
        )?),
        Entry::Check { id, label, checked } => Box::new(CheckMenuItem::with_id(
            app,
            id,
            escape(label),
            true,
            *checked,
            None::<&str>,
        )?),
        Entry::Separator => Box::new(PredefinedMenuItem::separator(app)?),
        Entry::Submenu { label, entries } => {
            let items = menu_items(app, entries)?;
            let refs: Vec<&dyn IsMenuItem<Wry>> = items.iter().map(Box::as_ref).collect();
            Box::new(Submenu::with_items(app, escape(label), true, &refs)?)
        }
    })
}

fn menu_items(app: &AppHandle, entries: &[Entry]) -> tauri::Result<Vec<Box<dyn IsMenuItem<Wry>>>> {
    entries.iter().map(|entry| menu_item(app, entry)).collect()
}

fn build_menu(app: &AppHandle, entries: &[Entry]) -> tauri::Result<Menu<Wry>> {
    let items = menu_items(app, entries)?;
    let refs: Vec<&dyn IsMenuItem<Wry>> = items.iter().map(Box::as_ref).collect();
    Menu::with_items(app, &refs)
}

/// Builds the tray icon; it stays for the life of the app. The menu starts in the default
/// language with no microphones listed: the device settings are not loaded yet, and
/// [`refresh`] draws it again when they are.
pub fn build(app: &AppHandle) -> tauri::Result<TrayIcon> {
    app.manage(TrayState::default());
    let menu = build_menu(
        app,
        &model(
            DEFAULT_LANGUAGE,
            &version(app),
            &Microphones::default(),
            false,
        ),
    )?;

    let icon = tray_icon();

    TrayIconBuilder::with_id(TRAY_ID)
        .icon(icon)
        .tooltip("Raphael")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match Action::from_id(event.id().as_ref()) {
            Some(action) => run(app, action),
            None => log::warn!("unknown tray menu item: {}", event.id().as_ref()),
        })
        .on_tray_icon_event(|tray, event| match event {
            TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } => crate::show_main_window(tray.app_handle()),
            // A right click follows almost every time the pointer comes to the icon, and the
            // menu opens at the end of it: read the microphones now so that it is right then.
            TrayIconEvent::Enter { .. } => rescan(tray.app_handle()),
            _ => {}
        })
        .build(app)
}

/// Does what a chosen menu item stands for.
fn run(app: &AppHandle, action: Action) {
    match action {
        Action::Microphone(name) => choose_microphone(app, name),
        Action::StopBackground => stop_background(app),
        Action::Quit => app.exit(0),
        Action::Open | Action::ShowHistory | Action::AddWord | Action::Settings => {
            if let Some((page, ask)) = action.destination() {
                // The page is told first, so that the window comes up on it.
                commands::tell_main_window(MainEvent::TrayOpen { page, action: ask });
                crate::show_main_window(app);
            }
        }
    }
}

/// Asks the core to stop the background task that runs. Esc does not do this; the tray does. The
/// core reports what was done by itself when the task has stopped.
fn stop_background(app: &AppHandle) {
    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        let Some(core) = app.try_state::<Arc<CoreProcess>>() else {
            return;
        };
        if let Err(err) = core.call("worker.cancel", json!({})).await {
            log::warn!("tray: could not stop the background task: {err}");
        }
    });
}

/// Tells the tray whether a task of the background worker is under way, and draws the menu again
/// when that is not what it shows.
pub fn set_background_running(app: &AppHandle, running: bool) {
    let Some(state) = app.try_state::<TrayState>() else {
        return;
    };
    if state.background.swap(running, Ordering::AcqRel) != running {
        refresh(app);
    }
}

/// Sets the microphone as the settings page does. The menu is drawn again afterwards whatever
/// came of it: choosing an item ticks it, and a choice that was refused (or was the one already
/// in use, which unticks it) must not leave a tick that is not true.
fn choose_microphone(app: &AppHandle, name: Option<String>) {
    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        if let Err(err) = commands::set_microphone(&app, name.as_deref()).await {
            log::warn!("tray: could not switch the microphone: {err}");
        }
        draw(&app).await;
    });
}

/// Draws the menu again from the interface language, the microphone of the device settings and
/// the microphones last listed. For when the language or the microphone has changed.
pub fn refresh(app: &AppHandle) {
    let app = app.clone();
    tauri::async_runtime::spawn(async move { draw(&app).await });
}

/// Asks the core for the microphones again and draws the menu if they are not the ones it shows.
/// A core that does not answer leaves the menu as it is.
pub fn rescan(app: &AppHandle) {
    let Some(state) = app.try_state::<TrayState>() else {
        return;
    };
    if state.scanning.swap(true, Ordering::AcqRel) {
        return;
    }
    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        let found = list_microphones(&app).await;
        if let Some(state) = app.try_state::<TrayState>() {
            if let Some(found) = found {
                let new = lock(&state.microphones).replace(found.clone()) != Some(found);
                if new {
                    draw(&app).await;
                }
            }
            state.scanning.store(false, Ordering::Release);
        }
    });
}

/// The microphones the core finds now (it reads the devices again for this, which it does not
/// while a recording has one open).
async fn list_microphones(app: &AppHandle) -> Option<Vec<String>> {
    let core = app.try_state::<Arc<CoreProcess>>()?;
    match core.call("audio.devices", json!({ "refresh": true })).await {
        Ok(reply) => Some(microphone_names(&reply)),
        Err(err) => {
            log::debug!("tray: the microphones were not listed: {err}");
            None
        }
    }
}

/// Reads what the menu shows and hands it to the main thread to draw.
async fn draw(app: &AppHandle) {
    let Some(state) = app.try_state::<TrayState>() else {
        return;
    };
    let _one_at_a_time = state.drawing.lock().await;
    let language = app
        .try_state::<LanguageCell>()
        .map_or_else(|| DEFAULT_LANGUAGE.to_owned(), |cell| cell.get());
    let selected = match app.try_state::<DeviceStore>() {
        Some(store) => store.get().await.microphone,
        None => None,
    };
    let microphones = Microphones {
        found: lock(&state.microphones).clone(),
        selected,
    };
    let entries = model(
        &language,
        &version(app),
        &microphones,
        state.background.load(Ordering::Acquire),
    );

    // Items are made on the main thread; one hop for the whole menu keeps this task from waiting
    // there for each of them.
    let handle = app.clone();
    let drawn = app.run_on_main_thread(move || {
        let Some(tray) = handle.tray_by_id(TRAY_ID) else {
            return;
        };
        if let Err(err) = build_menu(&handle, &entries).and_then(|menu| tray.set_menu(Some(menu))) {
            log::warn!("tray: could not draw the menu: {err}");
        }
    });
    if let Err(err) = drawn {
        log::warn!("tray: could not draw the menu: {err}");
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // ---- the menu ----

    /// The menu as lines of text: `-` for a separator, `x` or `o` for a ticked or an unticked
    /// item, `(label)` for an item that is off, and the entries of a submenu indented under it.
    fn outline(entries: &[Entry]) -> Vec<String> {
        let mut lines = Vec::new();
        for entry in entries {
            match entry {
                Entry::Separator => lines.push("-".to_owned()),
                Entry::Item { label, enabled, .. } if *enabled => lines.push(label.clone()),
                Entry::Item { label, .. } => lines.push(format!("({label})")),
                Entry::Check { label, checked, .. } => {
                    lines.push(format!("{} {label}", if *checked { "x" } else { "o" }));
                }
                Entry::Submenu { label, entries } => {
                    lines.push(format!("{label} >"));
                    lines.extend(outline(entries).into_iter().map(|line| format!("  {line}")));
                }
            }
        }
        lines
    }

    fn listed(names: &[&str], selected: Option<&str>) -> Microphones {
        Microphones {
            found: Some(names.iter().map(|name| (*name).to_owned()).collect()),
            selected: selected.map(str::to_owned),
        }
    }

    #[test]
    fn the_menu_in_traditional_chinese() {
        let menu = model(
            "zh-TW",
            "0.1.0",
            &listed(&["麥克風 (Realtek)", "耳機"], None),
            false,
        );
        assert_eq!(
            outline(&menu),
            [
                "打開 Raphael 首頁",
                "顯示歷史紀錄",
                "將單字加入字典",
                "-",
                "設定…",
                "選擇麥克風 >",
                "  x 自動偵測",
                "  o 麥克風 (Realtek)",
                "  o 耳機",
                "-",
                "(版本 0.1.0)",
                "結束 Raphael",
            ]
        );
    }

    #[test]
    fn the_menu_in_english() {
        let menu = model("en", "0.1.0", &listed(&["Headset"], Some("Headset")), false);
        assert_eq!(
            outline(&menu),
            [
                "Open Raphael Home",
                "Show History",
                "Add Word to Dictionary",
                "-",
                "Settings…",
                "Choose Microphone >",
                "  o Auto-detect",
                "  x Headset",
                "-",
                "(Version 0.1.0)",
                "Quit Raphael",
            ]
        );
    }

    #[test]
    fn the_menu_in_japanese() {
        let menu = model("ja", "0.1.0", &listed(&[], None), false);
        assert_eq!(
            outline(&menu),
            [
                "Raphael のホームを開く",
                "履歴を表示",
                "辞書に単語を追加",
                "-",
                "設定…",
                "マイクを選択 >",
                "  x 自動検出",
                "-",
                "(バージョン 0.1.0)",
                "Raphael を終了",
            ]
        );
    }

    #[test]
    fn a_language_the_menu_does_not_have_is_chinese() {
        let microphones = listed(&["Headset"], None);
        for language in ["", "fr", "zh-CN", "EN", "zh"] {
            assert_eq!(
                model(language, "0.1.0", &microphones, true),
                model("zh-TW", "0.1.0", &microphones, true),
                "{language:?}"
            );
        }
    }

    #[test]
    fn a_menu_speaks_one_language_only() {
        fn has_kana(text: &str) -> bool {
            text.chars().any(|c| ('\u{3040}'..='\u{30ff}').contains(&c))
        }
        fn has_han(text: &str) -> bool {
            text.chars().any(|c| ('\u{3400}'..='\u{9fff}').contains(&c))
        }
        for word in [
            OPEN,
            HISTORY,
            ADD_WORD,
            STOP_BACKGROUND,
            SETTINGS,
            MICROPHONE,
            AUTOMATIC,
            DISCONNECTED,
            VERSION,
            QUIT,
        ] {
            assert!(!has_kana(word.zh), "{}", word.zh);
            assert!(has_han(word.zh), "{}", word.zh);
            // Some Japanese words are kanji alone, like the Chinese.
            assert!(has_kana(word.ja) || has_han(word.ja), "{}", word.ja);
            assert!(!has_han(word.en) && !has_kana(word.en), "{}", word.en);
        }
    }

    #[test]
    fn the_menu_offers_to_stop_the_background_task_only_while_one_runs() {
        let microphones = listed(&["Headset"], None);
        for (language, stop) in [
            ("zh-TW", "中止背景任務"),
            ("en", "Stop Background Task"),
            ("ja", "バックグラウンドタスクを中止"),
        ] {
            let quiet = outline(&model(language, "0.1.0", &microphones, false));
            let busy = outline(&model(language, "0.1.0", &microphones, true));
            assert!(!quiet.iter().any(|line| line == stop), "{language}");
            // It sits with the items that act (open, history, add a word), above the first rule.
            let at = busy.iter().position(|line| line == stop).expect(language);
            assert_eq!(at, 3, "{language}");
            assert_eq!(busy[4], "-", "{language}");
            let mut without = busy.clone();
            without.remove(at);
            assert_eq!(without, quiet, "{language}: nothing else changes");
        }
    }

    #[test]
    fn stopping_the_background_task_is_an_action_that_opens_no_page() {
        assert_eq!(Action::StopBackground.id(), "stop_background");
        assert_eq!(
            Action::from_id("stop_background"),
            Some(Action::StopBackground)
        );
        assert_eq!(Action::StopBackground.destination(), None);
    }

    #[test]
    fn the_version_line_is_the_only_item_that_is_off() {
        for language in ["zh-TW", "en", "ja"] {
            let menu = model(language, "2.4.1", &Microphones::default(), false);
            let off: Vec<_> = menu
                .iter()
                .filter_map(|entry| match entry {
                    Entry::Item {
                        id,
                        label,
                        enabled: false,
                    } => Some((id.as_str(), label.as_str())),
                    _ => None,
                })
                .collect();
            assert_eq!(off.len(), 1, "{language}");
            assert_eq!(off[0].0, VERSION_ID);
            assert!(off[0].1.ends_with("2.4.1"), "{}", off[0].1);
        }
    }

    #[test]
    fn the_microphone_in_use_is_ticked_and_no_other() {
        let names = ["A", "B", "C"];
        for chosen in names {
            let menu = microphone_entries("en", &listed(&names, Some(chosen)));
            let ticked: Vec<_> = menu
                .iter()
                .filter_map(|entry| match entry {
                    Entry::Check {
                        label,
                        checked: true,
                        ..
                    } => Some(label.as_str()),
                    _ => None,
                })
                .collect();
            assert_eq!(ticked, [chosen]);
        }
    }

    #[test]
    fn the_automatic_choice_is_ticked_when_no_microphone_is_chosen() {
        let entries = microphone_entries("en", &listed(&["A", "B"], None));
        assert_eq!(outline(&entries), ["x Auto-detect", "o A", "o B"]);
    }

    #[test]
    fn a_chosen_microphone_that_is_not_plugged_in_stays_ticked_and_says_so() {
        let entries = microphone_entries("en", &listed(&["A"], Some("Gone")));
        assert_eq!(
            outline(&entries),
            ["o Auto-detect", "o A", "x Gone (disconnected)"]
        );
        let entries = microphone_entries("zh-TW", &listed(&[], Some("耳機")));
        assert_eq!(outline(&entries), ["o 自動偵測", "x 耳機（未連接）"]);
        let entries = microphone_entries("ja", &listed(&[], Some("ヘッドセット")));
        assert_eq!(
            outline(&entries),
            ["o 自動検出", "x ヘッドセット（未接続）"]
        );
    }

    #[test]
    fn before_the_core_has_listed_the_microphones_the_chosen_one_is_not_called_unplugged() {
        let unknown = Microphones {
            found: None,
            selected: Some("Headset".to_owned()),
        };
        assert_eq!(
            outline(&microphone_entries("en", &unknown)),
            ["o Auto-detect", "x Headset"]
        );
        assert_eq!(
            outline(&microphone_entries("en", &Microphones::default())),
            ["x Auto-detect"]
        );
    }

    #[test]
    fn every_item_has_its_own_id_and_a_choice_reads_back_as_itself() {
        let menu = model("en", "0.1.0", &listed(&["A", "B"], Some("B")), true);
        let mut ids = Vec::new();
        fn collect(entries: &[Entry], ids: &mut Vec<String>) {
            for entry in entries {
                match entry {
                    Entry::Item { id, .. } | Entry::Check { id, .. } => ids.push(id.clone()),
                    Entry::Submenu { entries, .. } => collect(entries, ids),
                    Entry::Separator => {}
                }
            }
        }
        collect(&menu, &mut ids);
        let mut unique = ids.clone();
        unique.sort();
        unique.dedup();
        assert_eq!(unique.len(), ids.len(), "{ids:?}");
        for id in ids {
            match Action::from_id(&id) {
                Some(action) => assert_eq!(action.id(), id),
                None => assert_eq!(id, VERSION_ID, "only the version line does nothing"),
            }
        }
    }

    #[test]
    fn an_id_names_the_microphone_it_chooses() {
        assert_eq!(Action::from_id("mic"), Some(Action::Microphone(None)));
        assert_eq!(
            Action::from_id("mic:USB Mic: left"),
            Some(Action::Microphone(Some("USB Mic: left".to_owned())))
        );
        // A name is whatever the device is called, which may look like an id.
        let odd = Action::Microphone(Some("open".to_owned()));
        assert_eq!(Action::from_id(&odd.id()), Some(odd));
        assert_eq!(Action::from_id("mic:"), None);
        assert_eq!(Action::from_id("version"), None);
        assert_eq!(Action::from_id("pause"), None);
        assert_eq!(Action::from_id(""), None);
    }

    #[test]
    fn each_page_item_opens_its_page() {
        assert_eq!(Action::Open.destination(), Some((TrayPage::Home, None)));
        assert_eq!(
            Action::ShowHistory.destination(),
            Some((TrayPage::History, None))
        );
        assert_eq!(
            Action::AddWord.destination(),
            Some((TrayPage::Dictionary, Some(TrayAction::AddWord)))
        );
        assert_eq!(
            Action::Settings.destination(),
            Some((TrayPage::Settings, None))
        );
        assert_eq!(Action::Quit.destination(), None);
        assert_eq!(Action::Microphone(None).destination(), None);
    }

    #[test]
    fn the_microphones_are_read_from_the_cores_reply() {
        let reply = json!({
            "devices": [
                {"index": 1, "name": "Mic A", "is_default": true},
                {"index": 4, "name": "Mic B", "is_default": false},
                {"index": 5, "name": ""},
                {"index": 6},
                {"index": 7, "name": 7},
            ],
            "selected": null,
        });
        assert_eq!(microphone_names(&reply), ["Mic A", "Mic B"]);
        assert!(microphone_names(&json!({"devices": []})).is_empty());
        assert!(microphone_names(&json!({})).is_empty());
        assert!(microphone_names(&json!({"devices": "none"})).is_empty());
    }

    #[test]
    fn an_ampersand_in_a_name_is_not_a_shortcut_key() {
        assert_eq!(escape("Mic & Speaker"), "Mic && Speaker");
        assert_eq!(escape("Mic"), "Mic");
    }
}
