//! What the user corrected in a pasted dictation: pure logic, no Windows.
//!
//! [`Tracker`] starts from the field's text right after the paste, finds the pasted text in it,
//! and follows the field from snapshot to snapshot. Between two snapshots (half a second apart)
//! the user makes one small edit at most, so the common prefix and suffix of the two texts leave
//! exactly the edited part in the middle. Every character of the field is either one that was
//! there right after the paste (it remembers which) or one typed since. That is enough to tell,
//! at the end, which stretches of the original text were replaced by which new ones, even when
//! the user first deleted a word and typed the right one later: no diff of the whole text is
//! needed, and no word that the user retyped whole is cut in two by a coincidental match.
//!
//! [`Tracker::corrections`] keeps the replacements that look like a misheard term put right
//! (see [`Correction`]'s rules).

use std::ops::Range;

/// The user replaced `misheard` with `word` in the pasted text.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Correction {
    pub word: String,
    pub misheard: String,
}

/// The most corrections taken from one dictation.
pub const MAX_CORRECTIONS: usize = 3;
/// The most CJK characters (kanji, kana, hangul) on either side of a correction.
const MAX_CJK: usize = 12;
/// The most Latin words on either side of a correction.
const MAX_WORDS: usize = 3;
/// The most characters on either side of a correction, all kinds together.
const MAX_CHARS: usize = 40;
/// The edits inside the pasted text may change at most this share of it; more is a rewrite.
const MAX_EDITED_SHARE: f64 = 0.3;
/// Below this share of the pasted text still in the field, the field was cleared (the message
/// was sent) or replaced.
pub const MIN_KEPT_SHARE: f64 = 0.5;

/// A field's text as the tracker compares it: line breaks as `\n` (edit controls report `\r\n`,
/// Word reports `\r`).
fn chars_of(text: &str) -> Vec<char> {
    let mut out = Vec::with_capacity(text.len());
    let mut chars = text.chars().peekable();
    while let Some(c) = chars.next() {
        if c == '\r' {
            if chars.peek() == Some(&'\n') {
                chars.next();
            }
            out.push('\n');
        } else {
            out.push(c);
        }
    }
    out
}

/// Where `needle` is in `hay`, the last occurrence (a dictation is mostly pasted at the end).
fn rfind(hay: &[char], needle: &[char]) -> Option<usize> {
    if needle.is_empty() || needle.len() > hay.len() {
        return None;
    }
    (0..=hay.len() - needle.len())
        .rev()
        .find(|&at| hay[at..at + needle.len()] == *needle)
}

/// Where `needle` is in `hay` when whitespace is left out of both (rich text editors turn line
/// breaks into paragraphs, and some report them as nothing at all): the span from the first to
/// the last character matched.
fn rfind_ignoring_whitespace(hay: &[char], needle: &[char]) -> Option<Range<usize>> {
    let (squeezed, at): (Vec<char>, Vec<usize>) = hay
        .iter()
        .enumerate()
        .filter(|(_, c)| !c.is_whitespace())
        .map(|(i, &c)| (c, i))
        .unzip();
    let needle: Vec<char> = needle
        .iter()
        .copied()
        .filter(|c| !c.is_whitespace())
        .collect();
    let start = rfind(&squeezed, &needle)?;
    Some(at[start]..at[start + needle.len() - 1] + 1)
}

/// Follows a text field from right after the paste to the user's last edit.
#[derive(Clone, Debug)]
pub struct Tracker {
    /// The field's text right after the paste.
    before: Vec<char>,
    /// Where the pasted text is in `before`.
    span: Range<usize>,
    /// The field's text now.
    current: Vec<char>,
    /// For each character of `current`: the index in `before` of the character it still is, or
    /// `None` if it was typed since. The indexes only go up.
    origin: Vec<Option<usize>>,
}

