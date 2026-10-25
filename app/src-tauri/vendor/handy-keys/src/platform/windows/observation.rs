//! A single, explicitly scoped edit observer beside the existing hotkey listener.
//!
//! The caller must vet UIA password/focus identity and check the paste checkpoint before opting in.
//! Hooks never block on a mutex, call UIA, log input, or suppress an input event.

use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant};

use windows::Win32::Foundation::{HWND, LPARAM, WPARAM};
use windows::Win32::UI::Input::Ime::ImmGetDefaultIMEWnd;
use windows::Win32::UI::Input::KeyboardAndMouse::{
    GetAsyncKeyState, GetKeyState, GetKeyboardLayout,
};
use windows::Win32::UI::WindowsAndMessaging::{
    GetForegroundWindow, GetGUIThreadInfo, GetWindowLongW, GetWindowThreadProcessId,
    SendMessageTimeoutW, ES_PASSWORD, GUITHREADINFO, GUI_INMENUMODE, GUI_INMOVESIZE,
    GUI_POPUPMENUMODE, GWL_STYLE, KBDLLHOOKSTRUCT, MSLLHOOKSTRUCT, SMTO_ABORTIFHUNG,
    WM_IME_CONTROL, WM_KEYDOWN, WM_SYSKEYDOWN,
};

use crate::observation::{
    ascii_capture_allowed, composition_state, deadline_after, edit_key, keyboard_origin,
    monotonic_ms, mouse_origin, permitted, submission_key, terminal_key, EditBuffer, EditEvent, HookHeartbeat,
    ImeAnswer, InputOrigin, InputStamp,
};

#[derive(Clone, Copy, PartialEq, Eq)]
enum Mode {
    Edits,
    Submissions,
    Terminal,
}

struct Session {
    mode: Mode,
    hwnd: isize,
    focus: isize,
    deadline: Instant,
    generation: u64,
    invalid: AtomicBool,
    complete: AtomicBool,
    buffer: Mutex<EditBuffer>,
}

static ACTIVE: AtomicBool = AtomicBool::new(false);
static GENERATION: AtomicU64 = AtomicU64::new(0);
static INPUT_SERIAL: AtomicU64 = AtomicU64::new(0);
static SESSION: OnceLock<Mutex<Option<Arc<Session>>>> = OnceLock::new();
/// Stamped by the hook thread; read to tell a quiet keyboard from hooks that are not running.
static HEARTBEAT: HookHeartbeat = HookHeartbeat::new();

/// The plain US keyboard layout: the only one whose keys map to ASCII one to one, and which has
/// no IME.
const US_LAYOUT: u32 = 0x0409_0409;
/// `IMC_GETOPENSTATUS` of `WM_IME_CONTROL` (the windows crate has no constant for it).
const IMC_GETOPENSTATUS: usize = 0x0005;
/// How long a question to another process's IME window may take when it is asked from inside the
/// keyboard hook (for an Enter key), whose own budget is a few hundred milliseconds.
const IME_QUERY_IN_HOOK_MS: u32 = 30;
/// ... and when the learning thread asks while arming an observer, where the app that has just
/// been pasted into may take longer to answer than a hook may wait.
const IME_QUERY_WHILE_ARMING_MS: u32 = 150;

fn registry() -> &'static Mutex<Option<Arc<Session>>> {
    SESSION.get_or_init(|| Mutex::new(None))
}

/// Metadata-only evidence of native focus and input serial immediately before a paste.
#[derive(Clone)]
pub struct InputCheckpoint {
    stamp: InputStamp,
}

impl InputCheckpoint {
    /// True only while the hooks have proven themselves alive since the paste (the hook thread's
    /// loop runs and the paste's own keys reached the hook), no physical or unknown injected
    /// input has occurred, native focus is unchanged, and fewer than 60 seconds have elapsed.
    /// Recheck after arming an observer.
    pub fn is_current(&self) -> bool {
        let before = INPUT_SERIAL.load(Ordering::Acquire);
        let Some(scope) = input_scope(Mode::Submissions) else {
            return false;
        };
        let after = INPUT_SERIAL.load(Ordering::Acquire);
        before == after
            && self.stamp.is_current(
                scope.hwnd,
                scope.focus,
                after,
                Instant::now(),
                &HEARTBEAT,
                monotonic_ms(),
            )
    }

