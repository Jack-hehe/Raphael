//! Pasting dictated text into the window that had focus when the dictation key was released.
//!
//! The controller asks [`Paster::foreground`] for the [`Target`] when the key is released, and
//! later hands the finished text to [`Paster::deliver`] together with that target. [`decide`]
//! then compares it with the window that has focus at that moment: the same window gets the
//! text pasted with the key chord it understands ([`shortcut_for`]); anything else (no target,
//! focus moved on, an elevated window we cannot type into) gets the text left on the clipboard
//! instead, so the user can paste it by hand after Raphael's notice.

#[cfg(windows)]
mod clipboard;
#[cfg(windows)]
mod windows;

#[cfg(windows)]
pub use self::windows::WindowsPaster;

use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Instant;

/// A window that can receive the dictated text.
#[derive(Clone, Debug, PartialEq)]
pub struct Target {
    /// The window handle (`HWND`) as a number.
    pub hwnd: isize,
    /// The id of the process that owns the window.
    pub pid: u32,
    /// The file name of that process's executable, such as `notepad.exe` (empty if unknown).
    pub process_name: String,
    /// Whether that process runs elevated ("as administrator"). Windows does not let a
    /// non-elevated program send keys to it.
    pub is_elevated: bool,
}

/// The key chord that makes a program paste.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Shortcut {
    CtrlV,
    CtrlShiftV,
    ShiftInsert,
}

/// Why the text was left on the clipboard instead of being pasted.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CopyReason {
    /// The target runs elevated and this app does not.
    Elevated,
    /// A different window (or none) has focus now than when the key was released.
    FocusChanged,
    /// No window had focus when the key was released.
    NoTarget,
}

/// What [`decide`] says to do with the text.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Decision {
    Paste(Shortcut),
    CopyOnly(CopyReason),
}

/// What [`Paster::deliver`] did with the text.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PasteOutcome {
    /// The paste chord went to the target, and the user's clipboard was put back (unless the
    /// user copied something new meanwhile, which then stays).
    Pasted,
    /// The text is on the clipboard for the user to paste by hand.
    CopiedOnly(CopyReason),
}

static DELIVERY_SEQUENCE: AtomicU64 = AtomicU64::new(0);

/// Metadata owned by one delivery, passed directly to its after-paste continuation.
pub struct PasteReceipt {
    pub(crate) sequence: u64,
    pub(crate) started: Instant,
    #[cfg(windows)]
    pub(crate) input: Option<handy_keys::InputCheckpoint>,
}

impl PasteReceipt {
    pub(crate) fn new() -> Self {
        Self {
            sequence: DELIVERY_SEQUENCE.fetch_add(1, Ordering::SeqCst) + 1,
            started: Instant::now(),
            #[cfg(windows)]
            input: None,
        }
    }
}

/// A paste outcome and its own input evidence; copy-only and failed sends carry no receipt.
pub struct PasteDelivery {
    pub outcome: PasteOutcome,
    pub receipt: Option<PasteReceipt>,
}

/// Pastes into `at_stop` (the window that had focus when the key was released) only if `now`
/// (the window that has focus at paste time) is still that window, and only if Windows lets us
/// send it keys: an elevated window accepts keys only from an elevated program.
pub fn decide(at_stop: Option<&Target>, now: Option<&Target>, self_elevated: bool) -> Decision {
    let Some(target) = at_stop else {
        return Decision::CopyOnly(CopyReason::NoTarget);
    };
    if now.is_none_or(|now| now.hwnd != target.hwnd) {
        return Decision::CopyOnly(CopyReason::FocusChanged);
    }
    if target.is_elevated && !self_elevated {
        return Decision::CopyOnly(CopyReason::Elevated);
    }
    Decision::Paste(shortcut_for(&target.process_name))
}

/// Terminals. Ctrl+Shift+V is their paste chord; Ctrl+V can reach the program running inside
/// them as a control character.
const CTRL_SHIFT_V_PROGRAMS: [&str; 9] = [
    "windowsterminal.exe",
    "wt.exe",
    "openconsole.exe",
    "conhost.exe",
    "cmd.exe",
    "powershell.exe",
    "pwsh.exe",
    "wezterm-gui.exe",
    "alacritty.exe",
];