/// One edit between `before` and `current`: `old` (indexes into `before`) became `new` (indexes
/// into `current`).
#[derive(Clone, Debug, PartialEq, Eq)]
struct Edit {
    old: Range<usize>,
    new: Range<usize>,
}

impl Tracker {
    /// Starts from the field's text right after the paste. `None` if the pasted text (without its
    /// leading and trailing whitespace) is not in it.
    pub fn new(field: &str, pasted: &str) -> Option<Self> {
        let before = chars_of(field);
        let pasted = chars_of(pasted.trim());
        let span = match rfind(&before, &pasted) {
            Some(at) => at..at + pasted.len(),
            None => rfind_ignoring_whitespace(&before, &pasted)?,
        };
        Some(Self {
            origin: (0..before.len()).map(Some).collect(),
            current: before.clone(),
            before,
            span,
        })
    }

    /// The tracker after the field changed to `text`.
    pub fn advanced(&self, text: &str) -> Self {
        let next = chars_of(text);
        let prefix = self
            .current
            .iter()
            .zip(&next)
            .take_while(|(a, b)| a == b)
            .count();
        let room = self.current.len().min(next.len()) - prefix;
        let suffix = self
            .current
            .iter()
            .rev()
            .zip(next.iter().rev())
            .take(room)
            .take_while(|(a, b)| a == b)
            .count();
        let mut origin = Vec::with_capacity(next.len());
        origin.extend_from_slice(&self.origin[..prefix]);
        origin.resize(next.len() - suffix, None);
        origin.extend_from_slice(&self.origin[self.current.len() - suffix..]);
        Self {
            before: self.before.clone(),
            span: self.span.clone(),
            current: next,
            origin,
        }
    }

    /// The share of the pasted text that is still in the field, unchanged.
    pub fn kept(&self) -> f64 {
        let kept = self
            .origin
            .iter()
            .flatten()
            .filter(|&&i| self.span.contains(&i))
            .count();
        kept as f64 / self.span.len().max(1) as f64
    }

    /// Every stretch where `before` and `current` differ, in order.
    fn edits(&self) -> Vec<Edit> {
        let mut edits = Vec::new();
        // Just past the last character that is still the original one, in both texts.
        let (mut old_at, mut new_at) = (0, 0);
        for (pos, origin) in self.origin.iter().enumerate() {
            let Some(i) = *origin else { continue };
            if i > old_at || pos > new_at {
                edits.push(Edit {
                    old: old_at..i,
                    new: new_at..pos,
                });
            }
            (old_at, new_at) = (i + 1, pos + 1);
        }
        if self.before.len() > old_at || self.current.len() > new_at {
            edits.push(Edit {
                old: old_at..self.before.len(),
                new: new_at..self.current.len(),
            });
        }
        edits
    }

    /// The edits inside the pasted text: those whose original part lies in it (an insertion
    /// counts when it is strictly inside). Any other edit (typing after the dictation, before it,
    /// or across its ends) is not about the dictation.
    fn edits_in_span(&self) -> Vec<Edit> {
        self.edits()
            .into_iter()
            .filter(|edit| {
                if edit.old.is_empty() {
                    self.span.start < edit.old.start && edit.old.start < self.span.end
                } else {
                    self.span.start <= edit.old.start && edit.old.end <= self.span.end
                }
            })
            .collect()
    }