    pub fn started(&self) -> Instant {
        self.stamp.started
    }
}

/// Captures only counter/focus metadata. No text observer or polling thread is started.
/// Requires the existing keyboard listener to be running: a hook that never started or died
/// cannot advance the counter, and a counter that stands still would then look like a quiet
/// keyboard. Without a recent heartbeat there is no checkpoint, so the caller learns nothing.
pub fn input_checkpoint(hwnd: isize) -> Option<InputCheckpoint> {
    if hwnd == 0 || !HEARTBEAT.loop_alive(monotonic_ms()) {
        return None;
    }
    let hook_mark = HEARTBEAT.mark();
    let serial = INPUT_SERIAL.load(Ordering::Acquire);
    let started = Instant::now();
    let scope = input_scope(Mode::Submissions)?;
    if scope.hwnd != hwnd || INPUT_SERIAL.load(Ordering::Acquire) != serial {
        return None;
    }
    Some(InputCheckpoint {
        stamp: InputStamp {
            hwnd,
            focus: scope.focus,
            serial,
            started,
            hook_mark,
        },
    })
}

/// Owns transient edits until drained. Dropping or stopping it discards every pending event.
pub struct EditObservation {
    session: Arc<Session>,
}

/// A callback's cancellation authority over one observation, never its replacement.
#[derive(Clone)]
pub struct EditCancellation {
    session: Arc<Session>,
}

impl EditCancellation {
    /// Clears only this session; late callbacks must not change the global generation.
    pub fn cancel(&self) {
        self.session.invalid.store(true, Ordering::Release);
        self.session
            .buffer
            .lock()
            .unwrap_or_else(|error| error.into_inner())
            .invalidate();
        let mut current = registry().lock().unwrap_or_else(|error| error.into_inner());
        if current
            .as_ref()
            .is_some_and(|item| Arc::ptr_eq(item, &self.session))
        {
            *current = None;
            ACTIVE.store(false, Ordering::Release);
        }
    }
}

impl EditObservation {
    /// Allows a focus callback to stop this observation after the owning field has moved on.
    pub fn cancellation(&self) -> EditCancellation {
        EditCancellation {
            session: self.session.clone(),
        }
    }

    /// The caller must revalidate UIA identity/password before using these events.
    /// Cancellation, an expired deadline, a stopped hook, or any unsafe input yields only
    /// `Invalidated`.
    pub fn drain(&mut self) -> Vec<EditEvent> {
        self.drain_at(monotonic_ms())
    }

    /// [`Self::drain`] at a given reading of the heartbeat clock.
    fn drain_at(&mut self, now_ms: u64) -> Vec<EditEvent> {
        let session = &self.session;
        if session.generation != GENERATION.load(Ordering::Acquire)
            || Instant::now() >= session.deadline
        {
            session.invalid.store(true, Ordering::Release);
        }
        // With the hook thread gone nothing more can be seen: a keyboard that looks quiet may be
        // one whose Enter was missed. What was captured up to a submission stays valid.
        if !session.complete.load(Ordering::Acquire) && !HEARTBEAT.loop_alive(now_ms) {
            session.invalid.store(true, Ordering::Release);
        }
        // Before submit, also check native focus when no hook event happened since the last poll.
        if !session.invalid.load(Ordering::Acquire)
            && !session.complete.load(Ordering::Acquire)
            && !in_scope(session)
        {
            session.invalid.store(true, Ordering::Release);
        }
        let mut buffer = session
            .buffer
            .lock()
            .unwrap_or_else(|error| error.into_inner());
        if session.invalid.load(Ordering::Acquire) {
            buffer.invalidate();
        }
        buffer.drain()
    }

