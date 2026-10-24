//! Follows a pasted dictation in the text a terminal shows.
//!
//! A terminal's UI Automation text is the screen and the history above it: prompts, old
//! commands, program output and, for a full-screen program (an editor or an interactive CLI), a frame
//! that is redrawn around the input with a status line below it. The pasted text can therefore
//! be anywhere in it, not only in its last row.
//!
//! A *place* is the run of rows that shows the pasted words. It is found by searching the whole
//! text for them (blanks and the box-drawing frame some programs draw around their input do not
//! count), found again on every read near where it was, and judged by [`Tracker`] alone: only
//! changes inside the pasted words can become corrections, whatever else the program redraws.
//! The watch ends when the place cannot be found any more or has been cleared. A keyboard
//! observer should call [`TerminalTracker::submitted`] before reading a post-Enter screen: screen
//! text alone cannot tell every program output from an edit.
//!
//! The pasted words may be on screen more than once (the same text sent earlier, an echo of the
//! input). Every copy is followed until exactly one of them is edited: that one is the input. Two
//! edited in one read cannot be told apart, and the watch ends without learning anything.

use std::ops::Range;

use super::corrections::{Correction, MIN_KEPT_SHARE, Tracker, is_cjk};

mod panes;

/// Bound both initial matching and later relocation, including when a provider returns history.
const MAX_TAIL_CHARS: usize = 20_000;
/// The most rows the pasted words may take up.
const MAX_REGION_ROWS: usize = 16;
/// How far, in rows, a place may move between two reads: a scroll, a redraw, a frame that grew.
const MAX_SHIFT: usize = 48;
/// The most copies followed while it is unclear which one is the input.
const MAX_PLACES: usize = 4;
/// How much of its text from the last read the rows of a place must still show to be that place.
const SAME_PLACE: f64 = 0.7;
/// Rows this much like the place, where it was, are taken to be it without looking further. A
/// weaker likeness may be another row that scrolled onto the old place, so a clearly better match
/// nearby wins over it.
const UNMOVED: f64 = 0.85;
/// How much better a match elsewhere must be to win over a weak one where the place was.
const CLEARLY_BETTER: f64 = 0.1;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TerminalDecision {
    /// The pasted rows are still being followed.
    Continue,
    /// Submission, or a cleared input line, ended the command's editable lifetime.
    Sent,
    /// The pasted rows cannot be found again conservatively.
    Lost,
}

/// The rows that show the pasted words.
#[derive(Clone, Debug)]
struct Place {
    tracker: Tracker,
    /// The rows, tidied (see [`tidy_row`]).
    rows: Vec<String>,
    /// `rows` joined (see [`join_rows`]): the text `tracker` follows.
    text: String,
    /// The index of `rows[0]` on the latest screen.
    start: usize,
    /// What stands before the pasted words in the first row (`PS C:\work>`, `>`). When they are
    /// gone, it still marks the input line.
    anchor: String,
}

#[derive(Clone, Debug)]
pub struct TerminalTracker {
    /// One unambiguous column of the original layout. A resize can move its borders; a pane
    /// appearing/disappearing cannot prove identity and ends the watch rather than switching panes.
    pane: usize,
    pane_count: usize,
    width: usize,
    /// The pasted words without blanks, for finding copies that appear later.
    needle: Vec<char>,
    /// The copies being followed, top to bottom; one once it is clear which is the input.
    places: Vec<Place>,
    /// Whether the input was told from the other copies (by being the one that was edited).
    resolved: bool,
    decision: TerminalDecision,
}

impl TerminalTracker {
    /// Finds the pasted words anywhere in `buffer`, ignoring blank rows, CRLF differences, blanks
    /// and frame characters. They may take up several rows, including physical wraps. If they
    /// are on screen more than once, the copies are followed until one of them is edited.
    pub fn new(buffer: &str, pasted: &str) -> Option<Self> {
        let columns = screen_columns(buffer);
        let needle = needle_of(pasted);
        let mut matches = columns.iter().enumerate().filter_map(|(pane, lines)| {
            let places: Vec<Place> = find_places(lines, &needle)
                .iter()
                .filter_map(|found| Place::new(lines, found))
                .collect();
            (!places.is_empty()).then_some((pane, places))
        });
        let (pane, mut places) = matches.next()?;
        // Identical text in different panes cannot identify the paste's input field.
        if matches.next().is_some() {
            return None;
        }
        keep_lowest(&mut places);
        if places.is_empty() {
            return None;
        }
        Some(Self {
            pane,
            pane_count: columns.len(),
            width: panes::width(&columns[pane]),
            needle,
            places,
            resolved: false,
            decision: TerminalDecision::Continue,
        })
    }

    /// Follows the pasted rows to where the new screen has them. End decisions are sticky and
    /// keep the last valid snapshot. A row that grew or shrank by a wrap is followed; rows that
    /// the program adds above or below are not part of the region. The UIA caller may supply a
    /// caret-bounded active buffer instead of the whole text.
    pub fn observe(&mut self, buffer: &str) -> TerminalDecision {
        if self.decision != TerminalDecision::Continue {
            return self.decision;
        }
        let columns = screen_columns(buffer);
        if columns.len() != self.pane_count {
            return self.end(TerminalDecision::Lost);
        }
        let Some(lines) = columns.get(self.pane) else {
            return self.end(TerminalDecision::Lost);
        };
        let width = panes::width(lines);
        if lines.is_empty() {
            return self.end(TerminalDecision::Sent);
        }

        let mut moved = Vec::new();
        let mut cleared = false;
        for place in &self.places {
            match relocate(place, lines, width != self.width) {
                Relocation::Moved(next) => {
                    // Only the pasted words count: a marker, a prompt or a status on the same
                    // rows changes without the user having edited anything.
                    let edited = next.tracker.edited();
                    moved.push((next, edited));
                }
                Relocation::Cleared => cleared = true,
                Relocation::Gone => {}
            }
        }
        if moved.is_empty() {
            return self.end(if cleared {
                TerminalDecision::Sent
            } else {
                TerminalDecision::Lost
            });
        }
        if overlap(moved.iter().map(|(place, _)| place)) {
            return self.end(TerminalDecision::Lost);
        }
        if !self.resolved {
            match moved.iter().filter(|(_, edited)| *edited).count() {
                0 => {}
                1 => {
                    moved.retain(|(_, edited)| *edited);
                    self.resolved = true;
                }
                _ => return self.end(TerminalDecision::Lost),
            }
        }

        let mut places: Vec<Place> = moved.into_iter().map(|(place, _)| place).collect();
        if !self.resolved {
            // The paste may have been drawn only now, below an older copy.
            for found in find_places(lines, &self.needle) {
                if places.iter().all(|place| !place.rows_overlap(&found.rows))
                    && let Some(place) = Place::new(lines, &found)
                {
                    places.push(place);
                }
            }
            places.sort_by_key(|place| place.start);
            keep_lowest(&mut places);
        }
        self.places = places;
        self.width = width;
        TerminalDecision::Continue
    }

    /// Freezes the last snapshot before Enter can turn it into history or program output.
    pub fn submitted(&mut self) -> TerminalDecision {
        if self.decision == TerminalDecision::Continue {
            self.end(TerminalDecision::Sent)
        } else {
            self.decision
        }
    }

