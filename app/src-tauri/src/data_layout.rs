//! Where Raphael keeps its files on Windows, and the move from where earlier builds kept them.
//!
//! - `%APPDATA%\Raphael` (Roaming): small user data (the database, `device.json`,
//!   `integrations.json`, the window position). It may follow a profile from PC to PC.
//! - `%LOCALAPPDATA%\Raphael` (this PC only): `models` (large, can be downloaded again), `logs`
//!   and `WebView2` (the webview's cache and local storage).
//!
//! Earlier builds put the models in the Roaming folder and the logs, the webview data and the
//! window position under the folders named after the app identifier. `migrate` moves what is
//! worth keeping and removes what is not, and does nothing the second time. It runs once, before
//! any window exists and before the core starts, and it touches only the exact paths named below.
//!
//! The speech models are never left out of reach. They are renamed into place, which on one
//! volume is instant (the usual case: both folders are on the user's profile drive). When a
//! rename is not possible they are copied by a background thread while the core keeps using the
//! old folder, and the old folder is removed by the next start, after the core that used it has
//! exited. A dictation therefore never waits for the move and never loses its models.

use std::ffi::OsString;
use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime};

use log::Level;

/// The webview's data folder, inside the local folder.
pub const WEBVIEW_DIR: &str = "WebView2";
/// The identifier of earlier builds, which named their folders.
const OLD_IDENTIFIER: &str = "app.raphael.desktop";
/// A `device.json.bad` (a settings file that could not be read) is kept this long, then deleted.
const BAD_SETTINGS_MAX_AGE: Duration = Duration::from_secs(7 * 24 * 60 * 60);

/// The four folders involved.
#[derive(Debug, Clone)]
pub struct Roots {
    /// `%APPDATA%\Raphael`
    pub roaming: PathBuf,
    /// `%LOCALAPPDATA%\Raphael`
    pub local: PathBuf,
    /// `%APPDATA%\app.raphael.desktop`
    pub old_roaming: PathBuf,
    /// `%LOCALAPPDATA%\app.raphael.desktop`
    pub old_local: PathBuf,
}

impl Roots {
    /// The folders for the values of `APPDATA` and `LOCALAPPDATA`; `None` when either is missing
    /// or empty (not Windows).
    pub fn new(appdata: Option<OsString>, local_appdata: Option<OsString>) -> Option<Self> {
        let appdata = PathBuf::from(appdata.filter(|dir| !dir.is_empty())?);
        let local = PathBuf::from(local_appdata.filter(|dir| !dir.is_empty())?);
        Some(Self {
            roaming: appdata.join("Raphael"),
            old_roaming: appdata.join(OLD_IDENTIFIER),
            local: local.join("Raphael"),
            old_local: local.join(OLD_IDENTIFIER),
        })
    }

    pub fn from_env() -> Option<Self> {
        Self::new(
            std::env::var_os("APPDATA"),
            std::env::var_os("LOCALAPPDATA"),
        )
    }

    fn old_models(&self) -> PathBuf {
        self.roaming.join("models")
    }

    fn models(&self) -> PathBuf {
        self.local.join("models")
    }

    fn models_partial(&self) -> PathBuf {
        self.local.join("models.partial")
    }

    fn old_webview(&self) -> PathBuf {
        self.old_local.join("EBWebView")
    }

    /// The webview's data folder, shared by every window of the app.
    pub fn webview(&self) -> PathBuf {
        self.local.join(WEBVIEW_DIR)
    }
}

/// Copying the models by hand, for when they could not be renamed.
#[derive(Debug, PartialEq, Eq)]
pub struct CopyJob {
    pub from: PathBuf,
    pub to: PathBuf,
    partial: PathBuf,
}

/// What `migrate` did and what the app has to do next.
#[derive(Debug, Default)]
pub struct Outcome {
    /// What happened, for the log (which does not exist yet when `migrate` runs).
    pub notes: Vec<(Level, String)>,
    /// The folder the core must use for the models instead of the default, when the models could
    /// not be moved and are still where earlier builds left them.
    pub models_dir: Option<PathBuf>,
    /// A copy to run in the background; the core uses `models_dir` meanwhile.
    pub copy: Option<CopyJob>,
}

impl Outcome {
    fn note(&mut self, level: Level, message: String) {
        self.notes.push((level, message));
    }
}

