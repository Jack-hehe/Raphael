//! Which windows a run may touch at all, before any question of the owner's.
//!
//! - **Raphael's own** windows (the main window, the overlay) never: a run could otherwise click
//!   its own 「是」 or change its own settings. They are left out of the listing and refused.
//! - **The shell's** (the taskbar, the desktop, the Start menu's host windows) are not apps: they
//!   are left out of the listing.
//! - **Apps Raphael never operates**: terminals and command prompts (a typed line there is a
//!   program run), the registry editor, the task manager, password managers and the system's
//!   credential and consent windows. They are listed, so that the model knows they are open, and
//!   refused (`refused_app`).
//!
//! An app is known by its executable's file name, in lower case (`excel.exe`), as the
//! always-allowed list of the device settings keeps it.

/// The executables Raphael never operates.
const REFUSED: &[&str] = &[
    // terminals and shells
    "cmd.exe",
    "powershell.exe",
    "powershell_ise.exe",
    "pwsh.exe",
    "windowsterminal.exe",
    "wt.exe",
    "openconsole.exe",
    "conhost.exe",
    "wsl.exe",
    "wslhost.exe",
    "bash.exe",
    "mintty.exe",
    "alacritty.exe",
    "wezterm-gui.exe",
    // the system's tools of power
    "regedit.exe",
    "taskmgr.exe",
    "mmc.exe",
    "msiexec.exe",
    "useraccountcontrolsettings.exe",
    "systemsettingsadminflows.exe",
    // sign-in, consent and credentials
    "consent.exe",
    "credentialuibroker.exe",
    "logonui.exe",
    "lockapp.exe",
    "securityhealthsystray.exe",
    "sechealthui.exe",
    // password managers
    "keepass.exe",
    "keepassxc.exe",
    "1password.exe",
    "bitwarden.exe",
    "dashlane.exe",
    "lastpass.exe",
    "enpass.exe",
    "nordpass.exe",
    "roboform.exe",
    "proton pass.exe",
];

/// The window classes of the shell, which are not apps.
const SHELL_CLASSES: &[&str] = &[
    "Shell_TrayWnd",
    "Shell_SecondaryTrayWnd",
    "Progman",
    "WorkerW",
    "NotifyIconOverflowWindow",
    "Windows.UI.Core.CoreWindow",
    "XamlExplorerHostIslandWindow",
    "TopLevelWindowForOverflowXamlIsland",
    "MultitaskingViewFrame",
    "ForegroundStaging",
    "Shell_InputSwitchTopLevelWindow",
];

/// An executable's file name as the lists keep it: the last part of `path`, in lower case.
pub fn exe_of(path: &str) -> String {
    path.rsplit(['\\', '/'])
        .next()
        .unwrap_or_default()
        .trim()
        .to_lowercase()
}

/// Whether Raphael never operates the app `exe` (a file name in lower case).
pub fn is_refused(exe: &str) -> bool {
    REFUSED.contains(&exe)
}

/// Whether a top-level window of class `class` is the shell's rather than an app's.
pub fn is_shell_class(class: &str) -> bool {
    SHELL_CLASSES.contains(&class)
}

/// The name an app goes by when its executable says none: its file name without `.exe`.
pub fn fallback_name(exe: &str) -> String {
    exe.strip_suffix(".exe").unwrap_or(exe).to_owned()
}

/// A name of the screen or of a file, made fit to send: control characters out, runs of spaces
/// one, cut to `limit` characters.
pub fn clean(text: &str, limit: usize) -> String {
    let one_line: String = text
        .chars()
        .map(|c| if c.is_control() { ' ' } else { c })
        .collect();
    let mut words = one_line.split_whitespace().collect::<Vec<_>>().join(" ");
    if words.chars().count() > limit {
        words = words
            .chars()
            .take(limit.saturating_sub(1))
            .collect::<String>()
            + "…";
    }
    words
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn an_app_is_known_by_its_executables_file_name_in_lower_case() {
        assert_eq!(
            exe_of(r"C:\Program Files\Microsoft Office\root\Office16\EXCEL.EXE"),
            "excel.exe"
        );
        assert_eq!(exe_of("calc.exe"), "calc.exe");
        assert_eq!(exe_of(""), "");
        assert_eq!(fallback_name("calculatorapp.exe"), "calculatorapp");
    }

    #[test]
    fn terminals_password_managers_and_the_systems_windows_are_refused() {
        for exe in [
            "cmd.exe",
            "powershell.exe",
            "windowsterminal.exe",
            "regedit.exe",
            "1password.exe",
            "keepassxc.exe",
            "consent.exe",
            "credentialuibroker.exe",
        ] {
            assert!(is_refused(exe), "{exe}");
        }
        for exe in [
            "excel.exe",
            "calculatorapp.exe",
            "explorer.exe",
            "chrome.exe",
            "notepad.exe",
        ] {
            assert!(!is_refused(exe), "{exe}");
        }
    }

    #[test]
    fn the_shells_windows_are_not_apps() {
        assert!(is_shell_class("Shell_TrayWnd") && is_shell_class("Progman"));
        assert!(!is_shell_class("CabinetWClass") && !is_shell_class("ApplicationFrameWindow"));
    }

    #[test]
    fn a_name_is_one_line_and_cut() {
        assert_eq!(clean("a\nb\t  c", 10), "a b c");
        assert_eq!(clean(&"字".repeat(30), 10), format!("{}…", "字".repeat(9)));
        assert_eq!(clean("  ", 10), "");
    }
}