/// The paste chord for a program, by the file name of its executable (case-insensitive).
pub fn shortcut_for(process_name: &str) -> Shortcut {
    if process_name.eq_ignore_ascii_case("mintty.exe") {
        Shortcut::ShiftInsert
    } else if CTRL_SHIFT_V_PROGRAMS
        .iter()
        .any(|name| process_name.eq_ignore_ascii_case(name))
    {
        Shortcut::CtrlShiftV
    } else {
        Shortcut::CtrlV
    }
}

/// Puts dictated text into the focused window. [`WindowsPaster`] is the real one; the
/// controller's tests use a fake.
pub trait Paster: Send + Sync + 'static {
    /// The window that has focus now, if any.
    fn foreground(&self) -> Option<Target>;

    /// Pastes `text` into `at_stop` if [`decide`] allows it, or leaves it on the clipboard.
    /// Blocks until the paste is finished and the user's clipboard is restored, which can take
    /// up to about 8 seconds if the target never reads the clipboard. If the clipboard cannot
    /// be used at all (another program keeps it open), nothing is pasted or copied: that is
    /// logged, and the outcome is still the one [`decide`] chose.
    fn deliver(&self, text: &str, at_stop: Option<&Target>) -> PasteOutcome;

    /// Keeps per-delivery metadata attached while its async continuation waits to resume.
    fn deliver_with_receipt(&self, text: &str, at_stop: Option<&Target>) -> PasteDelivery {
        let receipt = PasteReceipt::new();
        let outcome = self.deliver(text, at_stop);
        PasteDelivery {
            outcome,
            receipt: (outcome == PasteOutcome::Pasted).then_some(receipt),
        }
    }

    /// Gives the focus back to `target`, the window that had it before one of Raphael's own
    /// took it. Whether Windows let it.
    fn refocus(&self, target: &Target) -> bool {
        let _ = target;
        false
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn t(hwnd: isize, name: &str, elevated: bool) -> Target {
        Target {
            hwnd,
            pid: 1,
            process_name: name.into(),
            is_elevated: elevated,
        }
    }

    #[test]
    fn pastes_into_same_window() {
        assert_eq!(
            decide(
                Some(&t(1, "notepad.exe", false)),
                Some(&t(1, "notepad.exe", false)),
                false
            ),
            Decision::Paste(Shortcut::CtrlV)
        );
    }

    #[test]
    fn focus_change_copies_only() {
        // Review Focus 1
        assert_eq!(
            decide(
                Some(&t(1, "notepad.exe", false)),
                Some(&t(2, "chrome.exe", false)),
                false
            ),
            Decision::CopyOnly(CopyReason::FocusChanged)
        );
        assert_eq!(
            decide(Some(&t(1, "notepad.exe", false)), None, false),
            Decision::CopyOnly(CopyReason::FocusChanged)
        );
    }

    #[test]
    fn elevated_target_copies_only_unless_we_are_elevated() {
        let admin = t(1, "powershell.exe", true);
        assert_eq!(
            decide(Some(&admin), Some(&admin), false),
            Decision::CopyOnly(CopyReason::Elevated)
        );
        assert_eq!(
            decide(Some(&admin), Some(&admin), true),
            Decision::Paste(Shortcut::CtrlShiftV)
        );
    }

    #[test]
    fn no_target_copies_only() {
        assert_eq!(
            decide(None, None, false),
            Decision::CopyOnly(CopyReason::NoTarget)
        );
    }

    #[test]
    fn terminal_shortcuts() {
        assert_eq!(shortcut_for("WindowsTerminal.exe"), Shortcut::CtrlShiftV);
        assert_eq!(shortcut_for("mintty.exe"), Shortcut::ShiftInsert);
        assert_eq!(shortcut_for("Code.exe"), Shortcut::CtrlV);
    }

    #[test]
    fn every_listed_terminal_pastes_with_ctrl_shift_v() {
        for name in [
            "windowsterminal.exe",
            "wt.exe",
            "openconsole.exe",
            "conhost.exe",
            "cmd.exe",
            "powershell.exe",
            "pwsh.exe",
            "wezterm-gui.exe",
            "alacritty.exe",
            "PWSH.EXE",
        ] {
            assert_eq!(shortcut_for(name), Shortcut::CtrlShiftV, "{name}");
        }
        assert_eq!(shortcut_for("MinTTY.exe"), Shortcut::ShiftInsert);
    }

    #[test]
    fn a_focus_change_is_reported_before_elevation() {
        assert_eq!(
            decide(
                Some(&t(1, "powershell.exe", true)),
                Some(&t(2, "notepad.exe", false)),
                false
            ),
            Decision::CopyOnly(CopyReason::FocusChanged)
        );
    }
}
