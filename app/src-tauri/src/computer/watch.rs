//! The watch on the owner's own mouse and keyboard while Raphael operates: the owner wins.
//!
//! While a run is under way (armed by `computer.state` `begin` and `step`, off for `pause` and
//! `end`), any input of the owner's stops it at once: a key, a click, the wheel, or the mouse
//! moved more than a little. Raphael's own input never counts: everything it sends goes through
//! `SendInput`, which Windows marks as injected (`LLKHF_INJECTED`, `LLMHF_INJECTED`), and UI
//! Automation's patterns send no input at all.
//!
//! Two keys are left to others: Esc, which the hotkey service turns into the controller's cancel
//! (it hides the overlay and stops the run itself, and the shell then says 《了。已停止。》), and
//! the release of a key, so that a key the owner was holding when the run began does not stop it.
//! The mouse has a short grace after the watch is armed: the owner who just clicked 「是」 on the
//! overlay is still moving the hand away from it.
//!
//! [`Judge`] is the decision, a pure function of the events and the clock; [`hook`] holds the
//! low-level hooks of Windows that feed it, installed only while a run is under way. Nothing here
//! logs a key.

use std::time::{Duration, Instant};

/// The virtual-key code of Esc.
const VK_ESCAPE: u32 = 0x1B;
/// How far the mouse may move, in pixels added up, before it counts.
pub const MOVE_THRESHOLD_PX: i64 = 24;
/// How long after the watch is armed the mouse may move freely.
pub const MOVE_GRACE: Duration = Duration::from_millis(1500);

/// An input event, as the hooks see it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Input {
    Key { vk: u32, down: bool, injected: bool },
    Move { x: i32, y: i32, injected: bool },
    Button { injected: bool },
    Wheel { injected: bool },
}

/// Whether an input is the owner's, so that the run stops.
#[derive(Debug)]
pub struct Judge {
    armed_at: Option<Instant>,
    last: Option<(i32, i32)>,
    travelled: i64,
}

impl Default for Judge {
    fn default() -> Self {
        Self::new()
    }
}

impl Judge {
    pub fn new() -> Self {
        Self {
            armed_at: None,
            last: None,
            travelled: 0,
        }
    }

    /// The watch is on from `now`.
    pub fn arm(&mut self, now: Instant) {
        self.armed_at = Some(now);
        self.last = None;
        self.travelled = 0;
    }

    /// The watch is off.
    pub fn disarm(&mut self) {
        self.armed_at = None;
    }

    #[cfg(test)]
    pub fn armed(&self) -> bool {
        self.armed_at.is_some()
    }

    /// Whether `input`, at `now`, is the owner's own and stops the run. Once it has said so, the
    /// watch is off: the run is stopping.
    pub fn owners(&mut self, input: Input, now: Instant) -> bool {
        let Some(armed_at) = self.armed_at else {
            return false;
        };
        let theirs = match input {
            Input::Key { injected: true, .. }
            | Input::Move { injected: true, .. }
            | Input::Button { injected: true }
            | Input::Wheel { injected: true } => false,
            Input::Key { vk, down, .. } => down && vk != VK_ESCAPE,
            Input::Button { .. } | Input::Wheel { .. } => true,
            Input::Move { x, y, .. } => {
                let last = self.last.replace((x, y));
                if now.saturating_duration_since(armed_at) < MOVE_GRACE {
                    false
                } else if let Some((lx, ly)) = last {
                    self.travelled += i64::from((x - lx).abs()) + i64::from((y - ly).abs());
                    self.travelled > MOVE_THRESHOLD_PX
                } else {
                    false
                }
            }
        };
        if theirs {
            self.disarm();
        }
        theirs
    }
}

#[cfg(windows)]
pub mod hook {
    //! The low-level keyboard and mouse hooks of Windows, on a thread of their own with its
    //! message loop, installed by [`Watch::start`] and taken out by [`Watch::stop`] (or when it is
    //! dropped). A hook only reads: every event is passed on, whatever the judge says.

    use std::sync::Mutex;
    use std::sync::mpsc;
    use std::time::Instant;

    use windows::Win32::Foundation::{LPARAM, LRESULT, WPARAM};
    use windows::Win32::System::Threading::GetCurrentThreadId;
    use windows::Win32::UI::WindowsAndMessaging::{
        CallNextHookEx, DispatchMessageW, GetMessageW, KBDLLHOOKSTRUCT, LLKHF_INJECTED,
        LLMHF_INJECTED, MSG, MSLLHOOKSTRUCT, PostThreadMessageW, SetWindowsHookExW,
        TranslateMessage, UnhookWindowsHookEx, WH_KEYBOARD_LL, WH_MOUSE_LL, WM_KEYDOWN,
        WM_LBUTTONDOWN, WM_MBUTTONDOWN, WM_MOUSEHWHEEL, WM_MOUSEMOVE, WM_MOUSEWHEEL, WM_QUIT,
        WM_RBUTTONDOWN, WM_SYSKEYDOWN, WM_XBUTTONDOWN,
    };