/// What is not worth a note: a thing that is already gone.
fn gone(err: &io::Error) -> bool {
    err.kind() == io::ErrorKind::NotFound
}

/// The names in `dir` (empty when it is not there).
fn names(dir: &Path) -> Vec<OsString> {
    fs::read_dir(dir)
        .map(|entries| entries.flatten().map(|entry| entry.file_name()).collect())
        .unwrap_or_default()
}

/// Moves what earlier builds left in the wrong place. `models` says whether to deal with the
/// models (not when `RAPHAEL_MODELS_DIR` puts them elsewhere). Never fails: whatever cannot be
/// done is noted and tried again at the next start.
pub fn migrate(roots: &Roots, models: bool, now: SystemTime) -> Outcome {
    let mut outcome = Outcome::default();
    if models {
        migrate_models(roots, &mut outcome);
    }
    migrate_webview(roots, &mut outcome);
    migrate_window_state(roots, &mut outcome);
    remove_stale(roots, now, &mut outcome);
    outcome
}

fn migrate_models(roots: &Roots, outcome: &mut Outcome) {
    let (old, new) = (roots.old_models(), roots.models());
    if !old.is_dir() {
        return;
    }
    let missing: Vec<OsString> = {
        let present = names(&new);
        names(&old)
            .into_iter()
            .filter(|name| !present.contains(name))
            .collect()
    };
    if missing.is_empty() {
        // Everything in the old folder is in the new one: left over from a copy, or empty.
        match fs::remove_dir_all(&old) {
            Ok(()) => outcome.note(
                Level::Info,
                format!("removed the old models folder {}", old.display()),
            ),
            Err(err) => outcome.note(
                Level::Warn,
                format!(
                    "could not remove the old models folder {}: {err}",
                    old.display()
                ),
            ),
        }
        return;
    }
    if !new.exists() {
        if let Some(parent) = new.parent()
            && let Err(err) = fs::create_dir_all(parent)
        {
            outcome.note(
                Level::Warn,
                format!("could not create {}: {err}", parent.display()),
            );
        }
        match fs::rename(&old, &new) {
            Ok(()) => {
                outcome.note(
                    Level::Info,
                    format!(
                        "moved the models from {} to {}",
                        old.display(),
                        new.display()
                    ),
                );
                return;
            }
            Err(err) => {
                let partial = roots.models_partial();
                outcome.note(
                    Level::Warn,
                    format!(
                        "could not rename the models folder ({err}); the models stay in {} \
                         while they are copied to {}",
                        old.display(),
                        new.display()
                    ),
                );
                outcome.models_dir = Some(old.clone());
                outcome.copy = Some(CopyJob {
                    from: old,
                    to: new,
                    partial,
                });
                return;
            }
        }
    }
    // The new folder exists but lacks some of the models (a partial earlier move): move those.
    let mut failed = false;
    for name in &missing {
        if let Err(err) = fs::rename(old.join(name), new.join(name)) {
            failed = true;
            outcome.note(
                Level::Warn,
                format!(
                    "could not move {} to {}: {err}",
                    name.to_string_lossy(),
                    new.display()
                ),
            );
        }
    }
    if failed {
        outcome.models_dir = Some(old);
    } else {
        outcome.note(
            Level::Info,
            format!("moved the remaining models into {}", new.display()),
        );
        let _ = fs::remove_dir(&old);
    }
}

/// Copies the models into `models.partial` and renames that to `models`, so that `models` is
/// either absent or complete. The old folder is removed by the next start.
pub fn run_copy(job: &CopyJob) -> io::Result<()> {
    if job.to.exists() {
        return Ok(());
    }
    match fs::remove_dir_all(&job.partial) {
        Ok(()) => {}
        Err(err) if gone(&err) => {}
        Err(err) => return Err(err),
    }
    let result =
        copy_tree(&job.from, &job.partial).and_then(|()| fs::rename(&job.partial, &job.to));
    if result.is_err() {
        let _ = fs::remove_dir_all(&job.partial);
    }
    result
}