    /// Stops capture and clears pending input immediately.
    pub fn stop(&mut self) {
        self.cancellation().cancel();
    }
}

impl Drop for EditObservation {
    fn drop(&mut self) {
        self.stop();
    }
}

/// Watches at most one vetted native focus/window for at most 60 seconds.
/// Unsupported layouts, an open or unreachable IME, held chords, a stopped keyboard hook, or
/// unknown native focus fail closed.
/// UIA-unavailable applications cannot be vetted by this API alone.
pub fn observe_edits(hwnd: isize, duration: Duration) -> Option<EditObservation> {
    observe(hwnd, duration, Mode::Edits)
}

/// Observes only an unmodified, non-composing Enter in a vetted readable UIA field.
/// It never buffers text and supports non-US layouts and an open IME. An unknown composition
/// state at Enter invalidates the observation rather than treating a candidate commit as submit.
/// Without a running keyboard hook there is nothing to observe with, and no observation starts.
pub fn observe_submissions(hwnd: isize, duration: Duration) -> Option<EditObservation> {
    observe(hwnd, duration, Mode::Submissions)
}

/// Literal terminal edits when provable, otherwise a same-field UIA resynchronization.
pub fn observe_terminal_edits(hwnd: isize, duration: Duration) -> Option<EditObservation> {
    observe(hwnd, duration, Mode::Terminal)
}

fn observe(hwnd: isize, duration: Duration, mode: Mode) -> Option<EditObservation> {
    if hwnd == 0 || !HEARTBEAT.loop_alive(monotonic_ms()) {
        return None;
    }
    cancel_edit_observations();
    let scope = input_scope(mode)?;
    if scope.hwnd != hwnd || (mode == Mode::Edits && (unsafe_modifiers() || held(0x10))) {
        return None;
    }
    // The US layout rules out an IME, but ask anyway: an IME that is open, or cannot be asked,
    // is not an ASCII keyboard. (Once here; the layout is rechecked on every hook event.)
    if mode == Mode::Edits
        && !ascii_capture_allowed(ime_answer(scope.focus, IME_QUERY_WHILE_ARMING_MS))
    {
        return None;
    }
    let mut current = registry().lock().ok()?;
    let session = Arc::new(Session {
        mode,
        hwnd,
        focus: scope.focus,
        deadline: deadline_after(Instant::now(), duration)?,
        generation: GENERATION.load(Ordering::Acquire),
        invalid: AtomicBool::new(false),
        complete: AtomicBool::new(false),
        buffer: Mutex::new(EditBuffer::new()),
    });
    *current = Some(session.clone());
    ACTIVE.store(true, Ordering::Release);
    Some(EditObservation { session })
}

/// Cancels and clears all observations, including on settings changes or new dictation.
/// The generation changes before locking, so an in-flight hook cannot append valid input.
pub fn cancel_edit_observations() {
    GENERATION.fetch_add(1, Ordering::AcqRel);
    ACTIVE.store(false, Ordering::Release);
    let mut current = registry().lock().unwrap_or_else(|error| error.into_inner());
    if let Some(session) = current.take() {
        session.invalid.store(true, Ordering::Release);
        session
            .buffer
            .lock()
            .unwrap_or_else(|error| error.into_inner())
            .invalidate();
    }
}

fn held(vk: i32) -> bool {
    unsafe { GetAsyncKeyState(vk) as u16 & 0x8000 != 0 }
}

fn unsafe_modifiers() -> bool {
    [0x11, 0x12, 0x5B, 0x5C].into_iter().any(held)
}

struct Scope {
    hwnd: isize,
    focus: isize,
}