    /// The rows of the lowest copy, which is the input once that is known.
    pub fn region(&self) -> &str {
        self.places.last().map_or("", |place| place.text.as_str())
    }

    /// What was corrected in the input. Nothing while several copies are still unedited.
    pub fn corrections(&self) -> Vec<Correction> {
        match self.places.as_slice() {
            [only] => only.tracker.corrections(),
            _ => Vec::new(),
        }
    }

    fn end(&mut self, decision: TerminalDecision) -> TerminalDecision {
        self.decision = decision;
        decision
    }
}

impl Place {
    fn new(lines: &[String], found: &Found) -> Option<Self> {
        let rows = lines.get(found.rows.clone())?.to_vec();
        let text = join_rows(&rows);
        let anchor = rows
            .first()?
            .chars()
            .take(found.span.start)
            .collect::<String>()
            .trim()
            .to_owned();
        Some(Self {
            tracker: Tracker::with_span(&text, found.span.clone())?,
            start: found.rows.start,
            rows,
            text,
            anchor,
        })
    }

    fn rows_overlap(&self, rows: &Range<usize>) -> bool {
        self.start < rows.end && rows.start < self.start + self.rows.len()
    }
}

/// Keeps the lowest copies of a list ordered from the top.
fn keep_lowest(places: &mut Vec<Place>) {
    let excess = places.len().saturating_sub(MAX_PLACES);
    places.drain(..excess);
}

/// Whether two of the places are on the same rows.
fn overlap<'a>(places: impl Iterator<Item = &'a Place>) -> bool {
    let places: Vec<&Place> = places.collect();
    places.iter().enumerate().any(|(at, place)| {
        places
            .iter()
            .skip(at + 1)
            .any(|other| place.rows_overlap(&(other.start..other.start + other.rows.len())))
    })
}

// ---------- the screen ----------

/// Whether a break between two rows stands for a blank. Programs wrap at blanks and `tidy_row`
/// trimmed the blank that stood at the end of the row, so it almost always does; between two
/// characters of a script written without blanks the row was simply cut, and it stands for
/// nothing.
fn break_is_a_blank(above: &str, below: &str) -> bool {
    let spaceless =
        |c: char| is_cjk(c) || matches!(c, '\u{3000}'..='\u{303F}' | '\u{FF00}'..='\u{FFEF}');
    !(above.chars().next_back().is_some_and(spaceless)
        && below.chars().next().is_some_and(spaceless))
}

/// The text of some rows. A wrap that moves a word to the next row must not look like an edit to
/// [`Tracker`], which compares text with the text before: with line feeds every later wrap would
/// differ, and one letter typed could rewrite the whole paragraph.
fn join_rows(rows: &[String]) -> String {
    let mut text = String::new();
    for (at, row) in rows.iter().enumerate() {
        let row = if at == 0 {
            row.as_str()
        } else {
            without_prompt(row)
        };
        if at > 0 && break_is_a_blank(without_prompt(&rows[at - 1]), row) {
            text.push(' ');
        }
        text.push_str(row);
    }
    text
}

fn normalize(text: &str) -> String {
    text.replace("\r\n", "\n").replace('\r', "\n")
}

/// The box-drawing and block characters some programs draw their input frame (and a fake cursor)
/// with. They are not text.
fn is_frame(c: char) -> bool {
    matches!(c, '\u{2500}'..='\u{259F}')
}

/// A screen row as the tracker sees it: without frame characters and without the blanks that pad
/// the row out to its border. Typing then moves only the words, not the padding after them.
fn tidy_row(line: &str) -> String {
    let bare: String = line.chars().filter(|&c| !is_frame(c)).collect();
    bare.trim().to_owned()
}

fn without_prompt(row: &str) -> &str {
    // TUI prompts may be repeated on continuation rows; they are decoration, not pasted text.
    let row = row
        .strip_prefix(['>', '›', '❯'])
        .filter(|rest| rest.is_empty() || rest.starts_with(char::is_whitespace))
        .unwrap_or(row);
    row.trim()
}

/// The last [`MAX_TAIL_CHARS`] characters of the text, from the start of a row.
fn bounded_tail(buffer: &str) -> &str {
    // Never begin matching in a partial history row cut by our own tail bound.
    match buffer.char_indices().rev().nth(MAX_TAIL_CHARS - 1) {
        Some((at, _)) if at > 0 => {
            let Some(tail) = buffer.get(at..) else {
                return "";
            };
            tail.find(['\r', '\n'])
                .and_then(|end| tail.get(end..))
                .unwrap_or("")
        }
        _ => buffer,
    }
}

fn screen_columns(buffer: &str) -> Vec<Vec<String>> {
    let text = normalize(bounded_tail(buffer));
    panes::columns(&text)
        .into_iter()
        .map(|rows| {
            let mut lines: Vec<String> = rows.iter().map(|row| tidy_row(row)).collect();
            while lines.last().is_some_and(String::is_empty) {
                lines.pop();
            }
            lines
        })
        .collect()
}

/// A collapsed paste exposes no message text. Count only the known numeric placeholder syntax;
/// never treat it, nearby output, or a later post-Enter echo as the pasted region.
pub(super) fn collapsed_pastes(buffer: &str) -> usize {
    buffer
        .match_indices("[Pasted text #")
        .filter(|(at, prefix)| {
            let rest = &buffer[at + prefix.len()..];
            let Some((body, _)) = rest.split_once(']') else {
                return false;
            };
            let (id, lines) = body
                .split_once(" +")
                .map_or((body, None), |(id, lines)| (id, Some(lines)));
            let number =
                |value: &str| !value.is_empty() && value.bytes().all(|c| c.is_ascii_digit());
            number(id)
                && lines.is_none_or(|lines| {
                    lines
                        .strip_suffix(" lines")
                        .or_else(|| lines.strip_suffix(" line"))
                        .is_some_and(number)
                })
        })
        .count()
}

/// The pasted words without blanks and frame characters, which a screen does not keep as pasted.
fn needle_of(pasted: &str) -> Vec<char> {
    normalize(pasted)
        .chars()
        .filter(|&c| !c.is_whitespace() && !is_frame(c))
        .collect()
}

/// Where the pasted words are on the screen.
struct Found {
    /// The rows that hold them, first to last.
    rows: Range<usize>,
    /// From the first word to the last, in characters of the text of `rows`.
    span: Range<usize>,
}

