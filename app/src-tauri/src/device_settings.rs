//! Settings that belong to this computer: the hotkeys, the microphone, how the emblem looks, the
//! theme, autostart, and whether the first-run guide has been done.
//!
//! They live in `device.json` in Raphael's data folder (`%APPDATA%\Raphael`, or the folder named
//! by `RAPHAEL_DATA_DIR`, like the core's database). The settings of the brain (monthly limit,
//! model, language, history retention) are not here: they live in the core's database and the
//! main window reads and writes them through `settings.get` and `settings.set`.

use std::ffi::OsString;
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};

use handy_keys::{Hotkey, Key, Modifiers};
use serde::de::DeserializeOwned;
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::hotkey::{Bindings, parse_binding};

/// The file in the data folder.
pub const FILE_NAME: &str = "device.json";

/// The emblem's diameters in logical pixels: small, medium (the default) and as large as in the
/// anime.
pub const EMBLEM_SIZES: [u32; 3] = [120, 150, 194];

/// Which subtitle lines the overlay shows.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Subtitles {
    #[default]
    Both,
    Zh,
    Ja,
    Off,
}

impl Subtitles {
    /// The spelling the overlay page takes (`"both"`, `"zh"`, `"ja"`, `"off"`).
    pub fn as_str(self) -> &'static str {
        match self {
            Subtitles::Both => "both",
            Subtitles::Zh => "zh",
            Subtitles::Ja => "ja",
            Subtitles::Off => "off",
        }
    }
}

/// The theme of the main window.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Appearance {
    #[default]
    System,
    Light,
    Dark,
}

/// The settings in `device.json`.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct DeviceSettings {
    /// Key combinations that dictate, in the spelling of `hotkey::parse_binding`.
    pub dictation_keys: Vec<String>,
    /// Key combinations that paste the latest dictation again.
    pub paste_last_keys: Vec<String>,
    /// The name of the microphone; `None` is the system's default one.
    pub microphone: Option<String>,
    /// The emblem's diameter, one of [`EMBLEM_SIZES`].
    pub emblem_size: u32,
    pub subtitles: Subtitles,
    pub appearance: Appearance,
    /// Start with Windows.
    pub autostart: bool,
    /// The first-run guide has been done.
    pub onboarded: bool,
}

impl Default for DeviceSettings {
    fn default() -> Self {
        let keys = Bindings::default();
        Self {
            dictation_keys: keys.dictation,
            paste_last_keys: keys.paste_last,
            microphone: None,
            emblem_size: 150,
            subtitles: Subtitles::default(),
            appearance: Appearance::default(),
            autostart: true,
            onboarded: false,
        }
    }
}

impl DeviceSettings {
    /// Reads `path`. A missing file gives the defaults. A file that cannot be understood is
    /// renamed to `device.json.bad` (so that it is not lost) and the defaults are used. A file
    /// that is understood but holds a value that could not be set (an emblem size that does not
    /// exist, a key combination that is no good) gets the default for that one value.
    pub fn load(path: &Path) -> DeviceSettings {
        let text = match fs::read_to_string(path) {
            Ok(text) => text,
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => return Self::default(),
            Err(err) if err.kind() == std::io::ErrorKind::InvalidData => {
                log::error!("{} is not text ({err})", path.display());
                set_aside(path);
                return Self::default();
            }
            Err(err) => {
                // Not the file's fault, perhaps: leave it where it is.
                log::error!("could not read {}: {err}", path.display());
                return Self::default();
            }
        };
        match serde_json::from_str::<DeviceSettings>(&text) {
            Ok(mut settings) => {
                settings.repair();
                settings
            }
            Err(err) => {
                log::error!("{} cannot be understood ({err})", path.display());
                set_aside(path);
                Self::default()
            }
        }
    }

