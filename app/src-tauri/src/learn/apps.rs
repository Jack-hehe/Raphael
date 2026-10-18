//! App families used only by correction learning.

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum AppKind {
    Terminal,
    Chromium,
    CodeEditor,
    Other,
}

/// Classifies the executable basename, without changing paste-shortcut policy.
pub fn classify(process_name: &str) -> AppKind {
    let basename = process_name
        .rsplit(['\\', '/'])
        .next()
        .unwrap_or(process_name);
    match basename.to_ascii_lowercase().as_str() {
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
        "code.exe" | "code - insiders.exe" | "cursor.exe" | "vscodium.exe" => AppKind::CodeEditor,
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
        ] {
            assert_eq!(classify(name), AppKind::CodeEditor, "{name}");
        }
        assert!(needs_accessibility_hint(AppKind::CodeEditor));
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
