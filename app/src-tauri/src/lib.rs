//! Raphael's desktop shell: a tray app with a hidden main window that starts with Windows and
//! allows only one running copy.

pub mod commands;
pub mod core_process;
pub mod device_settings;
mod dictation;
mod first_run;
pub mod holdtap;
pub mod hotkey;
mod notices;
mod overlay;
mod paste;
pub mod rpc;
mod tray;

use tauri::{AppHandle, Manager, Runtime, WindowEvent, plugin::TauriPlugin};
use tauri_plugin_autostart::MacosLauncher;
use tauri_plugin_log::{RotationStrategy, Target, TargetKind, TimezoneStrategy};
use tauri_plugin_window_state::StateFlags;

/// Label of the main window, declared in `tauri.conf.json`.
const MAIN_WINDOW: &str = "main";
/// Label of the overlay window. It is created in Rust, never by the config.
const OVERLAY_WINDOW: &str = "overlay";

/// Writes a panic to the log before the default hook runs. The release build aborts on a panic
/// (`panic = "abort"`) and has no console, so without this a crash would leave no trace.
fn log_panics() {
    let default_hook = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        let thread = std::thread::current();
        let name = thread.name().unwrap_or("unnamed");
        log::error!("panic in thread {name}: {info}");
        log::logger().flush();
        default_hook(info);
    }));
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    log_panics();
    let builder = tauri::Builder::default();

    // The single-instance plugin must come first: a second launch hands over to the running
    // copy (which shows its main window) and exits before anything else starts.
    #[cfg(any(target_os = "macos", windows, target_os = "linux"))]
    let builder = builder.plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
        show_main_window(app);
    }));

    builder
        .plugin(tauri_plugin_autostart::init(
            MacosLauncher::LaunchAgent,
            Some(vec!["--autostart"]),
        ))
        .plugin(
            tauri_plugin_window_state::Builder::new()
                .with_denylist(&[OVERLAY_WINDOW])
                // With VISIBLE the plugin would show and focus windows when it restores them.
                .with_state_flags(StateFlags::all() & !StateFlags::VISIBLE)
                .build(),
        )
        .plugin(log_plugin())
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
            commands::core_call,
            commands::device_settings_get,
            commands::device_settings_set,
            commands::keyboard_has_altgr,
            commands::hotkeys_pause,
            commands::main_events,
        ])
        .setup(|app| {
            // Tauri keeps only the last `setup` call, so each part of the app starts in its own
            // section of this one closure, in dependency order: a part that needs another part's
            // handle comes after it.

            // ---- Tray ----
            // The tray reports its menu actions on this channel; the dictation controller (below)
            // takes the receiving end.
            let (tray_commands, tray_command_rx) = tokio::sync::mpsc::unbounded_channel();
            tray::build(app.handle(), tray_commands)?;

            // ---- Overlay ----
            // Raphael's emblem in the bottom-right corner, 150 px with both subtitle lines until
            // the settings say otherwise (`Overlay::apply_settings`). The dictation controller
            // takes it from the app state as `Arc<Overlay>`. Without the overlay the app still
            // works, so a failure is only logged.
            match overlay::Overlay::create(app.handle(), 150, "both") {
                Ok(overlay) => {
                    app.manage(overlay);
                }
                Err(err) => log::error!("could not create the overlay window: {err}"),
            }

            // ---- Python core ----
            // Started in the background and kept in Tauri state for the dictation controller. The
            // app does not need it to run (tray, settings), so a core that cannot be started is
            // logged and the app carries on without dictation.
            match core_process::resolve_core_command().and_then(core_process::CoreProcess::start) {
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
            // Ties the hotkeys, the core, the overlay, pasting and the tray's dictation items
            // together. It takes whatever of them started; without the core, dictation is off.
            #[cfg(windows)]
            dictation::spawn(app.handle(), hotkey_event_rx, tray_command_rx);
            #[cfg(not(windows))]
            {
                log::error!("dictation needs Windows");
                drop((hotkey_event_rx, tray_command_rx));
            }

            // ---- Device settings ----
            // `device.json`: loads it, puts it in force (hotkeys, overlay size and subtitles,
            // theme, autostart) and keeps the core told which microphone to use. It takes the
            // overlay, the core and the hotkeys from the app state, so it comes after them.
            commands::start(app.handle());

            // ---- First run ----
            // Until the first-run guide has been gone through, the main window opens by itself.
            // It reads the device settings, so it comes after them.
            first_run::start(app.handle());

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

/// Shows the main window, restores it if it was minimized, and gives it focus.
fn show_main_window(app: &AppHandle) {
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
/// (`%LOCALAPPDATA%\app.raphael.desktop\logs`), with local timestamps. The file rotates at 1 MB
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
        .max_file_size(1_000_000)
        .rotation_strategy(RotationStrategy::KeepSome(3))
        .timezone_strategy(TimezoneStrategy::UseLocal)
        .build()
}