fn input_scope(mode: Mode) -> Option<Scope> {
    unsafe {
        let hwnd = GetForegroundWindow();
        if hwnd.0.is_null() {
            return None;
        }
        let thread = GetWindowThreadProcessId(hwnd, None);
        if thread == 0 {
            return None;
        }
        let mut info = GUITHREADINFO {
            cbSize: std::mem::size_of::<GUITHREADINFO>() as u32,
            ..Default::default()
        };
        GetGUIThreadInfo(thread, &mut info).ok()?;
        if info.hwndFocus.0.is_null()
            || info.flags.0 & (GUI_INMENUMODE.0 | GUI_INMOVESIZE.0 | GUI_POPUPMENUMODE.0) != 0
            || GetWindowLongW(info.hwndFocus, GWL_STYLE) & ES_PASSWORD != 0
        {
            return None;
        }
        // A VK-only reconstruction is exact only on the plain US keyboard layout.
        // Reject international/dead-key variants and CJK layouts even in Latin mode.
        if mode == Mode::Edits && GetKeyboardLayout(thread).0 as usize as u32 != US_LAYOUT {
            return None;
        }
        Some(Scope {
            hwnd: hwnd.0 as isize,
            focus: info.hwndFocus.0 as isize,
        })
    }
}

fn in_scope(session: &Session) -> bool {
    input_scope(session.mode).is_some_and(|scope| {
        permitted(
            session.hwnd,
            session.focus,
            scope.hwnd,
            scope.focus,
            session.deadline,
            Instant::now(),
            true,
        )
    })
}

/// Asks the IME of the thread that owns `focus` whether it is open. `ImmGetContext` cannot be
/// used for that: it returns NULL for any window the calling thread does not own, which is every
/// window this hook looks at. The thread's default IME window answers `WM_IME_CONTROL` from
/// anywhere. A busy, hung or out-of-reach window ends in [`ImeAnswer::Unknown`].
fn ime_answer(focus: isize, timeout_ms: u32) -> ImeAnswer {
    // SAFETY: plain queries on a window handle; a stale handle only makes them fail.
    unsafe {
        let ime = ImmGetDefaultIMEWnd(HWND(focus as *mut _));
        if ime.0.is_null() {
            return ImeAnswer::NoImeWindow;
        }
        let mut status = 0usize;
        let sent = SendMessageTimeoutW(
            ime,
            WM_IME_CONTROL,
            WPARAM(IMC_GETOPENSTATUS),
            LPARAM(0),
            SMTO_ABORTIFHUNG,
            timeout_ms,
            Some(&mut status as *mut usize),
        );
        match (sent.0, status) {
            (0, _) => ImeAnswer::Unknown,
            (_, 0) => ImeAnswer::Closed,
            _ => ImeAnswer::Open,
        }
    }
}

/// Whether the window `focus` is on the plain US layout, which has no IME.
fn us_layout(focus: isize) -> bool {
    // SAFETY: plain queries on a window handle.
    unsafe {
        let thread = GetWindowThreadProcessId(HWND(focus as *mut _), None);
        thread != 0 && GetKeyboardLayout(thread).0 as usize as u32 == US_LAYOUT
    }
}

/// Whether an IME could be composing in `focus` as Enter goes down: `Some(false)` only when it
/// cannot (see [`composition_state`]). No composition text is ever read.
fn composing(focus: isize) -> Option<bool> {
    let us = us_layout(focus);
    // Only another layout can have an IME, and only then is the slower question worth asking.
    let ime = if us {
        ImeAnswer::NoImeWindow
    } else {
        ime_answer(focus, IME_QUERY_IN_HOOK_MS)
    };
    composition_state(us, ime)
}

/// Returns without waiting if a consumer owns either mutex. Any contention invalidates capture.
fn with_session(action: impl FnOnce(&Session)) {
    if !ACTIVE.load(Ordering::Acquire) {
        return;
    }
    let Ok(current) = registry().try_lock() else {
        GENERATION.fetch_add(1, Ordering::AcqRel);
        return;
    };
    let Some(session) = current.as_ref().cloned() else {
        return;
    };
    drop(current);
    if session.generation != GENERATION.load(Ordering::Acquire)
        || Instant::now() >= session.deadline
    {
        session.invalid.store(true, Ordering::Release);
    }
    if session.invalid.load(Ordering::Acquire) {
        if let Ok(mut buffer) = session.buffer.try_lock() {
            buffer.invalidate();
        }
        retire(&session);
        return;
    }
    action(&session);
}

