//! Raphael's desktop shell: a tray app with a hidden main window that starts with Windows and
//! allows only one running copy.

mod tray;

use tauri::{AppHandle, Manager, Runtime, WindowEvent, plugin::TauriPlugin};
use tauri_plugin_autostart::MacosLauncher;
use tauri_plugin_log::{RotationStrategy, Target, TargetKind, TimezoneStrategy};
use tauri_plugin_window_state::StateFlags;

/// Label of the main window, declared in `tauri.conf.json`.
const MAIN_WINDOW: &str = "main";
/// Label of the overlay window. It is created in Rust, never by the config.
const OVERLAY_WINDOW: &str = "overlay";

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
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
        .setup(|app| {
            // Each part of the app starts in its own section below, in dependency order: a part
            // that needs another part's handle comes after it.

            // ---- Tray ----
            // The tray reports its menu actions on this channel. For now they are only logged;
            // the dictation controller takes over the receiving end.
            let (tray_commands, mut tray_command_rx) = tokio::sync::mpsc::unbounded_channel();
            tray::build(app.handle(), tray_commands)?;
            tauri::async_runtime::spawn(async move {
                while let Some(command) = tray_command_rx.recv().await {
                    log::info!("tray command: {command:?}");
                }
            });

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
/// and the three newest rotated files are kept.
fn log_plugin<R: Runtime>() -> TauriPlugin<R> {
    tauri_plugin_log::Builder::new()
        .targets([
            Target::new(TargetKind::Stdout),
            Target::new(TargetKind::LogDir {
                file_name: Some("raphael".into()),
            }),
        ])
        .level(log::LevelFilter::Info)
        .max_file_size(1_000_000)
        .rotation_strategy(RotationStrategy::KeepSome(3))
        .timezone_strategy(TimezoneStrategy::UseLocal)
        .build()
}