fn copy_tree(from: &Path, to: &Path) -> io::Result<()> {
    fs::create_dir_all(to)?;
    for entry in fs::read_dir(from)? {
        let entry = entry?;
        let target = to.join(entry.file_name());
        if entry.file_type()?.is_dir() {
            copy_tree(&entry.path(), &target)?;
        } else {
            fs::copy(entry.path(), target)?;
        }
    }
    Ok(())
}

/// The webview folder keeps its cache and the local storage, so the whole folder is renamed.
/// Old logs are only history: they go.
fn migrate_webview(roots: &Roots, outcome: &mut Outcome) {
    let (old, new) = (roots.old_webview(), roots.webview());
    if old.is_dir() {
        if new.exists() {
            // Already in use at the new place: the old copy is only a cache nobody reads.
            remove_tree(&old, outcome);
        } else {
            let moved = fs::create_dir_all(&roots.local).and_then(|()| fs::rename(&old, &new));
            match moved {
                Ok(()) => outcome.note(
                    Level::Info,
                    format!("moved the webview data to {}", new.display()),
                ),
                Err(err) => outcome.note(
                    Level::Warn,
                    format!("could not move the webview data ({err}); it starts fresh"),
                ),
            }
        }
    }
    remove_tree(&roots.old_local.join("logs"), outcome);
    remove_empty(&roots.old_local, outcome);
}

/// `.window-state.json` (the main window's place and size) moves with the other small data.
fn migrate_window_state(roots: &Roots, outcome: &mut Outcome) {
    let name = ".window-state.json";
    let (old, new) = (roots.old_roaming.join(name), roots.roaming.join(name));
    if !old.is_file() {
        return;
    }
    if new.exists() {
        remove_file(&old, outcome);
    } else {
        let moved = fs::create_dir_all(&roots.roaming).and_then(|()| fs::rename(&old, &new));
        if let Err(err) = moved {
            outcome.note(Level::Warn, format!("could not move {name}: {err}"));
        }
    }
    remove_empty(&roots.old_roaming, outcome);
}

/// The old Roaming folder if it is empty, and a `device.json.bad` that has been kept long enough.
fn remove_stale(roots: &Roots, now: SystemTime, outcome: &mut Outcome) {
    remove_empty(&roots.old_roaming, outcome);

    let bad = roots.roaming.join("device.json.bad");
    let age = fs::metadata(&bad)
        .and_then(|meta| meta.modified())
        .ok()
        .and_then(|modified| now.duration_since(modified).ok());
    if age.is_some_and(|age| age > BAD_SETTINGS_MAX_AGE) {
        remove_file(&bad, outcome);
    }
}

fn remove_file(path: &Path, outcome: &mut Outcome) {
    match fs::remove_file(path) {
        Ok(()) => outcome.note(Level::Info, format!("removed {}", path.display())),
        Err(err) if gone(&err) => {}
        Err(err) => outcome.note(
            Level::Warn,
            format!("could not remove {}: {err}", path.display()),
        ),
    }
}

fn remove_tree(path: &Path, outcome: &mut Outcome) {
    match fs::remove_dir_all(path) {
        Ok(()) => outcome.note(Level::Info, format!("removed {}", path.display())),
        Err(err) if gone(&err) => {}
        Err(err) => outcome.note(
            Level::Warn,
            format!("could not remove {}: {err}", path.display()),
        ),
    }
}

/// Removes a folder only if nothing is in it.
fn remove_empty(path: &Path, outcome: &mut Outcome) {
    // A folder that is gone already or not empty needs nothing done.
    if fs::remove_dir(path).is_ok() {
        outcome.note(
            Level::Info,
            format!("removed the empty folder {}", path.display()),
        );
    }
}

