//! Which Raphael a build is: the release that the installer puts on the computer, or a
//! development build run from the `target` folder.
//!
//! Both run on the same computer, often at the same time. Under one identity they shared the
//! single-instance lock (so one could hand its launch to the other, or neither could), the
//! webview's data folder (whose browser process belongs to whichever started first), the start
//! with Windows entry, the log, `device.json` and the database, and a development build moved the
//! release's files about with the data migration of a newer version. A development build is
//! therefore another app: its identifier, its folders and its start with Windows entry are its
//! own. Only the speech models are shared, since they are large, are never changed once
//! downloaded, and every build reads them the same way.

use std::ffi::OsString;
use std::path::{Component, Path, PathBuf};

use tauri::utils::config::{AppDirectoriesOverride, Config};

/// What a build calls itself.
#[derive(Debug, PartialEq, Eq)]
pub struct Identity {
    /// Added to the identifier in `tauri.conf.json`. The identifier names the single-instance
    /// lock.
    pub identifier_suffix: &'static str,
    /// The folder name in `%APPDATA%` and `%LOCALAPPDATA%`.
    pub folder: &'static str,
    /// The name of the start with Windows entry in the registry.
    pub autostart_name: &'static str,
}

/// The installed app.
pub const RELEASE: Identity = Identity {
    identifier_suffix: "",
    folder: "Raphael",
    autostart_name: "Raphael",
};

/// A build with debug assertions: `tauri dev`, `cargo build`, `tauri build --debug`.
pub const DEVELOPMENT: Identity = Identity {
    identifier_suffix: ".dev",
    folder: "Raphael Dev",
    autostart_name: "Raphael Dev",
};

/// The identity of this build.
pub const fn current() -> &'static Identity {
    if cfg!(debug_assertions) {
        &DEVELOPMENT
    } else {
        &RELEASE
    }
}

impl Identity {
    /// Puts this identity into the config that `tauri.conf.json` gave (which is the release's):
    /// the identifier, and the folders of `appDirectoriesOverride` (the webview data, the logs,
    /// the window position).
    pub fn apply(&self, config: &mut Config) {
        if self == &RELEASE {
            return;
        }
        config.identifier.push_str(self.identifier_suffix);
        match config.app.app_directories_override.as_mut() {
            Some(AppDirectoriesOverride::Root(root)) => *root = self.rename(root),
            Some(AppDirectoriesOverride::Directories(dirs)) => {
                for dir in [
                    &mut dirs.config,
                    &mut dirs.data,
                    &mut dirs.local_data,
                    &mut dirs.cache,
                    &mut dirs.log,
                ]
                .into_iter()
                .flatten()
                {
                    *dir = self.rename(dir);
                }
            }
            None => {}
        }
    }

    /// `path` with each folder named like the release's folder renamed to this one's.
    fn rename(&self, path: &Path) -> PathBuf {
        path.components()
            .map(|component| match component {
                Component::Normal(name) if name == RELEASE.folder => {
                    Component::Normal(self.folder.as_ref())
                }
                other => other,
            })
            .collect()
    }

    /// The environment that gives the core and the device settings this identity's data folder.
    /// `data_dir`, `models_dir`, `appdata` and `local_appdata` are the values of
    /// `RAPHAEL_DATA_DIR`, `RAPHAEL_MODELS_DIR`, `APPDATA` and `LOCALAPPDATA`.
    ///
    /// The release keeps the defaults (the core and the app both default to `%APPDATA%\Raphael`).
    /// A development build gets `%APPDATA%\Raphael Dev` and the release's models in
    /// `%LOCALAPPDATA%\Raphael\models`. A `RAPHAEL_DATA_DIR` that is already set (the tests, the
    /// end-to-end runs) is left as it is, with whatever models folder goes with it.
    pub fn data_env(
        &self,
        data_dir: Option<OsString>,
        models_dir: Option<OsString>,
        appdata: Option<OsString>,
        local_appdata: Option<OsString>,
    ) -> Vec<(&'static str, PathBuf)> {
        let set = |value: &Option<OsString>| value.as_ref().is_some_and(|value| !value.is_empty());
        if self == &RELEASE || set(&data_dir) {
            return Vec::new();
        }
        let mut env = Vec::new();
        if let Some(appdata) = appdata.filter(|dir| !dir.is_empty()) {
            env.push(("RAPHAEL_DATA_DIR", Path::new(&appdata).join(self.folder)));
        }
        if !set(&models_dir)
            && let Some(local) = local_appdata.filter(|dir| !dir.is_empty())
        {
            env.push((
                "RAPHAEL_MODELS_DIR",
                Path::new(&local).join(RELEASE.folder).join("models"),
            ));
        }
        env
    }
}