fn retire(session: &Arc<Session>) {
    if let Ok(mut current) = registry().try_lock() {
        if current
            .as_ref()
            .is_some_and(|item| Arc::ptr_eq(item, session))
        {
            *current = None;
            ACTIVE.store(false, Ordering::Release);
        }
    }
}

/// Called beside the hotkey hook, after its existing suppression decision.
pub(super) fn keyboard(kb: &KBDLLHOOKSTRUCT, message: u32, blocked: bool) {
    // Every event counts as proof the procedure is installed, Raphael's own paste keys included.
    HEARTBEAT.key_event();
    match keyboard_origin(kb.flags.0, kb.dwExtraInfo) {
        InputOrigin::OwnInjected => return,
        InputOrigin::Physical => {
            INPUT_SERIAL.fetch_add(1, Ordering::AcqRel);
        }
        InputOrigin::OtherInjected => {
            INPUT_SERIAL.fetch_add(1, Ordering::AcqRel);
            invalidate_active();
            return;
        }
    }
    with_session(|session| {
        if session.complete.load(Ordering::Acquire) {
            return;
        }
        if blocked || !in_scope(session) {
            session.invalid.store(true, Ordering::Release);
        }
        if session.invalid.load(Ordering::Acquire) {
            record(session, EditEvent::Invalidated);
            return;
        }
        let down = matches!(message, WM_KEYDOWN | WM_SYSKEYDOWN);
        let vk = kb.vkCode as u16;
        let event = match session.mode {
            Mode::Terminal => {
                let caps = unsafe { GetKeyState(0x14) & 1 != 0 };
                terminal_key(
                    vk,
                    down,
                    held(0x10),
                    caps,
                    held(0x11),
                    [0x12, 0x5B, 0x5C].into_iter().any(held),
                    us_layout(session.focus),
                )
            }
            Mode::Edits => {
                let caps = unsafe { GetKeyState(0x14) & 1 != 0 };
                edit_key(vk, down, held(0x10), caps, unsafe_modifiers())
            }
            Mode::Submissions => {
                let composition = if vk == 0x0D && down {
                    composing(session.focus)
                } else {
                    None
                };
                submission_key(vk, down, held(0x10), unsafe_modifiers(), composition)
            }
        };
        if let Some(event) = event {
            record(session, event);
        }
    });
}

fn invalidate_active() {
    with_session(|session| {
        session.invalid.store(true, Ordering::Release);
        if let Ok(mut buffer) = session.buffer.try_lock() {
            buffer.invalidate();
        }
    });
}

fn record(session: &Session, event: EditEvent) {
    let Ok(mut buffer) = session.buffer.try_lock() else {
        session.invalid.store(true, Ordering::Release);
        return;
    };
    if session.invalid.load(Ordering::Acquire)
        || session.generation != GENERATION.load(Ordering::Acquire)
    {
        session.invalid.store(true, Ordering::Release);
        buffer.invalidate();
        return;
    }
    buffer.push(event);
    session.complete.store(buffer.complete(), Ordering::Release);
    if buffer.invalidated() {
        session.invalid.store(true, Ordering::Release);
    }
}

/// Any physical click invalidates caret/field identity before it can edit another field.
pub(super) fn mouse(mouse: &MSLLHOOKSTRUCT) {
    match mouse_origin(mouse.flags, mouse.dwExtraInfo) {
        InputOrigin::OwnInjected => return,
        InputOrigin::Physical => {
            INPUT_SERIAL.fetch_add(1, Ordering::AcqRel);
        }
        InputOrigin::OtherInjected => {
            INPUT_SERIAL.fetch_add(1, Ordering::AcqRel);
            invalidate_active();
            return;
        }
    }
    with_session(|session| {
        if session.complete.load(Ordering::Acquire) {
            return;
        }
        if session.mode == Mode::Terminal && in_scope(session) {
            record(session, EditEvent::Resync);
            return;
        }
        session.invalid.store(true, Ordering::Release);
        if let Ok(mut buffer) = session.buffer.try_lock() {
            buffer.invalidate();
        }
    });
}