/// What the app does at start: migrates unless `RAPHAEL_DATA_DIR` isolates the run (tests and
/// end-to-end runs must never move the real user's files).
pub fn migrate_at_start() -> Outcome {
    let isolated = std::env::var_os("RAPHAEL_DATA_DIR").is_some_and(|dir| !dir.is_empty());
    let models_elsewhere =
        std::env::var_os("RAPHAEL_MODELS_DIR").is_some_and(|dir| !dir.is_empty());
    match Roots::from_env() {
        Some(roots) if !isolated => migrate(&roots, !models_elsewhere, SystemTime::now()),
        _ => Outcome::default(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn roots(root: &Path) -> Roots {
        Roots::new(
            Some(root.join("Roaming").into_os_string()),
            Some(root.join("Local").into_os_string()),
        )
        .unwrap()
    }

    fn write(path: &Path, text: &str) {
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, text).unwrap();
    }

    fn now() -> SystemTime {
        SystemTime::now()
    }

    #[test]
    fn the_folders_follow_the_windows_convention() {
        let r = roots(Path::new("C:/u"));
        assert_eq!(r.roaming, Path::new("C:/u/Roaming/Raphael"));
        assert_eq!(r.local, Path::new("C:/u/Local/Raphael"));
        assert_eq!(r.models(), Path::new("C:/u/Local/Raphael/models"));
        assert_eq!(r.webview(), Path::new("C:/u/Local/Raphael/WebView2"));
        assert_eq!(r.old_roaming, Path::new("C:/u/Roaming/app.raphael.desktop"));
        assert!(Roots::new(None, Some("x".into())).is_none());
        assert!(Roots::new(Some("x".into()), Some("".into())).is_none());
    }

    #[test]
    fn the_app_directories_in_the_config_are_the_ones_migrated_to() {
        // Tauri resolves the log, config and webview folders from these, so a change of one
        // without the other would put files where the migration does not look.
        let config: serde_json::Value =
            serde_json::from_str(include_str!("../tauri.conf.json")).unwrap();
        let overrides = &config["app"]["appDirectoriesOverride"];
        assert_eq!(overrides["config"], "$DATA/Raphael");
        assert_eq!(overrides["data"], "$DATA/Raphael");
        assert_eq!(overrides["localData"], "$LOCALDATA/Raphael");
        assert_eq!(overrides["cache"], "$LOCALDATA/Raphael/cache");
        assert_eq!(overrides["log"], "$LOCALDATA/Raphael/logs");
        assert_eq!(config["app"]["windows"][0]["create"], false);
    }

    #[test]
    fn the_uninstaller_removes_both_folders_and_the_old_ones() {
        let hook = include_str!("../installer-hooks.nsh").replace("\r\n", "\n");
        let removals: Vec<&str> = hook
            .lines()
            .filter_map(|line| line.trim().strip_prefix("RmDir /r "))
            .collect();
        for folder in [
            r#""$APPDATA\Raphael""#,
            r#""$LOCALAPPDATA\Raphael""#,
            r#""$APPDATA\app.raphael.desktop""#,
            r#""$LOCALAPPDATA\app.raphael.desktop""#,
        ] {
            assert!(removals.contains(&folder), "{folder} in {removals:?}");
        }
        // Only when the user ticked the box, and never on an update.
        assert!(hook.contains("$DeleteAppDataCheckboxState = 1"));
        assert!(hook.contains("$UpdateMode <> 1"));
    }

    #[test]
    fn the_models_are_moved_with_a_rename() {
        let dir = tempfile::tempdir().unwrap();
        let r = roots(dir.path());
        write(&r.old_models().join("qwen/model.onnx"), "weights");
        write(&r.roaming.join("raphael.db"), "db");
        let outcome = migrate(&r, true, now());
        assert_eq!(
            fs::read_to_string(r.models().join("qwen/model.onnx")).unwrap(),
            "weights"
        );
        assert!(!r.old_models().exists());
        assert!(r.roaming.join("raphael.db").exists(), "small data stays");
        assert!(outcome.models_dir.is_none() && outcome.copy.is_none());
    }

    #[test]
    fn migrating_twice_changes_nothing() {
        let dir = tempfile::tempdir().unwrap();
        let r = roots(dir.path());
        write(&r.old_models().join("a/model"), "1");
        migrate(&r, true, now());
        let second = migrate(&r, true, now());
        assert!(second.notes.is_empty(), "{:?}", second.notes);
        assert_eq!(fs::read_to_string(r.models().join("a/model")).unwrap(), "1");
    }

    #[test]
    fn no_models_anywhere_is_fine() {
        let dir = tempfile::tempdir().unwrap();
        let r = roots(dir.path());
        let outcome = migrate(&r, true, now());
        assert!(outcome.notes.is_empty());
        assert!(!r.local.exists(), "nothing is created for nothing");
    }

    #[test]
    fn a_partly_moved_set_is_completed_without_overwriting() {
        let dir = tempfile::tempdir().unwrap();
        let r = roots(dir.path());
        write(&r.models().join("a/model"), "new a");
        write(&r.old_models().join("a/model"), "old a");
        write(&r.old_models().join("b/model"), "old b");
        let outcome = migrate(&r, true, now());
        assert_eq!(
            fs::read_to_string(r.models().join("a/model")).unwrap(),
            "new a"
        );
        assert_eq!(
            fs::read_to_string(r.models().join("b/model")).unwrap(),
            "old b"
        );
        assert!(outcome.models_dir.is_none());
        // `a` is in both, so the old folder still has it until the next start sees it is covered.
        migrate(&r, true, now());
        assert!(!r.old_models().exists());
    }

    #[test]
    fn a_failed_move_keeps_the_old_models_in_use() {
        let dir = tempfile::tempdir().unwrap();
        let r = roots(dir.path());
        write(&r.old_models().join("a/model"), "old a");
        // A file where the new folder should be: nothing can be moved into it.
        write(&r.models(), "not a folder");
        let outcome = migrate(&r, true, now());
        assert_eq!(
            outcome.models_dir.as_deref(),
            Some(r.old_models().as_path())
        );
        assert!(outcome.notes.iter().any(|(level, _)| *level == Level::Warn));
        assert_eq!(
            fs::read_to_string(r.old_models().join("a/model")).unwrap(),
            "old a"
        );
    }

    #[test]
    fn a_rename_that_cannot_happen_leaves_a_copy_to_run_in_the_background() {
        let dir = tempfile::tempdir().unwrap();
        let r = roots(dir.path());
        write(&r.old_models().join("a/model"), "weights");
        // Hand the copy job over directly: rename failure itself cannot be forced portably.
        let job = CopyJob {
            from: r.old_models(),
            to: r.models(),
            partial: r.models_partial(),
        };
        run_copy(&job).unwrap();
        assert_eq!(
            fs::read_to_string(r.models().join("a/model")).unwrap(),
            "weights"
        );
        assert!(!r.models_partial().exists());
        assert!(
            r.old_models().exists(),
            "the old folder stays until the next start"
        );
        let next = migrate(&r, true, now());
        assert!(!r.old_models().exists());
        assert!(next.models_dir.is_none());
    }

    #[test]
    fn a_stale_partial_copy_is_discarded_and_an_existing_target_is_left_alone() {
        let dir = tempfile::tempdir().unwrap();
        let r = roots(dir.path());
        write(&r.old_models().join("a"), "1");
        write(&r.models_partial().join("junk"), "half");
        let job = CopyJob {
            from: r.old_models(),
            to: r.models(),
            partial: r.models_partial(),
        };
        run_copy(&job).unwrap();
        assert!(!r.models().join("junk").exists());
        write(&r.models().join("kept"), "x");
        fs::remove_dir_all(r.models().join("a")).ok();
        run_copy(&job).unwrap();
        assert!(r.models().join("kept").exists());
    }

    #[test]
    fn a_failed_copy_leaves_no_models_folder_behind() {
        let dir = tempfile::tempdir().unwrap();
        let r = roots(dir.path());
        let job = CopyJob {
            from: r.old_models(), // does not exist
            to: r.models(),
            partial: r.models_partial(),
        };
        assert!(run_copy(&job).is_err());
        assert!(!r.models().exists() && !r.models_partial().exists());
    }

    #[test]
    fn the_models_are_left_alone_when_another_folder_is_named() {
        let dir = tempfile::tempdir().unwrap();
        let r = roots(dir.path());
        write(&r.old_models().join("a"), "1");
        migrate(&r, false, now());
        assert!(r.old_models().join("a").exists());
        assert!(!r.models().exists());
    }

    #[test]
    fn the_webview_data_is_moved_so_local_storage_survives() {
        let dir = tempfile::tempdir().unwrap();
        let r = roots(dir.path());
        write(
            &r.old_webview()
                .join("EBWebView/Default/Local Storage/leveldb/1"),
            "hint",
        );
        write(&r.old_local.join("logs/raphael.log"), "old log");
        migrate(&r, true, now());
        assert!(
            r.webview()
                .join("EBWebView/Default/Local Storage/leveldb/1")
                .exists()
        );
        assert!(
            !r.old_local.exists(),
            "the empty old folder goes with its logs"
        );
        migrate(&r, true, now());
        assert!(r.webview().exists());
    }

    #[test]
    fn an_old_webview_folder_is_dropped_once_the_new_one_is_in_use() {
        let dir = tempfile::tempdir().unwrap();
        let r = roots(dir.path());
        write(&r.webview().join("current"), "new");
        write(&r.old_webview().join("old"), "old");
        migrate(&r, true, now());
        assert!(r.webview().join("current").exists());
        assert!(!r.old_local.exists());
    }

    #[test]
    fn the_old_folder_is_kept_when_it_holds_something_unknown() {
        let dir = tempfile::tempdir().unwrap();
        let r = roots(dir.path());
        write(&r.old_local.join("logs/raphael.log"), "log");
        write(&r.old_local.join("mystery.txt"), "not ours to delete");
        write(&r.old_roaming.join("other.txt"), "keep");
        migrate(&r, true, now());
        assert!(r.old_local.join("mystery.txt").exists());
        assert!(r.old_roaming.join("other.txt").exists());
        assert!(!r.old_local.join("logs").exists());
    }

    #[test]
    fn an_empty_old_roaming_folder_is_removed() {
        let dir = tempfile::tempdir().unwrap();
        let r = roots(dir.path());
        fs::create_dir_all(&r.old_roaming).unwrap();
        migrate(&r, true, now());
        assert!(!r.old_roaming.exists());
    }

    #[test]
    fn the_window_position_moves_to_the_data_folder() {
        let dir = tempfile::tempdir().unwrap();
        let r = roots(dir.path());
        write(&r.old_roaming.join(".window-state.json"), "{}");
        migrate(&r, true, now());
        assert_eq!(
            fs::read_to_string(r.roaming.join(".window-state.json")).unwrap(),
            "{}"
        );
        assert!(!r.old_roaming.exists());
        // An existing one is not replaced.
        write(&r.old_roaming.join(".window-state.json"), "{old}");
        migrate(&r, true, now());
        assert_eq!(
            fs::read_to_string(r.roaming.join(".window-state.json")).unwrap(),
            "{}"
        );
        assert!(!r.old_roaming.exists());
    }

    #[test]
    fn only_an_old_device_json_bad_is_deleted() {
        let dir = tempfile::tempdir().unwrap();
        let r = roots(dir.path());
        write(&r.roaming.join("device.json.bad"), "broken");
        write(&r.roaming.join("device.json"), "{}");
        migrate(&r, true, now());
        assert!(
            r.roaming.join("device.json.bad").exists(),
            "a new one is kept"
        );
        let later = now() + Duration::from_secs(8 * 24 * 60 * 60);
        migrate(&r, true, later);
        assert!(!r.roaming.join("device.json.bad").exists());
        assert!(r.roaming.join("device.json").exists());
    }

    #[test]
    fn the_database_and_settings_are_never_touched() {
        let dir = tempfile::tempdir().unwrap();
        let r = roots(dir.path());
        for file in [
            "raphael.db",
            "raphael.db-wal",
            "device.json",
            "integrations.json",
        ] {
            write(&r.roaming.join(file), file);
        }
        let later = now() + Duration::from_secs(365 * 24 * 60 * 60);
        migrate(&r, true, later);
        for file in [
            "raphael.db",
            "raphael.db-wal",
            "device.json",
            "integrations.json",
        ] {
            assert_eq!(fs::read_to_string(r.roaming.join(file)).unwrap(), file);
        }
    }

    #[test]
    fn nothing_is_migrated_while_the_data_folder_is_overridden() {
        // The environment is shared by the tests of this process, so this one is the only test
        // that sets RAPHAEL_DATA_DIR, and it restores it.
        let dir = tempfile::tempdir().unwrap();
        let before = std::env::var_os("RAPHAEL_DATA_DIR");
        // SAFETY: no other test of this crate reads or writes this variable concurrently.
        unsafe { std::env::set_var("RAPHAEL_DATA_DIR", dir.path()) };
        let outcome = migrate_at_start();
        match before {
            Some(value) => unsafe { std::env::set_var("RAPHAEL_DATA_DIR", value) },
            None => unsafe { std::env::remove_var("RAPHAEL_DATA_DIR") },
        }
        assert!(outcome.notes.is_empty() && outcome.models_dir.is_none());
    }
}
