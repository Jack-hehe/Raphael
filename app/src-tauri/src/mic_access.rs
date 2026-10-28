//! Whether Windows lets desktop apps use the microphone.
//!
//! The privacy settings keep their answers in the registry, under `CapabilityAccessManager\
//! ConsentStore\microphone`: a `Value` of `Allow` or `Deny` for the device (HKLM) and for the
//! person (HKCU), and the same under `NonPackaged` for desktop apps such as Raphael. With the
//! switch off the microphone still opens, but it delivers silence, so the first-run guide asks
//! here before it blames the hardware. A missing value means the default, which is allowed.
//!
//! The reading goes through [`ConsentReader`], so that the rules can be tested without a registry.

use serde::Serialize;

/// Which switch of the privacy settings is off.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Blocker {
    /// 「Microphone access」 for the whole device.
    System,
    /// 「Microphone access」 for this person's apps.
    User,
    /// 「Let desktop apps access your microphone」.
    DesktopApps,
}

/// What `mic_access` answers: `blocked_by` names the first switch that is off.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
pub struct MicAccess {
    pub allowed: bool,
    pub blocked_by: Option<Blocker>,
}

/// Where a value is read.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Hive {
    LocalMachine,
    CurrentUser,
}

/// The `Value` under `subkey` of the hive, or `None` when there is none.
pub trait ConsentReader {
    fn value(&self, hive: Hive, subkey: &str) -> Option<String>;
}

const STORE: &str =
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone";
const NON_PACKAGED: &str = r"SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone\NonPackaged";

fn denied(reader: &dyn ConsentReader, hive: Hive, subkey: &str) -> bool {
    reader
        .value(hive, subkey)
        .is_some_and(|value| value.trim().eq_ignore_ascii_case("deny"))
}

/// Reads the privacy switches: the device's, then the person's, then the one for desktop apps
/// (either hive).
pub fn microphone_access(reader: &dyn ConsentReader) -> MicAccess {
    let blocked_by = if denied(reader, Hive::LocalMachine, STORE) {
        Some(Blocker::System)
    } else if denied(reader, Hive::CurrentUser, STORE) {
        Some(Blocker::User)
    } else if denied(reader, Hive::CurrentUser, NON_PACKAGED)
        || denied(reader, Hive::LocalMachine, NON_PACKAGED)
    {
        Some(Blocker::DesktopApps)
    } else {
        None
    };
    MicAccess {
        allowed: blocked_by.is_none(),
        blocked_by,
    }
}

/// The registry of this computer.
#[cfg(windows)]
pub struct WindowsConsent;

#[cfg(windows)]
impl ConsentReader for WindowsConsent {
    fn value(&self, hive: Hive, subkey: &str) -> Option<String> {
        use windows::Win32::Foundation::ERROR_SUCCESS;
        use windows::Win32::System::Registry::{
            HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE, RRF_RT_REG_SZ, RegGetValueW,
        };
        use windows::core::{HSTRING, w};

        let root = match hive {
            Hive::LocalMachine => HKEY_LOCAL_MACHINE,
            Hive::CurrentUser => HKEY_CURRENT_USER,
        };
        let subkey = HSTRING::from(subkey);
        // "Allow" and "Deny" are short; anything longer than this is not one of them.
        let mut buffer = [0u16; 64];
        let mut bytes = u32::try_from(std::mem::size_of_val(&buffer)).ok()?;
        // SAFETY: `buffer` and `bytes` outlive the call, and `bytes` is the size of `buffer`.
        let status = unsafe {
            RegGetValueW(
                root,
                &subkey,
                w!("Value"),
                RRF_RT_REG_SZ,
                None,
                Some(buffer.as_mut_ptr().cast()),
                Some(&mut bytes),
            )
        };
        if status != ERROR_SUCCESS {
            return None;
        }
        let units = (bytes as usize / 2).min(buffer.len());
        let text = String::from_utf16_lossy(&buffer[..units]);
        Some(text.trim_end_matches('\0').to_owned())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    /// A registry in a map: `(hive, subkey)` to the value.
    #[derive(Default)]
    struct Fake(HashMap<(bool, &'static str), &'static str>);

    impl Fake {
        fn with(mut self, hive: Hive, subkey: &'static str, value: &'static str) -> Self {
            self.0.insert((hive == Hive::LocalMachine, subkey), value);
            self
        }
    }

    impl ConsentReader for Fake {
        fn value(&self, hive: Hive, subkey: &str) -> Option<String> {
            self.0
                .get(&(hive == Hive::LocalMachine, subkey))
                .map(|value| (*value).to_owned())
        }
    }

    #[test]
    fn nothing_written_means_allowed() {
        let access = microphone_access(&Fake::default());
        assert_eq!(
            access,
            MicAccess {
                allowed: true,
                blocked_by: None
            }
        );
    }

    #[test]
    fn all_allowed_is_allowed() {
        let reader = Fake::default()
            .with(Hive::LocalMachine, STORE, "Allow")
            .with(Hive::CurrentUser, STORE, "Allow")
            .with(Hive::CurrentUser, NON_PACKAGED, "Allow");
        assert!(microphone_access(&reader).allowed);
    }

    #[test]
    fn each_switch_names_itself() {
        let system = Fake::default().with(Hive::LocalMachine, STORE, "Deny");
        assert_eq!(microphone_access(&system).blocked_by, Some(Blocker::System));
        let user = Fake::default().with(Hive::CurrentUser, STORE, "Deny");
        assert_eq!(microphone_access(&user).blocked_by, Some(Blocker::User));
        let desktop = Fake::default().with(Hive::CurrentUser, NON_PACKAGED, "Deny");
        assert_eq!(
            microphone_access(&desktop).blocked_by,
            Some(Blocker::DesktopApps)
        );
        assert!(!microphone_access(&desktop).allowed);
    }

    #[test]
    fn desktop_apps_are_read_in_both_hives() {
        let machine = Fake::default().with(Hive::LocalMachine, NON_PACKAGED, "Deny");
        assert_eq!(
            microphone_access(&machine).blocked_by,
            Some(Blocker::DesktopApps)
        );
    }

    #[test]
    fn the_device_switch_comes_first_and_case_does_not_matter() {
        let reader = Fake::default()
            .with(Hive::LocalMachine, STORE, "deny")
            .with(Hive::CurrentUser, STORE, "Deny")
            .with(Hive::CurrentUser, NON_PACKAGED, "Deny");
        assert_eq!(microphone_access(&reader).blocked_by, Some(Blocker::System));
    }

    #[test]
    fn unknown_values_do_not_block() {
        let reader = Fake::default().with(Hive::CurrentUser, STORE, "Prompt");
        assert!(microphone_access(&reader).allowed);
    }

    #[test]
    fn the_answer_serializes_with_stable_names() {
        let json = serde_json::to_string(&MicAccess {
            allowed: false,
            blocked_by: Some(Blocker::DesktopApps),
        })
        .unwrap();
        assert_eq!(json, r#"{"allowed":false,"blocked_by":"desktop_apps"}"#);
        let json = serde_json::to_string(&MicAccess {
            allowed: true,
            blocked_by: None,
        })
        .unwrap();
        assert_eq!(json, r#"{"allowed":true,"blocked_by":null}"#);
    }
}
