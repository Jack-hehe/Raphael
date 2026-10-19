//! Raphael's notices: the short reports the overlay shows as subtitles when a dictation could not
//! go the usual way, and the word list after learning from the user's corrections. Each one is
//! wrapped in 《》 and starts with its prefix: 告 (report) or 否 (refusal) in Chinese and Japanese,
//! 了 for the learned words, and `Notice.`, `Negative.` or `Understood.` in English.
//!
//! A notice exists in every interface language, and the overlay is sent only the one for the
//! language the person chose ([`Notice::text`]): never a Chinese and a Japanese line side by side.

use std::sync::{Arc, Mutex};

use crate::rpc::lock;

/// The interface language of the device settings that Raphael's notices and answers follow.
pub const DEFAULT_LANGUAGE: &str = "zh-TW";

/// The interface language (`ui_language` of the device settings): `"zh-TW"`, `"en"` or `"ja"`. The
/// device settings write it (at start, and when the person changes it) and the controller reads it
/// each time it has something to say. Clones share the value.
#[derive(Clone, Debug)]
pub struct LanguageCell(Arc<Mutex<String>>);

impl Default for LanguageCell {
    fn default() -> Self {
        Self(Arc::new(Mutex::new(DEFAULT_LANGUAGE.to_owned())))
    }
}

impl LanguageCell {
    pub fn get(&self) -> String {
        lock(&self.0).clone()
    }

    pub fn set(&self, language: &str) {
        language.clone_into(&mut lock(&self.0));
    }
}

/// One notice, in Traditional Chinese (Taiwan), English and Japanese.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Notice {
    pub zh: &'static str,
    pub en: &'static str,
    pub ja: &'static str,
}

impl Notice {
    /// The text for `language` (`"zh-TW"`, `"en"` or `"ja"`). A language the notices do not have
    /// gets the Traditional Chinese one, like an interface language that was never chosen.
    pub fn text(&self, language: &str) -> &'static str {
        match language {
            "en" => self.en,
            "ja" => self.ja,
            _ => self.zh,
        }
    }
}

/// The target window runs elevated, so the text was only copied.
pub const ELEVATED: Notice = Notice {
    zh: "《告。這個視窗不接受貼上。文字已複製，請按 Ctrl+V。》",
    en: "《Notice. This window does not accept pasting. The text is copied, so press Ctrl+V.》",
    ja: "《告。このウィンドウには貼り付けできません。コピー済みです。》",
};

/// Focus moved to another window after the key was released, so the text was only copied.
pub const FOCUS_CHANGED: Notice = Notice {
    zh: "《告。視窗已經切換。文字已複製，請按 Ctrl+V。》",
    en: "《Notice. The window changed. The text is copied, so press Ctrl+V.》",
    ja: "《告。ウィンドウが切り替わりました。コピー済みです。》",
};

/// The monthly limit is reached: the raw text is pasted without cleanup.
pub const BUDGET: Notice = Notice {
    zh: "《告。本月額度已用完，聽寫先不整理。》",
    en: "《Notice. This month's budget is used up. Dictation skips the cleanup for now.》",
    ja: "《告。今月の予算を使い切りました。整形せずに貼り付けます。》",
};

/// OpenRouter could not be reached: the raw text is pasted.
pub const OFFLINE: Notice = Notice {
    zh: "《告。無法連線到 OpenRouter。先貼上未整理的文字。》",
    en: "《Notice. Cannot reach OpenRouter. Pasting the text without cleanup.》",
    ja: "《告。OpenRouter に接続できません。整形前のテキストを貼り付けます。》",
};

/// No OpenRouter key is stored: the raw text is pasted.
pub const NO_KEY: Notice = Notice {
    zh: "《告。尚未設定 OpenRouter 金鑰。先貼上未整理的文字。》",
    en: "《Notice. No OpenRouter key is set. Pasting the text without cleanup.》",
    ja: "《告。OpenRouter のキーが未設定です。整形前のテキストを貼り付けます。》",
};

/// The core found no microphone to record from.
pub const NO_MIC: Notice = Notice {
    zh: "《否。找不到麥克風。》",
    en: "《Negative. No microphone found.》",
    ja: "《否。マイクが見つかりません。》",
};

/// The microphone is there but delivered no sound (muted, or a wireless headset asleep), and
/// nothing came of the recording (`silent` in the result of `dictation.stop`).
pub const NO_SOUND: Notice = Notice {
    zh: "《否。沒有收到聲音，請檢查麥克風。》",
    en: "《Negative. No sound received. Please check the microphone.》",
    ja: "《否。音声が届いていません。マイクを確認してください。》",
};

/// The tray's 「貼上上一次聽寫」: the menu took the focus, so the text is only copied.
pub const LAST_COPIED: Notice = Notice {
    zh: "《告。上一次的聽寫已複製。請按 Ctrl+V 貼上。》",
    en: "《Notice. The last dictation is copied. Press Ctrl+V to paste it.》",
    ja: "《告。前回の音声入力をコピーしました。Ctrl+V で貼り付けてください。》",
};

/// The core cannot dictate yet: the speech model is not ready (`NOT_CONFIGURED`).
pub const NOT_READY: Notice = Notice {
    zh: "《否。語音模型尚未就緒。》",
    en: "《Negative. The speech model is not ready yet.》",
    ja: "《否。音声モデルの準備ができていません。》",
};

