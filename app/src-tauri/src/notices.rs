//! Raphael's notices: the short reports the overlay shows as subtitles when a dictation could not
//! go the usual way, the word list after learning from the user's corrections, the line that says he
//! wrote down what he was told to remember, his greeting when a question to him begins, and the
//! announcements beside the emblem while he thinks about it. Each one is wrapped in 《》 and starts
//! with its prefix: 告 (report) or 否 (refusal) in Chinese and Japanese, 了 for the learned words and
//! for a memory, and `Notice.`, `Negative.` or `Understood.` in English.
//!
//! They are written in his manner: calm, exact and brief. A report states what happened and what
//! was done about it, a refusal states why, and neither apologises, jokes or exclaims. He calls the
//! person 主人, マスター or Master only where he addresses them, which here is the greeting alone.
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
    zh: "《告。此視窗權限較高，無法貼上。文字已複製，請按 Ctrl+V。》",
    en: "《Notice. This window has higher privileges and refuses the paste. The text is copied. Press Ctrl+V.》",
    ja: "《告。権限の高いウィンドウには貼り付けできません。コピー済みです。Ctrl+V で貼り付けてください。》",
};

/// Focus moved to another window after the key was released, so the text was only copied.
pub const FOCUS_CHANGED: Notice = Notice {
    zh: "《告。偵測到視窗切換。文字已複製，請按 Ctrl+V。》",
    en: "《Notice. The focus moved to another window. The text is copied. Press Ctrl+V.》",
    ja: "《告。ウィンドウの切り替えを検知しました。コピー済みです。Ctrl+V で貼り付けてください。》",
};

/// The monthly limit is reached: the raw text is pasted without cleanup.
pub const BUDGET: Notice = Notice {
    zh: "《告。本月額度已用盡。聽寫暫不整理，直接貼上原文。》",
    en: "《Notice. This month's budget is exhausted. Dictation is pasted without cleanup.》",
    ja: "《告。今月の予算が上限に達しました。整形を省略して貼り付けます。》",
};

/// OpenRouter could not be reached: the raw text is pasted.
pub const OFFLINE: Notice = Notice {
    zh: "《告。無法連線至 OpenRouter。改為貼上未整理的原文。》",
    en: "《Notice. OpenRouter is unreachable. The text is pasted without cleanup.》",
    ja: "《告。OpenRouter に接続できません。整形前のテキストを貼り付けます。》",
};

/// No OpenRouter key is stored: the raw text is pasted.
pub const NO_KEY: Notice = Notice {
    zh: "《告。未設定 OpenRouter 金鑰。改為貼上未整理的原文。》",
    en: "《Notice. No OpenRouter key is set. The text is pasted without cleanup.》",
    ja: "《告。OpenRouter のキーが未設定です。整形前のテキストを貼り付けます。》",
};

/// The core found no microphone to record from.
pub const NO_MIC: Notice = Notice {
    zh: "《否。未偵測到麥克風。》",
    en: "《Negative. No microphone detected.》",
    ja: "《否。マイクを検出できません。》",
};

/// The microphone is there but delivered no sound (muted, or a wireless headset asleep), and
/// nothing came of the recording (`silent` in the result of `dictation.stop`).
pub const NO_SOUND: Notice = Notice {
    zh: "《否。未收到聲音。請確認麥克風。》",
    en: "《Negative. No sound was received. Check the microphone.》",
    ja: "《否。音声を検出できませんでした。マイクを確認してください。》",
};

/// The core cannot dictate yet: the speech model is not ready (`NOT_CONFIGURED`).
pub const NOT_READY: Notice = Notice {
    zh: "《否。語音模型尚未就緒。》",
    en: "《Negative. The speech model is not ready.》",
    ja: "《否。音声モデルの準備が完了していません。》",
};

/// The cloud could not transcribe the speech and no local model is left to take over
/// (`NO_LOCAL_MODEL`).
pub const NO_LOCAL_MODEL: Notice = Notice {
    zh: "《否。雲端辨識失敗，且沒有本機模型。》",
    en: "《Negative. Cloud transcription failed and there is no local model.》",
    ja: "《否。クラウド認識に失敗しました。ローカルモデルがありません。》",
};

/// Some segments still failed after the final retry; the pasted text marks each gap.
pub const INCOMPLETE: Notice = Notice {
    zh: "《否。部分語音無法辨識，文字已標示缺漏。》",
    en: "《Negative. Some speech could not be transcribed. Gaps are marked in the text.》",
    ja: "《否。一部の音声を認識できませんでした。欠落箇所を表示しました。》",
};

/// The core is not running, so nothing can be dictated.
pub const CORE_DOWN: Notice = Notice {
    zh: "《否。核心未在運作。請重新啟動拉斐爾。》",
    en: "《Negative. The core is not running. Restart Raphael.》",
    ja: "《否。コアが動作していません。ラファエルを再起動してください。》",
};