    /// The edits with Latin words made whole: one inside a word ("Tyeless" fixed to "Typeless"
    /// by typing the "p") takes in the rest of the word, and edits with only word characters
    /// between them become one. Never past the pasted text.
    fn whole_words(&self, edits: Vec<Edit>) -> Vec<Edit> {
        let mut merged: Vec<Edit> = Vec::new();
        for edit in edits {
            if let Some(last) = merged.last_mut() {
                let gap = &self.before[last.old.end..edit.old.start];
                if self.touches_word(last)
                    && self.touches_word(&edit)
                    && gap.iter().all(|&c| is_word_char(c))
                {
                    last.old.end = edit.old.end;
                    last.new.end = edit.new.end;
                    continue;
                }
            }
            merged.push(edit);
        }
        for edit in &mut merged {
            if !self.touches_word(edit) {
                continue;
            }
            // Left: the original characters right before the edit, as long as they are word
            // characters (they are the same in both texts).
            while edit.old.start > self.span.start
                && edit.new.start > 0
                && self.origin[edit.new.start - 1] == Some(edit.old.start - 1)
                && is_word_char(self.before[edit.old.start - 1])
            {
                edit.old.start -= 1;
                edit.new.start -= 1;
            }
            while edit.old.end < self.span.end
                && edit.new.end < self.current.len()
                && self.origin[edit.new.end] == Some(edit.old.end)
                && is_word_char(self.before[edit.old.end])
            {
                edit.old.end += 1;
                edit.new.end += 1;
            }
        }
        merged
    }

    /// Whether the edit is about Latin words: it has a word character in it (old or new side),
    /// or it sits between two word characters ("Open Router" joined into "OpenRouter").
    fn touches_word(&self, edit: &Edit) -> bool {
        let inside = self.before[edit.old.clone()]
            .iter()
            .chain(&self.current[edit.new.clone()])
            .any(|&c| is_word_char(c));
        let between = edit.old.start > 0
            && self
                .before
                .get(edit.old.end)
                .is_some_and(|&c| is_word_char(c))
            && is_word_char(self.before[edit.old.start - 1]);
        inside || between
    }

    /// The corrections the user made in the pasted text, at most [`MAX_CORRECTIONS`]. A
    /// correction replaces a short run (up to 12 CJK characters, up to 3 Latin words) with a
    /// different short run that looks like a term: 2 CJK characters or more, or a Latin word of 3
    /// letters or more. Not a correction: a deletion or an insertion alone, a change of letter
    /// case only, a change across a line break, or digits and punctuation alone. None at all when
    /// the edits change more than 30% of the pasted text: that is a rewrite, not a correction.
    pub fn corrections(&self) -> Vec<Correction> {
        let edits = self.edits_in_span();
        // How much of the pasted text was changed: what was replaced or deleted, and for an
        // insertion what was inserted (before words are made whole: "Open Router" joined into
        // one word is one character changed).
        let edited: usize = edits
            .iter()
            .map(|edit| {
                if edit.old.is_empty() {
                    edit.new.len()
                } else {
                    edit.old.len()
                }
            })
            .sum();
        if edited as f64 > MAX_EDITED_SHARE * self.span.len() as f64 {
            return Vec::new();
        }
        self.whole_words(edits)
            .iter()
            .filter_map(|edit| {
                let misheard = trimmed(&self.before[edit.old.clone()]);
                let word = trimmed(&self.current[edit.new.clone()]);
                is_correction(&misheard, &word).then_some(Correction { word, misheard })
            })
            .take(MAX_CORRECTIONS)
            .collect()
    }
}

/// The text without whitespace and punctuation at its ends.
fn trimmed(chars: &[char]) -> String {
    let text: String = chars.iter().collect();
    text.trim_matches(|c: char| c.is_whitespace() || is_punctuation(c))
        .to_owned()
}

fn is_punctuation(c: char) -> bool {
    c.is_ascii_punctuation()
        || matches!(c, '\u{3000}'..='\u{303F}' | '\u{FF01}'..='\u{FF0F}' | '\u{FF1A}'..='\u{FF20}')
        || matches!(c, '\u{2010}'..='\u{205E}' | '\u{FF5B}'..='\u{FF65}')
}

