//! First run: until the first-run guide of the main window has been gone through (`onboarded` in
//! `device.json`), the main window opens by itself at start. After that the app starts hidden in
//! the tray.

use tauri::{AppHandle, Manager};

use crate::device_settings::{DeviceSettings, DeviceStore};

/// Whether the main window opens by itself at start with these settings.
pub fn opens_at_start(settings: &DeviceSettings) -> bool {
    !settings.onboarded
}

/// Whether the main window should open by itself, by what the store says now.
async fn should_open(store: &DeviceStore) -> bool {
    opens_at_start(&store.get().await)
}

/// Opens the main window if this is a first run. Called once from `setup`, after
/// `commands::start` has loaded the device settings. It cannot fail: whatever does not work is
/// logged, and the window can still be opened from the tray.
pub fn start(app: &AppHandle) {
    let app = app.clone();
    // Not in `setup` itself: the window is shown once the event loop is running.
    tauri::async_runtime::spawn(async move {
        let Some(store) = app.try_state::<DeviceStore>() else {
            log::warn!("first run: the device settings are not loaded, so the window stays hidden");
            return;
        };
        if should_open(&store).await {
            log::info!("first run: showing the main window");
            crate::show_main_window(&app);
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn a_fresh_install_opens_the_window() {
        assert!(opens_at_start(&DeviceSettings::default()));
    }

    #[test]
    fn a_finished_guide_leaves_the_window_in_the_tray() {
        let mut settings = DeviceSettings::default();
        settings.apply_patch(&json!({"onboarded": true})).unwrap();
        assert!(!opens_at_start(&settings));
    }

    #[test]
    fn other_settings_do_not_decide_it() {
        let mut settings = DeviceSettings::default();
        settings
            .apply_patch(&json!({"emblem_size": 194, "autostart": false, "appearance": "dark"}))
            .unwrap();
        assert!(opens_at_start(&settings));
    }

    #[tokio::test]
    async fn no_settings_file_is_a_first_run() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("device.json");
        let store = DeviceStore::new(path.clone(), DeviceSettings::load(&path));
        assert!(should_open(&store).await);
    }

    #[tokio::test]
    async fn a_saved_onboarded_flag_is_a_later_run() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("device.json");
        let mut settings = DeviceSettings::default();
        settings.apply_patch(&json!({"onboarded": true})).unwrap();
        settings.save(&path).unwrap();
        let store = DeviceStore::new(path.clone(), DeviceSettings::load(&path));
        assert!(!should_open(&store).await);
    }

    #[tokio::test]
    async fn finishing_the_guide_in_the_running_app_counts_at_the_next_start() {
        // the store the guide's last step writes to, and the file the next start reads
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("device.json");
        let store = DeviceStore::new(path.clone(), DeviceSettings::default());
        assert!(should_open(&store).await);
        {
            let mut current = store.lock().await;
            current.apply_patch(&json!({"onboarded": true})).unwrap();
            current.save(&path).unwrap();
        }
        assert!(!should_open(&store).await);
        assert!(!opens_at_start(&DeviceSettings::load(&path)));
    }

    #[test]
    fn a_broken_file_starts_the_guide_again() {
        // `load` moves an unreadable file aside and starts from the defaults
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("device.json");
        std::fs::write(&path, "{ not json").unwrap();
        assert!(opens_at_start(&DeviceSettings::load(&path)));
    }
}