/// The agent could not answer a question (it did not reply in time, or its reply cannot be shown).
/// The core's own refusals (no key, over budget, offline) arrive as answers and are shown as such.
pub const ASK_FAILED: Notice = Notice {
    zh: "《否。目前無法回答。》",
    en: "《Negative. No answer can be given at this time.》",
    ja: "《否。現在は回答できません。》",
};

/// Raphael wrote down what he was told to remember, in the same turn as his answer, which said
/// something else (`"notice": "remembered"` in the reply of `agent.ask`). It follows the answer.
pub const REMEMBERED: Notice = Notice {
    zh: "《了。已記下。》",
    en: "《Understood. Noted.》",
    ja: "《了。記憶しました。》",
};

/// A question to Raphael has begun: his greeting beside the emblem while he listens. A dictation
/// shows the emblem alone, so the two are never mistaken for each other. He stands by for an
/// order, as a report rather than a question (問 is for what takes a yes or a no), and it is the
/// one notice that addresses the person.
pub const GREETING: Notice = Notice {
    zh: "《告。主人，請下達指示。》",
    en: "《Notice. Standing by, Master.》",
    ja: "《告。マスター、ご指示をどうぞ。》",
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

    const ALL: [Notice; 13] = [
        ELEVATED,
        FOCUS_CHANGED,
        BUDGET,
        OFFLINE,
        NO_KEY,
        NO_MIC,
        NO_SOUND,
        NOT_READY,
        NO_LOCAL_MODEL,
        INCOMPLETE,
        CORE_DOWN,
        ASK_FAILED,
        GREETING,
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

    /// The room beside the largest emblem that one line of the caption may take at 14 px, in px.
    /// The window is 540 px wide and leaves about 300 beside an emblem of 194; this keeps a margin
    /// for the estimate below. overlay-style.test.ts checks that overlay.css leaves at least this.
    const CAPTION_ROOM_PX: usize = 280;

    /// About how wide `line` is at 14 px: a Chinese or Japanese character (the brackets and the
    /// ellipsis among them) fills a square, the rest is about half as wide.
    fn width_at_14px(line: &str) -> usize {
        line.chars()
            .map(|c| if c.is_ascii() { 8 } else { 14 })
            .sum()
    }

    /// How Raphael addresses the person: 主人 in Chinese, マスター in Japanese, Master in English.
    const ADDRESS: [&str; 3] = ["主人", "マスター", "Master"];

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
    fn raphael_never_exclaims_and_never_asks_in_a_notice() {
        // A report or a refusal states; only 問 asks, and none of these is one.
        for notice in ALL {
            for line in [notice.zh, notice.en, notice.ja] {
                assert!(!line.contains(['!', '！', '?', '？']), "{line}");
            }
        }
    }

    #[test]
    fn only_the_greeting_addresses_the_person_and_in_the_word_of_its_language() {
        // 主人 in Chinese, マスター in Japanese, Master in English: sparingly, as he does.
        for (line, address) in [
            (GREETING.zh, "主人"),
            (GREETING.ja, "マスター"),
            (GREETING.en, "Master"),
        ] {
            assert!(line.contains(address), "{line}");
            for other in ADDRESS.iter().filter(|other| **other != address) {
                assert!(!line.contains(other), "{line}");
            }
        }
        for notice in ALL.iter().filter(|notice| **notice != GREETING) {
            for line in [notice.zh, notice.en, notice.ja] {
                assert!(!ADDRESS.iter().any(|word| line.contains(word)), "{line}");
            }
        }
    }

    #[test]
    fn the_greeting_is_one_line_beside_the_largest_emblem() {
        for line in [GREETING.zh, GREETING.en, GREETING.ja] {
            let width = width_at_14px(line);
            assert!(width <= CAPTION_ROOM_PX, "{line}: about {width} px");
        }
    }

    #[test]
    fn the_memory_notice_is_an_acknowledgement_in_each_language_on_one_line() {
        assert_eq!(REMEMBERED.text("zh-TW"), "《了。已記下。》");
        assert_eq!(REMEMBERED.text("en"), "《Understood. Noted.》");
        assert_eq!(REMEMBERED.text("ja"), "《了。記憶しました。》");
        assert_eq!(REMEMBERED.text("fr"), REMEMBERED.zh);
        assert!(!has_kana(REMEMBERED.zh) && has_kana(REMEMBERED.ja));
        for line in [REMEMBERED.zh, REMEMBERED.en, REMEMBERED.ja] {
            assert!(
                line.ends_with('》') && !line.contains(['!', '！', '?', '？']),
                "{line}"
            );
            assert!(width_at_14px(line) <= CAPTION_ROOM_PX, "{line}");
            assert!(!ADDRESS.iter().any(|word| line.contains(word)), "{line}");
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