/// Gives this process the data folder of its identity (see [`Identity::data_env`]). The core
/// inherits the environment, and the device settings and the data migration read it.
///
/// # Safety
///
/// It changes the environment of the process, so no other thread may be running.
pub unsafe fn use_own_data(identity: &Identity) {
    let env = identity.data_env(
        std::env::var_os("RAPHAEL_DATA_DIR"),
        std::env::var_os("RAPHAEL_MODELS_DIR"),
        std::env::var_os("APPDATA"),
        std::env::var_os("LOCALAPPDATA"),
    );
    for (name, value) in env {
        // SAFETY: the caller promises that no other thread runs.
        unsafe { std::env::set_var(name, value) };
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn config() -> Config {
        serde_json::from_str(include_str!("../tauri.conf.json")).unwrap()
    }

    fn directories(config: &Config) -> Vec<PathBuf> {
        match config.app.app_directories_override.as_ref() {
            Some(AppDirectoriesOverride::Directories(dirs)) => [
                &dirs.config,
                &dirs.data,
                &dirs.local_data,
                &dirs.cache,
                &dirs.log,
            ]
            .into_iter()
            .map(|dir| dir.clone().unwrap())
            .collect(),
            other => panic!("expected the folders one by one, not {other:?}"),
        }
    }

    #[test]
    fn this_build_is_a_development_build_in_tests_and_a_release_otherwise() {
        assert_eq!(current() == &DEVELOPMENT, cfg!(debug_assertions));
    }

    #[test]
    fn the_release_keeps_what_the_config_says() {
        let mut release = config();
        RELEASE.apply(&mut release);
        assert_eq!(release.identifier, "app.raphael.desktop");
        assert_eq!(directories(&release), directories(&config()));
    }

    #[test]
    fn a_development_build_shares_no_lock_and_no_folder_with_the_release() {
        let release = config();
        let mut development = config();
        DEVELOPMENT.apply(&mut development);

        // The single-instance lock is named after the identifier.
        assert_eq!(development.identifier, "app.raphael.desktop.dev");
        let dev_dirs = directories(&development);
        assert_eq!(
            dev_dirs,
            [
                "$DATA/Raphael Dev",
                "$DATA/Raphael Dev",
                "$LOCALDATA/Raphael Dev",
                "$LOCALDATA/Raphael Dev/cache",
                "$LOCALDATA/Raphael Dev/logs",
            ]
            .map(PathBuf::from)
        );
        for dir in directories(&release) {
            assert!(!dev_dirs.contains(&dir), "{} is shared", dir.display());
        }
        assert_ne!(DEVELOPMENT.autostart_name, RELEASE.autostart_name);
        assert_ne!(DEVELOPMENT.folder, RELEASE.folder);
    }

    #[test]
    fn a_folder_that_only_contains_the_name_is_left_alone() {
        assert_eq!(
            DEVELOPMENT.rename(Path::new("$LOCALDATA/RaphaelTest/Raphael/logs")),
            Path::new("$LOCALDATA/RaphaelTest/Raphael Dev/logs")
        );
    }

    #[test]
    fn a_development_build_keeps_its_data_apart_and_reads_the_release_models() {
        let env = DEVELOPMENT.data_env(
            None,
            None,
            Some("C:/u/Roaming".into()),
            Some("C:/u/Local".into()),
        );
        assert_eq!(
            env,
            [
                (
                    "RAPHAEL_DATA_DIR",
                    PathBuf::from("C:/u/Roaming/Raphael Dev")
                ),
                (
                    "RAPHAEL_MODELS_DIR",
                    PathBuf::from("C:/u/Local/Raphael/models")
                ),
            ]
        );
        // An empty value counts as not set, as everywhere else.
        assert_eq!(
            DEVELOPMENT.data_env(
                Some("".into()),
                Some("".into()),
                Some("C:/u/Roaming".into()),
                Some("C:/u/Local".into()),
            ),
            env
        );
    }

    #[test]
    fn a_data_folder_chosen_by_the_caller_is_kept() {
        let appdata = || Some(OsString::from("C:/u/Roaming"));
        let local = || Some(OsString::from("C:/u/Local"));
        assert!(
            DEVELOPMENT
                .data_env(Some("D:/scratch".into()), None, appdata(), local())
                .is_empty()
        );
        assert_eq!(
            DEVELOPMENT.data_env(None, Some("D:/models".into()), appdata(), local()),
            [(
                "RAPHAEL_DATA_DIR",
                PathBuf::from("C:/u/Roaming/Raphael Dev")
            )]
        );
    }

    #[test]
    fn the_release_changes_no_environment() {
        assert!(
            RELEASE
                .data_env(None, None, Some("C:/r".into()), Some("C:/l".into()))
                .is_empty()
        );
    }
}
