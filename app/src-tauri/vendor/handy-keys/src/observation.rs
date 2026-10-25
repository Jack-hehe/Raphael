//! Transient, opt-in edit observations. No event is logged or persisted.

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::OnceLock;
use std::time::{Duration, Instant};

/// An edit in a field explicitly approved by the caller.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum EditEvent {
    Ascii(u8),
    Backspace,
    WordBackspace,
    /// A same-field edit whose committed text must be read from the vetted UIA provider.
    Resync,
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

/// The hook thread's message loop must have run this recently for the hooks to count as alive.
/// The loop wakes every 10 ms, so this is far above a healthy gap and well under the 500 ms
/// between two reads of a watched field: a dead hook cannot stay unnoticed through a read.
pub(crate) const HOOK_HEARTBEAT_MAX_AGE_MS: u64 = 250;

/// Milliseconds on a monotonic clock that starts at 1, so that 0 can mean "never".
pub(crate) fn monotonic_ms() -> u64 {
    static START: OnceLock<Instant> = OnceLock::new();
    let start = *START.get_or_init(Instant::now);
    u64::try_from(start.elapsed().as_millis())
        .unwrap_or(u64::MAX - 1)
        .saturating_add(1)
}

/// Shows that the low-level hooks are being serviced. The input counter alone cannot: when the
/// hook never started or died it stands still, which looks exactly like a quiet keyboard, and
/// every guard built on "no input happened" would be blind without saying so.
///
/// Two independent signals, both written only by the hook thread:
/// - `loop_ms`: when its message loop last ran (every 10 ms). Without that loop no hook callback
///   can run at all. It is checked every time the hooks are relied on, so a hook thread that
///   stops during a watch ends the watch.
/// - `key_events`: how many keyboard events reached the hook procedure. Windows silently removes
///   a low-level hook that is too slow, and the loop runs on regardless; only an event that
///   arrives proves the procedure is still installed. Raphael's own paste keys count: they pass
///   the hook like any other input. It is checked against the position taken before a paste, so
///   it vouches for the hook at the time of the paste (and later checks of the checkpoint), but a
///   hook removed afterwards, while the loop still runs, is not noticed until the next paste.
pub(crate) struct HookHeartbeat {
    loop_ms: AtomicU64,
    key_events: AtomicU64,
}

impl HookHeartbeat {
    pub(crate) const fn new() -> Self {
        Self {
            loop_ms: AtomicU64::new(0),
            key_events: AtomicU64::new(0),
        }
    }

    /// The hook thread's message loop ran at `now_ms`.
    pub(crate) fn beat(&self, now_ms: u64) {
        self.loop_ms.store(now_ms, Ordering::Release);
    }

    /// A keyboard event reached the hook procedure, whatever its origin.
    pub(crate) fn key_event(&self) {
        self.key_events.fetch_add(1, Ordering::AcqRel);
    }

    /// A position in the stream of keyboard events, to ask later whether more arrived.
    pub(crate) fn mark(&self) -> u64 {
        self.key_events.load(Ordering::Acquire)
    }

    /// Whether the message loop ran within [`HOOK_HEARTBEAT_MAX_AGE_MS`] of `now_ms`. False before
    /// its first run. A reading taken a moment before the beat it races with counts as fresh.
    pub(crate) fn loop_alive(&self, now_ms: u64) -> bool {
        let last = self.loop_ms.load(Ordering::Acquire);
        last != 0 && now_ms.saturating_sub(last) <= HOOK_HEARTBEAT_MAX_AGE_MS
    }

    /// Whether a keyboard event reached the hook procedure after `mark` was taken.
    pub(crate) fn saw_events_since(&self, mark: u64) -> bool {
        self.key_events.load(Ordering::Acquire) > mark
    }
}

/// Non-text evidence that no input or native focus change occurred since a paste began.
#[derive(Clone)]
pub(crate) struct InputStamp {
    pub(crate) hwnd: isize,
    pub(crate) focus: isize,
    pub(crate) serial: u64,
    pub(crate) started: Instant,
    /// [`HookHeartbeat::mark`] when the stamp was taken.
    pub(crate) hook_mark: u64,
}

impl InputStamp {
    /// True only while the hooks have proven themselves alive since the stamp (the loop runs, and
    /// the paste's own keys reached the procedure), no physical or unknown input was counted,
    /// native focus is unchanged, and the watch limit has not passed. A counter that stands
    /// still proves nothing unless the hook that moves it is known to work.
    pub(crate) fn is_current(
        &self,
        hwnd: isize,
        focus: isize,
        serial: u64,
        now: Instant,
        hooks: &HookHeartbeat,
        now_ms: u64,
    ) -> bool {
        hooks.loop_alive(now_ms)
            && hooks.saw_events_since(self.hook_mark)
            && serial == self.serial
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
        // Ctrl+M and Ctrl+J are an Enter to a shell, too.
        0x4D | 0x4A if chord => Some(EditEvent::Invalidated),
        _ => None,
    }
}

