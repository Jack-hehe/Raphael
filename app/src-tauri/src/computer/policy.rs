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
//!
//! Within a window, an element called like a sign-in, a payment field or a one-time code
//! ([`is_handoff_name`]) is never clicked, typed into or sent keys: the step is the owner's.

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

/// The words that make an element a step of the owner's: signing in, paying, a payment form's
/// fields, one-time codes. The core's lists (`raphael_core.computer.safety`, `_HANDOFF_EN` and
/// `_HANDOFF_CJK`), word for word: a test of the core's reads them here and fails when the two
/// part.
const HANDOFF_EN: &[&str] = &[
    "sign in",
    "signin",
    "log in",
    "login",
    "log on",
    "logon",
    "card number",
    "credit card",
    "debit card",
    "cvv",
    "cvc",
    "security code",
    "expiry",
    "expiration",
    "pay",
    "pay now",
    "payment",
    "checkout",
    "check out",
    "one-time code",
    "verification code",
    "otp",
    "2fa",
    "passcode",
    "password",
    "passwd",
];
const HANDOFF_CJK: &[&str] = &[
    "登入",
    "登錄",
    "登录",
    "ログイン",
    "サインイン",
    "卡號",
    "信用卡",
    "金融卡",
    "安全碼",
    "有效期限",
    "カード番号",
    "セキュリティコード",
    "有効期限",
    "付款",
    "支付",
    "結帳",
    "结账",
    "支払",
    "決済",
    "お支払い",
    "驗證碼",
    "验证码",
    "認証コード",
    "確認コード",
    "密碼",
    "パスワード",
];

/// A name the way it is matched: full-width letters and digits folded to their ASCII forms, lower
/// case, each run of spaces, underscores and hyphens one space (the core folds with NFKC, which
/// does this and more).
fn normal(text: &str) -> String {
    let folded: String = text
        .chars()
        .map(|c| match c {
            '\u{FF01}'..='\u{FF5E}' => char::from_u32(c as u32 - 0xFEE0).unwrap_or(c),
            '\u{3000}' => ' ',
            _ => c,
        })
        .flat_map(char::to_lowercase)
        .collect();
    folded
        .split(|c: char| c.is_whitespace() || c == '_' || c == '-')
        .filter(|part| !part.is_empty())
        .collect::<Vec<_>>()
        .join(" ")
}

/// Whether `word` (normal already) is in `text` (normal already) as a whole word: no ASCII letter
/// or digit right before or after it.
fn has_word(text: &str, word: &str) -> bool {
    let alnum = |c: Option<char>| c.is_some_and(|c| c.is_ascii_alphanumeric());
    text.match_indices(word).any(|(at, _)| {
        !alnum(text[..at].chars().next_back()) && !alnum(text[at + word.len()..].chars().next())
    })
}

/// Whether an element called `name` is a step of the owner's: a sign-in, a payment, a field of a
/// payment form or a one-time code. Checked on the live element just before keys or text reach
/// it, whatever the core's look said.
pub fn is_handoff_name(name: &str) -> bool {
    let text = normal(name);
    HANDOFF_EN.iter().any(|word| has_word(&text, &normal(word)))
        || HANDOFF_CJK.iter().any(|word| text.contains(word))
}

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
    fn a_sign_in_a_payment_or_a_code_is_the_owners_by_its_name() {
        for name in [
            "Sign in",
            "SIGN_IN",
            "Ｓｉｇｎ ｉｎ",
            "Log-in",
            "Pay now",
            "Card number",
            "CVV",
            "One-time code",
            "one time code",
            "Enter your password",
            "2FA",
            "登入",
            "ログイン",
            "卡號",
            "驗證碼",
            "お支払い",
        ] {
            assert!(is_handoff_name(name), "{name}");
        }
        for name in [
            "Search",
            "Paypal",
            "Postcode",
            "Repay later",
            "Display",
            "Sender",
            "Message",
            "七",
            "",
        ] {
            assert!(!is_handoff_name(name), "{name}");
        }
    }

    #[test]
    fn a_name_is_one_line_and_cut() {
        assert_eq!(clean("a\nb\t  c", 10), "a b c");
        assert_eq!(clean(&"字".repeat(30), 10), format!("{}…", "字".repeat(9)));
        assert_eq!(clean("  ", 10), "");
    }
}
