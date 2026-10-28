//! Split a rendered terminal by persistent vertical borders before removing its TUI frame.

use std::collections::BTreeMap;

/// Terminal cells, not Unicode scalar offsets. Ambiguous-width box drawing stays one cell.
/// Wide CJK/fullwidth/emoji ranges occupy two; common combining marks occupy none.
fn cells(c: char) -> usize {
    match c {
        '\u{0300}'..='\u{036f}'
        | '\u{1ab0}'..='\u{1aff}'
        | '\u{1dc0}'..='\u{1dff}'
        | '\u{200b}'..='\u{200f}'
        | '\u{20d0}'..='\u{20ff}'
        | '\u{fe00}'..='\u{fe0f}'
        | '\u{fe20}'..='\u{fe2f}'
        | '\u{e0100}'..='\u{e01ef}' => 0,
        '\u{1100}'..='\u{115f}'
        | '\u{2329}'..='\u{232a}'
        | '\u{2e80}'..='\u{a4cf}'
        | '\u{ac00}'..='\u{d7a3}'
        | '\u{f900}'..='\u{faff}'
        | '\u{fe10}'..='\u{fe19}'
        | '\u{fe30}'..='\u{fe6f}'
        | '\u{ff01}'..='\u{ff60}'
        | '\u{ffe0}'..='\u{ffe6}'
        | '\u{1f300}'..='\u{1faff}'
        | '\u{20000}'..='\u{3fffd}' => 2,
        _ => 1,
    }
}

pub(super) fn width(rows: &[String]) -> usize {
    rows.iter()
        .map(|row| row.chars().map(cells).sum())
        .max()
        .unwrap_or(0)
}

fn vertical(c: char) -> bool {
    // Vertical strokes, corners and junctions; never a purely horizontal frame rule.
    matches!(c, '│' | '┃' | '┆' | '┇' | '┊' | '┋' | '╎' | '╏' | '║'
        | '┌'..='╋' | '╒'..='╬' | '╭'..='╰')
}

/// Each result is a column screen with the original row indexes, including blank rows. Borders
/// must be present on most nonempty rows, so isolated input frames do not partition scrollback.
pub(super) fn columns(text: &str) -> Vec<Vec<String>> {
    let lines: Vec<&str> = text.split('\n').collect();
    let occupied = lines.iter().filter(|line| !line.trim().is_empty()).count();
    let mut counts = BTreeMap::<usize, usize>::new();
    for line in &lines {
        let mut column = 0;
        for c in line.chars() {
            if vertical(c) {
                *counts.entry(column).or_default() += 1;
            }
            column += if c == '\t' { 8 - column % 8 } else { cells(c) };
        }
    }
    let borders: Vec<usize> = counts
        .into_iter()
        .filter(|&(_, count)| count >= 3 && count * 5 >= occupied * 3)
        .map(|(column, _)| column)
        .collect();
    if borders.is_empty() {
        return vec![lines.iter().map(|line| (*line).to_owned()).collect()];
    }
    // Adjacent borders (herdr's ││) are a single boundary, not an empty pane.
    let mut cuts: Vec<std::ops::Range<usize>> = Vec::new();
    for column in borders {
        if let Some(last) = cuts.last_mut()
            && last.end == column
        {
            last.end += 1;
        } else {
            cuts.push(column..column + 1);
        }
    }
    // Bound the row/column matrix even when an unusual screen has thousands of blank rows
    // below many tiny framed cells. Ambiguous extreme layouts are not safe learning sources.
    if cuts.len() >= 32 {
        return Vec::new();
    }
    let mut panes = vec![vec![String::new(); lines.len()]; cuts.len() + 1];
    for (row, line) in lines.iter().enumerate() {
        let (mut column, mut pane) = (0, 0);
        for c in line.chars() {
            while cuts.get(pane).is_some_and(|cut| column >= cut.end) {
                pane += 1;
            }
            let width = if c == '\t' { 8 - column % 8 } else { cells(c) };
            if cuts.get(pane).is_none_or(|cut| column + width <= cut.start) {
                panes[pane][row].push(c);
            }
            column += width;
        }
    }
    // Only zero-width exterior strips are frame edges. An empty real pane still has width and
    // must keep its index when a neighboring CLI clears its output.
    let screen_width = lines
        .iter()
        .map(|line| {
            line.chars().fold(0, |column, c| {
                column + if c == '\t' { 8 - column % 8 } else { cells(c) }
            })
        })
        .max()
        .unwrap_or(0);
    let frame_only = |rows: &Vec<String>| {
        rows.iter().all(|row| {
            row.chars()
                .all(|c| c.is_whitespace() || matches!(c, '\u{2500}'..='\u{259f}'))
        })
    };
    // Some UIA rows end one cell past the common border (or pad a header beyond it).
    if cuts.last().is_some_and(|cut| cut.end >= screen_width)
        || panes.last().is_some_and(frame_only)
            && cuts
                .last()
                .is_some_and(|cut| screen_width.saturating_sub(cut.end) <= 2)
    {
        panes.pop();
    }
    if cuts.first().is_some_and(|cut| cut.start == 0)
        || panes.first().is_some_and(frame_only) && cuts.first().is_some_and(|cut| cut.start <= 2)
    {
        panes.remove(0);
    }
    panes
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn wide_cells_and_combining_marks_do_not_move_a_border() {
        let panes = columns("中文│alpha│日本\n測試│bravo│韓文\ne\u{301}   │delta│資料");
        assert_eq!(panes.len(), 3);
        assert_eq!(panes[1], ["alpha", "bravo", "delta"]);
    }

    #[test]
    fn corners_and_double_borders_share_a_column() {
        let panes = columns("side│┌────┐│tail\nleft││text││last\nnext│└────┘│done");
        assert_eq!(panes.len(), 3);
        assert_eq!(panes[1][1], "text");
    }

    #[test]
    fn tmux_and_zellij_style_borders_do_not_mix_neighboring_rows() {
        let panes =
            columns("one ║╔════╗║ two\none ║║text║║ two\none ║╚════╝║ two\none ║╟────╢║ two");
        assert_eq!(panes.len(), 3);
        assert_eq!(panes[1][1], "text");
    }

    #[test]
    fn extreme_column_counts_are_bounded_before_allocating_a_row_matrix() {
        let row = "a│".repeat(100);
        assert!(columns(&format!("{row}\n{row}\n{row}\n{}", "\n".repeat(1000))).is_empty());
    }
}
