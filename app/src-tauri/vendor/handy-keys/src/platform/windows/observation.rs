//! A single, explicitly scoped edit observer beside the existing hotkey listener.
//!
//! The caller must vet UIA password/focus identity and check the paste checkpoint before opting in.
//! Hooks never block on a mutex, call UIA, log input, or suppress an input event.

use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant};

use windows::Win32::Foundation::HWND;
use windows::Win32::UI::Input::Ime::{
    ImmGetCompositionStringW, ImmGetContext, ImmGetOpenStatus, ImmReleaseContext, GCS_COMPSTR,
};
use windows::Win32::UI::Input::KeyboardAndMouse::{
    GetAsyncKeyState, GetKeyState, GetKeyboardLayout,
};
use windows::Win32::UI::WindowsAndMessaging::{
    GetForegroundWindow, GetGUIThreadInfo, GetWindowLongW, GetWindowThreadProcessId, ES_PASSWORD,
    GUITHREADINFO, GUI_INMENUMODE, GUI_INMOVESIZE, GUI_POPUPMENUMODE, GWL_STYLE, KBDLLHOOKSTRUCT,
    MSLLHOOKSTRUCT, WM_KEYDOWN, WM_SYSKEYDOWN,
};

use crate::observation::{
    deadline_after, edit_key, keyboard_origin, mouse_origin, permitted, submission_key, EditBuffer,
    EditEvent, InputOrigin, InputStamp,
};

#[derive(Clone, Copy, PartialEq, Eq)]
enum Mode {
    Edits,
    Submissions,
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

fn registry() -> &'static Mutex<Option<Arc<Session>>> {
    SESSION.get_or_init(|| Mutex::new(None))
}

/// Metadata-only evidence of native focus and input serial immediately before a paste.
#[derive(Clone)]
pub struct InputCheckpoint {
    stamp: InputStamp,
}

impl InputCheckpoint {
    /// True only while no physical or unknown injected input has occurred, native focus is
    /// unchanged, and fewer than 60 seconds have elapsed. Recheck after arming an observer.
    pub fn is_current(&self) -> bool {
        let before = INPUT_SERIAL.load(Ordering::Acquire);
        let Some(scope) = input_scope(Mode::Submissions) else {
            return false;
        };
        let after = INPUT_SERIAL.load(Ordering::Acquire);
        before == after
            && self
                .stamp
                .is_current(scope.hwnd, scope.focus, after, Instant::now())
    }

    pub fn started(&self) -> Instant {
        self.stamp.started
    }
}

/// Captures only counter/focus metadata. No text observer or polling thread is started.
/// Requires the existing keyboard listener; a missed OS hook event cannot advance the counter.
pub fn input_checkpoint(hwnd: isize) -> Option<InputCheckpoint> {
    if hwnd == 0 {
        return None;
    }
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
    /// Cancellation, an expired deadline, or any unsafe input yields only `Invalidated`.
    pub fn drain(&mut self) -> Vec<EditEvent> {
        let session = &self.session;
        if session.generation != GENERATION.load(Ordering::Acquire)
            || Instant::now() >= session.deadline
        {
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
/// Unsupported layouts, an open IME, held chords, or unknown native focus fail closed.
/// UIA-unavailable applications cannot be vetted by this API alone.
pub fn observe_edits(hwnd: isize, duration: Duration) -> Option<EditObservation> {
    observe(hwnd, duration, Mode::Edits)
}

/// Observes only an unmodified, non-composing Enter in a vetted readable UIA field.
/// It never buffers text and supports non-US layouts and an open IME. An unknown composition
/// state at Enter invalidates the observation rather than treating a candidate commit as submit.
pub fn observe_submissions(hwnd: isize, duration: Duration) -> Option<EditObservation> {
    observe(hwnd, duration, Mode::Submissions)
}

fn observe(hwnd: isize, duration: Duration, mode: Mode) -> Option<EditObservation> {
    if hwnd == 0 {
        return None;
    }
    cancel_edit_observations();
    let scope = input_scope(mode)?;
    if scope.hwnd != hwnd || (mode == Mode::Edits && (unsafe_modifiers() || held(0x10))) {
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
        if mode == Mode::Edits {
            if GetKeyboardLayout(thread).0 as usize as u32 != 0x0409_0409 {
                return None;
            }
            let ime = ImmGetContext(info.hwndFocus);
            if !ime.0.is_null() {
                let open = ImmGetOpenStatus(ime).as_bool();
                let _ = ImmReleaseContext(info.hwndFocus, ime);
                if open {
                    return None;
                }
            }
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

fn composing(focus: isize) -> Option<bool> {
    unsafe {
        let hwnd = HWND(focus as *mut _);
        let ime = ImmGetContext(hwnd);
        if ime.0.is_null() {
            let thread = GetWindowThreadProcessId(hwnd, None);
            // No IME context is expected on the plain US layout. Other layouts remain unknown.
            return (thread != 0 && GetKeyboardLayout(thread).0 as usize as u32 == 0x0409_0409)
                .then_some(false);
        }
        // Only request the byte count. No composition text is ever copied into this process.
        let bytes = ImmGetCompositionStringW(ime, GCS_COMPSTR, None, 0);
        let open = ImmGetOpenStatus(ime).as_bool();
        let _ = ImmReleaseContext(hwnd, ime);
        if bytes > 0 {
            Some(true)
        } else if bytes == 0 || !open {
            Some(false)
        } else {
            None
        }
    }
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
        session.invalid.store(true, Ordering::Release);
        if let Ok(mut buffer) = session.buffer.try_lock() {
            buffer.invalidate();
        }
    });
}

/// The existing hook loop calls this every 10 ms to expire even an idle observation.
pub(super) fn tick() {
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
    use crate::observation::RAPHAEL_INPUT_MARKER;
    use windows::Win32::UI::WindowsAndMessaging::{KBDLLHOOKSTRUCT_FLAGS, LLKHF_INJECTED};

    static TEST_LOCK: Mutex<()> = Mutex::new(());

    fn observation(duration: Duration, complete: bool) -> EditObservation {
        cancel_edit_observations();
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
}
