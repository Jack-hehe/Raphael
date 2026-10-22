//! Raphael's notices: the short reports the overlay shows as subtitles when a dictation could not
//! go the usual way, the word list after learning from the user's corrections, his greeting when a
//! question to him begins, and the announcements beside the emblem while he thinks about it. Each
//! one is wrapped in 《》 and starts with its prefix: 告 (report) or 否 (refusal) in Chinese and
//! Japanese, 了 for the learned words, and `Notice.`, `Negative.` or `Understood.` in English.
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

/// A question to Raphael has begun: his greeting beside the emblem while he listens. A dictation
/// shows the emblem alone, so the two are never mistaken for each other. He stands by for an
/// order, as a report rather than a question (問 is for what takes a yes or a no), and it is the
/// one notice that addresses the person.
pub const GREETING: Notice = Notice {
    zh: "《告。主人，請下達指示。》",
    en: "《Notice. Standing by, Master.》",
    ja: "《告。マスター、ご指示をどうぞ。》",
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
/// question: like the percentage of a dictation it tells the person that he is at work. Each is a
/// short report of his own (告, `Notice.`) on the analysis that runs, trailing off because it is
/// still running: no jokes, no tips, no results. Short, so that it stays one line in the room
/// beside the largest emblem at 14 px. Generic words only, nothing quoted from anywhere.
pub const WAITING: Phrases = Phrases {
    zh: &[
        "《告。解析中……》",
        "《告。並列演算中……》",
        "《告。檢索最適解……》",
        "《告。思考加速中……》",
        "《告。整理情報中……》",
        "《告。比對已知條件……》",
        "《告。驗證推論中……》",
        "《告。統合各項資料……》",
        "《告。重新評估前提……》",
        "《告。計算成功率……》",
        "《告。篩選有效資訊……》",
        "《告。構築回答中……》",
        "《告。演算持續中……》",
        "《告。確認可行路徑……》",
        "《告。比較候補方案……》",
        "《告。推演中……》",
    ],
    en: &[
        "《Notice. Analyzing…》",
        "《Notice. Computing in parallel…》",
        "《Notice. Seeking the optimum…》",
        "《Notice. Accelerating thought…》",
        "《Notice. Sorting the data…》",
        "《Notice. Checking known facts…》",
        "《Notice. Verifying the logic…》",
        "《Notice. Integrating findings…》",
        "《Notice. Rechecking premises…》",
        "《Notice. Estimating the odds…》",
        "《Notice. Discarding noise…》",
        "《Notice. Composing the answer…》",
        "《Notice. Calculation continues…》",
        "《Notice. Tracing viable paths…》",
        "《Notice. Comparing options…》",
        "《Notice. Running inference…》",
    ],
    ja: &[
        "《告。解析を実行中……》",
        "《告。並列演算を実行中……》",
        "《告。最適解を検索中……》",
        "《告。思考を加速中……》",
        "《告。情報を整理中……》",
        "《告。既知の条件を照合中……》",
        "《告。推論を検証中……》",
        "《告。情報を統合中……》",
        "《告。前提を再評価中……》",
        "《告。成功率を算出中……》",
        "《告。有効な情報を選別中……》",
        "《告。回答を構築中……》",
        "《告。演算を継続中……》",
        "《告。実行可能な経路を確認中……》",
        "《告。候補を比較中……》",
        "《告。推論を展開中……》",
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

/// The most words an [`auto_added`] notice names; more are counted.
const AUTO_ADDED_NAMED: usize = 3;

/// Words the dictionary took in by itself, because they kept coming up in the dictation, in one
/// notice, in `language`: 《了。已加入「Tailscale」至辭典。》, or for more than three words
/// 《了。已加入「Tailscale」等 5 個詞至辭典。》.
pub fn auto_added(words: &[String], language: &str) -> String {
    let quoted = |open: &str, close: &str, separator: &str| {
        words
            .iter()
            .map(|word| format!("{open}{word}{close}"))
            .collect::<Vec<_>>()
            .join(separator)
    };
    let many = words.len() > AUTO_ADDED_NAMED;
    let (first, count) = (words.first().map_or("", String::as_str), words.len());
    match language {
        "en" if many => format!(
            "《Understood. {count} words added to the dictionary, among them \u{201c}{first}\u{201d}.》"
        ),
        "en" => format!(
            "《Understood. {} added to the dictionary.》",
            quoted("\u{201c}", "\u{201d}", ", ")
        ),
        "ja" if many => format!("《了。「{first}」など{count}語を辞書に追加しました。》"),
        "ja" => format!("《了。{}を辞書に追加しました。》", quoted("「", "」", "、")),
        _ if many => format!("《了。已加入「{first}」等 {count} 個詞至辭典。》"),
        _ => format!("《了。已加入{}至辭典。》", quoted("「", "」", "、")),
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
        NOT_READY,
        NO_LOCAL_MODEL,
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
    fn the_auto_added_notice_names_up_to_three_words_and_counts_more() {
        let words = |n: usize| {
            ["Tailscale", "OpenRouter", "Kokoro", "GPT-SoVITS", "Tauri"][..n]
                .iter()
                .map(|word| (*word).to_owned())
                .collect::<Vec<_>>()
        };
        assert_eq!(
            auto_added(&words(1), "zh-TW"),
            "《了。已加入「Tailscale」至辭典。》"
        );
        assert_eq!(
            auto_added(&words(3), "zh-TW"),
            "《了。已加入「Tailscale」、「OpenRouter」、「Kokoro」至辭典。》"
        );
        assert_eq!(
            auto_added(&words(5), "zh-TW"),
            "《了。已加入「Tailscale」等 5 個詞至辭典。》"
        );
        assert_eq!(
            auto_added(&words(2), "ja"),
            "《了。「Tailscale」、「OpenRouter」を辞書に追加しました。》"
        );
        assert_eq!(
            auto_added(&words(4), "ja"),
            "《了。「Tailscale」など4語を辞書に追加しました。》"
        );
        assert_eq!(
            auto_added(&words(2), "en"),
            "《Understood. \u{201c}Tailscale\u{201d}, \u{201c}OpenRouter\u{201d} added to the dictionary.》"
        );
        assert_eq!(
            auto_added(&words(4), "en"),
            "《Understood. 4 words added to the dictionary, among them \u{201c}Tailscale\u{201d}.》"
        );
        for language in ["", "fr", "EN"] {
            assert_eq!(
                auto_added(&words(1), language),
                auto_added(&words(1), "zh-TW")
            );
        }
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
    fn the_waiting_lines_are_a_book_of_twelve_to_twenty_in_every_language() {
        for (language, book) in [
            ("zh-TW", WAITING.zh),
            ("en", WAITING.en),
            ("ja", WAITING.ja),
        ] {
            assert!(
                (12..=20).contains(&book.len()),
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
    fn every_waiting_line_is_a_report_of_raphael_that_trails_off() {
        // 《告。…》, still running: the work goes on while it is shown.
        for line in WAITING.zh.iter().chain(WAITING.ja) {
            assert!(line.starts_with("《告。"), "{line}");
            assert!(line.ends_with("……》"), "{line}");
        }
        for line in WAITING.en {
            assert!(line.starts_with("《Notice. "), "{line}");
            assert!(line.ends_with("…》") && !line.ends_with("……》"), "{line}");
        }
    }

    #[test]
    fn each_waiting_book_stays_in_its_own_script() {
        for line in WAITING.zh {
            assert!(!has_kana(line), "{line}");
        }
        for line in WAITING.ja {
            assert!(has_kana(line), "{line}");
        }
        for line in WAITING.en {
            assert!(
                line.chars()
                    .all(|c| c.is_ascii() || matches!(c, '《' | '》' | '…')),
                "{line}"
            );
        }
    }

    #[test]
    fn no_waiting_line_jokes_quotes_or_reports_a_result() {
        // The analysis is under way: no tip about a key, no figure, no quotation, no address.
        for line in WAITING.zh.iter().chain(WAITING.en).chain(WAITING.ja) {
            assert!(!line.contains("Esc"), "{line}");
            assert!(
                !line.chars().any(|c| c.is_ascii_digit() || c == '%'),
                "{line}"
            );
            assert!(!line.contains(['「', '」', '『', '』', '"']), "{line}");
            assert!(!line.contains(['!', '！', '?', '？']), "{line}");
            assert!(!ADDRESS.iter().any(|word| line.contains(word)), "{line}");
        }
    }

    #[test]
    fn the_waiting_lines_fit_beside_the_largest_emblem() {
        for line in WAITING.zh.iter().chain(WAITING.en).chain(WAITING.ja) {
            let width = width_at_14px(line);
            assert!(width <= CAPTION_ROOM_PX, "{line}: about {width} px");
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