/// The core is not running, so nothing can be dictated.
pub const CORE_DOWN: Notice = Notice {
    zh: "《否。核心程序未執行，請重新啟動拉斐爾。》",
    en: "《Negative. The core is not running. Please restart Raphael.》",
    ja: "《否。コアが動作していません。ラファエルを再起動してください。》",
};

/// Words learned from the user's corrections after a paste, in one notice, in `language`:
/// 《了。「手機」、「Typeless」已加入詞典。》, `《Understood. “Typeless” added to the dictionary.》`
/// or 《了。「手機」を辞書に追加しました。》.
pub fn learned(words: &[String], language: &str) -> String {
    let quoted = |open: &str, close: &str, separator: &str| {
        words
            .iter()
            .map(|word| format!("{open}{word}{close}"))
            .collect::<Vec<_>>()
            .join(separator)
    };
    match language {
        "en" => format!(
            "《Understood. {} added to the dictionary.》",
            quoted("\u{201c}", "\u{201d}", ", ")
        ),
        "ja" => format!("《了。{}を辞書に追加しました。》", quoted("「", "」", "、")),
        _ => format!("《了。{}已加入詞典。》", quoted("「", "」", "、")),
    }
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

    const ALL: [Notice; 10] = [
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
    ];

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
    fn the_learned_notice_names_every_word_once_in_each_language() {
        let one = ["手機".to_owned()];
        assert_eq!(learned(&one, "zh-TW"), "《了。「手機」已加入詞典。》");
        assert_eq!(
            learned(&one, "ja"),
            "《了。「手機」を辞書に追加しました。》"
        );
        assert_eq!(
            learned(&one, "en"),
            "《Understood. \u{201c}手機\u{201d} added to the dictionary.》"
        );
        let two = ["手機".to_owned(), "Typeless".to_owned()];
        assert_eq!(
            learned(&two, "zh-TW"),
            "《了。「手機」、「Typeless」已加入詞典。》"
        );
        assert_eq!(
            learned(&two, "ja"),
            "《了。「手機」、「Typeless」を辞書に追加しました。》"
        );
        assert_eq!(
            learned(&two, "en"),
            "《Understood. \u{201c}手機\u{201d}, \u{201c}Typeless\u{201d} added to the dictionary.》"
        );
    }

    #[test]
    fn the_learned_notice_in_an_unknown_language_is_chinese() {
        let words = ["手機".to_owned()];
        for language in ["", "fr", "zh-CN", "EN"] {
            assert_eq!(learned(&words, language), learned(&words, "zh-TW"));
        }
    }

    #[test]
    fn a_notice_is_sent_in_one_language_only() {
        for notice in ALL {
            assert_eq!(notice.text("zh-TW"), notice.zh);
            assert_eq!(notice.text("en"), notice.en);
            assert_eq!(notice.text("ja"), notice.ja);
            // A language nobody chose is the default one, never a pair.
            for other in ["", "fr", "zh-CN", "EN", "zh"] {
                assert_eq!(notice.text(other), notice.zh, "{other:?}");
            }
        }
    }

    fn has_kana(text: &str) -> bool {
        text.chars().any(|c| ('\u{3040}'..='\u{30ff}').contains(&c))
    }

    #[test]
    fn notices_are_raphael_lines() {
        for notice in ALL {
            for line in [notice.zh, notice.ja] {
                assert!(
                    line.starts_with("《告。") || line.starts_with("《否。"),
                    "{line}"
                );
                assert!(line.ends_with('》'), "{line}");
            }
            assert!(
                notice.en.starts_with("《Notice. ") || notice.en.starts_with("《Negative. "),
                "{}",
                notice.en
            );
            assert!(notice.en.ends_with('》'), "{}", notice.en);
            // The same kind of report in every language: 告 is Notice, 否 is Negative.
            let report = notice.zh.starts_with("《告。");
            assert_eq!(notice.ja.starts_with("《告。"), report, "{}", notice.ja);
            assert_eq!(notice.en.starts_with("《Notice. "), report, "{}", notice.en);
        }
    }

    #[test]
    fn each_language_stays_in_its_own_script() {
        for notice in ALL {
            assert!(!has_kana(notice.zh), "{}", notice.zh);
            assert!(has_kana(notice.ja), "{}", notice.ja);
            // English has the 《》 of the style and nothing else outside ASCII.
            assert!(
                notice
                    .en
                    .chars()
                    .all(|c| c.is_ascii() || c == '《' || c == '》'),
                "{}",
                notice.en
            );
        }
    }

    #[test]
    fn the_language_cell_starts_in_chinese_like_the_settings() {
        assert_eq!(LanguageCell::default().get(), "zh-TW");
        assert_eq!(
            LanguageCell::default().get(),
            crate::device_settings::DeviceSettings::default().ui_language
        );
    }

    #[test]
    fn the_language_cell_is_shared_between_clones() {
        let cell = LanguageCell::default();
        let other = cell.clone();
        other.set("ja");
        assert_eq!(cell.get(), "ja");
        cell.set("en");
        assert_eq!(other.get(), "en");
    }
}