/// Kanji, kana and hangul: scripts written without spaces between words.
pub fn is_cjk(c: char) -> bool {
    matches!(c,
        '\u{3040}'..='\u{30FF}'   // hiragana, katakana
        | '\u{3400}'..='\u{4DBF}' // CJK extension A
        | '\u{4E00}'..='\u{9FFF}' // CJK unified ideographs
        | '\u{AC00}'..='\u{D7AF}' // hangul syllables
        | '\u{F900}'..='\u{FAFF}' // CJK compatibility ideographs
        | '\u{20000}'..='\u{2FFFF}')
}

/// A letter or digit of a script with spaces between words (Latin and the like).
fn is_word_char(c: char) -> bool {
    c.is_alphanumeric() && !is_cjk(c)
}

/// The Latin words of `text`: its runs of word characters.
fn words(text: &str) -> impl Iterator<Item = &str> {
    text.split(|c: char| !is_word_char(c))
        .filter(|word| !word.is_empty())
}

/// Whether `text` is short enough to be one term.
fn is_short(text: &str) -> bool {
    let cjk = text.chars().filter(|&c| is_cjk(c)).count();
    cjk <= MAX_CJK && words(text).count() <= MAX_WORDS && text.chars().count() <= MAX_CHARS
}

/// Whether `text` looks like a term: 2 CJK characters or more, or a word of 3 letters or more.
fn is_term(text: &str) -> bool {
    text.chars().filter(|&c| is_cjk(c)).count() >= 2
        || words(text).any(|word| word.chars().filter(|c| c.is_alphabetic()).count() >= 3)
}

