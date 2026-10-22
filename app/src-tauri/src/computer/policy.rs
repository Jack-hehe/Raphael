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
//! ([`is_handoff_name`]) is never clicked, typed into or sent keys: the step is the owner's. What
//! keys and typed text reach is read live just before they are sent, and held to what the core
//! judged ([`may_reach`]).

use super::Failure;
use super::keys::Chord;

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

/// What the keys or the typed text of a step would reach, read live just before they are sent.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Reach {
    /// The element with the focus is a password field.
    pub password: bool,
    /// It is called like a sign-in, a payment field or a code ([`is_handoff_name`]).
    pub handoff: bool,
    /// It is the element the step named, or a part of one that gives its focus to a part (the
    /// edit field of a combo box); true when the step named none.
    pub on_target: bool,
    /// It is a field whose text Delete and Backspace only edit: an Edit or a Document whose value
    /// can be set.
    pub edits_text: bool,
}

/// What a step sends to the element with the focus.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Press {
    Keys(Chord),
    Text,
}

/// Whether `press` may go to what it would reach, `confirmed` when the owner said yes to the
/// step. The core judged the element the step named, or the one its look said had the focus; this
/// holds the live focus to that judgement:
///
/// - never a password field (`password`), nor, unless the keys only move away from it, a sign-in,
///   a payment field or a code (`handoff`);
/// - only the element named, if one was (`focus` otherwise: it could not take the focus, or the
///   focus is elsewhere, and nothing is pressed or typed: a harmless element named while the
///   focus sits on a Send button must not press Send);
/// - keys that delete what is selected ([`Chord::deletes_items`]) only into a text field, unless
///   the owner said yes (`unconfirmed`): Delete on a file in File Explorer deletes the file.
///
/// A yes to the step does not let keys reach another element than the one named: the owner said
/// yes to the step the question showed.
pub fn may_reach(reach: Reach, press: Press, confirmed: bool) -> Result<(), Failure> {
    if reach.password {
        return Err(Failure::Password);
    }
    if !reach.on_target {
        return Err(Failure::Focus);
    }
    let moving_away = matches!(press, Press::Keys(chord) if chord.moves_away());
    if reach.handoff && !moving_away {
        return Err(Failure::Handoff);
    }
    let deletes = matches!(press, Press::Keys(chord) if chord.deletes_items());
    if deletes && !reach.edits_text && !confirmed {
        return Err(Failure::Unconfirmed);
    }
    Ok(())
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

    fn keys(text: &str) -> Press {
        Press::Keys(crate::computer::keys::parse_chord(text).unwrap())
    }

    /// A live focus on the element named, which is no text field.
    const ON_TARGET: Reach = Reach {
        password: false,
        handoff: false,
        on_target: true,
        edits_text: false,
    };

    #[test]
    fn keys_and_text_go_only_to_the_element_named() {
        // the element named could not take the focus, which stayed on a Send button
        let elsewhere = Reach {
            on_target: false,
            ..ON_TARGET
        };
        for press in [keys("Enter"), keys("Space"), keys("Tab"), Press::Text] {
            assert_eq!(
                may_reach(elsewhere, press, false),
                Err(Failure::Focus),
                "{press:?}"
            );
            // a yes to the step was a yes to the element named, not to what has the focus
            assert_eq!(may_reach(elsewhere, press, true), Err(Failure::Focus));
            assert_eq!(may_reach(ON_TARGET, press, false), Ok(()), "{press:?}");
        }
        assert_eq!(Failure::Focus.kind(), "focus");
    }

    #[test]
    fn a_password_field_or_a_sign_in_with_the_focus_is_the_owners() {
        let password = Reach {
            password: true,
            ..ON_TARGET
        };
        let sign_in = Reach {
            handoff: true,
            ..ON_TARGET
        };
        for press in [keys("Enter"), keys("Ctrl+V"), Press::Text] {
            assert_eq!(may_reach(password, press, true), Err(Failure::Password));
            assert_eq!(may_reach(sign_in, press, true), Err(Failure::Handoff));
        }
        // keys that only move away from a sign-in may go
        assert_eq!(may_reach(sign_in, keys("Tab"), false), Ok(()));
        assert_eq!(
            may_reach(password, keys("Tab"), false),
            Err(Failure::Password)
        );
    }

    #[test]
    fn keys_that_delete_reach_no_file_or_list_item_without_a_yes() {
        let text_field = Reach {
            edits_text: true,
            ..ON_TARGET
        };
        for text in [
            "Delete",
            "Ctrl+Delete",
            "Shift+Delete",
            "Backspace",
            "Ctrl+Backspace",
            "Ctrl+D",
        ] {
            // a selected file in File Explorer, a mail in a list
            assert_eq!(
                may_reach(ON_TARGET, keys(text), false),
                Err(Failure::Unconfirmed),
                "{text}"
            );
            assert_eq!(may_reach(ON_TARGET, keys(text), true), Ok(()), "{text}");
            // in a text field they edit its text
            assert_eq!(may_reach(text_field, keys(text), false), Ok(()), "{text}");
        }
        // other keys on a selected file, and typed text, go as before
        for press in [keys("Left"), keys("Ctrl+C"), keys("F2"), Press::Text] {
            assert_eq!(may_reach(ON_TARGET, press, false), Ok(()), "{press:?}");
        }
        assert_eq!(Failure::Unconfirmed.kind(), "unconfirmed");
    }

    #[test]
    fn a_name_is_one_line_and_cut() {
        assert_eq!(clean("a\nb\t  c", 10), "a b c");
        assert_eq!(clean(&"字".repeat(30), 10), format!("{}…", "字".repeat(9)));
        assert_eq!(clean("  ", 10), "");
    }
}
