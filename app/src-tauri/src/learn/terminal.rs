//! Tracks only the terminal rows that received a pasted dictation.
//!
//! A terminal document includes prompts, history and program output. Only a paste in its last
//! occupied row is accepted; the containing rows form a small field for [`Tracker`]. The prompt
//! prefix locates those rows again after scrollback moves. An unchanged command in history,
//! appended output, a new prompt or an uncertain row change ends observation without advancing
//! that field. A keyboard observer should call [`TerminalTracker::submitted`] before reading a
//! post-Enter screen: screen text alone cannot distinguish every output from an edit.

use std::ops::Range;

use super::corrections::{Correction, MIN_KEPT_SHARE, Tracker};

/// Bound both initial matching and later relocation, including when a provider returns history.
const MAX_TAIL_CHARS: usize = 20_000;
const MAX_REGION_ROWS: usize = 16;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TerminalDecision {
    /// The original command rows are still being followed.
    Continue,
    /// Submission, a new prompt or appended rows ended the command's editable lifetime.
    Sent,
    /// The command cannot be relocated conservatively.
    Lost,
}

#[derive(Clone, Debug)]
pub struct TerminalTracker {
    tracker: Tracker,
    region: String,
    rows: Vec<String>,
    prefix: String,
    before: Vec<String>,
    decision: TerminalDecision,
}

impl TerminalTracker {
    /// Finds the last paste near the active end, ignoring blank screen rows and CRLF differences.
    /// The text may occupy several rows, including physical wraps, but must end in the last
    /// occupied row. A match in old output is not enough to start a watch.
    pub fn new(buffer: &str, pasted: &str) -> Option<Self> {
        let lines = screen_rows(buffer);
        let text = lines.join("\n");
        let pasted = normalize(pasted.trim());
        let span = pasted_span(&text, &pasted)?;
        let start = text[..span.start].bytes().filter(|&b| b == b'\n').count();
        let end = text[..span.end].bytes().filter(|&b| b == b'\n').count();
        if end + 1 != lines.len() || end - start + 1 > MAX_REGION_ROWS {
            return None;
        }
        let line_start = text[..span.start].rfind('\n').map_or(0, |at| at + 1);
        let rows = lines[start..=end].to_vec();
        let region = rows.join("\n");
        Some(Self {
            // Use the matched screen spelling, including wraps, so an earlier exact match in
            // the same row cannot displace this last whitespace-flexible occurrence.
            tracker: Tracker::new(&region, &text[span.clone()])?,
            region,
            rows,
            prefix: text[line_start..span.start].to_owned(),
            before: context(&lines, start),
            decision: TerminalDecision::Continue,
        })
    }

