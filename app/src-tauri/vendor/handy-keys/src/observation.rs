//! Transient, opt-in edit observations. No event is logged or persisted.

use std::time::{Duration, Instant};

/// An edit in a field explicitly approved by the caller.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum EditEvent {
    Ascii(u8),
    Backspace,
    Delete,
    Left {
        shift: bool,
    },
    Right {
        shift: bool,
    },
    /// Submit without inserting a line break. Later events are ignored.
    Enter,
    /// Reconstruction is no longer safe; discard the entire observation.
    Invalidated,
}

pub(crate) const MAX_EVENTS: usize = 512;
pub(crate) const MAX_WATCH: Duration = Duration::from_secs(60);

/// Marks Raphael's own paste input, which must not invalidate its paste checkpoint.
pub const RAPHAEL_INPUT_MARKER: usize = 0x5250_484C;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum InputOrigin {
    Physical,
    OwnInjected,
    OtherInjected,
}

pub(crate) fn keyboard_origin(flags: u32, extra: usize) -> InputOrigin {
    input_origin(physical_keyboard(flags), extra)
}

pub(crate) fn mouse_origin(flags: u32, extra: usize) -> InputOrigin {
    input_origin(physical_mouse(flags), extra)
}

fn input_origin(physical: bool, extra: usize) -> InputOrigin {
    if physical {
        InputOrigin::Physical
    } else if extra == RAPHAEL_INPUT_MARKER {
        InputOrigin::OwnInjected
    } else {
        InputOrigin::OtherInjected
    }
}

/// Non-text evidence that no input or native focus change occurred since a paste began.
#[derive(Clone)]
pub(crate) struct InputStamp {
    pub(crate) hwnd: isize,
    pub(crate) focus: isize,
    pub(crate) serial: u64,
    pub(crate) started: Instant,
}

impl InputStamp {
    pub(crate) fn is_current(&self, hwnd: isize, focus: isize, serial: u64, now: Instant) -> bool {
        serial == self.serial
            && now >= self.started
            && permitted(
                self.hwnd,
                self.focus,
                hwnd,
                focus,
                self.started + MAX_WATCH,
                now,
                true,
            )
    }
}

pub(crate) fn deadline_after(now: Instant, duration: Duration) -> Option<Instant> {
    if duration.is_zero() {
        None
    } else {
        now.checked_add(duration.min(MAX_WATCH))
    }
}

pub(crate) fn physical_keyboard(flags: u32) -> bool {
    flags & (0x10 | 0x02) == 0
}

pub(crate) fn physical_mouse(flags: u32) -> bool {
    flags & 3 == 0
}

pub(crate) struct EditBuffer {
    events: Vec<EditEvent>,
    complete: bool,
    invalid: bool,
}

impl EditBuffer {
    pub(crate) fn new() -> Self {
        Self {
            events: Vec::with_capacity(MAX_EVENTS),
            complete: false,
            invalid: false,
        }
    }

    pub(crate) fn push(&mut self, event: EditEvent) {
        if self.invalid || self.complete {
            return;
        }
        if event == EditEvent::Invalidated || self.events.len() == MAX_EVENTS {
            self.invalidate();
            return;
        }
        self.complete = event == EditEvent::Enter;
        self.events.push(event);
    }

    pub(crate) fn invalidate(&mut self) {
        self.events.fill(EditEvent::Invalidated);
        self.events.clear();
        self.invalid = true;
    }

    pub(crate) fn drain(&mut self) -> Vec<EditEvent> {
        if self.invalid {
            return vec![EditEvent::Invalidated];
        }
        let drained = self.events.clone();
        self.events.fill(EditEvent::Invalidated);
        self.events.clear();
        drained
    }

    pub(crate) fn complete(&self) -> bool {
        self.complete
    }

    pub(crate) fn invalidated(&self) -> bool {
        self.invalid
    }
}

pub(crate) fn permitted(
    expected_hwnd: isize,
    expected_focus: isize,
    hwnd: isize,
    focus: isize,
    deadline: Instant,
    now: Instant,
    safe: bool,
) -> bool {
    expected_hwnd != 0
        && expected_focus != 0
        && hwnd == expected_hwnd
        && focus == expected_focus
        && now < deadline
        && safe
}