    /// Changes the settings named in `patch`, an object with some of the fields above. All or
    /// nothing: if anything in it is wrong (a field that does not exist, a value of the wrong
    /// type, an emblem size that is not in [`EMBLEM_SIZES`], a key combination that should not
    /// be offered) nothing changes and the error says what.
    pub fn apply_patch(&mut self, patch: &Value) -> Result<(), String> {
        let Some(fields) = patch.as_object() else {
            return Err("the settings to change must be a JSON object".to_owned());
        };
        let mut next = self.clone();
        for (name, value) in fields {
            match name.as_str() {
                "dictation_keys" => next.dictation_keys = parse_value(name, value)?,
                "paste_last_keys" => next.paste_last_keys = parse_value(name, value)?,
                "microphone" => next.microphone = parse_value(name, value)?,
                "emblem_size" => next.emblem_size = parse_value(name, value)?,
                "subtitles" => next.subtitles = parse_value(name, value)?,
                "appearance" => next.appearance = parse_value(name, value)?,
                "autostart" => next.autostart = parse_value(name, value)?,
                "onboarded" => next.onboarded = parse_value(name, value)?,
                other => return Err(format!("unknown setting \"{other}\"")),
            }
        }
        next.validate()?;
        *self = next;
        Ok(())
    }

    /// Writes the settings to `path`: to a temporary file first, which then replaces the file.
    pub fn save(&self, path: &Path) -> std::io::Result<()> {
        if let Some(dir) = path.parent() {
            fs::create_dir_all(dir)?;
        }
        let mut text = serde_json::to_string_pretty(self).map_err(std::io::Error::other)?;
        text.push('\n');

        let mut temporary = path.as_os_str().to_owned();
        temporary.push(".tmp");
        let temporary = PathBuf::from(temporary);
        let written = (|| {
            let mut file = fs::File::create(&temporary)?;
            file.write_all(text.as_bytes())?;
            file.sync_all()?;
            drop(file);
            // On Windows this replaces a file that is already there.
            fs::rename(&temporary, path)
        })();
        if written.is_err() {
            let _ = fs::remove_file(&temporary);
        }
        written
    }

    /// Whether the settings are ones the app can run with.
    fn validate(&self) -> Result<(), String> {
        if !EMBLEM_SIZES.contains(&self.emblem_size) {
            return Err(format!(
                "emblem_size must be one of {EMBLEM_SIZES:?}, not {}",
                self.emblem_size
            ));
        }
        if self.microphone.as_deref() == Some("") {
            return Err("microphone must be a name, or null for the default one".to_owned());
        }
        check_keys("快捷鍵", &self.dictation_keys)?;
        check_keys("貼上上一次聽寫的快捷鍵", &self.paste_last_keys)?;
        check_keys_apart(&self.dictation_keys, &self.paste_last_keys)
    }

    /// Replaces each value that [`validate`](Self::validate) would refuse with its default.
    fn repair(&mut self) {
        let default = Self::default();
        if !EMBLEM_SIZES.contains(&self.emblem_size) {
            log::warn!(
                "device settings: emblem size {} is not offered",
                self.emblem_size
            );
            self.emblem_size = default.emblem_size;
        }
        if self.microphone.as_deref() == Some("") {
            self.microphone = None;
        }
        if let Err(err) = check_keys("dictation_keys", &self.dictation_keys) {
            log::warn!("device settings: {err}");
            self.dictation_keys = default.dictation_keys.clone();
        }
        if let Err(err) = check_keys("paste_last_keys", &self.paste_last_keys) {
            log::warn!("device settings: {err}");
            self.paste_last_keys = default.paste_last_keys.clone();
        }
        if let Err(err) = check_keys_apart(&self.dictation_keys, &self.paste_last_keys) {
            log::warn!("device settings: {err}");
            self.dictation_keys = default.dictation_keys;
            self.paste_last_keys = default.paste_last_keys;
        }
    }
}

/// One field of a patch as the type it is stored as.
fn parse_value<T: DeserializeOwned>(name: &str, value: &Value) -> Result<T, String> {
    serde_json::from_value(value.clone()).map_err(|err| format!("bad value for \"{name}\": {err}"))
}

