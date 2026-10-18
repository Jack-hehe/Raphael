//! Raphael's notices: the short reports the overlay shows as subtitles when a dictation could not
//! go the usual way, and the word list after learning from the user's corrections. Each one is
//! wrapped in 《》 and starts with 告 or 否 (了 for the learned words).

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

/// The microphone is there but delivered no sound (muted, or a wireless headset asleep), and
/// nothing came of the recording (`silent` in the result of `dictation.stop`).
pub const NO_SOUND: Notice = Notice {
    zh: "《否。沒有收到聲音，請檢查麥克風。》",
    ja: "《否。音声が届いていません。マイクを確認してください。》",
};

/// The tray's 「貼上上一次聽寫」: the menu took the focus, so the text is only copied.
pub const LAST_COPIED: Notice = Notice {
    zh: "《告。上一次的聽寫已複製。請按 Ctrl+V 貼上。》",
    ja: "《告。前回の音声入力をコピーしました。Ctrl+V で貼り付けてください。》",
};

/// The core cannot dictate yet: the speech model is not ready (`NOT_CONFIGURED`).
pub const NOT_READY: Notice = Notice {
    zh: "《否。語音模型尚未就緒。》",
    ja: "《否。音声モデルの準備ができていません。》",
};

/// The core is not running, so nothing can be dictated.
pub const CORE_DOWN: Notice = Notice {
    zh: "《否。核心程序未執行，請重新啟動拉斐爾。》",
    ja: "《否。コアが動作していません。ラファエルを再起動してください。》",
};

/// Words learned from the user's corrections after a paste, in one notice:
/// 《了。已將「手機」、「Typeless」加入字典。》 and its Japanese line.
pub fn learned(words: &[String]) -> (String, String) {
    let quoted = words
        .iter()
        .map(|word| format!("「{word}」"))
        .collect::<Vec<_>>()
        .join("、");
    (
        format!("《了。已將{quoted}加入字典。》"),
        format!("《了。{quoted}を辞書に追加しました。》"),
    )
}

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
    fn the_learned_notice_names_every_word_once() {
        let (zh, ja) = learned(&["手機".to_owned()]);
        assert_eq!(zh, "《了。已將「手機」加入字典。》");
        assert_eq!(ja, "《了。「手機」を辞書に追加しました。》");
        let (zh, _) = learned(&["手機".to_owned(), "Typeless".to_owned()]);
        assert_eq!(zh, "《了。已將「手機」、「Typeless」加入字典。》");
    }

    #[test]
    fn notices_are_raphael_lines() {
        for notice in [
            ELEVATED,
            FOCUS_CHANGED,
            BUDGET,
            OFFLINE,
            NO_KEY,
            NO_MIC,
            NO_SOUND,
            LAST_COPIED,
            NOT_READY,
            CORE_DOWN,
        ] {
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
