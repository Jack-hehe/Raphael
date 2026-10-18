//! What the user corrected in a pasted dictation, with pure rules over character origins.
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
        // The OS adapter is optional. All alignment and acceptance rules below also work with
        // injected scalar ranges; a failed provider preserves the original conservative rules.
        let needs_words = self
            .edits_in_span()
            .iter()
            .any(|edit| self.single_han_replacement(edit));
        let ranges = needs_words.then(|| {
            let before: String = self.before.iter().collect();
            let current: String = self.current.iter().collect();
            Some((
                super::cjk_words::word_ranges(&before)?,
                super::cjk_words::word_ranges(&current)?,
            ))
        });
        match ranges.flatten() {
            Some((before, current)) => self.corrections_with_word_ranges(&before, &current),
            None => self.corrections_with_word_ranges(&[], &[]),
        }
    }

    /// Pure correction rules with optional, nonoverlapping word ranges in scalar indexes.
    pub(crate) fn corrections_with_word_ranges(
        &self,
        before_words: &[Range<usize>],
        current_words: &[Range<usize>],
    ) -> Vec<Correction> {
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
            .into_iter()
            .filter_map(|edit| {
                let edit = if self.single_han_replacement(&edit) {
                    self.whole_han_word(edit, before_words, current_words)?
                } else {
                    edit
                };
                let misheard = trimmed(&self.before[edit.old.clone()]);
                let word = trimmed(&self.current[edit.new.clone()]);
                is_correction(&misheard, &word).then_some(Correction { word, misheard })
            })
            .take(MAX_CORRECTIONS)
            .collect()
    }

    fn single_han_replacement(&self, edit: &Edit) -> bool {
        edit.old.len() == 1
            && edit.new.len() == 1
            && is_han(self.before[edit.old.start])
            && is_han(self.current[edit.new.start])
    }

    fn whole_han_word(
        &self,
        mut edit: Edit,
        before_words: &[Range<usize>],
        current_words: &[Range<usize>],
    ) -> Option<Edit> {
        if !valid_word_ranges(before_words, self.before.len())
            || !valid_word_ranges(current_words, self.current.len())
        {
            return None;
        }
        let word = current_words
            .iter()
            .find(|word| word.start <= edit.new.start && edit.new.end <= word.end)?;
        if !(2..=MAX_CJK).contains(&word.len())
            || !self.current[word.clone()].iter().copied().all(is_han)
            || is_common_grammar_word(&self.current[word.clone()])
        {
            return None;
        }
        while edit.new.start > word.start {
            if edit.old.start <= self.span.start
                || self.origin[edit.new.start - 1] != Some(edit.old.start - 1)
            {
                return None;
            }
            edit.old.start -= 1;
            edit.new.start -= 1;
        }
        while edit.new.end < word.end {
            if edit.old.end >= self.span.end || self.origin[edit.new.end] != Some(edit.old.end) {
                return None;
            }
            edit.old.end += 1;
            edit.new.end += 1;
        }
        // Both original edges must be real word boundaries. If the original was already one
        // complete multi-character word, this could be a meaning/style edit, so do not alias it.
        let starts_word = before_words.iter().any(|word| word.start == edit.old.start);
        let ends_word = before_words.iter().any(|word| word.end == edit.old.end);
        let was_word = before_words
            .iter()
            .any(|word| word.start <= edit.old.start && edit.old.end <= word.end && word.len() > 1);
        (starts_word && ends_word && !was_word).then_some(edit)
    }
}

fn valid_word_ranges(words: &[Range<usize>], text_len: usize) -> bool {
    !words.is_empty()
        && words
            .iter()
            .all(|word| word.start < word.end && word.end <= text_len)
        && words.windows(2).all(|pair| pair[0].end <= pair[1].start)
}

/// These common grammar/stance/time words are conservative exclusions, not a POS or semantic
/// classifier. Word boundaries alone cannot prove that an edit is a misheard name or noun.
fn is_common_grammar_word(chars: &[char]) -> bool {
    matches!(
        chars.iter().collect::<String>().as_str(),
        "已經"
            | "已经"
            | "已是"
            | "一直"
            | "一起"
            | "一些"
            | "一樣"
            | "一样"
            | "因為"
            | "因为"
            | "所以"
            | "但是"
            | "可是"
            | "如果"
            | "雖然"
            | "虽然"
            | "就是"
            | "只是"
            | "還是"
            | "还是"
            | "不是"
            | "不要"
            | "不能"
            | "不會"
            | "不会"
            | "不再"
            | "沒有"
            | "没有"
            | "可以"
            | "可能"
            | "能夠"
            | "能够"
            | "應該"
            | "应该"
            | "需要"
            | "自己"
            | "我們"
            | "我们"
            | "你們"
            | "你们"
            | "他們"
            | "他们"
            | "她們"
            | "她们"
            | "這個"
            | "这个"
            | "那個"
            | "那个"
            | "這些"
            | "这些"
            | "那些"
            | "什麼"
            | "什么"
            | "怎麼"
            | "怎么"
            | "為什麼"
            | "为什么"
            | "如何"
            | "感覺"
            | "感觉"
            | "感到"
            | "覺得"
            | "觉得"
            | "記得"
            | "记得"
            | "今年"
            | "明年"
            | "今天"
            | "明天"
            | "昨天"
    )
}