    use super::{Input, Judge};
    use crate::rpc::lock;

    /// What the hooks report to: the judge, and who is told when the owner's input stops the run.
    struct Watching {
        judge: Judge,
        told: Option<Box<dyn Fn() + Send>>,
    }

    /// One watch at a time: the hooks are plain functions and find it here.
    static WATCHING: Mutex<Option<Watching>> = Mutex::new(None);

    fn judge(input: Input) {
        let mut watching = lock(&WATCHING);
        let Some(watch) = watching.as_mut() else {
            return;
        };
        if watch.judge.owners(input, Instant::now())
            && let Some(told) = &watch.told
        {
            told();
        }
    }

    unsafe extern "system" fn keyboard(code: i32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
        if code >= 0 {
            // SAFETY: for WH_KEYBOARD_LL, lparam points to a KBDLLHOOKSTRUCT.
            let event = unsafe { &*(lparam.0 as *const KBDLLHOOKSTRUCT) };
            let message = wparam.0 as u32;
            judge(Input::Key {
                vk: event.vkCode,
                down: message == WM_KEYDOWN || message == WM_SYSKEYDOWN,
                injected: event.flags.0 & LLKHF_INJECTED.0 != 0,
            });
        }
        // SAFETY: passing the event on, as every hook must.
        unsafe { CallNextHookEx(None, code, wparam, lparam) }
    }

    unsafe extern "system" fn mouse(code: i32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
        if code >= 0 {
            // SAFETY: for WH_MOUSE_LL, lparam points to an MSLLHOOKSTRUCT.
            let event = unsafe { &*(lparam.0 as *const MSLLHOOKSTRUCT) };
            let injected = event.flags & LLMHF_INJECTED != 0;
            let input = match wparam.0 as u32 {
                WM_MOUSEMOVE => Some(Input::Move {
                    x: event.pt.x,
                    y: event.pt.y,
                    injected,
                }),
                WM_LBUTTONDOWN | WM_RBUTTONDOWN | WM_MBUTTONDOWN | WM_XBUTTONDOWN => {
                    Some(Input::Button { injected })
                }
                WM_MOUSEWHEEL | WM_MOUSEHWHEEL => Some(Input::Wheel { injected }),
                _ => None,
            };
            if let Some(input) = input {
                judge(input);
            }
        }
        // SAFETY: passing the event on, as every hook must.
        unsafe { CallNextHookEx(None, code, wparam, lparam) }
    }

    /// The hooks' thread, while they are in.
    pub struct Watch {
        thread_id: u32,
        thread: Option<std::thread::JoinHandle<()>>,
    }

    impl Watch {
        /// Puts the hooks in, unarmed; `told` is called (on the hooks' thread, so it must be
        /// quick) when the owner's input stops the run.
        pub fn start(told: Box<dyn Fn() + Send>) -> Option<Watch> {
            *lock(&WATCHING) = Some(Watching {
                judge: Judge::new(),
                told: Some(told),
            });
            let (ready, started) = mpsc::channel();
            let thread = std::thread::Builder::new()
                .name("raphael-computer-watch".into())
                .spawn(move || {
                    // SAFETY: the hooks are this thread's, and taken out before it ends.
                    let hooks = unsafe {
                        (
                            SetWindowsHookExW(WH_KEYBOARD_LL, Some(keyboard), None, 0),
                            SetWindowsHookExW(WH_MOUSE_LL, Some(mouse), None, 0),
                        )
                    };
                    let id = unsafe { GetCurrentThreadId() };
                    let ok = hooks.0.is_ok() && hooks.1.is_ok();
                    let _ = ready.send(ok.then_some(id));
                    if ok {
                        let mut message = MSG::default();
                        // SAFETY: a plain message loop, ended by WM_QUIT.
                        while unsafe { GetMessageW(&mut message, None, 0, 0) }.as_bool() {
                            unsafe {
                                let _ = TranslateMessage(&message);
                                DispatchMessageW(&message);
                            }
                        }
                    }
                    for hook in [hooks.0, hooks.1].into_iter().flatten() {
                        // SAFETY: a hook this thread put in.
                        let _ = unsafe { UnhookWindowsHookEx(hook) };
                    }
                })
                .ok()?;
            match started.recv().ok().flatten() {
                Some(thread_id) => Some(Watch {
                    thread_id,
                    thread: Some(thread),
                }),
                None => {
                    log::warn!("computer: the watch on the owner's input could not start");
                    let _ = thread.join();
                    *lock(&WATCHING) = None;
                    None
                }
            }
        }

