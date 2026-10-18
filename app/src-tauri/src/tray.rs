//! The system tray icon and its menu.
//!
//! Left click shows the main window. The right-click menu has the items below. "Open" and
//! "Quit" act at once; the two dictation items only send a [`TrayCommand`] on the channel given
//! to [`build`], whose receiving end the dictation controller holds.

use std::path::{Path, PathBuf};
use tauri::{
    AppHandle,
    menu::{Menu, MenuItem, PredefinedMenuItem},
    tray::{MouseButton, MouseButtonState, TrayIcon, TrayIconBuilder, TrayIconEvent},
};
use tokio::sync::mpsc::UnboundedSender;

fn private_tray_icon_path(crate_dir: &Path, is_dev: bool) -> Option<PathBuf> {
    // Release builds must not read a path into the developer's checkout.
    if !is_dev {
        return None;
    }
    let path = crate_dir.join("icons-private").join("tray-icon.png");
    path.is_file().then_some(path)
}

fn tray_icon() -> tauri::image::Image<'static> {
    if let Some(path) = private_tray_icon_path(
        Path::new(env!("CARGO_MANIFEST_DIR")),
        cfg!(debug_assertions),
    ) {
        match tauri::image::Image::from_path(&path) {
            Ok(icon) => {
                log::info!("tray: using private icon from {}", path.display());
                return icon;
            }
            Err(error) => log::warn!(
                "tray: could not load private icon {}: {error}",
                path.display()
            ),
        }
    }
    log::info!("tray: using embedded public icon");
    tauri::include_image!("icons/tray-icon.png")
}

/// Id of the tray icon (`AppHandle::tray_by_id` finds it by this).
pub const TRAY_ID: &str = "raphael";

/// What a tray menu item asks the rest of the app to do.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TrayCommand {
    /// 「貼上上一次聽寫」: put the latest dictation on the clipboard again (the menu has taken the
    /// focus, so it cannot be pasted for the user) and say to press Ctrl+V.
    PasteLast,
    /// 「暫停 1 小時」: stop listening to the dictation hotkey for an hour.
    PauseOneHour,
}

/// Builds the tray icon. Menu items `paste_last` and `pause` send their [`TrayCommand`] on
/// `commands`; the icon stays for the life of the app.
pub fn build(app: &AppHandle, commands: UnboundedSender<TrayCommand>) -> tauri::Result<TrayIcon> {
    let open = MenuItem::with_id(app, "open", "打開 Raphael", true, None::<&str>)?;
    let paste_last = MenuItem::with_id(app, "paste_last", "貼上上一次聽寫", true, None::<&str>)?;
    let pause = MenuItem::with_id(app, "pause", "暫停 1 小時", true, None::<&str>)?;
    let separator = PredefinedMenuItem::separator(app)?;
    let quit = MenuItem::with_id(app, "quit", "結束 Raphael", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&open, &paste_last, &pause, &separator, &quit])?;

    let icon = tray_icon();

    TrayIconBuilder::with_id(TRAY_ID)
        .icon(icon)
        .tooltip("Raphael")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(move |app, event| match event.id().as_ref() {
            "open" => crate::show_main_window(app),
            "paste_last" => send(&commands, TrayCommand::PasteLast),
            "pause" => send(&commands, TrayCommand::PauseOneHour),
            "quit" => app.exit(0),
            other => log::warn!("unknown tray menu item: {other}"),
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                crate::show_main_window(tray.app_handle());
            }
        })
        .build(app)
}

fn send(commands: &UnboundedSender<TrayCommand>, command: TrayCommand) {
    if commands.send(command).is_err() {
        log::warn!("tray command {command:?} dropped: nobody is listening");
    }
}

#[cfg(test)]
mod tests {
    use super::private_tray_icon_path;
    use std::fs;

    #[test]
    fn private_tray_path_requires_a_file_in_a_dev_build() {
        let crate_dir = tempfile::tempdir().unwrap();
        let icon_dir = crate_dir.path().join("icons-private");
        let icon_path = icon_dir.join("tray-icon.png");
        assert_eq!(private_tray_icon_path(crate_dir.path(), true), None);

        fs::create_dir_all(&icon_path).unwrap();
        assert_eq!(private_tray_icon_path(crate_dir.path(), true), None);
        fs::remove_dir(&icon_path).unwrap();
        fs::write(&icon_path, b"path selection only").unwrap();

        assert_eq!(
            private_tray_icon_path(crate_dir.path(), true),
            Some(icon_path)
        );
        assert_eq!(private_tray_icon_path(crate_dir.path(), false), None);
    }
}
