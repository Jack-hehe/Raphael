//! Raphael's notices: the short reports the overlay shows as subtitles when a dictation could not
//! go the usual way, the word list after learning from the user's corrections, and his greeting
//! when a question to him begins. Each one is
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

/// The agent could not answer a question (it did not reply in time, or its reply cannot be shown).
/// The core's own refusals (no key, over budget, offline) arrive as answers and are shown as such.
pub const ASK_FAILED: Notice = Notice {
    zh: "《否。目前無法回答。》",
    en: "《Negative. Unable to answer at this time.》",
    ja: "《否。現在は回答できません。》",
};

/// A question to Raphael has begun: his greeting beside the emblem while he listens. A dictation
/// shows the emblem alone, so the two are never mistaken for each other.
pub const GREETING: Notice = Notice {
    zh: "《告。今天有什麼事可以幫忙嗎？》",
    en: "《Notice. What can I help you with today?》",
    ja: "《告。今日は何をお手伝いしましょうか？》",
};

/// A phrase book in Traditional Chinese (Taiwan), English and Japanese. The overlay is sent the
/// book of one language, as it is sent one text of a [`Notice`].
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Phrases {
    pub zh: &'static [&'static str],
    pub en: &'static [&'static str],
    pub ja: &'static [&'static str],
}

impl Phrases {
    /// The lines for `language` (`"zh-TW"`, `"en"` or `"ja"`). A language the book does not have
    /// gets the Traditional Chinese ones, as in [`Notice::text`].
    pub fn lines(&self, language: &str) -> &'static [&'static str] {
        match language {
            "en" => self.en,
            "ja" => self.ja,
            _ => self.zh,
        }
    }
}

/// What the overlay writes beside the emblem, one line after another, while Raphael thinks about a
/// question: like the percentage of a dictation it tells the person that he is at work. These are
/// no reports of his (no 《》), just quiet words. Mostly the calculation of an analytical skill,
/// with a few game-tip jokes at the end. Short, because the room beside the emblem is about 300
/// px wide at 14 px. Generic words only, nothing quoted from anywhere.
pub const WAITING: Phrases = Phrases {
    zh: &[
        "解析中……",
        "並列演算中……",
        "檢索最適解……",
        "思考加速中……",
        "整理情報中……",
        "確認可行路徑……",
        "比對已知條件……",
        "展開候補方案……",
        "驗證推論結果……",
        "統合各項情報……",
        "重新評估前提……",
        "計算成功率……",
        "篩選有效資訊……",
        "構築回答模型……",
        "把答案從異空間拉回來……",
        "替思考迴路上油……",
        "計算失敗的可能性：0%……",
        "給演算核心倒杯茶……",
        "從資料海裡撈答案……",
        "拉斐爾小知識：Esc 可以中止一切",
    ],
    en: &[
        "Analyzing…",
        "Running parallel calculations…",
        "Searching for the best answer…",
        "Accelerating thought…",
        "Sorting out the information…",
        "Checking viable paths…",
        "Comparing known conditions…",
        "Expanding candidate plans…",
        "Verifying the reasoning…",
        "Integrating the findings…",
        "Re-evaluating assumptions…",
        "Estimating the odds…",
        "Filtering useful information…",
        "Building an answer model…",
        "Fetching answers from afar…",
        "Oiling the thinking circuits…",
        "Odds of failure: 0%…",
        "Making the core a cup of tea…",
        "Fishing answers out of the data…",
        "Raphael tip: Esc stops everything",
    ],
    ja: &[
        "解析中……",
        "並列演算を実行中……",
        "最適解を検索中……",
        "思考を加速中……",
        "情報を整理中……",
        "実行可能な経路を確認中……",
        "既知の条件を照合中……",
        "候補案を展開中……",
        "推論結果を検証中……",
        "情報を統合中……",
        "前提を再評価中……",
        "成功率を算出中……",
        "有効な情報を選別中……",
        "回答モデルを構築中……",
        "答えを異空間から引き戻し中……",
        "思考回路に油を差し中……",
        "失敗する確率：0%……",
        "演算コアにお茶を淹れ中……",
        "データの海で答えを釣り中……",
        "ラファエル豆知識：Esc ですべて中止",
    ],
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

    const ALL: [Notice; 12] = [
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
    fn the_waiting_lines_are_a_book_of_about_twenty_in_every_language() {
        for (language, book) in [
            ("zh-TW", WAITING.zh),
            ("en", WAITING.en),
            ("ja", WAITING.ja),
        ] {
            assert!(
                (18..=24).contains(&book.len()),
                "{language}: {}",
                book.len()
            );
            let mut seen = std::collections::HashSet::new();
            for line in book {
                assert!(seen.insert(line), "{language}: {line} twice");
                assert!(
                    !line.is_empty() && line.trim() == *line,
                    "{language}: {line:?}"
                );
            }
        }
    }

    #[test]
    fn the_waiting_lines_are_sent_in_one_language_only() {
        assert_eq!(WAITING.lines("zh-TW"), WAITING.zh);
        assert_eq!(WAITING.lines("en"), WAITING.en);
        assert_eq!(WAITING.lines("ja"), WAITING.ja);
        // A language nobody chose is the default one, never a mix.
        for other in ["", "fr", "zh-CN", "EN", "zh"] {
            assert_eq!(WAITING.lines(other), WAITING.zh, "{other:?}");
        }
    }

    #[test]
    fn the_waiting_lines_are_quiet_ones_and_each_language_stays_in_its_own_script() {
        for line in WAITING.zh.iter().chain(WAITING.en).chain(WAITING.ja) {
            // Not Raphael speaking: no 《》, and nobody is quoted either.
            assert!(!line.contains(['《', '》', '「', '」']), "{line}");
        }
        for line in WAITING.zh {
            assert!(!has_kana(line), "{line}");
        }
        // Nearly every Japanese line has kana; "解析中……" reads the same in both.
        let with_kana = WAITING.ja.iter().filter(|line| has_kana(line)).count();
        assert!(with_kana >= WAITING.ja.len() - 2, "{with_kana}");
        for line in WAITING.en {
            assert!(line.chars().all(|c| c.is_ascii() || c == '…'), "{line}");
        }
    }

    #[test]
    fn the_waiting_lines_fit_beside_the_emblem() {
        // At 14 px: a Chinese or Japanese character (and the ellipsis) fills a square, the rest
        // is about half as wide. The window leaves about 300 px beside the largest emblem.
        for line in WAITING.zh.iter().chain(WAITING.en).chain(WAITING.ja) {
            let width: usize = line
                .chars()
                .map(|c| if c.is_ascii() { 8 } else { 14 })
                .sum();
            assert!(width <= 270, "{line}: about {width} px");
        }
    }

    #[test]
    fn every_waiting_book_is_mostly_calculation_and_ends_with_the_esc_tip() {
        for book in [WAITING.zh, WAITING.en, WAITING.ja] {
            assert!(book.last().is_some_and(|line| line.contains("Esc")));
            assert_eq!(book.iter().filter(|line| line.contains("Esc")).count(), 1);
            // The running tasks trail off; only the tip does not.
            assert!(book.iter().filter(|line| line.contains('…')).count() >= book.len() - 2);
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