fn is_han(character: char) -> bool {
    matches!(character,
        '\u{3400}'..='\u{4DBF}'
        | '\u{4E00}'..='\u{9FFF}'
        | '\u{F900}'..='\u{FAFF}'
        | '\u{20000}'..='\u{2FFFF}')
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
            .corrections_with_word_ranges(&[], &[])
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

    #[cfg(windows)]
    #[test]
    #[ignore = "needs the Windows zh-TW word breaker"]
    fn windows_word_boundaries_learn_phone_and_name() {
        let learned = |pasted: &str, from: &str, to: &str| {
            Tracker::new(pasted, pasted)
                .unwrap()
                .advanced(&pasted.replacen(from, to, 1))
                .corrections()
                .into_iter()
                .map(|correction| (correction.misheard, correction.word))
                .collect::<Vec<_>>()
        };
        assert_eq!(learned(PASTED, "首機", "手機"), pairs(&[("首機", "手機")]));
        let pasted = "我今天使用拉菲爾整理會議紀錄，整體操作很流暢，明天也會繼續使用";
        assert_eq!(
            learned(pasted, "拉菲爾", "拉斐爾"),
            pairs(&[("拉菲爾", "拉斐爾")])
        );
        let pasted = "😀今天帶著首機去開會\n下午再一起確認新的工作安排";
        assert_eq!(learned(pasted, "首機", "手機"), pairs(&[("首機", "手機")]));
        for (from, to) in [
            ("房子", "孩子"),
            ("台北", "臺北"),
            ("可能", "可以"),
            ("感覺", "感到"),
            ("以經", "已經"),
            ("可已", "可以"),
            ("敢覺", "感覺"),
            ("阿咪咕", "阿米咕"),
        ] {
            let pasted = format!("今天討論{from}的相關內容，下午還會繼續確認其他細節");
            assert!(
                learned(&pasted, from, to).is_empty(),
                "{from} must not alias {to}"
            );
        }
        for (field, pasted) in [
            (
                "手雞已放在桌上，下午再拿去開會",
                "雞已放在桌上，下午再拿去開會",
            ),
            ("今天開會之前請先帶著首機", "今天開會之前請先帶著首"),
        ] {
            let current = field.replace("手雞", "手機").replace("首機", "手機");
            assert!(
                Tracker::new(field, pasted)
                    .unwrap()
                    .advanced(&current)
                    .corrections()
                    .is_empty()
            );
        }
    }

    fn word_range(text: &str, word: &str) -> Range<usize> {
        let byte = text.find(word).expect("fixture word is present");
        let start = text[..byte].chars().count();
        start..start + word.chars().count()
    }

    fn with_han_word(pasted: &str, from: &str, to: &str) -> Vec<Correction> {
        let current = pasted.replacen(from, to, 1);
        let old = word_range(pasted, from);
        let fragments: Vec<_> = old.clone().map(|index| index..index + 1).collect();
        Tracker::new(pasted, pasted)
            .unwrap()
            .advanced(&current)
            .corrections_with_word_ranges(&fragments, &[word_range(&current, to)])
    }

    #[test]
    fn one_han_edit_uses_the_complete_injected_word() {
        assert_eq!(
            with_han_word(PASTED, "首機", "手機"),
            vec![Correction {
                word: "手機".into(),
                misheard: "首機".into()
            }]
        );
        let pasted = "今天使用拉菲爾整理文件，明天也會繼續使用這個工具";
        assert_eq!(
            with_han_word(pasted, "拉菲爾", "拉斐爾"),
            vec![Correction {
                word: "拉斐爾".into(),
                misheard: "拉菲爾".into()
            }]
        );
    }

    #[test]
    fn scalar_word_ranges_remain_correct_after_emoji_and_newlines() {
        let pasted = "😀今天帶著首機去開會\n下午再一起確認新的工作安排";
        assert_eq!(
            with_han_word(pasted, "首機", "手機"),
            vec![Correction {
                word: "手機".into(),
                misheard: "首機".into()
            }]
        );
    }

    #[test]
    fn a_word_that_was_already_complete_is_not_aliased() {
        for (from, to) in [
            ("房子", "孩子"),
            ("台北", "臺北"),
            ("可能", "可以"),
            ("感覺", "感到"),
        ] {
            let pasted = format!("今天討論{from}的相關內容，下午還會繼續確認其他細節");
            let current = pasted.replacen(from, to, 1);
            let tracker = Tracker::new(&pasted, &pasted).unwrap().advanced(&current);
            assert!(
                tracker
                    .corrections_with_word_ranges(
                        &[word_range(&pasted, from)],
                        &[word_range(&current, to)]
                    )
                    .is_empty(),
                "{from} must not become an alias of {to}"
            );
        }
    }

    #[test]
    fn grammar_words_are_excluded_even_when_the_old_spelling_was_fragmented() {
        for (from, to) in [
            ("以經", "已經"),
            ("可已", "可以"),
            ("可熊", "可能"),
            ("敢覺", "感覺"),
            ("感倒", "感到"),
            ("今夭", "今天"),
        ] {
            let pasted = format!("今天討論{from}的相關內容，下午還會繼續確認其他細節");
            assert!(
                with_han_word(&pasted, from, to).is_empty(),
                "{to} is a grammar exclusion"
            );
        }
    }

    #[test]
    fn word_expansion_stops_at_both_pasted_edges() {
        let before = "手雞已放在桌上，下午再拿去開會";
        let current = before.replace("手雞", "手機");
        let tracker = Tracker::new(before, "雞已放在桌上，下午再拿去開會")
            .unwrap()
            .advanced(&current);
        assert!(
            tracker
                .corrections_with_word_ranges(&[0..1, 1..2], std::slice::from_ref(&(0..2)))
                .is_empty()
        );

        let before = "今天開會之前請先帶著首機";
        let current = before.replace("首機", "手機");
        let tracker = Tracker::new(before, "今天開會之前請先帶著首")
            .unwrap()
            .advanced(&current);
        let old = word_range(before, "首機");
        let fragments: Vec<_> = old.map(|index| index..index + 1).collect();
        assert!(
            tracker
                .corrections_with_word_ranges(&fragments, &[word_range(&current, "手機")])
                .is_empty()
        );
    }

    #[test]
    fn only_unchanged_origin_characters_can_complete_a_han_word() {
        let pasted = "今天使用拉菲小耳整理文件，明天也會繼續使用這個工具";
        let first = pasted.replace('菲', "斐");
        let current = first.replace('耳', "爾");
        let tracker = Tracker::new(pasted, pasted)
            .unwrap()
            .advanced(&first)
            .advanced(&current);
        let fragments: Vec<_> = word_range(pasted, "拉菲小耳")
            .map(|index| index..index + 1)
            .collect();
        assert!(
            tracker
                .corrections_with_word_ranges(&fragments, &[word_range(&current, "拉斐小爾")])
                .is_empty()
        );
    }

    #[test]
    fn missing_invalid_or_overlapping_ranges_fail_closed() {
        let current = PASTED.replace("首機", "手機");
        let tracker = Tracker::new(PASTED, PASTED).unwrap().advanced(&current);
        for (before, after) in [
            (vec![], std::slice::from_ref(&(9..11)).to_vec()),
            (vec![9..10, 10..11], vec![]),
            (
                vec![9..10, 10..11],
                std::slice::from_ref(&(9..1000)).to_vec(),
            ),
            (vec![9..10, 10..11], vec![9..10, 9..11]),
            (
                vec![9..10, 10..1000],
                std::slice::from_ref(&(9..11)).to_vec(),
            ),
            (vec![9..11, 10..11], std::slice::from_ref(&(9..11)).to_vec()),
        ] {
            assert!(
                tracker
                    .corrections_with_word_ranges(&before, &after)
                    .is_empty()
            );
        }
    }

    #[test]
    fn partial_original_word_edges_are_not_expanded() {
        let current = PASTED.replace("首機", "手機");
        let tracker = Tracker::new(PASTED, PASTED).unwrap().advanced(&current);
        assert!(
            tracker
                .corrections_with_word_ranges(&[8..10, 10..11], std::slice::from_ref(&(9..11)))
                .is_empty()
        );
        assert!(
            tracker
                .corrections_with_word_ranges(&[9..10, 10..12], std::slice::from_ref(&(9..11)))
                .is_empty()
        );
    }

    #[test]
    fn han_expansion_does_not_override_the_rewrite_or_script_guards() {
        assert!(with_han_word("首機", "首機", "手機").is_empty());
        let mixed = "今天帶著首a機去開會，下午再一起確認新的工作安排";
        assert!(with_han_word(mixed, "首a機", "手a機").is_empty());
        let kana = "今天帶著カ機去開會，下午再一起確認新的工作安排";
        assert!(with_han_word(kana, "カ機", "手機").is_empty());
        let long = "今天使用首一二三四五六七八九十甲乙處理資料，明天也會繼續確認其他細節";
        assert!(
            with_han_word(
                long,
                "首一二三四五六七八九十甲乙",
                "手一二三四五六七八九十甲乙"
            )
            .is_empty()
        );
    }

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