/// The existing hook loop calls this every 10 ms to expire even an idle observation. It is also
/// the heartbeat: it runs only while the thread that owns the hooks is pumping messages.
pub(super) fn tick() {
    HEARTBEAT.beat(monotonic_ms());
    with_session(|session| {
        if !session.complete.load(Ordering::Acquire) && !in_scope(session) {
            session.invalid.store(true, Ordering::Release);
            if let Ok(mut buffer) = session.buffer.try_lock() {
                buffer.invalidate();
            }
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::observation::{HOOK_HEARTBEAT_MAX_AGE_MS, RAPHAEL_INPUT_MARKER};
    use windows::Win32::UI::WindowsAndMessaging::{KBDLLHOOKSTRUCT_FLAGS, LLKHF_INJECTED};

    static TEST_LOCK: Mutex<()> = Mutex::new(());

    /// A reading of the heartbeat clock long after any beat these tests make.
    fn much_later() -> u64 {
        monotonic_ms() + 10 * 3_600_000
    }

    /// Pretends the hook thread is running, whatever the scheduler does to the test meanwhile.
    fn hooks_running() {
        HEARTBEAT.beat(monotonic_ms() + 3_600_000);
    }

    fn observation(duration: Duration, complete: bool) -> EditObservation {
        cancel_edit_observations();
        hooks_running();
        let mut buffer = EditBuffer::new();
        buffer.push(EditEvent::Ascii(b'x'));
        if complete {
            buffer.push(EditEvent::Enter);
        }
        let session = Arc::new(Session {
            mode: Mode::Edits,
            hwnd: 11,
            focus: 12,
            deadline: Instant::now() + duration,
            generation: GENERATION.load(Ordering::Acquire),
            invalid: AtomicBool::new(false),
            complete: AtomicBool::new(complete),
            buffer: Mutex::new(buffer),
        });
        *registry().lock().unwrap() = Some(session.clone());
        ACTIVE.store(true, Ordering::Release);
        EditObservation { session }
    }

    #[test]
    fn cancellation_clears_pending_input_even_after_submit() {
        let _serial = TEST_LOCK.lock().unwrap();
        let mut observed = observation(Duration::from_secs(20), true);
        cancel_edit_observations();
        assert_eq!(observed.drain(), [EditEvent::Invalidated]);
        assert!(!ACTIVE.load(Ordering::Acquire));
        assert!(registry().lock().unwrap().is_none());
    }

    #[test]
    fn idle_expiration_clears_and_removes_the_observer() {
        let _serial = TEST_LOCK.lock().unwrap();
        let mut observed = observation(Duration::ZERO, true);
        tick();
        assert!(!ACTIVE.load(Ordering::Acquire));
        assert!(registry().lock().unwrap().is_none());
        assert_eq!(observed.drain(), [EditEvent::Invalidated]);
    }

    #[test]
    fn a_contended_registry_never_waits_and_discards_the_observation() {
        let _serial = TEST_LOCK.lock().unwrap();
        let mut observed = observation(Duration::from_secs(20), true);
        let locked = registry().lock().unwrap();
        with_session(|_| panic!("a contended hook must return before touching the session"));
        drop(locked);
        assert_eq!(observed.drain(), [EditEvent::Invalidated]);
    }

    #[test]
    fn a_physical_click_discards_pending_ascii_without_a_focus_poll() {
        let _serial = TEST_LOCK.lock().unwrap();
        let mut observed = observation(Duration::from_secs(20), false);
        mouse(&MSLLHOOKSTRUCT::default());
        assert_eq!(observed.drain(), [EditEvent::Invalidated]);
    }

    #[test]
    fn own_paste_input_and_post_submit_input_are_never_observed() {
        let _serial = TEST_LOCK.lock().unwrap();
        let mut observed = observation(Duration::from_secs(20), true);
        let injected = KBDLLHOOKSTRUCT {
            vkCode: 0x41,
            flags: LLKHF_INJECTED,
            dwExtraInfo: RAPHAEL_INPUT_MARKER,
            ..Default::default()
        };
        keyboard(&injected, WM_KEYDOWN, false);
        let physical = KBDLLHOOKSTRUCT {
            flags: KBDLLHOOKSTRUCT_FLAGS(0),
            ..injected
        };
        keyboard(&physical, WM_KEYDOWN, false);
        mouse(&MSLLHOOKSTRUCT::default());
        assert_eq!(observed.drain(), [EditEvent::Ascii(b'x'), EditEvent::Enter]);
    }

    #[test]
    fn unknown_injected_keyboard_input_invalidates_without_recording_its_content() {
        let _serial = TEST_LOCK.lock().unwrap();
        let mut observed = observation(Duration::from_secs(20), false);
        let serial = INPUT_SERIAL.load(Ordering::Acquire);
        keyboard(
            &KBDLLHOOKSTRUCT {
                vkCode: 0x41,
                flags: LLKHF_INJECTED,
                ..Default::default()
            },
            WM_KEYDOWN,
            false,
        );
        assert_eq!(INPUT_SERIAL.load(Ordering::Acquire), serial + 1);
        assert_eq!(observed.drain(), [EditEvent::Invalidated]);
    }

    #[test]
    fn unknown_injected_mouse_input_invalidates_without_waiting_for_focus() {
        let _serial = TEST_LOCK.lock().unwrap();
        let mut observed = observation(Duration::from_secs(20), false);
        mouse(&MSLLHOOKSTRUCT {
            flags: 1,
            ..Default::default()
        });
        assert_eq!(observed.drain(), [EditEvent::Invalidated]);
    }

    #[test]
    fn checkpoint_counter_runs_without_an_active_text_observer() {
        let _serial = TEST_LOCK.lock().unwrap();
        cancel_edit_observations();
        let serial = INPUT_SERIAL.load(Ordering::Acquire);
        keyboard(&KBDLLHOOKSTRUCT::default(), WM_KEYDOWN, false);
        mouse(&MSLLHOOKSTRUCT::default());
        assert_eq!(INPUT_SERIAL.load(Ordering::Acquire), serial + 2);
        keyboard(
            &KBDLLHOOKSTRUCT {
                flags: LLKHF_INJECTED,
                dwExtraInfo: RAPHAEL_INPUT_MARKER,
                ..Default::default()
            },
            WM_KEYDOWN,
            false,
        );
        mouse(&MSLLHOOKSTRUCT {
            flags: 1,
            dwExtraInfo: RAPHAEL_INPUT_MARKER,
            ..Default::default()
        });
        assert_eq!(INPUT_SERIAL.load(Ordering::Acquire), serial + 2);
        assert!(!ACTIVE.load(Ordering::Acquire));
    }

    #[test]
    fn stopping_an_old_handle_does_not_cancel_the_replacement() {
        let _serial = TEST_LOCK.lock().unwrap();
        let mut old = observation(Duration::from_secs(20), true);
        let mut next = observation(Duration::from_secs(20), true);
        old.stop();
        assert!(ACTIVE.load(Ordering::Acquire));
        assert_eq!(old.drain(), [EditEvent::Invalidated]);
        assert_eq!(next.drain(), [EditEvent::Ascii(b'x'), EditEvent::Enter]);
    }

    #[test]
    fn a_late_focus_callback_cannot_cancel_a_replacement_observer() {
        let _serial = TEST_LOCK.lock().unwrap();
        let old = observation(Duration::from_secs(20), true);
        let late_callback = old.cancellation();
        drop(old);
        let mut next = observation(Duration::from_secs(20), true);
        let generation = GENERATION.load(Ordering::Acquire);
        late_callback.cancel();
        assert_eq!(GENERATION.load(Ordering::Acquire), generation);
        assert!(ACTIVE.load(Ordering::Acquire));
        assert_eq!(next.drain(), [EditEvent::Ascii(b'x'), EditEvent::Enter]);
    }

    #[test]
    fn a_scoped_focus_callback_clears_its_own_pending_input() {
        let _serial = TEST_LOCK.lock().unwrap();
        let mut current = observation(Duration::from_secs(20), true);
        current.cancellation().cancel();
        assert_eq!(current.drain(), [EditEvent::Invalidated]);
        assert!(!ACTIVE.load(Ordering::Acquire));
        assert!(registry().lock().unwrap().is_none());
    }

    #[test]
    fn a_contended_buffer_drops_capture_without_waiting() {
        let _serial = TEST_LOCK.lock().unwrap();
        let mut observed = observation(Duration::from_secs(20), false);
        let locked = observed.session.buffer.lock().unwrap();
        record(&observed.session, EditEvent::Ascii(b'z'));
        drop(locked);
        assert_eq!(observed.drain(), [EditEvent::Invalidated]);
    }

    #[test]
    fn every_hook_loop_pass_beats_the_heart() {
        let _serial = TEST_LOCK.lock().unwrap();
        cancel_edit_observations();
        HEARTBEAT.beat(0);
        assert!(!HEARTBEAT.loop_alive(monotonic_ms()));
        tick();
        assert!(HEARTBEAT.loop_alive(monotonic_ms()));
    }

    #[test]
    fn every_keyboard_event_reaches_the_heartbeat_even_the_pastes_own() {
        let _serial = TEST_LOCK.lock().unwrap();
        cancel_edit_observations();
        let own = KBDLLHOOKSTRUCT {
            vkCode: 0x56,
            flags: LLKHF_INJECTED,
            dwExtraInfo: RAPHAEL_INPUT_MARKER,
            ..Default::default()
        };
        let other = KBDLLHOOKSTRUCT {
            dwExtraInfo: 0,
            ..own
        };
        let physical = KBDLLHOOKSTRUCT {
            flags: KBDLLHOOKSTRUCT_FLAGS(0),
            ..own
        };
        for event in [own, other, physical] {
            let mark = HEARTBEAT.mark();
            keyboard(&event, WM_KEYDOWN, false);
            assert!(HEARTBEAT.saw_events_since(mark));
        }
        // The mouse hook does not stand in for the keyboard one.
        let mark = HEARTBEAT.mark();
        mouse(&MSLLHOOKSTRUCT::default());
        assert!(!HEARTBEAT.saw_events_since(mark));
    }

    #[test]
    fn a_stopped_hook_loop_invalidates_an_open_observation() {
        let _serial = TEST_LOCK.lock().unwrap();
        let mut observed = observation(Duration::from_secs(20), false);
        let stopped = much_later() + HOOK_HEARTBEAT_MAX_AGE_MS;
        assert_eq!(observed.drain_at(stopped), [EditEvent::Invalidated]);
        // Terminal: even a loop that comes back cannot revive it.
        hooks_running();
        assert_eq!(observed.drain(), [EditEvent::Invalidated]);
    }

    #[test]
    fn what_a_submitted_observation_captured_survives_a_hook_that_stops_afterwards() {
        let _serial = TEST_LOCK.lock().unwrap();
        let mut observed = observation(Duration::from_secs(20), true);
        assert_eq!(
            observed.drain_at(much_later()),
            [EditEvent::Ascii(b'x'), EditEvent::Enter]
        );
    }

    #[test]
    fn nothing_is_checkpointed_or_observed_while_the_hook_is_not_running() {
        let _serial = TEST_LOCK.lock().unwrap();
        cancel_edit_observations();
        HEARTBEAT.beat(0);
        // Refused before any window is looked at: a standing-still counter would prove nothing.
        assert!(input_checkpoint(1).is_none());
        assert!(observe_submissions(1, Duration::from_secs(1)).is_none());
        assert!(observe_edits(1, Duration::from_secs(1)).is_none());
        assert!(!ACTIVE.load(Ordering::Acquire));
    }
}