pub(crate) fn ascii_key(vk: u16, shift: bool, caps: bool) -> Option<u8> {
    match vk {
        0x41..=0x5A => Some(if shift ^ caps {
            vk as u8
        } else {
            vk as u8 + 32
        }),
        0x30..=0x39 => Some(if shift {
            b")!@#$%^&*("[(vk - 0x30) as usize]
        } else {
            vk as u8
        }),
        0x20 => Some(b' '),
        0xBA..=0xC0 => {
            let at = (vk - 0xBA) as usize;
            Some(if shift {
                b":+<_>?~"[at]
            } else {
                b";=,-./`"[at]
            })
        }
        0xDB..=0xDE => {
            let at = (vk - 0xDB) as usize;
            Some(if shift { b"{|}\""[at] } else { b"[\\]'"[at] })
        }
        _ => None,
    }
}

/// Only unmodified editing and Shift selection are reconstructable.
pub(crate) fn edit_key(
    vk: u16,
    down: bool,
    shift: bool,
    caps: bool,
    chord: bool,
) -> Option<EditEvent> {
    if chord || matches!(vk, 0x11 | 0x12 | 0x5B | 0x5C | 0xA2..=0xA5) {
        return Some(EditEvent::Invalidated);
    }
    if !down || matches!(vk, 0x10 | 0xA0 | 0xA1) {
        return None;
    }
    Some(match vk {
        0x08 if !shift => EditEvent::Backspace,
        0x2E if !shift => EditEvent::Delete,
        0x25 => EditEvent::Left { shift },
        0x27 => EditEvent::Right { shift },
        0x0D if !shift => EditEvent::Enter,
        _ => ascii_key(vk, shift, caps)
            .map(EditEvent::Ascii)
            .unwrap_or(EditEvent::Invalidated),
    })
}

