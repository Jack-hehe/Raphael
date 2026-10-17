//! Raphael's notices: the short reports the overlay shows as subtitles when a dictation could not
//! go the usual way. Each one is wrapped in 《》 and starts with 告 or 否.

/// One notice, in Traditional Chinese (Taiwan) and Japanese.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Notice {
    pub zh: &'static str,
    pub ja: &'static str,
}

/// The target window runs elevated, so the text was only copied.
pub const ELEVATED: Notice = Notice {
    zh: "《告。這個視窗不接受貼上。文字已複製，請按 Ctrl+V。》",
    ja: "《告。このウィンドウには貼り付けできません。コピー済みです。》",
};

/// Focus moved to another window after the key was released, so the text was only copied.
pub const FOCUS_CHANGED: Notice = Notice {
    zh: "《告。視窗已經切換。文字已複製，請按 Ctrl+V。》",
    ja: "《告。ウィンドウが切り替わりました。コピー済みです。》",
};

/// The monthly limit is reached: the raw text is pasted without cleanup.
pub const BUDGET: Notice = Notice {
    zh: "《告。本月額度已用完，聽寫先不整理。》",
    ja: "《告。今月の予算を使い切りました。整形せずに貼り付けます。》",
};

/// OpenRouter could not be reached: the raw text is pasted.
pub const OFFLINE: Notice = Notice {
    zh: "《告。無法連線到 OpenRouter。先貼上未整理的文字。》",
    ja: "《告。OpenRouter に接続できません。整形前のテキストを貼り付けます。》",
};

/// No OpenRouter key is stored: the raw text is pasted.
pub const NO_KEY: Notice = Notice {
    zh: "《告。尚未設定 OpenRouter 金鑰。先貼上未整理的文字。》",
    ja: "《告。OpenRouter のキーが未設定です。整形前のテキストを貼り付けます。》",
};

/// The core found no microphone to record from.
pub const NO_MIC: Notice = Notice {
    zh: "《否。找不到麥克風。》",
    ja: "《否。マイクが見つかりません。》",
};

/// The notice for the `fallback_reason` of a `dictation.stop` result, if it gets one. A plain
/// `"timeout"` (the cleanup took too long) and the other reasons are not worth a notice.
pub fn notice_for_fallback(reason: Option<&str>) -> Option<Notice> {
    match reason? {
        "budget" => Some(BUDGET),
        "no_key" => Some(NO_KEY),
        "error:network" | "error:timeout" | "error:server" => Some(OFFLINE),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fallback_reasons_map_to_notices() {
        assert_eq!(notice_for_fallback(Some("budget")).unwrap().zh, BUDGET.zh);
        assert!(notice_for_fallback(Some("suspicious")).is_none());
        assert!(notice_for_fallback(None).is_none());
    }

    #[test]
    fn every_fallback_reason_has_its_notice() {
        assert_eq!(notice_for_fallback(Some("no_key")), Some(NO_KEY));
        for reason in ["error:network", "error:timeout", "error:server"] {
            assert_eq!(notice_for_fallback(Some(reason)), Some(OFFLINE), "{reason}");
        }
        for reason in ["timeout", "empty", "error:other", ""] {
            assert_eq!(notice_for_fallback(Some(reason)), None, "{reason}");
        }
    }

    #[test]
    fn notices_are_raphael_lines() {
        for notice in [ELEVATED, FOCUS_CHANGED, BUDGET, OFFLINE, NO_KEY, NO_MIC] {
            for line in [notice.zh, notice.ja] {
                assert!(
                    line.starts_with("《告。") || line.starts_with("《否。"),
                    "{line}"
                );
                assert!(line.ends_with('》'), "{line}");
            }
        }
    }
}