/// Renames the file that could not be understood, so that it is not overwritten and lost.
fn set_aside(path: &Path) {
    let mut bad = path.as_os_str().to_owned();
    bad.push(".bad");
    if let Err(err) = fs::rename(path, &bad) {
        log::error!("could not rename {} to {:?}: {err}", path.display(), bad);
    }
}

/// Whether `keys` can be the key combinations of one action: at least one, each fit to be a
/// hotkey, none twice.
fn check_keys(what: &str, keys: &[String]) -> Result<(), String> {
    if keys.is_empty() {
        return Err(format!("{what}：至少要有一組按鍵"));
    }
    let mut seen: Vec<Hotkey> = Vec::new();
    for text in keys {
        validate_binding(text)?;
        let hotkey = parse_binding(text)?;
        if seen.contains(&hotkey) {
            return Err(format!("{what}：同一組按鍵不能設兩次"));
        }
        seen.push(hotkey);
    }
    Ok(())
}

/// Whether no key combination is used for both actions. (`AltRight+V` and `AltRight+KeyV` are the
/// same one.)
fn check_keys_apart(dictation: &[String], paste_last: &[String]) -> Result<(), String> {
    for text in dictation {
        let hotkey = parse_binding(text)?;
        for other in paste_last {
            if parse_binding(other)? == hotkey {
                return Err("聽寫和貼上上一次聽寫不能用同一組按鍵".to_owned());
            }
        }
    }
    Ok(())
}

/// Whether `key` is one of F1 to F24.
fn is_function_key(key: Key) -> bool {
    matches!(
        key,
        Key::F1
            | Key::F2
            | Key::F3
            | Key::F4
            | Key::F5
            | Key::F6
            | Key::F7
            | Key::F8
            | Key::F9
            | Key::F10
            | Key::F11
            | Key::F12
            | Key::F13
            | Key::F14
            | Key::F15
            | Key::F16
            | Key::F17
            | Key::F18
            | Key::F19
            | Key::F20
            | Key::F21
            | Key::F22
            | Key::F23
            | Key::F24
    )
}

/// Whether `text` may be offered as a hotkey: `Ok`, or what is wrong with it (in Chinese, for
/// the settings page).
///
/// Besides being a key combination `hotkey::parse_binding` understands, it must not get in the
/// way of typing. Without a modifier a key is a hotkey only if it is a function key; Shift
/// does not count as a modifier here, because Shift+V is a capital V; a modifier on its own
/// must be Right Alt, never Left Alt, Shift, Ctrl or Win alone; and Esc, which cancels a
/// dictation, is never part of a hotkey.
pub fn validate_binding(text: &str) -> Result<(), String> {
    let Hotkey { modifiers, key } =
        parse_binding(text).map_err(|_| "這組按鍵無法使用".to_owned())?;
    match key {
        None => {
            if modifiers == Modifiers::OPT_RIGHT {
                Ok(())
            } else {
                Err(
                    "只有右 Alt 可以單獨當快捷鍵；左 Alt、Ctrl、Shift、Win 要搭配其他按鍵"
                        .to_owned(),
                )
            }
        }
        Some(Key::Escape) => Err("Esc 用來取消聽寫，不能當快捷鍵".to_owned()),
        Some(key) => {
            let has_command_modifier =
                modifiers.intersects(Modifiers::CTRL | Modifiers::OPT | Modifiers::CMD);
            if is_function_key(key) || has_command_modifier {
                Ok(())
            } else {
                Err("單獨的字母、數字或符號鍵會干擾平常打字，請搭配 Alt、Ctrl 或 Win，或使用功能鍵（F1 到 F24）".to_owned())
            }
        }
    }
}