/// Terminal edits either reconstruct a literal US-layout edit or request authoritative UIA text.
/// Modifier presses alone do not lose a paste; their eventual chord determines the operation.
pub(crate) fn terminal_key(
    vk: u16,
    down: bool,
    shift: bool,
    caps: bool,
    control: bool,
    other_modifier: bool,
    ascii: bool,
) -> Option<EditEvent> {
    if !down || matches!(vk, 0x10..=0x12 | 0xA0..=0xA5) {
        return None;
    }
    if vk == 0x09 || (control && matches!(vk, 0x4D | 0x4A)) {
        return Some(EditEvent::Invalidated);
    }
    // Enter never authorizes reading a post-submit echo, including on an IME layout.
    if vk == 0x0D {
        return Some(EditEvent::Enter);
    }
    if ascii && control && vk == 0x08 && !other_modifier && !shift {
        return Some(EditEvent::WordBackspace);
    }
    if control || other_modifier || !ascii {
        return Some(EditEvent::Resync);
    }
    edit_key(vk, down, shift, caps, false).map(|event| {
        if event == EditEvent::Invalidated {
            EditEvent::Resync
        } else {
            event
        }
    })
}

/// What the focused window's IME says when asked from outside its process. `ImmGetContext`
/// cannot do that (it only answers for the calling thread's own windows), so the question goes
/// to the thread's default IME window as `WM_IME_CONTROL` / `IMC_GETOPENSTATUS`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum ImeAnswer {
    /// The thread has no IME window: nothing there can compose.
    NoImeWindow,
    Closed,
    Open,
    /// The IME window did not answer in time (busy, hung, or out of reach).
    Unknown,
}

/// Whether Enter may be a plain submission, as [`submission_key`] wants it: `Some(false)` only
/// when nothing can be composing. The plain US layout has no IME. For any other layout only a
/// closed IME proves it; an open one may be composing, and its composition string cannot be
/// read from here, so that and every other answer stays unknown and Enter invalidates.
pub(crate) fn composition_state(us_layout: bool, ime: ImeAnswer) -> Option<bool> {
    (us_layout || ime == ImeAnswer::Closed).then_some(false)
}