        /// The watch counts the owner's input from now (with the mouse's grace).
        pub fn arm(&self) {
            if let Some(watch) = lock(&WATCHING).as_mut() {
                watch.judge.arm(Instant::now());
            }
        }

        /// The owner's input does not count (a question is on screen).
        pub fn disarm(&self) {
            if let Some(watch) = lock(&WATCHING).as_mut() {
                watch.judge.disarm();
            }
        }

        /// Takes the hooks out and ends their thread.
        pub fn stop(mut self) {
            self.end();
        }

        fn end(&mut self) {
            *lock(&WATCHING) = None;
            if let Some(thread) = self.thread.take() {
                // SAFETY: a message to a thread of this process.
                let _ =
                    unsafe { PostThreadMessageW(self.thread_id, WM_QUIT, WPARAM(0), LPARAM(0)) };
                let _ = thread.join();
            }
        }
    }

    impl Drop for Watch {
        fn drop(&mut self) {
            self.end();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn key(vk: u32, down: bool, injected: bool) -> Input {
        Input::Key { vk, down, injected }
    }

    fn mv(x: i32, y: i32, injected: bool) -> Input {
        Input::Move { x, y, injected }
    }

    #[test]
    fn nothing_counts_while_the_watch_is_off() {
        let mut judge = Judge::new();
        let now = Instant::now();
        assert!(!judge.owners(key(0x41, true, false), now));
        assert!(!judge.owners(Input::Button { injected: false }, now));
    }

    #[test]
    fn a_key_click_or_wheel_of_the_owners_stops_the_run_and_the_watch_goes_off() {
        for input in [
            key(0x41, true, false),
            key(0xA5, true, false), // Right Alt
            Input::Button { injected: false },
            Input::Wheel { injected: false },
        ] {
            let mut judge = Judge::new();
            let now = Instant::now();
            judge.arm(now);
            assert!(judge.owners(input, now), "{input:?}");
            assert!(!judge.armed());
            assert!(!judge.owners(input, now), "it said so once");
        }
    }

    #[test]
    fn raphaels_own_input_never_counts() {
        let mut judge = Judge::new();
        let now = Instant::now();
        judge.arm(now);
        let later = now + MOVE_GRACE * 2;
        for input in [
            key(0x41, true, true),
            Input::Button { injected: true },
            Input::Wheel { injected: true },
            mv(0, 0, true),
            mv(500, 500, true),
        ] {
            assert!(!judge.owners(input, later), "{input:?}");
        }
        assert!(judge.armed());
    }

    #[test]
    fn esc_and_a_released_key_are_left_to_the_controller() {
        let mut judge = Judge::new();
        let now = Instant::now();
        judge.arm(now);
        assert!(!judge.owners(key(VK_ESCAPE, true, false), now));
        assert!(!judge.owners(key(0x41, false, false), now));
        assert!(judge.armed());
    }

    #[test]
    fn the_mouse_counts_after_its_grace_and_beyond_a_little_way() {
        let mut judge = Judge::new();
        let now = Instant::now();
        judge.arm(now);
        // within the grace: the hand leaving the overlay's button
        assert!(!judge.owners(mv(0, 0, false), now));
        assert!(!judge.owners(mv(300, 300, false), now + Duration::from_millis(500)));
        let later = now + MOVE_GRACE + Duration::from_millis(10);
        assert!(!judge.owners(mv(300, 300, false), later));
        assert!(!judge.owners(mv(310, 305, false), later)); // 15 px: a jolt of the desk
        assert!(judge.owners(mv(320, 310, false), later)); // 30 px in all
    }

    #[test]
    fn arming_again_starts_the_count_again() {
        let mut judge = Judge::new();
        let now = Instant::now();
        judge.arm(now);
        let later = now + MOVE_GRACE * 2;
        assert!(!judge.owners(mv(0, 0, false), later));
        assert!(!judge.owners(mv(20, 0, false), later));
        judge.arm(later);
        let after = later + MOVE_GRACE * 2;
        assert!(!judge.owners(mv(20, 0, false), after));
        assert!(!judge.owners(mv(40, 0, false), after));
        judge.disarm();
        assert!(!judge.owners(Input::Button { injected: false }, after));
    }
}