/// Every place the pasted words are on the screen, top to bottom. Blanks (a physical wrap, the
/// padding of a frame) do not matter. The spans are in the text [`join_rows`] makes. Copies that share rows are one place, and the later one is
/// kept, as the last occurrence is the one a paste lands in.
fn find_places(lines: &[String], needle: &[char]) -> Vec<Found> {
    if needle.is_empty() {
        return Vec::new();
    }
    let mut chars = Vec::new();
    let mut at = Vec::new();
    for (row, line) in lines.iter().enumerate() {
        let prompt = line.chars().count() - without_prompt(line).chars().count();
        for (column, c) in line.chars().enumerate().skip(prompt) {
            if !c.is_whitespace() {
                chars.push(c);
                at.push((row, column));
            }
        }
    }
    let mut found: Vec<Found> = Vec::new();
    let mut i = 0;
    while let Some(window) = chars.get(i..i + needle.len()) {
        if window != needle {
            i += 1;
            continue;
        }
        let (Some(&(first_row, _)), Some(&(last_row, _))) =
            (at.get(i), at.get(i + needle.len() - 1))
        else {
            break;
        };
        i += needle.len();
        if last_row - first_row >= MAX_REGION_ROWS {
            continue;
        }
        let joined = join_rows(&lines[first_row..=last_row]);
        let (joined_chars, positions): (Vec<char>, Vec<usize>) = joined
            .chars()
            .enumerate()
            .filter(|(_, c)| !c.is_whitespace())
            .map(|(at, c)| (c, at))
            .unzip();
        let Some(start) = joined_chars
            .windows(needle.len())
            .rposition(|part| part == needle)
        else {
            continue;
        };
        let place = Found {
            rows: first_row..last_row + 1,
            span: positions[start]..positions[start + needle.len() - 1] + 1,
        };
        match found.last_mut() {
            Some(previous) if previous.rows.end > place.rows.start => *previous = place,
            _ => found.push(place),
        }
    }
    found
}

// ---------- following a place ----------

enum Relocation {
    /// The place, as it is on the new screen.
    Moved(Place),
    /// The input line is still there but the pasted words are not: sent, or deleted.
    Cleared,
    /// Nothing on the screen looks like it.
    Gone,
}

/// How much of `old` is still in `new` when only one stretch changed: the characters they share
/// at the start and at the end, as a share of the longer one.
fn likeness(old: &[char], new: &str) -> f64 {
    let new: Vec<char> = new.chars().collect();
    let longest = old.len().max(new.len());
    if longest == 0 {
        return 1.0;
    }
    let prefix = old.iter().zip(&new).take_while(|(a, b)| a == b).count();
    let suffix = old
        .get(prefix..)
        .unwrap_or_default()
        .iter()
        .rev()
        .zip(new.get(prefix..).unwrap_or_default().iter().rev())
        .take_while(|(a, b)| a == b)
        .count();
    (prefix + suffix) as f64 / longest as f64
}