    /// Advances only the command rows. End decisions are sticky and preserve the last valid
    /// snapshot. Row-count changes are deliberately conservative: a new row could be output,
    /// rather than a soft wrap. The UIA caller may supply a caret-bounded active buffer instead.
    pub fn observe(&mut self, buffer: &str) -> TerminalDecision {
        if self.decision != TerminalDecision::Continue {
            return self.decision;
        }
        let lines = screen_rows(buffer);
        if lines.is_empty() {
            return self.end(TerminalDecision::Sent);
        }
        if lines.ends_with(&self.rows) {
            self.before = context(&lines, lines.len() - self.rows.len());
            return TerminalDecision::Continue;
        }

        // Prefer the active prompt to an identical older command left in history.
        let start = if !self.prefix.is_empty() {
            lines
                .iter()
                .rposition(|line| line.starts_with(&self.prefix))
        } else if !self.before.is_empty() {
            lines
                .windows(self.before.len())
                .rposition(|rows| rows == self.before)
                .map(|at| at + self.before.len())
        } else {
            Some(0)
        };
        let Some(start) = start else {
            let remains_in_history = lines.windows(self.rows.len()).any(|rows| rows == self.rows);
            return self.end(if remains_in_history {
                TerminalDecision::Sent
            } else {
                TerminalDecision::Lost
            });
        };
        if start + self.rows.len() != lines.len() {
            return self.end(TerminalDecision::Sent);
        }
        let region = lines[start..].join("\n");
        let next = self.tracker.advanced(&region);
        if next.kept() < MIN_KEPT_SHARE {
            return self.end(TerminalDecision::Sent);
        }
        self.tracker = next;
        self.region = region;
        self.rows = lines[start..].to_vec();
        self.before = context(&lines, start);
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

    pub fn region(&self) -> &str {
        &self.region
    }

    pub fn corrections(&self) -> Vec<Correction> {
        self.tracker.corrections()
    }

    fn end(&mut self, decision: TerminalDecision) -> TerminalDecision {
        self.decision = decision;
        decision
    }
}

fn normalize(text: &str) -> String {
    text.replace("\r\n", "\n").replace('\r', "\n")
}

fn screen_rows(buffer: &str) -> Vec<String> {
    // Never begin matching in a partial history row cut by our own tail bound.
    let tail = match buffer.char_indices().rev().nth(MAX_TAIL_CHARS - 1) {
        Some((at, _)) if at > 0 => {
            let tail = &buffer[at..];
            tail.find(['\r', '\n']).map_or("", |end| &tail[end..])
        }
        _ => buffer,
    };
    let text = normalize(tail);
    let mut lines: Vec<String> = text.split('\n').map(str::to_owned).collect();
    while lines.last().is_some_and(|line| line.trim().is_empty()) {
        lines.pop();
    }
    lines
}

fn context(lines: &[String], start: usize) -> Vec<String> {
    lines[start.saturating_sub(2)..start].to_vec()
}

/// Includes whitespace-flexible matching so that physical wraps do not hide the active paste.
fn pasted_span(text: &str, pasted: &str) -> Option<Range<usize>> {
    if pasted.is_empty() {
        return None;
    }
    let exact = text.rfind(pasted).map(|at| at..at + pasted.len());
    let (positions, squeezed): (Vec<_>, String) = text
        .char_indices()
        .filter(|(_, c)| !c.is_whitespace())
        .unzip();
    let needle: String = pasted.chars().filter(|c| !c.is_whitespace()).collect();
    let flexible = if needle.is_empty() {
        None
    } else {
        squeezed.rfind(&needle).map(|at| {
            let first = squeezed[..at].chars().count();
            let last = first + needle.chars().count() - 1;
            let end = positions[last] + text[positions[last]..].chars().next().unwrap().len_utf8();
            positions[first]..end
        })
    };
    match (exact, flexible) {
        (Some(exact), Some(flexible)) => Some(if exact.start >= flexible.start {
            exact
        } else {
            flexible
        }),
        (exact, flexible) => exact.or(flexible),
    }
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
    fn a_historical_occurrence_below_a_different_active_prompt_is_rejected() {
        let buffer = format!(
            "{}\nold output\nPS C:\\work> another command",
            command(PASTED)
        );
        assert!(TerminalTracker::new(&buffer, PASTED).is_none());
        assert!(TerminalTracker::new("PS> ", "   ").is_none());
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
                ("Tyeless".to_owned(), "Typeless".to_owned()),
                ("Open Router".to_owned(), "OpenRouter".to_owned())
            ]
        );
    }

    #[test]
    fn output_after_the_tracked_rows_finishes_without_becoming_a_correction() {
        let mut tracker = TerminalTracker::new(&command(PASTED), PASTED).unwrap();
        tracker.observe(&command(FIRST));
        assert_eq!(tracker.observe(&command(FIXED)), TerminalDecision::Continue);
        let output = format!(
            "{}\r\nUse Typeless and AnotherTool for the local command today\r\nPS C:\\work> ",
            command(FIXED)
        );
        assert_eq!(tracker.observe(&output), TerminalDecision::Sent);
        assert_eq!(tracker.region(), command(FIXED));
        assert_eq!(pairs(&tracker).len(), 2);
        assert_eq!(tracker.observe("PS> unrelated"), TerminalDecision::Sent);
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
    fn a_new_prompt_does_not_overwrite_the_last_command_snapshot() {
        let mut tracker = TerminalTracker::new(&command(PASTED), PASTED).unwrap();
        tracker.observe(&command(FIXED));
        assert_eq!(tracker.observe("PS C:\\work> "), TerminalDecision::Sent);
        assert_eq!(tracker.region(), command(FIXED));
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
        assert_eq!(tracker.region(), format!("PS> {pasted}"));
        assert_eq!(
            tracker.observe(&format!(
                "new history\nPS> {}",
                pasted.replace("Tyeless", "Typeless")
            )),
            TerminalDecision::Continue
        );
        assert_eq!(
            pairs(&tracker),
            [("Tyeless".to_owned(), "Typeless".to_owned())]
        );
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
        assert_eq!(
            pairs(&tracker),
            [("Tyeless".to_owned(), "Typeless".to_owned())]
        );
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
    fn active_edits_win_over_an_identical_old_command() {
        let initial = format!("{}\nold output\n{}", command(PASTED), command(PASTED));
        let mut tracker = TerminalTracker::new(&initial, PASTED).unwrap();
        assert_eq!(
            tracker.observe(&format!(
                "{}\nold output\n{}",
                command(PASTED),
                command(FIRST)
            )),
            TerminalDecision::Continue
        );
        assert_eq!(
            pairs(&tracker),
            [("Tyeless".to_owned(), "Typeless".to_owned())]
        );
    }

    #[test]
    fn appended_output_without_a_new_prompt_is_not_part_of_the_region() {
        let mut tracker = TerminalTracker::new(&command(PASTED), PASTED).unwrap();
        assert_eq!(
            tracker.observe(&format!(
                "{}\nUse Typeless and Open Router for the local command today",
                command(PASTED)
            )),
            TerminalDecision::Sent
        );
        assert!(pairs(&tracker).is_empty());
        assert_eq!(tracker.region(), command(PASTED));
    }

    #[test]
    fn an_ambiguous_change_in_physical_row_count_freezes_previous_corrections() {
        let mut tracker = TerminalTracker::new(&command(PASTED), PASTED).unwrap();
        tracker.observe(&command(FIRST));
        assert_eq!(
            tracker
                .observe("PS C:\\work> Use Typeless and Open Router\n for the local command today"),
            TerminalDecision::Sent
        );
        assert_eq!(tracker.region(), command(FIRST));
        assert_eq!(
            pairs(&tracker),
            [("Tyeless".to_owned(), "Typeless".to_owned())]
        );
    }

    #[test]
    fn an_unprefixed_command_uses_its_previous_row_as_an_anchor() {
        let mut tracker = TerminalTracker::new(&format!("input:\n{PASTED}"), PASTED).unwrap();
        assert_eq!(
            tracker.observe(&format!("input:\n{FIRST}")),
            TerminalDecision::Continue
        );
        assert_eq!(
            pairs(&tracker),
            [("Tyeless".to_owned(), "Typeless".to_owned())]
        );
        assert_eq!(
            tracker.observe(&format!("different context:\n{FIXED}")),
            TerminalDecision::Lost
        );
    }

    #[test]
    fn a_wrapped_active_paste_wins_over_an_exact_older_match() {
        let buffer = format!(
            "{}\noutput\nPS> Use Tyeless and Open Router\n for the local command today",
            command(PASTED)
        );
        let mut tracker = TerminalTracker::new(&buffer, PASTED).unwrap();
        assert_eq!(
            tracker.region(),
            "PS> Use Tyeless and Open Router\n for the local command today"
        );
        assert_eq!(
            tracker.observe(&format!(
                "{}\noutput\nPS> Use Typeless and Open Router\n for the local command today",
                command(PASTED)
            )),
            TerminalDecision::Continue
        );
        assert_eq!(
            pairs(&tracker),
            [("Tyeless".to_owned(), "Typeless".to_owned())]
        );
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
        assert_eq!(
            pairs(&tracker),
            [("Tyeless".to_owned(), "Typeless".to_owned())]
        );
    }

    #[test]
    fn only_the_last_whitespace_variant_in_one_row_is_tracked() {
        let pasted = "Please use Tyeless command today";
        let buffer = format!("PS> {pasted}; Please use  Tyeless command today");
        let mut tracker = TerminalTracker::new(&buffer, pasted).unwrap();
        assert_eq!(
            tracker.observe(
                "PS> Please use Tyeless command today; Please use  Typeless command today"
            ),
            TerminalDecision::Continue
        );
        assert_eq!(
            pairs(&tracker),
            [("Tyeless".to_owned(), "Typeless".to_owned())]
        );
    }
}