/// Raphael's data folder: `override_dir` (the value of `RAPHAEL_DATA_DIR`) if it is set and not
/// empty, else `Raphael` in `appdata` (the value of `APPDATA`).
fn data_dir_in(
    override_dir: Option<OsString>,
    appdata: Option<OsString>,
) -> Result<PathBuf, String> {
    if let Some(dir) = override_dir.filter(|dir| !dir.is_empty()) {
        return Ok(PathBuf::from(dir));
    }
    match appdata.filter(|dir| !dir.is_empty()) {
        Some(appdata) => Ok(Path::new(&appdata).join("Raphael")),
        None => Err("APPDATA is not set, so there is no data folder".to_owned()),
    }
}

/// Raphael's data folder, the same one the core uses.
pub fn data_dir() -> Result<PathBuf, String> {
    data_dir_in(
        std::env::var_os("RAPHAEL_DATA_DIR"),
        std::env::var_os("APPDATA"),
    )
}

/// Where `device.json` is.
pub fn settings_path() -> Result<PathBuf, String> {
    data_dir().map(|dir| dir.join(FILE_NAME))
}

/// The settings as they are now, and where they are saved. Kept in the app state.
pub struct DeviceStore {
    path: PathBuf,
    current: tokio::sync::Mutex<DeviceSettings>,
}

impl DeviceStore {
    pub fn new(path: PathBuf, settings: DeviceSettings) -> Self {
        Self {
            path,
            current: tokio::sync::Mutex::new(settings),
        }
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    /// The settings now.
    pub async fn get(&self) -> DeviceSettings {
        self.current.lock().await.clone()
    }

    /// Locks the settings for a change: nobody else changes them until the guard is dropped.
    pub async fn lock(&self) -> tokio::sync::MutexGuard<'_, DeviceSettings> {
        self.current.lock().await
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn rejects_unknown_key_and_bad_size() {
        let mut s = DeviceSettings::default();
        assert!(s.apply_patch(&json!({"emblem_size": 151})).is_err());
        assert!(s.apply_patch(&json!({"nope": 1})).is_err());
        s.apply_patch(&json!({"emblem_size": 194, "appearance": "dark"}))
            .unwrap();
        assert_eq!((s.emblem_size, s.appearance), (194, Appearance::Dark));
    }

    #[test]
    fn corrupt_file_falls_back_to_defaults() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join(FILE_NAME);
        std::fs::write(&path, "{oops").unwrap();

        assert_eq!(DeviceSettings::load(&path), DeviceSettings::default());

        assert!(!path.exists(), "the bad file is set aside");
        let bad = dir.path().join("device.json.bad");
        assert_eq!(std::fs::read_to_string(bad).unwrap(), "{oops");
    }

    #[test]
    fn defaults_are_the_documented_ones() {
        let s = DeviceSettings::default();
        assert_eq!(s.dictation_keys, ["AltRight"]);
        assert_eq!(s.paste_last_keys, ["AltRight+V"]);
        assert_eq!(s.microphone, None);
        assert_eq!(s.emblem_size, 150);
        assert_eq!(s.subtitles, Subtitles::Both);
        assert_eq!(s.appearance, Appearance::System);
        assert!(s.autostart);
        assert!(!s.onboarded);
    }

    #[test]
    fn json_uses_snake_case_names() {
        let s = DeviceSettings {
            subtitles: Subtitles::Zh,
            appearance: Appearance::Light,
            microphone: Some("耳機麥克風".to_owned()),
            ..DeviceSettings::default()
        };
        assert_eq!(
            serde_json::to_value(&s).unwrap(),
            json!({
                "dictation_keys": ["AltRight"],
                "paste_last_keys": ["AltRight+V"],
                "microphone": "耳機麥克風",
                "emblem_size": 150,
                "subtitles": "zh",
                "appearance": "light",
                "autostart": true,
                "onboarded": false,
            })
        );
        for (value, text) in [
            (Subtitles::Both, "both"),
            (Subtitles::Zh, "zh"),
            (Subtitles::Ja, "ja"),
            (Subtitles::Off, "off"),
        ] {
            assert_eq!(serde_json::to_value(value).unwrap(), json!(text));
            assert_eq!(value.as_str(), text);
        }
    }