/// Whether replacing `misheard` with `word` is a correction worth learning.
fn is_correction(misheard: &str, word: &str) -> bool {
    !misheard.is_empty()
        && !word.is_empty()
        && !misheard.contains('\n')
        && !word.contains('\n')
        && misheard.to_lowercase() != word.to_lowercase()
        && is_short(misheard)
        && is_short(word)
        && is_term(word)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The corrections from the field right after the paste, through each later snapshot.
    fn learned(pasted: &str, snapshots: &[&str]) -> Vec<(String, String)> {
        let mut tracker = Tracker::new(snapshots[0], pasted).expect("the pasted text is found");
        for text in &snapshots[1..] {
            tracker = tracker.advanced(text);
        }
        tracker
            .corrections()
            .into_iter()
            .map(|c| (c.misheard, c.word))
            .collect()
    }

    /// The corrections when `pasted` is edited with each `(from, to)` in turn, one snapshot each.
    fn learned_after(pasted: &str, replacements: &[(&str, &str)]) -> Vec<(String, String)> {
        let mut snapshots = vec![pasted.to_owned()];
        for (from, to) in replacements {
            let last = snapshots.last().unwrap();
            assert!(last.contains(from), "{from} is in {last}");
            snapshots.push(last.replacen(from, to, 1));
        }
        let snapshots: Vec<&str> = snapshots.iter().map(String::as_str).collect();
        learned(pasted, &snapshots)
    }

    fn pairs(list: &[(&str, &str)]) -> Vec<(String, String)> {
        list.iter()
            .map(|(misheard, word)| ((*misheard).to_owned(), (*word).to_owned()))
            .collect()
    }

    const NONE: Vec<(String, String)> = Vec::new();

    const PASTED: &str = "我昨天買了一支新的首機，拍照很漂亮，電池也很耐用，整體來說我很滿意";

    // ---------- finding the pasted text ----------

    #[test]
    fn the_pasted_text_is_found_after_what_was_there() {
        let tracker = Tracker::new(&format!("哈囉\r\n{PASTED}"), PASTED).unwrap();
        assert_eq!(tracker.span, 3..3 + PASTED.chars().count());
    }

    #[test]
    fn line_breaks_match_whatever_the_field_uses() {
        let pasted = "第一行\r\n第二行";
        assert!(Tracker::new("第一行\n第二行", pasted).is_some());
        assert!(Tracker::new("第一行\r第二行", pasted).is_some());
    }

    #[test]
    fn whitespace_the_editor_dropped_does_not_hide_the_pasted_text() {
        let tracker = Tracker::new("前面 第一行第二行", "第一行\n第二行").unwrap();
        assert_eq!(tracker.span, 3..9);
    }

    #[test]
    fn the_last_occurrence_is_taken() {
        let tracker = Tracker::new("好的。好的。", "好的。").unwrap();
        assert_eq!(tracker.span, 3..6);
    }

    #[test]
    fn a_field_without_the_pasted_text_is_not_tracked() {
        assert!(Tracker::new("別的東西", PASTED).is_none());
        assert!(Tracker::new("", PASTED).is_none());
        assert!(Tracker::new("什麼", "   ").is_none());
    }

    // ---------- CJK ----------

    #[test]
    fn a_word_selected_and_retyped_is_learned_whole() {
        // select 首機 and delete it, type 手機 (the IME's composition shows in between)
        assert_eq!(
            learned_after(
                PASTED,
                &[("首機", ""), ("新的，", "新的shou，"), ("shou", "手機")]
            ),
            pairs(&[("首機", "手機")])
        );
    }

    #[test]
    fn a_word_typed_over_in_one_go() {
        // straight from the wrong word to the right one: only 首 differs, too short for a term
        assert_eq!(learned_after(PASTED, &[("首機", "手機")]), NONE);
        assert_eq!(
            learned_after(PASTED, &[("首機", "手提電話")]),
            pairs(&[("首機", "手提電話")])
        );
    }

    #[test]
    fn no_edit_learns_nothing() {
        assert_eq!(learned(PASTED, &[PASTED, PASTED]), NONE);
    }

    #[test]
    fn a_deletion_alone_is_no_correction() {
        assert_eq!(learned_after(PASTED, &[("新的首機", "新的")]), NONE);
    }

    #[test]
    fn an_insertion_alone_is_no_correction() {
        assert_eq!(learned_after(PASTED, &[("拍照很", "拍照真的很")]), NONE);
    }

    #[test]
    fn a_long_cjk_run_is_no_term() {
        assert_eq!(
            learned_after(
                PASTED,
                &[("電池也很耐用", "電池也是非常非常非常非常非常非常的耐用")]
            ),
            NONE
        );
    }

    #[test]
    fn punctuation_around_the_word_is_left_out() {
        assert_eq!(
            learned_after(PASTED, &[("首機", "「手提電話」")]),
            pairs(&[("首機", "手提電話")])
        );
    }

    #[test]
    fn digits_or_punctuation_alone_are_not_learned() {
        let pasted = "會議改到下午三點，大家準時到場喔，記得帶筆電和充電器過來";
        assert_eq!(learned_after(pasted, &[("三點", "15:00")]), NONE);
        assert_eq!(learned_after(pasted, &[("三點，", "三點！！")]), NONE);
    }

    // ---------- Latin ----------

    const EN: &str = "I tried type less yesterday and it works really well for long emails";

    #[test]
    fn latin_words_replaced_by_a_term_are_learned() {
        assert_eq!(
            learned_after(EN, &[("type less", "Typeless")]),
            pairs(&[("type less", "Typeless")])
        );
    }

    #[test]
    fn a_word_fixed_from_inside_is_learned_whole() {
        let pasted = "We moved the servers to tailscail last week and it just works";
        assert_eq!(
            learned_after(pasted, &[("tailscail", "Tailscale")]),
            pairs(&[("tailscail", "Tailscale")])
        );
        // one letter typed into the word
        let pasted = "I tried Tyeless yesterday and it works really well for long emails";
        assert_eq!(
            learned_after(pasted, &[("Tyeless", "Typeless")]),
            pairs(&[("Tyeless", "Typeless")])
        );
    }

    #[test]
    fn a_change_of_letter_case_only_is_not_learned() {
        assert_eq!(learned_after(EN, &[("type less", "Type Less")]), NONE);
    }

    #[test]
    fn short_words_are_not_terms() {
        assert_eq!(learned_after(EN, &[("it works", "it is")]), NONE);
    }

    #[test]
    fn more_than_three_words_are_no_term() {
        let pasted = "Please send the report to the whole team before the meeting on Friday morning ok thanks";
        assert_eq!(
            learned_after(pasted, &[("the whole team", "every single person here")]),
            NONE
        );
    }

    // ---------- mixed ----------

    #[test]
    fn a_cjk_run_replaced_by_a_latin_term_is_learned() {
        let pasted = "我們用泰斯克連回家裡的電腦，速度還不錯，設定也很簡單";
        assert_eq!(
            learned_after(pasted, &[("泰斯克", "Tailscale")]),
            pairs(&[("泰斯克", "Tailscale")])
        );
    }

    #[test]
    fn latin_words_joined_in_chinese_text_are_learned_whole() {
        let pasted = "我今天試了 Open Router 的新模型，感覺回答比較快一點點";
        assert_eq!(
            learned_after(pasted, &[("Open Router", "OpenRouter")]),
            pairs(&[("Open Router", "OpenRouter")])
        );
    }

    #[test]
    fn katakana_counts_as_cjk() {
        let pasted = "明日はラファエロ先生に会いに行きますので、よろしくお願いします";
        assert_eq!(
            learned_after(
                pasted,
                &[("ラファエロ", ""), ("は先生", "はラファエル先生")]
            ),
            pairs(&[("ラファエロ", "ラファエル")])
        );
    }

    // ---------- the whole dictation ----------

    #[test]
    fn a_rewrite_of_most_of_the_text_learns_nothing() {
        let pasted = "明天下午三點開會，記得帶筆電";
        assert_eq!(learned_after(pasted, &[("明天下午三", "後天早上十")]), NONE);
    }

    #[test]
    fn edits_outside_the_pasted_text_do_not_count() {
        let before = format!("舊的首機。{PASTED}");
        let fixed = format!("舊的手提電話。{PASTED}");
        let added = format!("{fixed}你覺得呢");
        assert_eq!(learned(PASTED, &[&before, &fixed, &added]), NONE);
    }

    #[test]
    fn at_most_three_corrections() {
        let pasted = "甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥天干地支，這是一段很長的文字用來測試最多三個修正的規則是不是有效";
        assert_eq!(
            learned_after(
                pasted,
                &[
                    ("甲乙", "一二"),
                    ("戊己", "三四"),
                    ("子丑", "五六"),
                    ("辰巳", "七八")
                ]
            ),
            pairs(&[("甲乙", "一二"), ("戊己", "三四"), ("子丑", "五六")])
        );
    }

    #[test]
    fn two_corrections_in_one_dictation() {
        let pasted =
            "我用泰斯克連回家，再用歐噴肉特叫模型，整體來說還算順利，大概花了十分鐘左右就弄好了";
        assert_eq!(
            learned_after(
                pasted,
                &[("泰斯克", "Tailscale"), ("歐噴肉特", "OpenRouter")]
            ),
            pairs(&[("泰斯克", "Tailscale"), ("歐噴肉特", "OpenRouter")])
        );
    }

    #[test]
    fn a_change_across_a_line_break_is_not_learned() {
        let pasted = "第一點是首機\n第二點是電腦，還有其他的東西要說明一下";
        assert_eq!(learned_after(pasted, &[("首機\n第二點是", "手機")]), NONE);
    }

    // ---------- the field emptied ----------

    #[test]
    fn kept_drops_when_the_field_is_cleared() {
        let tracker = Tracker::new(PASTED, PASTED).unwrap();
        assert_eq!(tracker.kept(), 1.0);
        assert_eq!(tracker.advanced("").kept(), 0.0);
        let fixed = PASTED.replacen("首機", "手機", 1);
        assert!(tracker.advanced(&fixed).kept() > MIN_KEPT_SHARE);
    }
}