/// Whether keys can be rebuilt into ASCII text: no IME may sit between the keyboard and the
/// field. An IME that cannot be asked counts as in the way.
pub(crate) fn ascii_capture_allowed(ime: ImeAnswer) -> bool {
    matches!(ime, ImeAnswer::NoImeWindow | ImeAnswer::Closed)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn terminal_chords_paste_and_ime_request_text_without_guessing_characters() {
        assert_eq!(
            terminal_key(0x11, true, false, false, true, false, true),
            None
        );
        assert_eq!(
            terminal_key(0x08, true, false, false, true, false, true),
            Some(EditEvent::WordBackspace)
        );
        for (vk, control, ascii) in [
            (0x56, true, true),
            (0x2D, false, true),
            (0x41, false, false),
            (0x08, true, false),
        ] {
            assert_eq!(
                terminal_key(vk, true, false, false, control, false, ascii),
                Some(EditEvent::Resync)
            );
        }
        assert_eq!(
            terminal_key(0x41, true, true, false, false, false, true),
            Some(EditEvent::Ascii(b'A'))
        );
        assert_eq!(
            terminal_key(0x0D, true, false, false, false, false, false),
            Some(EditEvent::Enter)
        );
    }

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
    fn control_m_and_control_j_are_enter_to_a_shell_and_end_the_observation() {
        for vk in [0x4D, 0x4A] {
            assert_eq!(
                submission_key(vk, true, false, true, Some(false)),
                Some(EditEvent::Invalidated)
            );
            // The letters alone are typing, which a submission observer never buffers.
            assert_eq!(submission_key(vk, true, false, false, Some(false)), None);
            assert_eq!(submission_key(vk, false, false, true, Some(false)), None);
        }
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

    /// A heartbeat whose loop ran at 1000 ms, and a stamp taken before one keyboard event.
    fn live_hooks() -> (HookHeartbeat, InputStamp, Instant) {
        let now = Instant::now();
        let hooks = HookHeartbeat::new();
        hooks.beat(1_000);
        let stamp = InputStamp {
            hwnd: 7,
            focus: 8,
            serial: 12,
            started: now,
            hook_mark: hooks.mark(),
        };
        // The paste's own keys reach the hook procedure after the stamp.
        hooks.key_event();
        (hooks, stamp, now)
    }

    #[test]
    fn checkpoint_rejects_input_window_focus_and_deadline_changes() {
        let (hooks, stamp, now) = live_hooks();
        let current =
            |hwnd, focus, serial, at| stamp.is_current(hwnd, focus, serial, at, &hooks, 1_000);
        assert!(current(7, 8, 12, now + Duration::from_secs(1)));
        assert!(!current(7, 8, 13, now));
        assert!(!current(9, 8, 12, now));
        assert!(!current(7, 9, 12, now));
        assert!(!current(7, 8, 12, now + Duration::from_secs(60)));
        assert!(!current(7, 8, 12, now - Duration::from_secs(1)));
        let unknown = InputStamp {
            hwnd: 0,
            focus: 0,
            ..stamp.clone()
        };
        assert!(!unknown.is_current(0, 0, 12, now, &hooks, 1_000));
    }

    #[test]
    fn a_hook_that_never_ran_is_not_alive() {
        let hooks = HookHeartbeat::new();
        assert!(!hooks.loop_alive(0));
        assert!(!hooks.loop_alive(1));
        assert!(!hooks.loop_alive(1_000_000));
        assert!(monotonic_ms() > 0, "0 is reserved for 'never'");
    }

    #[test]
    fn the_hook_loop_is_alive_only_shortly_after_its_last_beat() {
        let hooks = HookHeartbeat::new();
        hooks.beat(1_000);
        assert!(hooks.loop_alive(1_000));
        assert!(hooks.loop_alive(1_000 + HOOK_HEARTBEAT_MAX_AGE_MS));
        assert!(!hooks.loop_alive(1_000 + HOOK_HEARTBEAT_MAX_AGE_MS + 1));
        // A reader that took its clock just before the beat it races with is not punished.
        assert!(hooks.loop_alive(999));
        hooks.beat(2_000);
        assert!(hooks.loop_alive(2_100));
    }

    #[test]
    fn keyboard_events_count_only_after_the_mark() {
        let hooks = HookHeartbeat::new();
        hooks.key_event();
        let mark = hooks.mark();
        assert!(!hooks.saw_events_since(mark));
        hooks.key_event();
        assert!(hooks.saw_events_since(mark));
        assert!(!hooks.saw_events_since(hooks.mark()));
    }

    #[test]
    fn a_stamp_needs_a_live_loop_and_an_event_since_it_was_taken() {
        let (hooks, stamp, now) = live_hooks();
        assert!(stamp.is_current(7, 8, 12, now, &hooks, 1_000));

        // The hook thread stopped: the counter stands still, but that proves nothing.
        assert!(!stamp.is_current(7, 8, 12, now, &hooks, 1_000 + HOOK_HEARTBEAT_MAX_AGE_MS + 1));

        // The loop runs but no key, not even the paste's own, reached the procedure since.
        let silent = HookHeartbeat::new();
        silent.beat(1_000);
        let stamp = InputStamp {
            hook_mark: silent.mark(),
            ..stamp
        };
        assert!(!stamp.is_current(7, 8, 12, now, &silent, 1_000));
        silent.key_event();
        assert!(stamp.is_current(7, 8, 12, now, &silent, 1_000));

        // The hook never started at all.
        let never = HookHeartbeat::new();
        never.key_event();
        assert!(!stamp.is_current(7, 8, 12, now, &never, 1_000));
    }

    #[test]
    fn an_ime_that_is_closed_cannot_be_composing_and_no_other_answer_proves_it() {
        for answer in [ImeAnswer::Open, ImeAnswer::Unknown, ImeAnswer::NoImeWindow] {
            let composing = composition_state(false, answer);
            assert_ne!(composing, Some(false), "{answer:?}");
            assert_eq!(
                submission_key(0x0D, true, false, false, composing),
                Some(EditEvent::Invalidated),
                "{answer:?}"
            );
        }
        assert_eq!(composition_state(false, ImeAnswer::Closed), Some(false));
        assert_eq!(
            submission_key(
                0x0D,
                true,
                false,
                false,
                composition_state(false, ImeAnswer::Closed)
            ),
            Some(EditEvent::Enter)
        );
    }

    #[test]
    fn the_us_layout_has_no_ime_whatever_the_ime_window_says() {
        for answer in [
            ImeAnswer::NoImeWindow,
            ImeAnswer::Closed,
            ImeAnswer::Open,
            ImeAnswer::Unknown,
        ] {
            assert_eq!(composition_state(true, answer), Some(false), "{answer:?}");
        }
    }

    #[test]
    fn ascii_capture_needs_proof_that_no_ime_is_in_the_way() {
        assert!(ascii_capture_allowed(ImeAnswer::NoImeWindow));
        assert!(ascii_capture_allowed(ImeAnswer::Closed));
        assert!(!ascii_capture_allowed(ImeAnswer::Open));
        assert!(!ascii_capture_allowed(ImeAnswer::Unknown));
    }
}