    #[test]
    fn a_missing_file_gives_the_defaults() {
        let dir = tempfile::tempdir().unwrap();
        assert_eq!(
            DeviceSettings::load(&dir.path().join(FILE_NAME)),
            DeviceSettings::default()
        );
    }

    #[test]
    fn a_partial_file_keeps_the_defaults_for_the_rest() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join(FILE_NAME);
        std::fs::write(&path, r#"{"emblem_size": 120, "onboarded": true}"#).unwrap();
        let s = DeviceSettings::load(&path);
        assert_eq!((s.emblem_size, s.onboarded), (120, true));
        assert_eq!(s.dictation_keys, ["AltRight"]);
        assert!(path.exists());
    }

    #[test]
    fn values_that_cannot_be_set_get_their_defaults_on_load() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join(FILE_NAME);
        std::fs::write(
            &path,
            r#"{"emblem_size": 151, "dictation_keys": ["V"], "paste_last_keys": [],
                "subtitles": "ja"}"#,
        )
        .unwrap();
        let s = DeviceSettings::load(&path);
        assert_eq!(s.emblem_size, 150);
        assert_eq!(s.dictation_keys, ["AltRight"]);
        assert_eq!(s.paste_last_keys, ["AltRight+V"]);
        assert_eq!(s.subtitles, Subtitles::Ja, "what was fine stays");
    }

    #[test]
    fn a_value_of_the_wrong_type_sets_the_file_aside() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join(FILE_NAME);
        std::fs::write(&path, r#"{"subtitles": "loud"}"#).unwrap();
        assert_eq!(DeviceSettings::load(&path), DeviceSettings::default());
        assert!(dir.path().join("device.json.bad").exists());
    }

    #[test]
    fn saving_and_loading_round_trip() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("Raphael").join(FILE_NAME); // the folder does not exist yet
        let mut s = DeviceSettings::default();
        s.apply_patch(&json!({
            "dictation_keys": ["F9"],
            "microphone": "麥克風陣列",
            "emblem_size": 120,
            "subtitles": "off",
            "appearance": "dark",
            "autostart": false,
            "onboarded": true,
        }))
        .unwrap();
        s.save(&path).unwrap();
        assert_eq!(DeviceSettings::load(&path), s);
        // a second save replaces the file, and no temporary file is left behind
        s.apply_patch(&json!({"onboarded": false})).unwrap();
        s.save(&path).unwrap();
        assert_eq!(DeviceSettings::load(&path), s);
        let names: Vec<_> = std::fs::read_dir(path.parent().unwrap())
            .unwrap()
            .map(|entry| entry.unwrap().file_name())
            .collect();
        assert_eq!(names, [OsString::from(FILE_NAME)]);
    }

    #[test]
    fn a_patch_is_all_or_nothing() {
        let mut s = DeviceSettings::default();
        let before = s.clone();
        assert!(
            s.apply_patch(&json!({"appearance": "dark", "emblem_size": 7}))
                .is_err()
        );
        assert_eq!(s, before);
        assert!(
            s.apply_patch(&json!({"appearance": "dark", "nope": true}))
                .is_err()
        );
        assert_eq!(s, before);
        assert!(s.apply_patch(&json!([1, 2])).is_err());
        assert_eq!(s, before);
    }

    #[test]
    fn a_patch_checks_types() {
        let mut s = DeviceSettings::default();
        for bad in [
            json!({"autostart": "yes"}),
            json!({"onboarded": 1}),
            json!({"emblem_size": "150"}),
            json!({"emblem_size": 150.5}),
            json!({"microphone": 3}),
            json!({"microphone": ""}),
            json!({"subtitles": "loud"}),
            json!({"appearance": 1}),
            json!({"dictation_keys": "AltRight"}),
            json!({"dictation_keys": [1]}),
        ] {
            assert!(s.apply_patch(&bad).is_err(), "{bad}");
        }
        assert_eq!(s, DeviceSettings::default());
    }

    #[test]
    fn the_microphone_can_go_back_to_the_default() {
        let mut s = DeviceSettings::default();
        s.apply_patch(&json!({"microphone": "耳機麥克風"})).unwrap();
        assert_eq!(s.microphone.as_deref(), Some("耳機麥克風"));
        s.apply_patch(&json!({"microphone": null})).unwrap();
        assert_eq!(s.microphone, None);
    }

    #[test]
    fn a_patch_checks_the_key_combinations() {
        let mut s = DeviceSettings::default();
        s.apply_patch(&json!({"dictation_keys": ["F9"]})).unwrap();
        assert_eq!(s.dictation_keys, ["F9"]);
        // a lone Left Alt: only Right Alt may be alone
        assert!(
            s.apply_patch(&json!({"dictation_keys": ["AltLeft"]}))
                .is_err()
        );
        // none at all would leave no way to dictate
        assert!(s.apply_patch(&json!({"dictation_keys": []})).is_err());
        // a bare typing key, a lone Shift
        assert!(s.apply_patch(&json!({"dictation_keys": ["KeyV"]})).is_err());
        assert!(
            s.apply_patch(&json!({"dictation_keys": ["ShiftLeft"]}))
                .is_err()
        );
        // the same combination twice, or for both actions
        assert!(
            s.apply_patch(&json!({"dictation_keys": ["F9", "F9"]}))
                .is_err()
        );
        assert!(
            s.apply_patch(&json!({"dictation_keys": ["AltRight+KeyV"]}))
                .is_err(),
            "AltRight+V already pastes the last dictation"
        );
        assert_eq!(s.dictation_keys, ["F9"]);
    }

    #[test]
    fn offered_key_combinations() {
        for good in [
            "AltRight",
            "AltRight+V",
            "AltRight+KeyV",
            "AltRight+Space",
            "ControlLeft+AltLeft+KeyD",
            "ControlRight+Digit1",
            "MetaLeft+KeyJ",
            "F9",
            "ControlLeft+F9",
            "ShiftLeft+F9",
        ] {
            assert_eq!(validate_binding(good), Ok(()), "{good}");
        }
        for bad in [
            "",
            "V",
            "KeyV",
            "Space",
            "Escape",
            "AltRight+Escape",
            "ControlLeft+Escape",
            "Enter",
            "Digit1",
            "AltLeft",
            "Alt",
            "ShiftLeft",
            "ShiftRight",
            "ControlLeft",
            "ControlRight",
            "MetaLeft",
            "MetaRight",
            "Shift+KeyV",
            "ShiftLeft+Space",
            "ControlLeft+ShiftLeft",
            "AltRight+ShiftRight",
            "nonsense",
            "AltRight+nonsense",
        ] {
            assert!(validate_binding(bad).is_err(), "{bad}");
        }
    }

    #[test]
    fn only_right_alt_may_be_alone() {
        // keys.ts gives the same words
        let lone = "只有右 Alt 可以單獨當快捷鍵；左 Alt、Ctrl、Shift、Win 要搭配其他按鍵";
        for text in ["AltLeft", "Alt", "ShiftRight", "ControlLeft", "MetaRight"] {
            assert_eq!(validate_binding(text), Err(lone.to_owned()), "{text}");
        }
        assert_eq!(validate_binding("AltRight"), Ok(()));
    }

    #[test]
    fn the_data_folder() {
        let appdata = Some(OsString::from(r"C:\Users\someone\AppData\Roaming"));
        assert_eq!(
            data_dir_in(None, appdata.clone()).unwrap(),
            Path::new(r"C:\Users\someone\AppData\Roaming").join("Raphael")
        );
        // the override wins, an empty one does not count
        assert_eq!(
            data_dir_in(Some(OsString::from(r"D:\data")), appdata.clone()).unwrap(),
            PathBuf::from(r"D:\data")
        );
        assert_eq!(
            data_dir_in(Some(OsString::new()), appdata).unwrap(),
            Path::new(r"C:\Users\someone\AppData\Roaming").join("Raphael")
        );
        assert!(data_dir_in(None, None).is_err());
    }
}