/// Finds the rows of `place` on the new screen: where they were if they still look the same,
/// otherwise the closest rows that do. A wrap may have added a row or taken one away. Rows that
/// look equally like it at the same distance above and below cannot be told apart.
fn relocate(place: &Place, lines: &[String], rewrapped: bool) -> Relocation {
    let old: Vec<char> = place.text.chars().collect();
    let height = place.rows.len();
    // The best row count from `start` on, and how much the rows look like the place.
    let best_at = |start: usize| -> Option<(usize, f64)> {
        let mut best: Option<(usize, f64)> = None;
        let heights = if rewrapped {
            std::iter::once(height)
                .chain((1..=MAX_REGION_ROWS).filter(|&len| len != height))
                .collect()
        } else {
            vec![height, height + 1, height.saturating_sub(1)]
        };
        for len in heights {
            if len == 0 {
                continue;
            }
            let Some(rows) = lines.get(start..start + len) else {
                continue;
            };
            let like = likeness(&old, &join_rows(rows));
            if best.is_none_or(|(_, top)| like > top) {
                best = Some((len, like));
            }
        }
        best.filter(|&(_, like)| like >= SAME_PLACE)
    };

    let from = place.start.saturating_sub(MAX_SHIFT);
    let to = (place.start + MAX_SHIFT).min(lines.len().saturating_sub(1));
    // Where it was: taken at once when it looks the same, and otherwise only if nothing nearby
    // looks clearly more like it.
    let here = best_at(place.start);
    let chosen = match here {
        Some((len, like)) if like >= UNMOVED => Some((place.start, len)),
        _ => {
            // The most alike rows elsewhere, the nearest if several are equally alike; `tie` when
            // two at the same distance, above and below, are.
            let mut best: Option<(usize, usize, f64)> = None;
            let mut tie = false;
            for start in (from..=to).filter(|&start| start != place.start) {
                let Some((len, like)) = best_at(start) else {
                    continue;
                };
                let Some((best_start, _, top)) = best else {
                    best = Some((start, len, like));
                    continue;
                };
                let (distance, best_distance) = (
                    start.abs_diff(place.start),
                    best_start.abs_diff(place.start),
                );
                let same = (like - top).abs() <= 1e-9;
                if like > top + 1e-9 || (same && distance < best_distance) {
                    best = Some((start, len, like));
                    tie = false;
                } else if same && distance == best_distance {
                    tie = true;
                }
            }
            match (here, best) {
                (Some((_, here_like)), Some((start, len, like)))
                    if like > here_like + CLEARLY_BETTER =>
                {
                    // Rows elsewhere are the place, and where it was now shows something else.
                    if tie {
                        return Relocation::Gone;
                    }
                    Some((start, len))
                }
                (Some((len, _)), _) => Some((place.start, len)),
                (None, Some((start, len, _))) => {
                    if tie {
                        return Relocation::Gone;
                    }
                    Some((start, len))
                }
                (None, None) => None,
            }
        }
    };

    let Some((start, len)) = chosen else {
        let line_is_there = !place.anchor.is_empty()
            && lines
                .get(from..=to)
                .is_some_and(|rows| rows.iter().any(|row| row.starts_with(&place.anchor)));
        return if line_is_there {
            Relocation::Cleared
        } else {
            Relocation::Gone
        };
    };
    let Some(rows) = lines.get(start..start + len) else {
        return Relocation::Gone;
    };
    let text = join_rows(rows);
    let tracker = place.tracker.advanced(&text);
    if tracker.kept() < MIN_KEPT_SHARE {
        return Relocation::Cleared;
    }
    Relocation::Moved(Place {
        tracker,
        rows: rows.to_vec(),
        text,
        start,
        anchor: place.anchor.clone(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    const PASTED: &str = "Use Tyeless and Open Router for the local command today";
    const FIRST: &str = "Use Typeless and Open Router for the local command today";
    const FIXED: &str = "Use Typeless and OpenRouter for the local command today";

    fn command(text: &str) -> String {
        format!("PS C:\\work> {text}")
    }

    fn pairs(tracker: &TerminalTracker) -> Vec<(String, String)> {
        tracker
            .corrections()
            .into_iter()
            .map(|c| (c.misheard, c.word))
            .collect()
    }

    fn typeless() -> (String, String) {
        ("Tyeless".to_owned(), "Typeless".to_owned())
    }

    /// A full-screen coding assistant: the conversation, an input in a frame, a status line.
    fn frame(above: &[&str], input: &str, status: &str) -> String {
        let mut rows: Vec<String> = above.iter().map(|row| (*row).to_owned()).collect();
        rows.push(format!("╭{}╮", "─".repeat(78)));
        rows.push(format!("│ > {input:<74} │"));
        rows.push(format!("╰{}╯", "─".repeat(78)));
        rows.push(format!("  {status}"));
        rows.join("\r\n")
    }

    const CONVERSATION: [&str; 4] = ["● Sure, I can help with that.", "", "> run the tests", ""];

    // ---------- a shell prompt ----------

    #[test]
    fn a_wrapped_paste_is_found_in_the_middle_pane_without_neighbor_text() {
        let before = "left status │╭────────────────────────────────╮│ right status\n\
                      left output ││ › Use Tyeless and Open Router   ││ right output\n\
                      left output ││   for the local command today   ││ right output\n\
                      left status │╰────────────────────────────────╯│ right status";
        let mut tracker = TerminalTracker::new(before, PASTED).expect("paste in middle pane");
        assert_eq!(
            tracker.observe(&before.replace("Tyeless", "Typeless")),
            TerminalDecision::Continue
        );
        assert_eq!(pairs(&tracker), vec![typeless()]);
        assert!(!tracker.region().contains("left"));
        assert!(!tracker.region().contains("right"));
    }

    const HERDR: &str = include_str!("terminal/fixtures/herdr-anonymized.txt");
    const HERDR_PASTE: &str = "名範例保留：cloud 畫面結構測試文，Archive Notebook 字用於匿名範例保留畫面結構測試文字用，於匿 Archive 名範例保留畫面結構。測試文字用於匿名範例保留畫 Cloud（面 cloud 結構測試文）。";

    #[test]
    fn the_anonymized_real_herdr_layout_finds_and_corrects_the_middle_pane() {
        let mut tracker = TerminalTracker::new(HERDR, HERDR_PASTE).expect("middle-pane paste");
        assert_eq!(tracker.pane, 1);
        assert_eq!(tracker.pane_count, 3);
        let edited = HERDR.replacen("：cloud", "：tools", 1);
        assert_eq!(tracker.observe(&edited), TerminalDecision::Continue);
        assert_eq!(pairs(&tracker), [("cloud".into(), "tools".into())]);
    }

    #[test]
    fn edits_to_other_panes_in_the_real_layout_are_never_corrections() {
        let mut tracker = TerminalTracker::new(HERDR, HERDR_PASTE).unwrap();
        let edited = HERDR
            .replace("● Archive", "● Project")
            .replace("││› Notebook", "││› ChangedX");
        assert_eq!(tracker.observe(&edited), TerminalDecision::Continue);
        assert!(pairs(&tracker).is_empty());
    }

    fn multiplex(input: &str, width: usize) -> String {
        wrapped_frame(input, width)
            .lines()
            .map(|row| format!("left status │{row}│ other output"))
            .collect::<Vec<_>>()
            .join("\n")
    }

    #[test]
    fn a_resized_column_rewraps_by_several_rows_without_learning_the_redraw() {
        let mut tracker = TerminalTracker::new(&multiplex(PASTED, 60), PASTED).unwrap();
        assert_eq!(
            tracker.observe(&multiplex(PASTED, 14)),
            TerminalDecision::Continue
        );
        assert!(pairs(&tracker).is_empty());
        assert_eq!(
            tracker.observe(&multiplex(FIRST, 14)),
            TerminalDecision::Continue
        );
        assert_eq!(pairs(&tracker), [typeless()]);
    }

    #[test]
    fn a_different_pane_cannot_take_over_when_the_tracked_pane_is_removed() {
        let mut tracker = TerminalTracker::new(&multiplex(PASTED, 30), PASTED).unwrap();
        assert_eq!(tracker.observe(&command(FIRST)), TerminalDecision::Lost);
        assert!(pairs(&tracker).is_empty());
    }

    #[test]
    fn repeated_prompt_markers_are_not_words_in_a_wrapped_input() {
        let screen = "╭────────────────────────────╮\n│ › Use Tyeless and Open     │\n│ > Router for the local     │\n│ › command today            │\n╰────────────────────────────╯\nready";
        let mut tracker = TerminalTracker::new(screen, PASTED).unwrap();
        assert_eq!(
            tracker.observe(&screen.replace("Tyeless and Open     ", "Typeless and Open    ")),
            TerminalDecision::Continue
        );
        assert_eq!(pairs(&tracker), [typeless()]);
    }

    #[test]
    fn wide_cjk_words_wrap_inside_their_own_pane_and_can_be_corrected() {
        let pasted = "我昨天買了一支新的首機，拍照很漂亮，電池也很耐用，整體來說我很滿意";
        let screen = |input: &str| {
            input
                .chars()
                .collect::<Vec<_>>()
                .chunks(9)
                .enumerate()
                .map(|(row, text)| {
                    let text: String = text.iter().collect();
                    let prompt = if row == 0 { "› " } else { "  " };
                    format!(
                        "側欄狀態 │ {prompt}{text}{} │ other output",
                        " ".repeat(18 - text.chars().count() * 2)
                    )
                })
                .collect::<Vec<_>>()
                .join("\n")
        };
        let mut tracker = TerminalTracker::new(&screen(pasted), pasted).unwrap();
        let fixed = pasted.replace("首機", "電話");
        assert_eq!(tracker.observe(&screen(&fixed)), TerminalDecision::Continue);
        assert_eq!(pairs(&tracker), [("首機".into(), "電話".into())]);
    }

    #[test]
    fn an_empty_neighbor_keeps_its_column_and_cannot_stop_the_watch() {
        let before = multiplex(PASTED, 20);
        let mut tracker = TerminalTracker::new(&before, PASTED).unwrap();
        let cleared = before.replace("left status", "           ");
        assert_eq!(tracker.observe(&cleared), TerminalDecision::Continue);
        assert!(pairs(&tracker).is_empty());
    }

    #[test]
    fn identical_pastes_in_different_panes_are_ambiguous() {
        let screen = (0..3)
            .map(|_| format!("{PASTED} │ {PASTED}"))
            .collect::<Vec<_>>()
            .join("\n");
        assert!(TerminalTracker::new(&screen, PASTED).is_none());
    }

    #[test]
    fn collapsed_paste_placeholders_are_counted_but_not_followed() {
        let screen = "> [Pasted text #1 +3 lines]\n[Pasted text #2 +1 line]\n[Pasted text #3]";
        assert_eq!(collapsed_pastes(screen), 3);
        assert!(TerminalTracker::new(screen, PASTED).is_none());
        assert_eq!(
            collapsed_pastes(
                "[Pasted text #unknown +3 lines] [Pasted text #1 +many lines] [1 +3 lines]"
            ),
            0
        );
    }

    #[test]
    fn last_active_occurrence_is_selected_instead_of_a_command_in_history() {
        let buffer = format!(
            "{}\r\nold output\r\n{}\r\n  \r\n",
            command(PASTED),
            command(PASTED)
        );
        let tracker = TerminalTracker::new(&buffer, PASTED).unwrap();
        assert_eq!(tracker.region(), command(PASTED));
    }

    #[test]
    fn corrections_are_extracted_only_from_the_pasted_rows() {
        let initial = format!("old history\n{}", command(PASTED));
        let mut tracker = TerminalTracker::new(&initial, PASTED).unwrap();
        let first = format!(
            "unrelated rewritten history\n{}",
            command(&PASTED.replace("Tyeless", "Typeless"))
        );
        assert_eq!(tracker.observe(&first), TerminalDecision::Continue);
        assert_eq!(
            tracker.observe(&format!("another output\n{}", command(FIXED))),
            TerminalDecision::Continue
        );
        assert_eq!(
            pairs(&tracker),
            [
                typeless(),
                ("Open Router".to_owned(), "OpenRouter".to_owned())
            ]
        );
    }

    #[test]
    fn output_after_the_command_is_not_part_of_the_region() {
        // The keyboard observer ends the watch at Enter. Rows below the command are just rows
        // below it: a status line looks the same as output until Enter says which it is.
        let mut tracker = TerminalTracker::new(&command(PASTED), PASTED).unwrap();
        tracker.observe(&command(FIRST));
        assert_eq!(tracker.observe(&command(FIXED)), TerminalDecision::Continue);
        let output = format!(
            "{}\r\nUse Typeless and AnotherTool for the local command today\r\nPS C:\\work> ",
            command(FIXED)
        );
        assert_eq!(tracker.observe(&output), TerminalDecision::Continue);
        assert_eq!(tracker.region(), command(FIXED));
        assert_eq!(pairs(&tracker).len(), 2);
        assert_eq!(tracker.submitted(), TerminalDecision::Sent);
        assert_eq!(tracker.observe("PS> unrelated"), TerminalDecision::Sent);
        assert_eq!(pairs(&tracker).len(), 2);
    }

    #[test]
    fn an_enter_signal_freezes_the_last_pre_submit_snapshot() {
        let mut tracker = TerminalTracker::new(&command(PASTED), PASTED).unwrap();
        tracker.observe(&command(FIXED));
        assert_eq!(tracker.submitted(), TerminalDecision::Sent);
        assert_eq!(
            tracker.observe("new program output"),
            TerminalDecision::Sent
        );
        assert_eq!(tracker.region(), command(FIXED));
    }

    #[test]
    fn a_cleared_prompt_ends_the_watch_and_keeps_the_last_snapshot() {
        let mut tracker = TerminalTracker::new(&command(PASTED), PASTED).unwrap();
        tracker.observe(&command(FIRST));
        tracker.observe(&command(FIXED));
        assert_eq!(tracker.observe("PS C:\\work> "), TerminalDecision::Sent);
        assert_eq!(tracker.region(), command(FIXED));
        assert_eq!(pairs(&tracker).len(), 2);
    }

    #[test]
    fn scrollback_truncation_does_not_move_the_tracked_command() {
        let initial = format!("oldest\nolder\nold\n{}", command(PASTED));
        let mut tracker = TerminalTracker::new(&initial, PASTED).unwrap();
        tracker.observe(&format!("older\nold\n{}", command(FIRST)));
        assert_eq!(
            tracker.observe(&format!("old\n{}", command(FIXED))),
            TerminalDecision::Continue
        );
        assert_eq!(pairs(&tracker).len(), 2);
    }

    #[test]
    fn multiline_pastes_keep_only_their_containing_rows() {
        let pasted = "Use Tyeless for this command today\nthen use Open Router for the next step";
        let mut tracker =
            TerminalTracker::new(&format!("history\r\nPS> {pasted}\r\n\r\n"), pasted).unwrap();
        // The rows of one paste are one text; a break between them stands for a blank.
        assert_eq!(
            tracker.region(),
            "PS> Use Tyeless for this command today then use Open Router for the next step"
        );
        assert_eq!(
            tracker.observe(&format!(
                "new history\nPS> {}",
                pasted.replace("Tyeless", "Typeless")
            )),
            TerminalDecision::Continue
        );
        assert_eq!(pairs(&tracker), [typeless()]);
    }

    #[test]
    fn physical_wraps_can_be_found_and_edited_without_tracking_history() {
        let initial = "history\nPS> Use Tyeless and Open Router\n for the local command today\n\n";
        let mut tracker = TerminalTracker::new(initial, PASTED).unwrap();
        assert_eq!(
            tracker
                .observe("history\nPS> Use Typeless and Open Router\n for the local command today"),
            TerminalDecision::Continue
        );
        assert_eq!(pairs(&tracker), [typeless()]);
    }

    #[test]
    fn a_command_that_is_re_wrapped_is_followed_and_the_wrap_is_not_learned() {
        let mut tracker = TerminalTracker::new(&command(PASTED), PASTED).unwrap();
        tracker.observe(&command(FIRST));
        // The window got narrower: the same words, one row more.
        assert_eq!(
            tracker
                .observe("PS C:\\work> Use Typeless and Open Router\n for the local command today"),
            TerminalDecision::Continue
        );
        assert_eq!(tracker.region(), command(FIRST));
        assert_eq!(pairs(&tracker), [typeless()]);
        // Wider again, and one more word fixed.
        assert_eq!(tracker.observe(&command(FIXED)), TerminalDecision::Continue);
        assert_eq!(pairs(&tracker).len(), 2);
    }

    #[test]
    fn a_replacement_screen_does_not_get_learned_as_an_edit() {
        let mut tracker = TerminalTracker::new(&command(PASTED), PASTED).unwrap();
        assert_eq!(
            tracker.observe("error: the terminal is disconnected"),
            TerminalDecision::Lost
        );
        assert!(pairs(&tracker).is_empty());
        assert_eq!(tracker.region(), command(PASTED));
    }

    #[test]
    fn a_large_command_rewrite_keeps_the_previous_valid_snapshot() {
        let mut tracker = TerminalTracker::new(&command(PASTED), PASTED).unwrap();
        assert_eq!(
            tracker.observe("PS C:\\work> completely different text"),
            TerminalDecision::Sent
        );
        assert!(pairs(&tracker).is_empty());
    }

    #[test]
    fn a_paste_outside_the_bounded_tail_is_not_found() {
        let buffer = format!("{}\n{}", command(PASTED), "x".repeat(24_000));
        assert!(TerminalTracker::new(&buffer, PASTED).is_none());
    }

    #[test]
    fn a_blank_paste_or_a_paste_that_is_not_there_starts_nothing() {
        assert!(TerminalTracker::new("PS> ", "   ").is_none());
        assert!(TerminalTracker::new("PS> something else", PASTED).is_none());
        assert!(TerminalTracker::new("", PASTED).is_none());
    }

    #[test]
    fn huge_history_and_unicode_do_not_break_tail_boundaries() {
        let pasted = "Please use Tyeless for this command today";
        let buffer = format!("{}\r\nPS> {pasted}\r\n\r\n", "\u{6f22}".repeat(25_000));
        let mut tracker = TerminalTracker::new(&buffer, pasted).unwrap();
        assert_eq!(
            tracker.observe("PS> Please use Typeless for this command today\r\n  \r\n"),
            TerminalDecision::Continue
        );
        assert_eq!(pairs(&tracker), [typeless()]);
    }

    #[test]
    fn only_the_last_whitespace_variant_in_one_row_is_tracked() {
        let pasted = "Please use Tyeless command today";
        let buffer = format!("PS> {pasted}; Please use  Tyeless command today");
        let mut tracker = TerminalTracker::new(&buffer, pasted).unwrap();
        assert_eq!(tracker.places.len(), 1);
        assert_eq!(
            tracker.observe(
                "PS> Please use Tyeless command today; Please use  Typeless command today"
            ),
            TerminalDecision::Continue
        );
        assert_eq!(pairs(&tracker), [typeless()]);
    }

    // ---------- a full-screen program ----------

    #[test]
    fn tui_input_above_a_status_line_can_be_corrected() {
        let mut tracker = TerminalTracker::new(
            &frame(&CONVERSATION, PASTED, "? for shortcuts   context: 20%"),
            PASTED,
        )
        .unwrap();
        assert_eq!(tracker.region(), format!("> {PASTED}"));
        assert_eq!(
            tracker.observe(&frame(
                &CONVERSATION,
                FIRST,
                "? for shortcuts   context: 21%"
            )),
            TerminalDecision::Continue
        );
        assert_eq!(tracker.region(), format!("> {FIRST}"));
        assert_eq!(pairs(&tracker), [typeless()]);
    }

    #[test]
    fn the_padding_up_to_a_frame_border_does_not_turn_an_edit_into_a_larger_one() {
        let mut tracker =
            TerminalTracker::new(&frame(&CONVERSATION, PASTED, "ready"), PASTED).unwrap();
        // One letter longer: the padding before the border is one blank shorter.
        tracker.observe(&frame(&CONVERSATION, FIRST, "ready"));
        // One letter shorter: one blank longer.
        tracker.observe(&frame(&CONVERSATION, FIXED, "ready"));
        assert_eq!(
            pairs(&tracker),
            [
                typeless(),
                ("Open Router".to_owned(), "OpenRouter".to_owned())
            ]
        );
    }

    #[test]
    fn a_status_line_that_changes_every_read_is_not_the_input() {
        let mut tracker =
            TerminalTracker::new(&frame(&CONVERSATION, PASTED, "thinking (3s)"), PASTED).unwrap();
        for seconds in 4..8 {
            assert_eq!(
                tracker.observe(&frame(
                    &CONVERSATION,
                    PASTED,
                    &format!("thinking ({seconds}s)")
                )),
                TerminalDecision::Continue
            );
        }
        assert!(pairs(&tracker).is_empty());
        assert_eq!(
            tracker.observe(&frame(&CONVERSATION, FIRST, "thinking (9s)")),
            TerminalDecision::Continue
        );
        assert_eq!(pairs(&tracker), [typeless()]);
    }

    #[test]
    fn an_input_frame_that_moves_down_while_the_conversation_grows_is_followed() {
        let mut tracker =
            TerminalTracker::new(&frame(&CONVERSATION, PASTED, "ready"), PASTED).unwrap();
        let mut longer = CONVERSATION.to_vec();
        longer.extend(["● Running the tests...", "  ⎿ 12 passed", "", "● Done."]);
        assert_eq!(
            tracker.observe(&frame(&longer, FIRST, "ready")),
            TerminalDecision::Continue
        );
        assert_eq!(tracker.region(), format!("> {FIRST}"));
        assert_eq!(pairs(&tracker), [typeless()]);
    }

    #[test]
    fn an_input_frame_that_moves_up_when_the_program_clears_rows_is_followed() {
        let mut long = CONVERSATION.to_vec();
        long.extend(["● Running the tests...", "  ⎿ 12 passed", "", "● Done."]);
        let mut tracker = TerminalTracker::new(&frame(&long, PASTED, "ready"), PASTED).unwrap();
        assert_eq!(
            tracker.observe(&frame(&CONVERSATION, FIRST, "ready")),
            TerminalDecision::Continue
        );
        assert_eq!(pairs(&tracker), [typeless()]);
    }

    #[test]
    fn a_screen_that_scrolls_one_row_at_a_time_is_followed() {
        let conversation = [
            "line 1", "line 2", "line 3", "line 4", "line 5", "line 6", "line 7", "line 8",
        ];
        let rows = |skip: usize| frame(&conversation[skip..], PASTED, "ready");
        let mut tracker = TerminalTracker::new(&rows(0), PASTED).unwrap();
        for skip in 1..4 {
            assert_eq!(tracker.observe(&rows(skip)), TerminalDecision::Continue);
        }
        let edited = frame(&conversation[4..], FIRST, "ready");
        assert_eq!(tracker.observe(&edited), TerminalDecision::Continue);
        assert_eq!(pairs(&tracker), [typeless()]);
    }

    #[test]
    fn a_paste_that_is_wrapped_inside_a_frame_is_one_place_and_stays_findable() {
        let rows = |first: &str| {
            format!(
                "output\n╭{b}╮\n│ > {first:<40} │\n│   {rest:<40} │\n╰{b}╯\n  ready",
                b = "─".repeat(44),
                rest = "for the local command today"
            )
        };
        let mut tracker =
            TerminalTracker::new(&rows("Use Tyeless and Open Router"), PASTED).unwrap();
        assert_eq!(tracker.region(), format!("> {PASTED}"));
        // The watch compares the region with the pasted text by itself, wraps and all.
        assert!(Tracker::new(tracker.region(), PASTED).is_some());
        assert_eq!(
            tracker.observe(&rows("Use Typeless and Open Router")),
            TerminalDecision::Continue
        );
        assert_eq!(pairs(&tracker), [typeless()]);
    }

    #[test]
    fn cjk_text_in_a_frame_is_followed() {
        let pasted = "我昨天買了一支新的首機，拍照很漂亮，電池也很耐用，整體來說我很滿意";
        let fixed = pasted.replacen("首機", "手提電話", 1);
        let mut tracker = TerminalTracker::new(&frame(&[], pasted, "ready"), pasted).unwrap();
        assert_eq!(
            tracker.observe(&frame(&[], &fixed, "ready")),
            TerminalDecision::Continue
        );
        assert_eq!(
            pairs(&tracker),
            [("首機".to_owned(), "手提電話".to_owned())]
        );
    }

    #[test]
    fn frame_characters_and_blanks_are_not_text() {
        assert_eq!(tidy_row("│ > hello world        │"), "> hello world");
        assert_eq!(tidy_row("╭──────╮"), "");
        assert_eq!(tidy_row("  ▌ typed █ here  "), "typed  here");
        assert_eq!(tidy_row("plain"), "plain");
    }

    #[test]
    fn a_paste_that_needs_too_many_rows_is_not_followed() {
        let pasted: String = (0..MAX_REGION_ROWS + 4)
            .map(|n| format!("word{n} filler words to make a row"))
            .collect::<Vec<_>>()
            .join("\n");
        assert!(TerminalTracker::new(&pasted, &pasted).is_none());
    }

    // ---------- the same words more than once ----------

    #[test]
    fn the_copy_in_the_history_is_not_the_input() {
        let echo = format!("> {PASTED}");
        let conversation = ["● Sure.", "", echo.as_str(), "● Done.", ""];
        let mut tracker =
            TerminalTracker::new(&frame(&conversation, PASTED, "ready"), PASTED).unwrap();
        assert_eq!(tracker.places.len(), 2);
        // Unedited copies teach nothing.
        assert!(pairs(&tracker).is_empty());
        assert_eq!(tracker.region(), format!("> {PASTED}"));
        assert_eq!(
            tracker.observe(&frame(&conversation, FIRST, "ready")),
            TerminalDecision::Continue
        );
        assert_eq!(tracker.places.len(), 1);
        assert_eq!(tracker.region(), format!("> {FIRST}"));
        assert_eq!(pairs(&tracker), [typeless()]);
    }

    #[test]
    fn a_copy_below_the_input_is_told_apart_by_which_one_changes() {
        let screen = |input: &str| {
            format!(
                "╭{b}╮\r\n│ > {input:<74} │\r\n╰{b}╯\r\n  preview: {PASTED}\r\n  ready",
                b = "─".repeat(78)
            )
        };
        let mut tracker = TerminalTracker::new(&screen(PASTED), PASTED).unwrap();
        assert_eq!(tracker.places.len(), 2);
        assert_eq!(tracker.observe(&screen(FIRST)), TerminalDecision::Continue);
        assert_eq!(tracker.region(), format!("> {FIRST}"));
        assert_eq!(pairs(&tracker), [typeless()]);
    }

    #[test]
    fn two_copies_edited_in_one_read_cannot_be_told_apart() {
        let screen = |first: &str, second: &str| format!("> {first}\nother\n> {second}\nready");
        let mut tracker = TerminalTracker::new(&screen(PASTED, PASTED), PASTED).unwrap();
        assert_eq!(tracker.places.len(), 2);
        assert_eq!(
            tracker.observe(&screen(FIRST, FIRST)),
            TerminalDecision::Lost
        );
        assert!(pairs(&tracker).is_empty());
    }

    #[test]
    fn a_paste_drawn_after_the_first_read_is_found_below_an_older_copy() {
        let screen = |input: &str| format!("> {PASTED}\n● Done.\n\n> {input}\nready");
        // The program has not drawn the paste yet: the older copy is all there is.
        let mut tracker = TerminalTracker::new(&screen(""), PASTED).unwrap();
        assert_eq!(tracker.places.len(), 1);
        assert_eq!(tracker.observe(&screen(PASTED)), TerminalDecision::Continue);
        assert_eq!(tracker.places.len(), 2);
        assert_eq!(tracker.region(), format!("> {PASTED}"));
        assert_eq!(tracker.observe(&screen(FIRST)), TerminalDecision::Continue);
        assert_eq!(tracker.places.len(), 1);
        assert_eq!(pairs(&tracker), [typeless()]);
    }

    #[test]
    fn no_more_than_the_lowest_copies_are_followed() {
        let buffer = (0..7)
            .map(|n| format!("> {PASTED}\nsplit {n}"))
            .collect::<Vec<_>>()
            .join("\n");
        let tracker = TerminalTracker::new(&buffer, PASTED).unwrap();
        assert_eq!(tracker.places.len(), MAX_PLACES);
        assert_eq!(tracker.places[0].start, 6);
    }

    #[test]
    fn rows_that_look_the_same_above_and_below_after_a_move_give_up() {
        let line = format!("> {PASTED}");
        let screen = |rows: [&str; 9]| rows.join("\n");
        let mut tracker = TerminalTracker::new(
            &screen(["a", "b", "c", "d", "e", &line, "g", "h", "i"]),
            PASTED,
        )
        .unwrap();
        // The input is gone from where it was, and two copies are as close to that place.
        let moved = screen(["a", "b", "c", &line, "e", "x", "g", &line, "i"]);
        assert_eq!(tracker.observe(&moved), TerminalDecision::Lost);
        assert!(pairs(&tracker).is_empty());
    }

    #[test]
    fn the_nearer_of_two_look_alikes_is_the_place() {
        let line = format!("> {PASTED}");
        let first = format!("{line}\nsame\nstuff\nhere\n{line}\nready");
        let mut tracker = TerminalTracker::new(&first, PASTED).unwrap();
        // Edit the lower one; the upper stays.
        let second = format!("{line}\nsame\nstuff\nhere\n> {FIRST}\nready");
        assert_eq!(tracker.observe(&second), TerminalDecision::Continue);
        // A further edit still finds it at its row.
        let third = format!("{line}\nsame\nstuff\nhere\n> {FIXED}\nready");
        assert_eq!(tracker.observe(&third), TerminalDecision::Continue);
        assert_eq!(pairs(&tracker).len(), 2);
    }

    /// A full-screen program's input, word-wrapped to `width` characters inside a frame.
    fn wrapped_frame(input: &str, width: usize) -> String {
        let mut rows: Vec<String> = Vec::new();
        let mut row = String::new();
        for word in input.split(' ') {
            if !row.is_empty() && row.len() + 1 + word.len() > width {
                rows.push(std::mem::take(&mut row));
            }
            if !row.is_empty() {
                row.push(' ');
            }
            row.push_str(word);
        }
        rows.push(row);
        let border = "─".repeat(width + 4);
        let mut screen = vec![format!("╭{border}╮")];
        for (at, row) in rows.iter().enumerate() {
            let lead = if at == 0 { "> " } else { "  " };
            screen.push(format!("│ {lead}{row:<width$} │"));
        }
        screen.push(format!("╰{border}╯"));
        screen.push("  ready".to_owned());
        screen.join("\r\n")
    }

    #[test]
    fn an_edit_that_moves_words_across_the_wraps_of_several_rows_is_one_edit() {
        let pasted = "Use Tyeless and Open Router for the local command today please";
        let fixed = pasted.replace("Tyeless", "Typeless");
        // 27 characters wide: the longer word pushes "Router" down, and with it every wrap after.
        let mut tracker = TerminalTracker::new(&wrapped_frame(pasted, 27), pasted).unwrap();
        assert_eq!(
            tracker.observe(&wrapped_frame(&fixed, 27)),
            TerminalDecision::Continue
        );
        assert_eq!(tracker.region(), format!("> {fixed}"));
        assert_eq!(pairs(&tracker), [typeless()]);
    }

    #[test]
    fn chinese_text_cut_into_rows_without_blanks_is_followed_through_a_longer_edit() {
        let pasted = "我昨天買了一支新的首機拍照很漂亮電池也很耐用整體來說我很滿意";
        let fixed = pasted.replacen("首機", "手提電話", 1);
        // Ten characters to a row, cut wherever the row ends.
        let screen = |text: &str| {
            let chars: Vec<char> = text.chars().collect();
            let mut rows: Vec<String> = chars
                .chunks(10)
                .enumerate()
                .map(|(at, chunk)| {
                    let lead = if at == 0 { "> " } else { "  " };
                    format!("{lead}{}", chunk.iter().collect::<String>())
                })
                .collect();
            rows.push("  ready".to_owned());
            rows.join("\r\n")
        };
        let mut tracker = TerminalTracker::new(&screen(pasted), pasted).unwrap();
        assert_eq!(tracker.region(), format!("> {pasted}"));
        assert_eq!(tracker.observe(&screen(&fixed)), TerminalDecision::Continue);
        assert_eq!(tracker.region(), format!("> {fixed}"));
        assert_eq!(
            pairs(&tracker),
            [("首機".to_owned(), "手提電話".to_owned())]
        );
    }

    #[test]
    fn rows_are_joined_by_a_blank_unless_both_sides_are_written_without_blanks() {
        let rows =
            |rows: &[&str]| join_rows(&rows.iter().map(|r| (*r).to_owned()).collect::<Vec<_>>());
        assert_eq!(rows(&["one two", "three"]), "one two three");
        assert_eq!(rows(&["我們", "他們"]), "我們他們");
        assert_eq!(rows(&["我們，", "他們"]), "我們，他們");
        assert_eq!(rows(&["我們", "and"]), "我們 and");
        assert_eq!(rows(&["and", "我們"]), "and 我們");
        assert_eq!(rows(&["above", "", "below"]), "above  below");
        assert_eq!(rows(&["only"]), "only");
    }

    #[test]
    fn a_marker_that_changes_beside_a_copy_does_not_make_it_the_input() {
        let screen =
            |marker: &str, input: &str| format!("{marker} {PASTED}\n● Done.\n\n> {input}\nready");
        let mut tracker = TerminalTracker::new(&screen("●", PASTED), PASTED).unwrap();
        assert_eq!(tracker.places.len(), 2);
        // The bullet in front of the older copy is redrawn; nobody edited anything.
        assert_eq!(
            tracker.observe(&screen("○", PASTED)),
            TerminalDecision::Continue
        );
        assert_eq!(tracker.places.len(), 2);
        // Now the input is edited.
        assert_eq!(
            tracker.observe(&screen("○", FIRST)),
            TerminalDecision::Continue
        );
        assert_eq!(tracker.places.len(), 1);
        assert_eq!(tracker.region(), format!("> {FIRST}"));
        assert_eq!(pairs(&tracker), [typeless()]);
    }

    #[test]
    fn a_lookalike_row_that_scrolls_onto_the_old_place_does_not_capture_it() {
        let input = format!("> {PASTED}");
        let lookalike = "> Use Tyeless and Open Router for the other tools today";
        // Not close enough to be taken for the place without a look around.
        let like = likeness(&input.chars().collect::<Vec<_>>(), lookalike);
        assert!((SAME_PLACE..UNMOVED).contains(&like), "{like}");

        let mut tracker = TerminalTracker::new(
            &["a", "b", "c", "d", "e", &input, "ready"].join("\n"),
            PASTED,
        )
        .unwrap();
        // Three rows were added above the input, and an older, similar line took its old place.
        let moved = [
            "f", "g", "h", "i", "j", lookalike, "k", "l", &input, "ready",
        ]
        .join("\n");
        assert_eq!(tracker.observe(&moved), TerminalDecision::Continue);
        assert_eq!(tracker.region(), input);
        assert!(pairs(&tracker).is_empty());
    }

    #[test]
    fn a_place_that_changed_a_lot_in_one_read_stays_when_nothing_looks_better() {
        let input = format!("> {PASTED}");
        let rewritten = "> Use Tyeless and Open Router for the other tools today";
        let mut tracker = TerminalTracker::new(&["a", &input, "ready"].join("\n"), PASTED).unwrap();
        assert_eq!(
            tracker.observe(&["a", rewritten, "ready"].join("\n")),
            TerminalDecision::Continue
        );
        assert_eq!(tracker.region(), rewritten);
    }

    /// A tiny deterministic generator (xorshift): the point is to meet odd boundaries, not luck.
    struct Rng(u64);

    impl Rng {
        fn next(&mut self) -> u64 {
            self.0 ^= self.0 << 13;
            self.0 ^= self.0 >> 7;
            self.0 ^= self.0 << 17;
            self.0
        }

        /// A string of 0 to `longest` characters of `alphabet`.
        fn text(&mut self, alphabet: &[char], longest: u64) -> String {
            let length = self.next() % (longest + 1);
            (0..length)
                .map(|_| alphabet[(self.next() % alphabet.len() as u64) as usize])
                .collect()
        }
    }

    #[test]
    fn odd_screens_never_panic() {
        // Multi-byte letters, emoji, frame characters, blanks of every kind and both line breaks.
        let alphabet: Vec<char> =
            "ab Cd\n\r\t\u{a0}\u{2502}\u{256d}\u{2500}\u{2588}\u{6f22}\u{5b57}\u{e9}\u{1f4a1}.>$"
                .chars()
                .collect();
        let mut rng = Rng(0x2545_F491_4F6C_DD1D);
        let mut followed = 0;
        for _ in 0..400 {
            let pasted = rng.text(&alphabet, 10);
            let above = rng.text(&alphabet, 40);
            let below = rng.text(&alphabet, 40);
            let screen = format!("{above}\n{pasted}\n{below}");
            let Some(mut tracker) = TerminalTracker::new(&screen, &pasted) else {
                continue;
            };
            followed += 1;
            for _ in 0..6 {
                let edited = rng.text(&alphabet, 12);
                let above = rng.text(&alphabet, 60);
                let below = rng.text(&alphabet, 60);
                let _ = tracker.observe(&format!("{above}\n{edited}\n{below}"));
                let _ = tracker.region();
                let _ = tracker.corrections();
            }
            let _ = tracker.submitted();
        }
        assert!(followed > 100, "only {followed} screens were followed");
    }

    #[test]
    fn a_session_of_edits_moves_and_redraws_only_ever_learns_what_is_in_the_input() {
        let words = [
            "alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india",
            "juliet", "kilo", "lima",
        ];
        let mut rng = Rng(0x1234_5678_9ABC_DEF1);
        let mut learned = 0;
        for _ in 0..400 {
            let pick = |rng: &mut Rng| words[(rng.next() % words.len() as u64) as usize];
            let mut input: Vec<&str> = (0..8 + rng.next() % 3).map(|_| pick(&mut rng)).collect();
            let pasted = input.join(" ");
            let mut history: Vec<String> = (0..rng.next() % 4)
                .map(|n| format!("history row {n} {}", pick(&mut rng)))
                .collect();
            // Half of the time the same words are already in the conversation.
            if rng.next() & 1 == 0 {
                history.insert(0, format!("> {pasted}"));
            }
            let status = |rng: &mut Rng| format!("context {}%  {}", rng.next() % 100, pick(rng));
            let screen = |history: &[String], input: &[&str], status: &str| {
                let rows: Vec<&str> = history.iter().map(String::as_str).collect();
                frame(&rows, &input.join(" "), status)
            };
            let first_status = status(&mut rng);
            let Some(mut tracker) =
                TerminalTracker::new(&screen(&history, &input, &first_status), &pasted)
            else {
                continue;
            };
            for _ in 0..6 {
                match rng.next() % 4 {
                    // One word of the input is replaced by another.
                    0 | 1 => {
                        let at = (rng.next() % input.len() as u64) as usize;
                        input[at] = pick(&mut rng);
                    }
                    // The conversation grows or scrolls.
                    2 => {
                        if rng.next() & 1 == 0 || history.is_empty() {
                            history.push(format!("output {}", pick(&mut rng)));
                        } else {
                            history.remove(0);
                        }
                    }
                    _ => {}
                }
                let next_status = status(&mut rng);
                tracker.observe(&screen(&history, &input, &next_status));
            }
            let finally = input.join(" ");
            for correction in tracker.corrections() {
                learned += 1;
                assert!(
                    pasted.contains(&correction.misheard),
                    "{} is not in what was pasted: {pasted}",
                    correction.misheard
                );
                assert!(
                    finally.contains(&correction.word),
                    "{} is not in the input: {finally}",
                    correction.word
                );
            }
        }
        assert!(learned > 20, "only {learned} corrections were learned");
    }
}