pub(crate) fn submission_key(
    vk: u16,
    down: bool,
    shift: bool,
    chord: bool,
    composing: Option<bool>,
) -> Option<EditEvent> {
    if !down {
        return None;
    }
    match vk {
        0x09 => Some(EditEvent::Invalidated),
        0x0D if !shift && !chord && composing == Some(false) => Some(EditEvent::Enter),
        0x0D => Some(EditEvent::Invalidated),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn shift_punctuation_and_caps_match_us_ascii() {
        assert_eq!(ascii_key(0x41, false, false), Some(b'a'));
        assert_eq!(ascii_key(0x41, true, false), Some(b'A'));
        assert_eq!(ascii_key(0x41, true, true), Some(b'a'));
        assert_eq!(ascii_key(0x31, true, false), Some(b'!'));
        assert_eq!(ascii_key(0xBA, true, false), Some(b':'));
        assert_eq!(ascii_key(0xDE, false, false), Some(b'\''));
        assert_eq!(ascii_key(0xE5, false, false), None);
        assert_eq!(ascii_key(0x0D, false, false), None);
    }

    #[test]
    fn both_window_and_focus_and_strict_deadline_are_required() {
        let now = Instant::now();
        let end = now + Duration::from_secs(1);
        assert!(permitted(7, 8, 7, 8, end, now, true));
        assert!(!permitted(7, 8, 9, 8, end, now, true));
        assert!(!permitted(7, 8, 7, 9, end, now, true));
        assert!(!permitted(7, 8, 7, 8, end, end, true));
        assert!(!permitted(7, 8, 7, 8, end, now, false));
        assert!(!permitted(0, 0, 0, 0, end, now, true));
    }

    #[test]
    fn enter_freezes_the_buffer_after_preserving_pre_submit_edits() {
        let mut buffer = EditBuffer::new();
        buffer.push(EditEvent::Ascii(b'x'));
        buffer.push(EditEvent::Backspace);
        buffer.push(EditEvent::Enter);
        buffer.push(EditEvent::Ascii(b'z'));
        assert!(buffer.complete());
        assert_eq!(
            buffer.drain(),
            [
                EditEvent::Ascii(b'x'),
                EditEvent::Backspace,
                EditEvent::Enter
            ]
        );
        assert!(buffer.drain().is_empty());
    }

    #[test]
    fn invalidation_discards_pending_text_and_remains_terminal() {
        let mut buffer = EditBuffer::new();
        buffer.push(EditEvent::Ascii(b'x'));
        buffer.invalidate();
        assert!(buffer.invalidated());
        buffer.push(EditEvent::Ascii(b'z'));
        assert_eq!(buffer.drain(), [EditEvent::Invalidated]);
        assert_eq!(buffer.drain(), [EditEvent::Invalidated]);
    }

    #[test]
    fn overflow_discards_every_pending_edit() {
        let mut buffer = EditBuffer::new();
        for _ in 0..=MAX_EVENTS {
            buffer.push(EditEvent::Ascii(b'x'));
        }
        assert_eq!(buffer.drain(), [EditEvent::Invalidated]);
    }

    #[test]
    fn chords_ime_tab_and_unknown_caret_commands_invalidate() {
        for vk in [0x09, 0x11, 0x12, 0xA5, 0x24, 0x26, 0xE5, 0xE7] {
            assert_eq!(
                edit_key(vk, true, false, false, false),
                Some(EditEvent::Invalidated)
            );
        }
        assert_eq!(
            edit_key(0x56, true, false, false, true),
            Some(EditEvent::Invalidated)
        );
        assert_eq!(
            edit_key(0x0D, true, true, false, false),
            Some(EditEvent::Invalidated)
        );
        assert_eq!(
            edit_key(0x41, true, true, false, false),
            Some(EditEvent::Ascii(b'A'))
        );
        assert_eq!(
            edit_key(0x25, true, true, false, false),
            Some(EditEvent::Left { shift: true })
        );
        assert_eq!(edit_key(0x41, false, false, false, false), None);
    }

    #[test]
    fn requested_watch_is_clamped_before_its_deadline() {
        let now = Instant::now();
        let deadline = deadline_after(now, Duration::from_secs(300)).unwrap();
        assert_eq!(deadline_after(now, Duration::ZERO), None);
        assert_eq!(
            deadline_after(now, Duration::from_secs(1)),
            Some(now + Duration::from_secs(1))
        );
        assert!(!permitted(
            1,
            2,
            1,
            2,
            deadline,
            now + Duration::from_secs(60),
            true
        ));
    }

    #[test]
    fn injected_flags_are_never_classified_as_physical_input() {
        assert!(physical_keyboard(0));
        assert!(!physical_keyboard(0x10));
        assert!(!physical_keyboard(0x02));
        assert!(physical_mouse(0));
        assert!(!physical_mouse(1));
        assert!(!physical_mouse(2));
    }

    #[test]
    fn submission_mode_never_buffers_typing_or_ime_text() {
        for vk in [0x41, 0x08, 0x2E, 0x25, 0xE5, 0xE7] {
            assert_eq!(submission_key(vk, true, false, false, Some(true)), None);
        }
        assert_eq!(
            submission_key(0x0D, true, false, false, Some(false)),
            Some(EditEvent::Enter)
        );
        assert_eq!(submission_key(0x0D, false, false, false, Some(false)), None);
        assert_eq!(
            submission_key(0x09, true, false, false, Some(false)),
            Some(EditEvent::Invalidated)
        );
    }

    #[test]
    fn composition_or_unknown_composition_or_modified_enter_is_not_submission() {
        for composing in [Some(true), None] {
            assert_eq!(
                submission_key(0x0D, true, false, false, composing),
                Some(EditEvent::Invalidated)
            );
        }
        assert_eq!(
            submission_key(0x0D, true, true, false, Some(false)),
            Some(EditEvent::Invalidated)
        );
        assert_eq!(
            submission_key(0x0D, true, false, true, Some(false)),
            Some(EditEvent::Invalidated)
        );
    }

    #[test]
    fn only_known_injected_paste_input_is_excluded_from_checkpoint_serials() {
        assert_eq!(keyboard_origin(0, 0), InputOrigin::Physical);
        assert_eq!(
            keyboard_origin(0, RAPHAEL_INPUT_MARKER),
            InputOrigin::Physical
        );
        assert_eq!(
            keyboard_origin(0x10, RAPHAEL_INPUT_MARKER),
            InputOrigin::OwnInjected
        );
        assert_eq!(keyboard_origin(0x02, 0), InputOrigin::OtherInjected);
        assert_eq!(mouse_origin(0, RAPHAEL_INPUT_MARKER), InputOrigin::Physical);
        assert_eq!(
            mouse_origin(1, RAPHAEL_INPUT_MARKER),
            InputOrigin::OwnInjected
        );
        assert_eq!(mouse_origin(2, 0), InputOrigin::OtherInjected);
    }

    #[test]
    fn checkpoint_rejects_input_window_focus_and_deadline_changes() {
        let now = Instant::now();
        let stamp = InputStamp {
            hwnd: 7,
            focus: 8,
            serial: 12,
            started: now,
        };
        assert!(stamp.is_current(7, 8, 12, now + Duration::from_secs(1)));
        assert!(!stamp.is_current(7, 8, 13, now));
        assert!(!stamp.is_current(9, 8, 12, now));
        assert!(!stamp.is_current(7, 9, 12, now));
        assert!(!stamp.is_current(7, 8, 12, now + Duration::from_secs(60)));
        assert!(!stamp.is_current(7, 8, 12, now - Duration::from_secs(1)));
        let unknown = InputStamp {
            hwnd: 0,
            focus: 0,
            ..stamp
        };
        assert!(!unknown.is_current(0, 0, 12, now));
    }
}
