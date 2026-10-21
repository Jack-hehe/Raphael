//! App families used only by correction learning.

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum AppKind {
    Terminal,
    Chromium,
    CodeEditor,
    Other,
}

/// The Electron editors built on VS Code, by executable basename and by the name the main window
/// knows them by. All have the `editor.accessibilitySupport` setting.
const EDITORS: [(&str, &str); 7] = [
    ("code.exe", "vscode"),
    ("code - insiders.exe", "vscode"),
    ("cursor.exe", "cursor"),
    ("vscodium.exe", "vscodium"),
    ("windsurf.exe", "windsurf"),
    ("trae.exe", "trae"),
    ("positron.exe", "positron"),
];

fn basename(process_name: &str) -> String {
    process_name
        .rsplit(['\\', '/'])
        .next()
        .unwrap_or(process_name)
        .to_ascii_lowercase()
}

/// Which editor the executable is, as the main window names it, if it is a code editor.
pub fn editor_id(process_name: &str) -> Option<&'static str> {
    let basename = basename(process_name);
    EDITORS
        .iter()
        .find(|(exe, _)| *exe == basename)
        .map(|(_, id)| *id)
}

/// Classifies the executable basename, without changing paste-shortcut policy.
pub fn classify(process_name: &str) -> AppKind {
    if editor_id(process_name).is_some() {
        return AppKind::CodeEditor;
    }
    match basename(process_name).as_str() {
        "windowsterminal.exe"
        | "wt.exe"
        | "conhost.exe"
        | "openconsole.exe"
        | "powershell.exe"
        | "pwsh.exe"
        | "cmd.exe"
        | "wezterm-gui.exe"
        | "alacritty.exe"
        | "mintty.exe" => AppKind::Terminal,
        "chrome.exe" | "msedge.exe" | "line.exe" | "discord.exe" | "slack.exe" | "chatgpt.exe"
        | "notion.exe" => AppKind::Chromium,
        _ => AppKind::Other,
    }
}

/// Only code editors have the editor.accessibilitySupport setting named by the hint.
pub fn needs_accessibility_hint(kind: AppKind) -> bool {
    kind == AppKind::CodeEditor
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn terminal_hosts_are_recognized_by_exact_case_insensitive_basename() {
        for name in [
            "WindowsTerminal.exe",
            "wt.exe",
            "wezterm-gui.exe",
            "alacritty.exe",
            "mintty.exe",
            "CONHOST.EXE",
            "OpenConsole.exe",
            "powershell.exe",
            "pwsh.exe",
            "cmd.exe",
            "C:\\Windows\\System32\\cmd.exe",
        ] {
            assert_eq!(classify(name), AppKind::Terminal, "{name}");
        }
        assert_eq!(classify("not-cmd.exe"), AppKind::Other);
        assert_eq!(classify("WindowsTerminal.exe.bak"), AppKind::Other);
    }

    #[test]
    fn code_editors_have_their_own_accessibility_hint_family() {
        for name in [
            "Code.exe",
            "Code - Insiders.exe",
            "Cursor.exe",
            "VSCodium.exe",
            "Windsurf.exe",
            "Trae.exe",
            "Positron.exe",
            "C:\\Users\\me\\AppData\\Local\\Programs\\Microsoft VS Code\\Code.exe",
        ] {
            assert_eq!(classify(name), AppKind::CodeEditor, "{name}");
        }
        assert!(needs_accessibility_hint(AppKind::CodeEditor));
    }

    #[test]
    fn editors_are_named_for_the_main_window() {
        assert_eq!(editor_id("Code.exe"), Some("vscode"));
        assert_eq!(editor_id("Code - Insiders.exe"), Some("vscode"));
        assert_eq!(
            editor_id("C:\\Program Files\\Cursor\\CURSOR.EXE"),
            Some("cursor")
        );
        assert_eq!(editor_id("VSCodium.exe"), Some("vscodium"));
        assert_eq!(editor_id("chrome.exe"), None);
        assert_eq!(editor_id("notcode.exe"), None);
        assert_eq!(editor_id("Code.exe.bak"), None);
        assert_eq!(editor_id(""), None);
        // Every editor the learner treats as one has a name for the main window, and the other
        // way round.
        for (exe, id) in EDITORS {
            assert_eq!(classify(exe), AppKind::CodeEditor, "{exe}");
            assert_eq!(editor_id(exe), Some(id), "{exe}");
        }
    }

    #[test]
    fn chromium_and_electron_apps_activate_the_provider_without_an_editor_hint() {
        for name in [
            "chrome.exe",
            "msedge.exe",
            "LINE.exe",
            "Discord.exe",
            "Slack.exe",
            "ChatGPT.exe",
            "Notion.exe",
        ] {
            assert_eq!(classify(name), AppKind::Chromium, "{name}");
        }
        assert!(!needs_accessibility_hint(AppKind::Chromium));
        assert!(!needs_accessibility_hint(AppKind::Terminal));
        assert!(!needs_accessibility_hint(AppKind::Other));
    }
}
