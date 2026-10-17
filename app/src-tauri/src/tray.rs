//! The system tray icon and its menu.
//!
//! Left click shows the main window. The right-click menu has the items below. "Open" and
//! "Quit" act at once; the two dictation items only send a [`TrayCommand`] on the channel given
//! to [`build`], whose receiving end the dictation controller holds.

use tauri::{
    AppHandle,
    menu::{Menu, MenuItem, PredefinedMenuItem},
    tray::{MouseButton, MouseButtonState, TrayIcon, TrayIconBuilder, TrayIconEvent},
};
use tokio::sync::mpsc::UnboundedSender;

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
    let open = MenuItem::with_id(app, "open", "打開拉斐爾", true, None::<&str>)?;
    let paste_last = MenuItem::with_id(app, "paste_last", "貼上上一次聽寫", true, None::<&str>)?;
    let pause = MenuItem::with_id(app, "pause", "暫停 1 小時", true, None::<&str>)?;
    let separator = PredefinedMenuItem::separator(app)?;
    let quit = MenuItem::with_id(app, "quit", "結束拉斐爾", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&open, &paste_last, &pause, &separator, &quit])?;

    let icon = app.default_window_icon().cloned().ok_or_else(|| {
        tauri::Error::InvalidIcon(std::io::Error::new(
            std::io::ErrorKind::NotFound,
            "the app has no default window icon",
        ))
    })?;

    TrayIconBuilder::with_id(TRAY_ID)
        .icon(icon)
        .tooltip("拉斐爾")
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
