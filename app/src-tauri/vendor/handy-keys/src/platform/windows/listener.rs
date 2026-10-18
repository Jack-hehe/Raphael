//! Windows low-level keyboard hook implementation
//!
//! RAPHAEL PATCH (Task 17). This is handy-keys 0.3.4 (crates.io package, upstream commit e7d1b8e)
//! with two changes in this file, a one-line re-export in `lib.rs` (`accept_injected_input`) and a
//! call to it in the Windows tests of `tests/synthetic_input.rs`.
//!
//! First change: while a hotkey holds a modifier key swallowed (a modifier-only hotkey such as
//! Right Alt), the hook also swallows that key's auto-repeat key-downs and its key-up until the
//! key is released. Chord detection no longer relies on GetAsyncKeyState for swallowed keys: the
//! hook remembers them (`swallowed_modifiers`, `swallow_modifier_event`) and
//! `reconcile_modifiers` counts them as held. Only a key-up whose key-down was swallowed is
//! swallowed: a modifier release whose press reached the app always reaches it too (upstream
//! also swallowed a release when the tracked state matched a blocking hotkey after it, which left
//! Shift stuck system-wide in "hold Shift, press Right Alt, release Shift").
//!
//! Why, found with an injected-key probe: (1) unpatched, the repeats and the key-up reached the
//! foreground app, which read them as a tap of Alt and could open its menu bar or Office KeyTips
//! after a long hold. (2) GetAsyncKeyState never sees a key whose down was swallowed, so the first
//! other key or mouse button pressed during the hold made `reconcile_modifiers` believe the
//! modifier had been released. The hold ended early, and a chord such as Right Alt+V never
//! matched, so a hotkey on that chord (Raphael's PasteLast) never fired.
//!
//! A swallowed key whose key-up is missed (an elevated window took focus, the hook timed out)
//! would stay "held" for good, so a bare V would fire PasteLast and Esc would become Right
//! Alt+Esc. Guard: a physically held key keeps producing auto-repeat key-downs, so each swallowed
//! key remembers the time of its last event (`SwallowedModifiers`), and when a new non-modifier
//! key goes down (`handle_regular_key`), a swallowed key that has been silent for more than
//! `STALE_SWALLOW_MS` and that the async key state does not report held is forgotten, and the
//! reconciliation then releases it. In Right Alt+V the Alt repeats until V goes down, so the
//! chord works; the auto-repeats of a V that was accepted do not ask again (`last_key_down`),
//! because Windows repeats the V and not the Alt while V is down. Limit: Alt held, another key
//! pressed and released, then 1.5 s of silence with Alt still held, and the next key press ends
//! the Alt hold (Windows stops repeating the Alt once another key was pressed).
//!
//! Second change: injected keyboard events (LLKHF_INJECTED, such as Raphael's own paste with its
//! SendInput key-ups and the Ctrl+V chord sent by enigo) pass the hook untouched and never count
//! for hotkeys or get swallowed, unless the program calls `accept_injected_input(true)` (only
//! tests that drive the hook with SendInput do). See `passes_untouched`. Mouse events are not
//! affected.
//!
//! Third change: an opt-in edit observer runs beside this hook, with bounded in-memory events,
//! strict focus/window and deadline gates, and no effect on suppression or hotkey behavior.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{self, Sender};
use std::sync::{Arc, OnceLock};
use std::thread::{self, JoinHandle};
use std::time::Instant;

use windows::core::PCWSTR;
use windows::Win32::Foundation::{HWND, LPARAM, LRESULT, WPARAM};
use windows::Win32::System::RemoteDesktop::{
    WTSRegisterSessionNotification, WTSUnRegisterSessionNotification,
};
use windows::Win32::UI::Input::KeyboardAndMouse::{
    GetAsyncKeyState, SendInput, INPUT, INPUT_0, INPUT_KEYBOARD, KEYBDINPUT, KEYBD_EVENT_FLAGS,
    KEYEVENTF_KEYUP, VIRTUAL_KEY, VK_LCONTROL, VK_LMENU, VK_LSHIFT, VK_LWIN, VK_RCONTROL, VK_RMENU,
    VK_RSHIFT, VK_RWIN,
};
use windows::Win32::UI::WindowsAndMessaging::{
    CallNextHookEx, CreateWindowExW, DefWindowProcW, DestroyWindow, DispatchMessageW,
    GetForegroundWindow, GetWindowThreadProcessId, MsgWaitForMultipleObjects, PeekMessageW,
    RegisterClassW, SetWindowsHookExW, TranslateMessage, UnhookWindowsHookEx, HHOOK,
    KBDLLHOOKSTRUCT, LLKHF_EXTENDED, LLKHF_INJECTED, LLKHF_LOWER_IL_INJECTED, MSG, MSLLHOOKSTRUCT,
    PM_REMOVE, QS_ALLINPUT, WH_KEYBOARD_LL, WH_MOUSE_LL, WINDOW_EX_STYLE, WINDOW_STYLE, WM_KEYDOWN,
    WM_LBUTTONDOWN, WM_LBUTTONUP, WM_MBUTTONDOWN, WM_MBUTTONUP, WM_POWERBROADCAST, WM_QUIT,
    WM_RBUTTONDOWN, WM_RBUTTONUP, WM_SYSKEYDOWN, WM_XBUTTONDOWN, WM_XBUTTONUP, WNDCLASSW,
};

use crate::error::Result;
use crate::platform::state::{release_events, stale_modifiers, BlockingHotkeys, RECONCILABLE};
use crate::types::{Key, KeyEvent, Modifiers};

use super::keycode::{is_altgr_phantom_ctrl, map_key, vk_to_modifier};

const HOOK_LOOP_TIMEOUT_MS: u32 = 10;

/// Modifiers whose lone press-and-release makes the shell activate a menu:
/// Win opens the Start menu, Alt focuses the active window's menu bar.
const MENU_MODIFIERS: Modifiers = Modifiers::CMD.union(Modifiers::OPT);

/// Unassigned virtual key injected as the "menu mask" (AutoHotkey's default
/// mask key). The shell arms its menu when a Win/Alt down→up pair passes
/// with no other key in between; when we swallow the real key of a Win/Alt
/// hotkey, this inert keystroke is substituted so the modifier release no
/// longer reads as a lone tap (cjpais/Handy#917). The VK maps to nothing in
/// this crate (`vk_to_modifier` and `map_key` both return None), so even
/// without the dwExtraInfo marker it could never be blocked on its way to
/// the shell.
const MENU_MASK_VK: u16 = 0xE8;

/// dwExtraInfo marker stamped on injected mask events so the keyboard hook
/// recognizes its own injections and passes them through untouched
/// ("HKMM" in ASCII).
const MENU_MASK_EXTRA_INFO: usize = 0x484B_4D4D;

/// Whether the keyboard hook takes injected key events (SendInput, other programs' simulated
/// typing) into account. Off by default: they pass through untouched, so a program that types
/// for the user, such as a paste routine, can never trigger or swallow a hotkey. Only tests
/// that drive the hook with SendInput turn it on.
static ACCEPT_INJECTED: AtomicBool = AtomicBool::new(false);

/// Sets whether injected keyboard events count for hotkeys (default: they do not). The setting
/// is process-wide and takes effect on the next key event.
pub fn accept_injected_input(accept: bool) {
    ACCEPT_INJECTED.store(accept, Ordering::Relaxed);
}

fn injected_input_accepted() -> bool {
    ACCEPT_INJECTED.load(Ordering::Relaxed)
}

/// Whether a keyboard event must pass the hook untouched: no modifier tracking, no reconciliation,
/// no event, no swallowing. That is the menu mask we inject ourselves, and (unless injected input
/// is accepted) any event with an injected flag. `flags` is `KBDLLHOOKSTRUCT::flags`.
fn passes_untouched(vk_code: u16, flags: u32, extra_info: usize, accept_injected: bool) -> bool {
    let own_mask = vk_code == MENU_MASK_VK && extra_info == MENU_MASK_EXTRA_INFO;
    let injected = flags & (LLKHF_INJECTED.0 | LLKHF_LOWER_IL_INJECTED.0) != 0;
    own_mask || (injected && !accept_injected)
}

// WTS session notification plumbing not exposed by the `windows` crate bindings.
const NOTIFY_FOR_THIS_SESSION: u32 = 0;
const WM_WTSSESSION_CHANGE: u32 = 0x02B1;
// WM_WTSSESSION_CHANGE wParam values (wtsapi32.h).
const WTS_CONSOLE_CONNECT: usize = 0x1;
const WTS_REMOTE_CONNECT: usize = 0x3;
const WTS_SESSION_LOCK: usize = 0x7;
const WTS_SESSION_UNLOCK: usize = 0x8;
// WM_POWERBROADCAST wParam values (winuser.h) -- the PBT_* constants live
// behind the Win32_System_Power feature, not worth enabling for two values.
// RESUMEAUTOMATIC fires on every wake; RESUMESUSPEND follows it only when the
// wake was user-initiated.
const PBT_APMRESUMESUSPEND: usize = 0x7;
const PBT_APMRESUMEAUTOMATIC: usize = 0x12;

/// The side-specific modifier keys we track, paired with their virtual-key
/// codes. Ordered to match `state::MODIFIER_RELEASE_ORDER`.
const MODIFIER_KEYS: [(VIRTUAL_KEY, Modifiers); 8] = [
    (VK_LWIN, Modifiers::CMD_LEFT),
    (VK_RWIN, Modifiers::CMD_RIGHT),
    (VK_LSHIFT, Modifiers::SHIFT_LEFT),
    (VK_RSHIFT, Modifiers::SHIFT_RIGHT),
    (VK_LCONTROL, Modifiers::CTRL_LEFT),
    (VK_RCONTROL, Modifiers::CTRL_RIGHT),
    (VK_LMENU, Modifiers::OPT_LEFT),
    (VK_RMENU, Modifiers::OPT_RIGHT),
];

/// Thread-local state for the keyboard hook callback.
///
/// Windows low-level hooks require a callback function with a specific signature,
/// so we use thread-local storage to access our state from within the callback.
struct HookContext {
    event_sender: Sender<KeyEvent>,
    current_modifiers: Modifiers,
    blocking_hotkeys: Option<BlockingHotkeys>,
    /// AltGr's phantom Left Ctrl is currently held. The hook drops the
    /// phantom's own events, but GetAsyncKeyState still reports LCtrl as
    /// down while AltGr is held, so reconciliation must not adopt it.
    altgr_phantom_ctrl: bool,
    /// A menu mask has been injected for the current Win/Alt hold. One per
    /// hold defuses the shell's menu heuristic for the whole hold; cleared
    /// once no Win/Alt modifier remains held.
    menu_mask_sent: bool,
    /// Modifier keys whose key-down a hotkey swallowed and whose key-up has not been seen yet.
    /// While a key is in here its auto-repeats and its key-up are swallowed too, and
    /// reconciliation treats it as physically held (the system's async key state never saw the
    /// swallowed key-down). Cleared by a session change or resume, where key-ups get lost, and
    /// per key when it has been silent for `STALE_SWALLOW_MS` as another key goes down.
    swallowed_modifiers: SwallowedModifiers,
    /// The last non-modifier key that went down and has not come up yet. A key-down of the same
    /// key again is an auto-repeat.
    last_key_down: Option<Key>,
}

thread_local! {
    static HOOK_CONTEXT: std::cell::RefCell<Option<HookContext>> = const { std::cell::RefCell::new(None) };

    /// Set by `watcher_wndproc` when a message delivered via SendMessage
    /// (bypassing the queue) asks for a hook re-install. The message loop
    /// owns the hook handles, so the wndproc can only request the re-install,
    /// not perform it. Thread-local: the wndproc runs on the hook thread, and
    /// a second listener in the same process must not see this one's requests.
    static REINSTALL_REQUESTED: std::cell::Cell<bool> = const { std::cell::Cell::new(false) };
}

/// Take (and clear) a pending hook re-install request from the wndproc.
fn take_reinstall_request() -> bool {
    REINSTALL_REQUESTED.with(|flag| flag.replace(false))
}

/// What draining the thread message queue observed.
#[derive(Default)]
struct DrainOutcome {
    /// WM_QUIT received -- exit the message loop.
    quit: bool,
    /// A session change (lock/unlock/connect) or a resume from sleep
    /// occurred -- reconcile modifier state, since key-ups on the secure
    /// desktop (or from before a suspend) never reach the hook.
    reconcile: bool,
    /// The interactive desktop came back (unlock, console/remote connect, or
    /// resume from sleep) -- re-install hooks in case Windows silently
    /// removed them.
    reinstall_hooks: bool,
}

/// Drain all pending thread messages.
fn drain_thread_messages(msg: &mut MSG) -> DrainOutcome {
    let mut outcome = DrainOutcome::default();
    unsafe {
        while PeekMessageW(msg, None, 0, 0, PM_REMOVE).as_bool() {
            if msg.message == WM_QUIT {
                outcome.quit = true;
                return outcome;
            }
            if msg.message == WM_WTSSESSION_CHANGE {
                match msg.wParam.0 {
                    WTS_SESSION_LOCK => outcome.reconcile = true,
                    WTS_SESSION_UNLOCK | WTS_CONSOLE_CONNECT | WTS_REMOTE_CONNECT => {
                        outcome.reconcile = true;
                        outcome.reinstall_hooks = true;
                    }
                    _ => {}
                }
            }
            // WM_POWERBROADCAST is documented as sent, not posted, so the
            // wndproc is the expected delivery path; this arm is a cheap
            // safety net in case some Windows build posts it instead.
            if msg.message == WM_POWERBROADCAST
                && matches!(msg.wParam.0, PBT_APMRESUMESUSPEND | PBT_APMRESUMEAUTOMATIC)
            {
                outcome.reconcile = true;
                outcome.reinstall_hooks = true;
            }
            let _ = TranslateMessage(msg);
            DispatchMessageW(msg);
        }
    }
    outcome
}

/// Wait for new input/messages or until timeout expires.
fn wait_for_message_or_timeout(timeout_ms: u32) {
    unsafe {
        let _ = MsgWaitForMultipleObjects(None, false, timeout_ms, QS_ALLINPUT);
    }
}

/// Snapshot which tracked modifier keys are physically held right now.
///
/// Note: if the calling thread's desktop is not active (e.g. the lock screen's
/// secure desktop is up), GetAsyncKeyState reports every key as up -- which is
/// the correct answer for our purposes: treat everything as released.
fn physical_modifiers() -> Modifiers {
    let mut held = Modifiers::empty();
    for (vk, modifier) in MODIFIER_KEYS {
        // High bit set = key currently down.
        if unsafe { GetAsyncKeyState(vk.0 as i32) } as u16 & 0x8000 != 0 {
            held |= modifier;
        }
    }
    held
}

/// Physically-held modifiers we missed the press of and should adopt.
/// (`stale_modifiers`/`release_events`, the other half of reconciliation,
/// live in crate::platform::state, shared with the Linux listener.)
///
/// While AltGr's phantom Left Ctrl is held, CTRL_LEFT is excluded: it is
/// physically down per GetAsyncKeyState, but the user only pressed Right
/// Alt. A real Left Ctrl press during that window still sets CTRL_LEFT
/// through its own hook event (which carries a non-phantom scancode).
fn adoptable_modifiers(
    tracked: Modifiers,
    physical: Modifiers,
    altgr_phantom_ctrl: bool,
) -> Modifiers {
    let mut adopt = physical & RECONCILABLE & !tracked;
    if altgr_phantom_ctrl {
        adopt &= !Modifiers::CTRL_LEFT;
    }
    adopt
}

/// Reconcile tracked modifiers against the physical keyboard state.
///
/// Corrects drift from missed events: secure desktop transitions swallow
/// key-ups (Win+L delivers the Win key DOWN but its UP happens on the secure
/// desktop), leaving modifiers stuck on until the user happens to press them
/// again. Mirrors `reconcile_modifiers` in the macOS listener.
///
/// Stale modifiers are cleared with synthetic release events so consumers
/// tracking press/release state recover; missed presses are adopted silently
/// and ride along on the next real event.
///
/// Known micro-race: GetAsyncKeyState reads the state *now*, not at the time
/// the event being processed was generated, so a modifier pressed in the
/// sub-millisecond window between a keystroke entering the queue and its hook
/// callback running gets adopted onto that earlier keystroke's event. It is
/// self-correcting (the modifier's own event arrives right after) and
/// unavoidable with this API — macOS avoids it because CGEvent flags are
/// stamped per event.
fn reconcile_modifiers(ctx: &mut HookContext) {
    reconcile_modifiers_from(ctx, physical_modifiers());
}

/// [`reconcile_modifiers`] with the async key state given, so that the logic can be tested
/// without reading the real keyboard.
fn reconcile_modifiers_from(ctx: &mut HookContext, async_held: Modifiers) {
    // Keys whose down we swallowed never reached the async key state but are still held.
    let physical = async_held | ctx.swallowed_modifiers.held();
    // Phantom Ctrl only exists while AltGr is held; if LCtrl reads as up,
    // the phantom is gone (covers a phantom key-up swallowed by a secure
    // desktop transition, which would otherwise suppress CTRL_LEFT adoption
    // forever).
    if ctx.altgr_phantom_ctrl && !physical.contains(Modifiers::CTRL_LEFT) {
        ctx.altgr_phantom_ctrl = false;
    }
    for event in release_events(
        ctx.current_modifiers,
        stale_modifiers(ctx.current_modifiers, physical),
    ) {
        log::debug!(
            "hook: reconcile releases {:?} (tracked {:?}, async {async_held:?}, swallowed {:?})",
            event.changed_modifier,
            ctx.current_modifiers,
            ctx.swallowed_modifiers.held()
        );
        ctx.current_modifiers = event.modifiers;
        let _ = ctx.event_sender.send(event);
    }
    ctx.current_modifiers |=
        adoptable_modifiers(ctx.current_modifiers, physical, ctx.altgr_phantom_ctrl);
    // A Win/Alt-up swallowed by a secure desktop transition ends the hold
    // without the modifier branch observing it; clear the mask flag here so
    // the next hold gets its own mask.
    if !ctx.current_modifiers.intersects(MENU_MODIFIERS) {
        ctx.menu_mask_sent = false;
    }
}

/// Reconcile modifier state from the hook thread's message loop (used on
/// session change and resume from sleep, where no input event accompanies
/// the state change).
fn reconcile_modifiers_in_context() {
    HOOK_CONTEXT.with(|ctx_cell| {
        if let Some(ctx) = ctx_cell.borrow_mut().as_mut() {
            reconcile_after_session_change(ctx, physical_modifiers());
        }
    });
}

/// What a session change or a resume does to the hook state, given the async key state. The
/// key-ups of swallowed keys (and of an ordinary key that was down) may have gone to the secure
/// desktop, so the hook forgets them and takes the system's word for what is held.
fn reconcile_after_session_change(ctx: &mut HookContext, async_held: Modifiers) {
    log::debug!("hook: session change or resume; forgetting swallowed keys");
    ctx.swallowed_modifiers.clear();
    ctx.last_key_down = None;
    reconcile_modifiers_from(ctx, async_held);
}

/// Wndproc for the watcher window. (The `windows` crate's DefWindowProcW is
/// a generic Rust wrapper, so it cannot be used as lpfnWndProc directly.)
///
/// Handles both delivery modes together with the drain loop: the drain loop
/// covers posted delivery of WM_WTSSESSION_CHANGE (what Windows 11 26200
/// does, verified live), while this path covers builds that deliver it via
/// SendMessage, which bypasses the message queue — and WM_POWERBROADCAST,
/// which is documented as always sent. Double handling on the posted path is
/// harmless: the second reconcile finds nothing stale, and re-install
/// requests coalesce through REINSTALL_REQUESTED. The re-install itself
/// stays on the message loop, which owns the hook handles; this wndproc only
/// requests it.
unsafe extern "system" fn watcher_wndproc(
    hwnd: HWND,
    msg: u32,
    wparam: WPARAM,
    lparam: LPARAM,
) -> LRESULT {
    match msg {
        WM_WTSSESSION_CHANGE => match wparam.0 {
            WTS_SESSION_LOCK => reconcile_modifiers_in_context(),
            WTS_SESSION_UNLOCK | WTS_CONSOLE_CONNECT | WTS_REMOTE_CONNECT => {
                reconcile_modifiers_in_context();
                REINSTALL_REQUESTED.with(|flag| flag.set(true));
            }
            _ => {}
        },
        // Resume from sleep/hibernate: Windows can silently drop LL hooks
        // across a suspend (Handy #1620), and key-ups from before the
        // suspend are long gone -- re-arm the hooks and reconcile.
        WM_POWERBROADCAST => {
            if matches!(wparam.0, PBT_APMRESUMESUSPEND | PBT_APMRESUMEAUTOMATIC) {
                reconcile_modifiers_in_context();
                REINSTALL_REQUESTED.with(|flag| flag.set(true));
            }
        }
        _ => {}
    }
    DefWindowProcW(hwnd, msg, wparam, lparam)
}

/// Create a hidden top-level window that watches WTS session changes and
/// power (suspend/resume) broadcasts. Top-level, not message-only, is load-
/// bearing: broadcast messages such as WM_POWERBROADCAST are never delivered
/// to message-only windows. Without WS_VISIBLE it has no taskbar or Alt-Tab
/// presence. WM_WTSSESSION_CHANGE is posted to it and picked out of the
/// queue by `drain_thread_messages` (verified live: lock 0x7 and unlock 0x8
/// both arrive); WM_POWERBROADCAST is sent and lands in `watcher_wndproc`.
///
/// Returns None on failure. Non-fatal: hooks still work, and stale modifiers
/// are still corrected lazily by `reconcile_modifiers` on the next key event.
unsafe fn create_watcher_window() -> Option<HWND> {
    let class_name: Vec<u16> = "HandyKeysSystemWatcher\0".encode_utf16().collect();
    let wnd_class = WNDCLASSW {
        lpfnWndProc: Some(watcher_wndproc),
        lpszClassName: PCWSTR(class_name.as_ptr()),
        ..Default::default()
    };
    // May fail with ERROR_CLASS_ALREADY_EXISTS (e.g. a second listener in the
    // same process); CreateWindowExW below still succeeds against the
    // existing class, so the result is deliberately ignored.
    RegisterClassW(&wnd_class);

    let hwnd = CreateWindowExW(
        WINDOW_EX_STYLE::default(),
        PCWSTR(class_name.as_ptr()),
        PCWSTR::null(),
        WINDOW_STYLE::default(),
        0,
        0,
        0,
        0,
        None,
        None,
        None,
        None,
    )
    .ok()?;

    if WTSRegisterSessionNotification(hwnd, NOTIFY_FOR_THIS_SESSION).is_err() {
        let _ = DestroyWindow(hwnd);
        return None;
    }

    Some(hwnd)
}

/// Clean up the watcher window. Must run on the creating thread.
unsafe fn destroy_watcher_window(hwnd: HWND) {
    let _ = WTSUnRegisterSessionNotification(hwnd);
    let _ = DestroyWindow(hwnd);
}

/// Re-install the low-level hooks, defensively: Windows silently removes an LL
/// hook whose callback exceeds its timeout budget, and a session away from the
/// interactive desktop or a suspend/resume cycle is a common moment for that
/// to surface (Handy #1620). (Note lock/unlock does NOT inherently invalidate
/// hooks -- they survived it in live testing.)
///
/// The replacements are installed before the old hooks are removed, so a
/// failure never leaves us hook-less. No messages are pumped between install
/// and unhook, so no event is delivered twice.
unsafe fn reinstall_hooks(kb_hook: &mut HHOOK, mouse_hook: &mut HHOOK) -> bool {
    let new_kb = match SetWindowsHookExW(WH_KEYBOARD_LL, Some(keyboard_hook_proc), None, 0) {
        Ok(h) => h,
        Err(_) => return false,
    };
    let new_mouse = match SetWindowsHookExW(WH_MOUSE_LL, Some(mouse_hook_proc), None, 0) {
        Ok(h) => h,
        Err(_) => {
            let _ = UnhookWindowsHookEx(new_kb);
            return false;
        }
    };
    let _ = UnhookWindowsHookEx(*kb_hook);
    let _ = UnhookWindowsHookEx(*mouse_hook);
    *kb_hook = new_kb;
    *mouse_hook = new_mouse;
    true
}

/// Internal listener state returned to KeyboardListener
pub(crate) struct WindowsListenerState {
    pub event_receiver: mpsc::Receiver<KeyEvent>,
    pub thread_handle: Option<JoinHandle<()>>,
    pub running: Arc<AtomicBool>,
    pub blocking_hotkeys: Option<BlockingHotkeys>,
}

/// Spawn a Windows low-level keyboard hook listener
pub(crate) fn spawn(blocking_hotkeys: Option<BlockingHotkeys>) -> Result<WindowsListenerState> {
    let (tx, rx) = mpsc::channel();
    let running = Arc::new(AtomicBool::new(true));
    let thread_running = Arc::clone(&running);
    let thread_blocking = blocking_hotkeys.clone();

    let handle = thread::spawn(move || {
        // Initialize thread-local hook context
        HOOK_CONTEXT.with(|ctx| {
            *ctx.borrow_mut() = Some(HookContext {
                event_sender: tx,
                current_modifiers: Modifiers::empty(),
                blocking_hotkeys: thread_blocking,
                altgr_phantom_ctrl: false,
                menu_mask_sent: false,
                swallowed_modifiers: SwallowedModifiers::default(),
                last_key_down: None,
            });
        });

        // Install the low-level keyboard hook
        let kb_hook =
            unsafe { SetWindowsHookExW(WH_KEYBOARD_LL, Some(keyboard_hook_proc), None, 0) };

        let mut kb_hook = match kb_hook {
            Ok(h) => h,
            Err(e) => {
                eprintln!("Failed to install keyboard hook: {:?}", e);
                return;
            }
        };

        // Install the low-level mouse hook
        let mouse_hook = unsafe { SetWindowsHookExW(WH_MOUSE_LL, Some(mouse_hook_proc), None, 0) };

        let mut mouse_hook = match mouse_hook {
            Ok(h) => h,
            Err(e) => {
                eprintln!("Failed to install mouse hook: {:?}", e);
                // Clean up keyboard hook before returning
                unsafe {
                    let _ = UnhookWindowsHookEx(kb_hook);
                }
                return;
            }
        };

        // Watch for session changes (Win+L lock/unlock, RDP connect) and
        // suspend/resume: the secure desktop swallows key-up events, so
        // modifier state must be reconciled when the session comes back, and
        // Windows can silently drop LL hooks across a sleep, so they are
        // re-armed on resume.
        let watcher_hwnd = unsafe { create_watcher_window() };

        // Message loop - required for low-level hooks to function.
        // Keep the short timeout so shutdown polling behavior remains unchanged.
        let mut msg = MSG::default();
        loop {
            super::observation::tick();
            // Check if we should stop
            if !thread_running.load(Ordering::SeqCst) {
                break;
            }

            // Process all pending messages
            let outcome = drain_thread_messages(&mut msg);
            if outcome.quit {
                break;
            }
            if outcome.reconcile {
                reconcile_modifiers_in_context();
            }
            // The wndproc requests re-installs for messages that arrive via
            // SendMessage (dispatched inside PeekMessageW, never seen by the
            // drain loop) -- WM_POWERBROADCAST always, WM_WTSSESSION_CHANGE
            // on builds that send rather than post it.
            if outcome.reinstall_hooks || take_reinstall_request() {
                unsafe {
                    if reinstall_hooks(&mut kb_hook, &mut mouse_hook) {
                        // At most a few lines per unlock/resume, and it makes
                        // the re-arm observable in the field.
                        eprintln!("handy-keys: re-armed hooks after session/power change");
                    } else {
                        // Keep the old hooks: they usually still work (the
                        // reinstall is defensive hardening, not a repair).
                        eprintln!(
                            "handy-keys: failed to re-install hooks after session/power change"
                        );
                    }
                }
            }

            // Wait for messages or timeout — unlike thread::sleep, this returns
            // immediately when a message arrives, so hook callbacks are never delayed.
            wait_for_message_or_timeout(HOOK_LOOP_TIMEOUT_MS);
        }

        // Clean up the watcher window, then the hooks
        if let Some(hwnd) = watcher_hwnd {
            unsafe {
                destroy_watcher_window(hwnd);
            }
        }
        unsafe {
            let _ = UnhookWindowsHookEx(kb_hook);
            let _ = UnhookWindowsHookEx(mouse_hook);
        }

        // Clear thread-local state
        HOOK_CONTEXT.with(|ctx| {
            *ctx.borrow_mut() = None;
        });
    });

    Ok(WindowsListenerState {
        event_receiver: rx,
        thread_handle: Some(handle),
        running,
        blocking_hotkeys,
    })
}

/// Low-level keyboard hook callback
///
/// This function is called by Windows for every keyboard event system-wide.
/// It must return quickly to avoid input lag.
unsafe extern "system" fn keyboard_hook_proc(code: i32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
    // If code < 0, we must pass to next hook without processing
    if code < 0 {
        return CallNextHookEx(None, code, wparam, lparam);
    }

    let mut should_block = false;
    let mut needs_mask = false;

    // Process the keyboard event
    HOOK_CONTEXT.with(|ctx_cell| {
        let mut ctx_ref = ctx_cell.borrow_mut();
        if let Some(ctx) = ctx_ref.as_mut() {
            // Extract key information from KBDLLHOOKSTRUCT
            let kb_struct = &*(lparam.0 as *const KBDLLHOOKSTRUCT);
            let vk_code = kb_struct.vkCode as u16;
            let scan_code = kb_struct.scanCode;
            let is_extended = (kb_struct.flags.0 & LLKHF_EXTENDED.0) != 0;

            let is_key_down = matches!(wparam.0 as u32, WM_KEYDOWN | WM_SYSKEYDOWN);

            // Raphael patch: the raw Alt events, before any filtering, for tracking down a hotkey
            // that does not fire (which window is in front, and the flags Windows set).
            if matches!(vk_code, 0xA4 | 0xA5) {
                let mut pid = 0u32;
                let foreground = GetForegroundWindow();
                GetWindowThreadProcessId(foreground, Some(&mut pid));
                log::debug!(
                    "hook: raw vk=0x{vk_code:02X} down={is_key_down} flags=0x{:02X} extra={:#x} foreground_pid={pid} own={}",
                    kb_struct.flags.0,
                    kb_struct.dwExtraInfo,
                    pid == std::process::id()
                );
            }

            // Our own injected menu mask: pass it through untouched. It
            // exists solely for the shell's benefit and must not update
            // modifier state, trigger reconciliation, or be emitted.
            // Likewise any other injected event (see ACCEPT_INJECTED).
            if passes_untouched(
                vk_code,
                kb_struct.flags.0,
                kb_struct.dwExtraInfo,
                injected_input_accepted(),
            ) {
                return;
            }

            // On AltGr layouts Windows synthesizes a Left Ctrl press/release
            // around every Right Alt event (captured live on UK layout:
            // identical timestamps, scancode 0x21D). Drop it entirely — no
            // modifier update, no emit — so AltGr+key reads as OptRight
            // only. The event is still passed down the hook chain.
            if is_altgr_phantom_ctrl(vk_code, scan_code) {
                ctx.altgr_phantom_ctrl = is_key_down;
                return;
            }

            // Check if this is a modifier key
            if let Some(modifier) = vk_to_modifier(vk_code) {
                let prev_modifiers = ctx.current_modifiers;

                // Update modifier state
                if is_key_down {
                    ctx.current_modifiers |= modifier;
                } else {
                    ctx.current_modifiers &= !modifier;
                }

                // Only emit event if modifiers actually changed
                if ctx.current_modifiers != prev_modifiers {
                    // Check if modifier-only combo should be blocked
                    should_block =
                        should_block_hotkey(&ctx.blocking_hotkeys, ctx.current_modifiers, None);

                    // A blocked modifier press (the Shift of a Win+Shift
                    // modifier-only hotkey) is as invisible to the shell as
                    // a blocked regular key, so it needs the same mask.
                    if needs_menu_mask(should_block, ctx.current_modifiers, ctx.menu_mask_sent) {
                        ctx.menu_mask_sent = true;
                        needs_mask = true;
                    }

                    let _ = ctx.event_sender.send(KeyEvent {
                        modifiers: ctx.current_modifiers,
                        key: None,
                        is_key_down,
                        changed_modifier: Some(modifier),
                    });
                }

                // The foreground app never saw the first key-down, so it must not see the
                // auto-repeats (they would look like fresh presses) or the key-up (a lone
                // Alt/Win release opens a menu) either.
                should_block = swallow_modifier_event(
                    &mut ctx.swallowed_modifiers,
                    modifier,
                    is_key_down,
                    should_block,
                    now_ms(),
                );
                log::debug!(
                    "hook: modifier vk=0x{vk_code:02X} down={is_key_down} {prev_modifiers:?} -> {:?} block={should_block} swallowed={:?}",
                    ctx.current_modifiers,
                    ctx.swallowed_modifiers.held()
                );

                // The hold ends once no Win/Alt modifier remains held; the
                // next hold gets a fresh mask.
                if !ctx.current_modifiers.intersects(MENU_MODIFIERS) {
                    ctx.menu_mask_sent = false;
                }
            } else {
                // Non-modifier key (see `handle_regular_key`).
                let outcome = handle_regular_key(
                    ctx,
                    map_key(vk_code, scan_code, is_extended),
                    is_key_down,
                    physical_modifiers(),
                    now_ms(),
                );
                should_block = outcome.should_block;
                needs_mask = outcome.needs_mask;
            }
        }
    });

    super::observation::keyboard(
        &*(lparam.0 as *const KBDLLHOOKSTRUCT),
        wparam.0 as u32,
        should_block,
    );

    // Outside the HOOK_CONTEXT borrow: the injected events re-enter this
    // hook (where the dwExtraInfo marker passes them straight through).
    if needs_mask {
        send_menu_mask();
    }

    if should_block {
        // Return non-zero to block the event from propagating
        LRESULT(1)
    } else {
        // Pass to next hook in chain
        CallNextHookEx(None, code, wparam, lparam)
    }
}

/// Low-level mouse hook callback
///
/// This function is called by Windows for every mouse event system-wide.
unsafe extern "system" fn mouse_hook_proc(code: i32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
    // If code < 0, we must pass to next hook without processing
    if code < 0 {
        return CallNextHookEx(None, code, wparam, lparam);
    }

    // Only button transitions matter; bail out early for moves and wheel
    // events so the hot path stays free of state access.
    if !matches!(
        wparam.0 as u32,
        WM_LBUTTONDOWN
            | WM_LBUTTONUP
            | WM_RBUTTONDOWN
            | WM_RBUTTONUP
            | WM_MBUTTONDOWN
            | WM_MBUTTONUP
            | WM_XBUTTONDOWN
            | WM_XBUTTONUP
    ) {
        return CallNextHookEx(None, code, wparam, lparam);
    }

    let mut should_block = false;
    let mut needs_mask = false;

    super::observation::mouse(&*(lparam.0 as *const MSLLHOOKSTRUCT));

    // Process the mouse event
    HOOK_CONTEXT.with(|ctx_cell| {
        let mut ctx_ref = ctx_cell.borrow_mut();
        if let Some(ctx) = ctx_ref.as_mut() {
            let mouse_struct = &*(lparam.0 as *const MSLLHOOKSTRUCT);

            // Stale modifiers must not gate button reporting (or decorate the
            // event), so reconcile before reading them.
            reconcile_modifiers(ctx);

            // Only report left/right clicks when modifiers are held (to avoid noise)
            let has_modifiers = !ctx.current_modifiers.is_empty();

            let (key, is_down) = match wparam.0 as u32 {
                WM_LBUTTONDOWN if has_modifiers => (Some(Key::MouseLeft), true),
                WM_LBUTTONUP if has_modifiers => (Some(Key::MouseLeft), false),
                WM_RBUTTONDOWN if has_modifiers => (Some(Key::MouseRight), true),
                WM_RBUTTONUP if has_modifiers => (Some(Key::MouseRight), false),
                // Middle and X buttons always reported
                WM_MBUTTONDOWN => (Some(Key::MouseMiddle), true),
                WM_MBUTTONUP => (Some(Key::MouseMiddle), false),
                WM_XBUTTONDOWN => {
                    // High word of mouseData contains which X button (1 or 2)
                    let xbutton = (mouse_struct.mouseData >> 16) & 0xFFFF;
                    let key = if xbutton == 1 {
                        Some(Key::MouseX1)
                    } else if xbutton == 2 {
                        Some(Key::MouseX2)
                    } else {
                        None
                    };
                    (key, true)
                }
                WM_XBUTTONUP => {
                    let xbutton = (mouse_struct.mouseData >> 16) & 0xFFFF;
                    let key = if xbutton == 1 {
                        Some(Key::MouseX1)
                    } else if xbutton == 2 {
                        Some(Key::MouseX2)
                    } else {
                        None
                    };
                    (key, false)
                }
                _ => (None, false),
            };

            if let Some(key) = key {
                should_block =
                    should_block_hotkey(&ctx.blocking_hotkeys, ctx.current_modifiers, Some(key));

                // A blocked Win/Alt+click hides the click from the shell
                // just like a blocked key, so the hold needs the same mask.
                if needs_menu_mask(should_block, ctx.current_modifiers, ctx.menu_mask_sent) {
                    ctx.menu_mask_sent = true;
                    needs_mask = true;
                }

                let _ = ctx.event_sender.send(KeyEvent {
                    modifiers: ctx.current_modifiers,
                    key: Some(key),
                    is_key_down: is_down,
                    changed_modifier: None,
                });
            }
        }
    });

    if needs_mask {
        send_menu_mask();
    }

    if should_block {
        // Return non-zero to block the event from propagating
        LRESULT(1)
    } else {
        // Pass to next hook in chain
        CallNextHookEx(None, code, wparam, lparam)
    }
}

/// Whether blocking this event must be accompanied by a menu-mask injection:
/// a real event is being swallowed while a Win/Alt modifier is held, and the
/// current hold has not been masked yet. Key-ups need masking too: a key
/// pressed *before* the modifier and released during the hold has its up
/// blocked, but its down passed to the shell before the hold began, so it
/// never disarmed the menu heuristic (verified live: without the up-mask,
/// F20↓ Win↓ F20↑ Win↑ with Win+F20 blocked opens Start). `already_sent`
/// dedupes auto-repeat, release edges, and later events within one hold.
fn needs_menu_mask(should_block: bool, modifiers: Modifiers, already_sent: bool) -> bool {
    should_block && !already_sent && modifiers.intersects(MENU_MODIFIERS)
}

/// How long a swallowed modifier key may be silent before another key press treats it as
/// released. While a key is physically held, Windows repeats it (the most recently pressed key
/// repeats; the first repeat comes after the keyboard delay setting, at most 1 s), so a swallowed
/// modifier that is really held produces an event at least every 1 s until some other key goes
/// down. Longer than that means its key-up was missed (an elevated window took focus, the hook
/// timed out, ...): see `SwallowedModifiers::stale`. In Right Alt+V the Alt repeats until V goes
/// down, so the last Alt event is recent when V arrives and the chord still works.
const STALE_SWALLOW_MS: u64 = 1500;

/// Milliseconds on a monotonic clock, counted from the first call. Only differences matter.
fn now_ms() -> u64 {
    static START: OnceLock<Instant> = OnceLock::new();
    START.get_or_init(Instant::now).elapsed().as_millis() as u64
}

/// The modifier keys whose key-down a hotkey swallowed and whose key-up has not been seen, each
/// with the time of its last event (the key-down or the latest auto-repeat).
#[derive(Debug)]
struct SwallowedModifiers {
    held: Modifiers,
    /// Clock value of each key's last event, in `MODIFIER_KEYS` order; meaningful while the key
    /// is in `held`.
    last_event_ms: [u64; MODIFIER_KEYS.len()],
}

impl Default for SwallowedModifiers {
    fn default() -> Self {
        Self {
            held: Modifiers::empty(),
            last_event_ms: [0; MODIFIER_KEYS.len()],
        }
    }
}

impl SwallowedModifiers {
    fn held(&self) -> Modifiers {
        self.held
    }

    /// Records a swallowed key-down (the first or a repeat) of `modifier` at `now_ms`.
    fn touch(&mut self, modifier: Modifiers, now_ms: u64) {
        self.held.insert(modifier);
        if let Some(index) = MODIFIER_KEYS.iter().position(|(_, m)| *m == modifier) {
            self.last_event_ms[index] = now_ms;
        }
    }

    /// The swallowed keys to treat as released: silent for more than `STALE_SWALLOW_MS` at
    /// `now_ms` and not reported held by the system (`reported_held`, the async key state).
    /// A pure function of its arguments.
    fn stale(&self, now_ms: u64, reported_held: Modifiers) -> Modifiers {
        let mut stale = Modifiers::empty();
        for (index, (_, modifier)) in MODIFIER_KEYS.iter().enumerate() {
            let silent_for = now_ms.saturating_sub(self.last_event_ms[index]);
            if self.held.contains(*modifier)
                && !reported_held.contains(*modifier)
                && silent_for > STALE_SWALLOW_MS
            {
                stale |= *modifier;
            }
        }
        stale
    }

    fn forget(&mut self, gone: Modifiers) {
        self.held.remove(gone);
    }

    fn clear(&mut self) {
        self.held = Modifiers::empty();
    }
}

/// Whether a modifier key event must be swallowed, and bookkeeping for keys held swallowed.
///
/// `blocked_by_hotkey` is what the hotkey table said for this event; for an auto-repeat it is
/// false, because a repeat does not change the modifier state. A key-down that a hotkey blocks
/// starts a swallowed hold; the repeats and the key-up of that hold are swallowed too, and the
/// key-up ends it. The key-up of a key whose key-down was not swallowed is never swallowed,
/// whatever the table says after it: the app saw the press, so it must see the release, or the
/// key would stay down system-wide.
fn swallow_modifier_event(
    swallowed: &mut SwallowedModifiers,
    modifier: Modifiers,
    is_key_down: bool,
    blocked_by_hotkey: bool,
    now_ms: u64,
) -> bool {
    if is_key_down {
        let swallow = blocked_by_hotkey || swallowed.held.contains(modifier);
        if swallow {
            swallowed.touch(modifier, now_ms);
        }
        swallow
    } else if swallowed.held.contains(modifier) {
        swallowed.forget(modifier);
        true
    } else {
        false
    }
}

/// What the hook decides for a key event, besides sending the `KeyEvent`.
#[derive(Debug, Default)]
struct KeyOutcome {
    /// Swallow the event.
    should_block: bool,
    /// Inject the menu mask once the event is handled.
    needs_mask: bool,
}

/// Handles a key event that is not a modifier key, given the async key state and the clock
/// (`key` is `None` for a key the crate does not know). Everything the hook does for such a key
/// except the system calls, so that it can be tested without a keyboard.
fn handle_regular_key(
    ctx: &mut HookContext,
    key: Option<Key>,
    is_key_down: bool,
    async_held: Modifiers,
    now_ms: u64,
) -> KeyOutcome {
    let is_repeat = match key {
        Some(key) if is_key_down => ctx.last_key_down.replace(key) == Some(key),
        Some(key) => {
            if ctx.last_key_down == Some(key) {
                ctx.last_key_down = None;
            }
            false
        }
        None => false,
    };

    // A swallowed modifier that has been silent too long lost its key-up: forget it before the
    // reconciliation below, which then releases it from the tracked state. Only a new key press
    // asks the question. The release of a chord key and the repeats of a key that was already
    // accepted come while the modifier is quiet (Windows repeats the most recent key only), and
    // must not undo the chord.
    if is_key_down && !is_repeat {
        let stale = ctx.swallowed_modifiers.stale(now_ms, async_held);
        if !stale.is_empty() {
            log::debug!("hook: forgetting stale swallowed {stale:?}");
        }
        ctx.swallowed_modifiers.forget(stale);
    }

    // Reconcile tracked modifiers against the physical keyboard first, so a key-up missed during
    // a secure desktop transition can't stick a modifier onto this event. (Not done for modifier
    // events: inside a low-level hook the async key state does not yet include the in-flight
    // change, so reconciling there would fight the toggle logic.)
    reconcile_modifiers_from(ctx, async_held);

    let mut outcome = KeyOutcome::default();
    if let Some(key) = key {
        outcome.should_block =
            should_block_hotkey(&ctx.blocking_hotkeys, ctx.current_modifiers, Some(key));

        // The shell can no longer see the key we are about to swallow; without a substitute,
        // releasing Win/Alt would read as a lone tap and open the Start menu (#917).
        if needs_menu_mask(
            outcome.should_block,
            ctx.current_modifiers,
            ctx.menu_mask_sent,
        ) {
            ctx.menu_mask_sent = true;
            outcome.needs_mask = true;
        }

        let _ = ctx.event_sender.send(KeyEvent {
            modifiers: ctx.current_modifiers,
            key: Some(key),
            is_key_down,
            changed_modifier: None,
        });
    }
    outcome
}

/// Inject a press+release of the menu mask key via SendInput. Must be called
/// outside the HOOK_CONTEXT borrow, since the injected events re-enter the
/// keyboard hook on this thread's next message pump.
fn send_menu_mask() {
    let key = |flags: KEYBD_EVENT_FLAGS| INPUT {
        r#type: INPUT_KEYBOARD,
        Anonymous: INPUT_0 {
            ki: KEYBDINPUT {
                wVk: VIRTUAL_KEY(MENU_MASK_VK),
                wScan: 0,
                dwFlags: flags,
                time: 0,
                dwExtraInfo: MENU_MASK_EXTRA_INFO,
            },
        },
    };
    let inputs = [key(KEYBD_EVENT_FLAGS(0)), key(KEYEVENTF_KEYUP)];
    let sent = unsafe { SendInput(&inputs, std::mem::size_of::<INPUT>() as i32) };
    if sent != inputs.len() as u32 {
        // Rare (e.g. UIPI filtering); the cost is the pre-mask behavior:
        // the shell may open its menu when the modifier is released.
        eprintln!("handy-keys: menu mask injection failed");
    }
}

/// Check if a hotkey combination should be blocked
fn should_block_hotkey(
    blocking_hotkeys: &Option<BlockingHotkeys>,
    modifiers: Modifiers,
    key: Option<Key>,
) -> bool {
    if let Some(ref hotkeys) = blocking_hotkeys {
        if let Ok(set) = hotkeys.lock() {
            return set
                .iter()
                .any(|h| h.modifiers.matches(modifiers) && h.key == key);
        }
    }
    false
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{Duration, Instant};
    use windows::Win32::UI::WindowsAndMessaging::PostQuitMessage;

    fn clear_message_queue() {
        let mut msg = MSG::default();
        unsafe { while PeekMessageW(&mut msg, None, 0, 0, PM_REMOVE).as_bool() {} }
    }

    #[test]
    fn wait_times_out_when_no_messages() {
        clear_message_queue();
        let start = Instant::now();
        wait_for_message_or_timeout(20);
        let elapsed = start.elapsed();
        assert!(
            elapsed >= Duration::from_millis(8),
            "expected wait to block close to timeout, elapsed={elapsed:?}"
        );
        clear_message_queue();
    }

    #[test]
    fn wait_returns_immediately_when_message_is_pending() {
        clear_message_queue();
        unsafe {
            PostQuitMessage(0);
        }
        let start = Instant::now();
        wait_for_message_or_timeout(200);
        let elapsed = start.elapsed();
        assert!(
            elapsed < Duration::from_millis(100),
            "expected pending message to wake wait early, elapsed={elapsed:?}"
        );
        clear_message_queue();
    }

    #[test]
    fn drain_messages_stops_on_wm_quit() {
        clear_message_queue();
        unsafe {
            PostQuitMessage(0);
        }
        let mut msg = MSG::default();
        assert!(drain_thread_messages(&mut msg).quit);
        clear_message_queue();
    }

    #[test]
    fn should_block_registered_mouse_hotkey() {
        use crate::types::Hotkey;
        let mut hotkeys = std::collections::HashSet::new();
        hotkeys.insert(Hotkey::new(Modifiers::empty(), Key::MouseX1).unwrap());
        let blocking_hotkeys = Some(std::sync::Arc::new(std::sync::Mutex::new(hotkeys)));

        assert!(should_block_hotkey(
            &blocking_hotkeys,
            Modifiers::empty(),
            Some(Key::MouseX1)
        ));
        assert!(!should_block_hotkey(
            &blocking_hotkeys,
            Modifiers::empty(),
            Some(Key::MouseX2)
        ));
    }

    fn post_thread_message(message: u32, wparam: usize) {
        use windows::Win32::System::Threading::GetCurrentThreadId;
        use windows::Win32::UI::WindowsAndMessaging::PostThreadMessageW;
        unsafe {
            PostThreadMessageW(GetCurrentThreadId(), message, WPARAM(wparam), LPARAM(0)).unwrap();
        }
    }

    #[test]
    fn drain_reports_session_unlock_with_reinstall() {
        clear_message_queue();
        post_thread_message(WM_WTSSESSION_CHANGE, WTS_SESSION_UNLOCK);
        let mut msg = MSG::default();
        let outcome = drain_thread_messages(&mut msg);
        assert!(outcome.reconcile);
        assert!(outcome.reinstall_hooks);
        assert!(!outcome.quit);
        clear_message_queue();
    }

    #[test]
    fn drain_reports_session_lock_without_reinstall() {
        clear_message_queue();
        post_thread_message(WM_WTSSESSION_CHANGE, WTS_SESSION_LOCK);
        let mut msg = MSG::default();
        let outcome = drain_thread_messages(&mut msg);
        assert!(outcome.reconcile);
        assert!(!outcome.reinstall_hooks);
        clear_message_queue();
    }

    #[test]
    fn drain_ignores_unrelated_session_events() {
        clear_message_queue();
        // WTS_SESSION_LOGOFF (0x6) is not a state we react to.
        post_thread_message(WM_WTSSESSION_CHANGE, 0x6);
        let mut msg = MSG::default();
        let outcome = drain_thread_messages(&mut msg);
        assert!(!outcome.reconcile);
        assert!(!outcome.reinstall_hooks);
        clear_message_queue();
    }

    #[test]
    fn drain_reports_resume_with_reinstall() {
        clear_message_queue();
        post_thread_message(WM_POWERBROADCAST, PBT_APMRESUMEAUTOMATIC);
        let mut msg = MSG::default();
        let outcome = drain_thread_messages(&mut msg);
        assert!(outcome.reconcile);
        assert!(outcome.reinstall_hooks);
        assert!(!outcome.quit);
        clear_message_queue();
    }

    #[test]
    fn drain_ignores_non_resume_power_events() {
        clear_message_queue();
        // PBT_APMSUSPEND (0x4): nothing to re-arm while going down.
        post_thread_message(WM_POWERBROADCAST, 0x4);
        let mut msg = MSG::default();
        let outcome = drain_thread_messages(&mut msg);
        assert!(!outcome.reconcile);
        assert!(!outcome.reinstall_hooks);
        clear_message_queue();
    }

    /// Drive the wndproc directly, as SendMessage delivery would. A null HWND
    /// is fine: DefWindowProcW ignores messages for windows it can't resolve,
    /// and our handling never touches the handle.
    fn send_to_wndproc(msg: u32, wparam: usize) {
        unsafe {
            watcher_wndproc(HWND::default(), msg, WPARAM(wparam), LPARAM(0));
        }
    }

    #[test]
    fn wndproc_requests_reinstall_on_resume() {
        let _ = take_reinstall_request();
        send_to_wndproc(WM_POWERBROADCAST, PBT_APMRESUMESUSPEND);
        assert!(take_reinstall_request());
        // The request is consumed by the take.
        assert!(!take_reinstall_request());
    }

    #[test]
    fn wndproc_requests_reinstall_on_session_unlock() {
        let _ = take_reinstall_request();
        send_to_wndproc(WM_WTSSESSION_CHANGE, WTS_SESSION_UNLOCK);
        assert!(take_reinstall_request());
    }

    #[test]
    fn wndproc_does_not_request_reinstall_on_lock_or_suspend() {
        let _ = take_reinstall_request();
        send_to_wndproc(WM_WTSSESSION_CHANGE, WTS_SESSION_LOCK);
        send_to_wndproc(WM_POWERBROADCAST, 0x4); // PBT_APMSUSPEND
        assert!(!take_reinstall_request());
    }

    // stale_modifiers / release_events themselves are covered in
    // crate::platform::state, where they now live.

    #[test]
    fn adoption_picks_up_missed_presses() {
        let adopt = adoptable_modifiers(
            Modifiers::CTRL_LEFT,
            Modifiers::CTRL_LEFT | Modifiers::SHIFT_LEFT,
            false,
        );
        assert_eq!(adopt, Modifiers::SHIFT_LEFT);
    }

    #[test]
    fn adoption_skips_ctrl_left_while_altgr_phantom_held() {
        // AltGr held: GetAsyncKeyState reports LCtrl+RAlt down, but only
        // OptRight reflects a user keypress. CTRL_LEFT must not be adopted.
        let adopt = adoptable_modifiers(
            Modifiers::OPT_RIGHT,
            Modifiers::CTRL_LEFT | Modifiers::OPT_RIGHT,
            true,
        );
        assert_eq!(adopt, Modifiers::empty());
    }

    #[test]
    fn adoption_keeps_other_modifiers_while_altgr_phantom_held() {
        // A genuinely-held Shift missed during a secure desktop transition
        // is still adopted while the phantom flag is set.
        let adopt = adoptable_modifiers(
            Modifiers::OPT_RIGHT,
            Modifiers::CTRL_LEFT | Modifiers::OPT_RIGHT | Modifiers::SHIFT_LEFT,
            true,
        );
        assert_eq!(adopt, Modifiers::SHIFT_LEFT);
    }

    #[test]
    fn menu_mask_fires_for_blocked_key_under_win() {
        assert!(needs_menu_mask(true, Modifiers::CMD_LEFT, false));
    }

    #[test]
    fn menu_mask_fires_under_alt() {
        // Alt has the sibling heuristic: a lone Alt tap focuses the menu bar.
        assert!(needs_menu_mask(
            true,
            Modifiers::OPT_LEFT | Modifiers::SHIFT_LEFT,
            false
        ));
    }

    #[test]
    fn menu_mask_only_when_blocking() {
        assert!(!needs_menu_mask(false, Modifiers::CMD_LEFT, false));
    }

    #[test]
    fn menu_mask_once_per_hold() {
        // Covers auto-repeat and the release edge of an already-masked press.
        assert!(!needs_menu_mask(true, Modifiers::CMD_LEFT, true));
    }

    #[test]
    fn menu_mask_not_for_ctrl_shift_combos() {
        // Ctrl/Shift releases trigger no shell menu; no mask needed.
        assert!(!needs_menu_mask(
            true,
            Modifiers::CTRL_LEFT | Modifiers::SHIFT_LEFT,
            false
        ));
    }

    #[test]
    fn menu_mask_vk_is_inert_to_our_own_mapping() {
        // Load-bearing: the injected mask must fall through the hook to
        // reach the shell. The dwExtraInfo marker guarantees that for our
        // own injections; this pins the fallback property that the VK maps
        // to nothing, so even an unmarked vkE8 press stays a no-op.
        assert!(vk_to_modifier(MENU_MASK_VK).is_none());
        assert!(map_key(MENU_MASK_VK, 0, false).is_none());
    }

    #[test]
    fn reconcile_clears_mask_flag_when_hold_ends_off_desktop() {
        // Win+L: the Win-up happens on the secure desktop and never reaches
        // the hook. Reconciliation emits the stale release and must also
        // clear the mask flag so the next hold gets its own mask. (The async
        // key state is passed in as "nothing held", the answer on the secure
        // desktop; upstream called GetAsyncKeyState here, which fails when a
        // key is really held while the tests run.)
        let (tx, rx) = mpsc::channel();
        let mut ctx = HookContext {
            event_sender: tx,
            current_modifiers: Modifiers::CMD_LEFT,
            blocking_hotkeys: None,
            altgr_phantom_ctrl: false,
            menu_mask_sent: true,
            swallowed_modifiers: SwallowedModifiers::default(),
            last_key_down: None,
        };
        reconcile_modifiers_from(&mut ctx, Modifiers::empty());
        assert_eq!(ctx.current_modifiers, Modifiers::empty());
        assert!(!ctx.menu_mask_sent);
        // The stale CMD_LEFT was cleared via a synthetic release event.
        let release = rx.try_recv().expect("expected a synthetic release");
        assert_eq!(release.changed_modifier, Some(Modifiers::CMD_LEFT));
        assert!(!release.is_key_down);
    }

    #[test]
    fn injected_key_events_pass_untouched_unless_accepted() {
        // Raphael's own paste sends key-ups with SendInput and dwExtraInfo 0, and the paste chord
        // through enigo. None of it may reach the hotkey logic. Real events carry the flags 0x80
        // (up), 0x01 (extended) or 0x20 (Alt down), never the injected bits.
        let injected = [
            LLKHF_INJECTED.0,
            LLKHF_INJECTED.0 | 0x80,
            LLKHF_INJECTED.0 | LLKHF_EXTENDED.0,
            LLKHF_LOWER_IL_INJECTED.0,
            LLKHF_LOWER_IL_INJECTED.0 | LLKHF_INJECTED.0,
        ];
        for flags in injected {
            assert!(passes_untouched(0xA5, flags, 0, false), "flags {flags:#x}");
            assert!(!passes_untouched(0xA5, flags, 0, true), "flags {flags:#x}");
        }
    }

    #[test]
    fn real_key_events_are_handled() {
        for flags in [0, 0x80, LLKHF_EXTENDED.0, 0x20, 0xA1] {
            assert!(!passes_untouched(0xA5, flags, 0, false), "flags {flags:#x}");
            assert!(!passes_untouched(0xA5, flags, 0, true), "flags {flags:#x}");
        }
    }

    #[test]
    fn the_menu_mask_always_passes_untouched() {
        // Marked with its extra info, whatever the flags and the setting say.
        for accept in [false, true] {
            assert!(passes_untouched(
                MENU_MASK_VK,
                0,
                MENU_MASK_EXTRA_INFO,
                accept
            ));
        }
        // The marker alone, or the key alone, is not the mask.
        assert!(!passes_untouched(MENU_MASK_VK, 0, 0, true));
        assert!(!passes_untouched(0xA5, 0, MENU_MASK_EXTRA_INFO, true));
    }

    #[test]
    fn injected_input_is_ignored_until_the_application_accepts_it() {
        assert!(!injected_input_accepted());
        accept_injected_input(true);
        assert!(injected_input_accepted());
        accept_injected_input(false);
        assert!(!injected_input_accepted());
    }

    #[test]
    fn swallowed_modifier_repeats_and_release_are_swallowed_too() {
        let mut swallowed = SwallowedModifiers::default();
        // The first key-down: the hotkey table blocks it.
        assert!(swallow_modifier_event(
            &mut swallowed,
            Modifiers::OPT_RIGHT,
            true,
            true,
            0
        ));
        assert_eq!(swallowed.held(), Modifiers::OPT_RIGHT);
        // Auto-repeats leave the modifier state alone, so the table says "not blocked".
        for _ in 0..2 {
            assert!(swallow_modifier_event(
                &mut swallowed,
                Modifiers::OPT_RIGHT,
                true,
                false,
                30
            ));
        }
        // The release is swallowed and ends the hold.
        assert!(swallow_modifier_event(
            &mut swallowed,
            Modifiers::OPT_RIGHT,
            false,
            false,
            60
        ));
        assert_eq!(swallowed.held(), Modifiers::empty());
        // The next press that no hotkey blocks passes, with its release.
        assert!(!swallow_modifier_event(
            &mut swallowed,
            Modifiers::OPT_RIGHT,
            true,
            false,
            90
        ));
        assert!(!swallow_modifier_event(
            &mut swallowed,
            Modifiers::OPT_RIGHT,
            false,
            false,
            120
        ));
    }

    #[test]
    fn only_the_swallowed_modifier_is_swallowed() {
        let mut swallowed = SwallowedModifiers::default();
        assert!(swallow_modifier_event(
            &mut swallowed,
            Modifiers::OPT_RIGHT,
            true,
            true,
            0
        ));
        // Left Shift is an ordinary key: pressed and released while Right Alt is held.
        assert!(!swallow_modifier_event(
            &mut swallowed,
            Modifiers::SHIFT_LEFT,
            true,
            false,
            10
        ));
        assert!(!swallow_modifier_event(
            &mut swallowed,
            Modifiers::SHIFT_LEFT,
            false,
            false,
            20
        ));
        assert_eq!(swallowed.held(), Modifiers::OPT_RIGHT);
    }

    #[test]
    fn a_release_whose_press_was_not_swallowed_is_not_swallowed() {
        // Hold Left Shift, press Right Alt (no hotkey blocks that: Shift+Right Alt is not the
        // Right Alt hotkey, so its press reached the app), release Left Shift. The tracked state
        // is now exactly Right Alt, so the hotkey table says "blocked" for this Shift release. It
        // must still reach the app: the app saw the press, and a swallowed release would leave
        // Shift stuck down system-wide.
        let mut swallowed = SwallowedModifiers::default();
        assert!(!swallow_modifier_event(
            &mut swallowed,
            Modifiers::SHIFT_LEFT,
            true,
            false,
            0
        ));
        assert!(!swallow_modifier_event(
            &mut swallowed,
            Modifiers::SHIFT_LEFT,
            false,
            true,
            100
        ));
        assert_eq!(swallowed.held(), Modifiers::empty());
    }

    #[test]
    fn the_release_of_a_swallowed_key_is_swallowed_whatever_the_table_says() {
        let mut swallowed = SwallowedModifiers::default();
        assert!(swallow_modifier_event(
            &mut swallowed,
            Modifiers::CMD_LEFT,
            true,
            true,
            0
        ));
        // The release changes the tracked state to "nothing", which no hotkey blocks.
        assert!(swallow_modifier_event(
            &mut swallowed,
            Modifiers::CMD_LEFT,
            false,
            false,
            50
        ));
        assert_eq!(swallowed.held(), Modifiers::empty());
    }

    #[test]
    fn the_staleness_limit_is_longer_than_the_longest_repeat_delay() {
        // Windows waits at most 1 s before a held key starts to repeat.
        const { assert!(STALE_SWALLOW_MS > 1000) };
    }

    #[test]
    fn a_swallowed_modifier_is_stale_only_after_the_limit() {
        let mut swallowed = SwallowedModifiers::default();
        swallow_modifier_event(&mut swallowed, Modifiers::OPT_RIGHT, true, true, 1000);
        assert_eq!(
            swallowed.stale(1000, Modifiers::empty()),
            Modifiers::empty()
        );
        // Exactly at the limit it still counts as held; one millisecond later it does not.
        assert_eq!(
            swallowed.stale(1000 + STALE_SWALLOW_MS, Modifiers::empty()),
            Modifiers::empty()
        );
        assert_eq!(
            swallowed.stale(1000 + STALE_SWALLOW_MS + 1, Modifiers::empty()),
            Modifiers::OPT_RIGHT
        );
        // A clock that reads earlier than the last event is never stale.
        assert_eq!(swallowed.stale(10, Modifiers::empty()), Modifiers::empty());
    }

    #[test]
    fn a_swallowed_modifier_the_system_reports_held_is_not_stale() {
        let mut swallowed = SwallowedModifiers::default();
        swallow_modifier_event(&mut swallowed, Modifiers::OPT_RIGHT, true, true, 0);
        let late = STALE_SWALLOW_MS * 10;
        assert_eq!(
            swallowed.stale(late, Modifiers::OPT_RIGHT),
            Modifiers::empty()
        );
        // Another key being held says nothing about this one.
        assert_eq!(
            swallowed.stale(late, Modifiers::CTRL_LEFT),
            Modifiers::OPT_RIGHT
        );
    }

    #[test]
    fn auto_repeats_keep_a_swallowed_modifier_fresh() {
        let mut swallowed = SwallowedModifiers::default();
        swallow_modifier_event(&mut swallowed, Modifiers::OPT_RIGHT, true, true, 0);
        // The user keeps holding: Windows repeats the key every ~30 ms after the first delay.
        for at in (500..5000).step_by(30) {
            assert!(swallow_modifier_event(
                &mut swallowed,
                Modifiers::OPT_RIGHT,
                true,
                false,
                at
            ));
        }
        assert_eq!(
            swallowed.stale(5000, Modifiers::empty()),
            Modifiers::empty()
        );
        assert_eq!(
            swallowed.stale(4970 + STALE_SWALLOW_MS + 1, Modifiers::empty()),
            Modifiers::OPT_RIGHT
        );
    }

    #[test]
    fn each_swallowed_modifier_ages_on_its_own() {
        let mut swallowed = SwallowedModifiers::default();
        swallow_modifier_event(&mut swallowed, Modifiers::OPT_RIGHT, true, true, 0);
        swallow_modifier_event(&mut swallowed, Modifiers::CTRL_RIGHT, true, true, 1000);
        let now = STALE_SWALLOW_MS + 1;
        assert_eq!(
            swallowed.stale(now, Modifiers::empty()),
            Modifiers::OPT_RIGHT
        );
        swallowed.forget(Modifiers::OPT_RIGHT);
        assert_eq!(swallowed.held(), Modifiers::CTRL_RIGHT);
        assert_eq!(swallowed.stale(now, Modifiers::empty()), Modifiers::empty());
        assert_eq!(
            swallowed.stale(1000 + STALE_SWALLOW_MS + 1, Modifiers::empty()),
            Modifiers::CTRL_RIGHT
        );
    }

    /// A hook context in the middle of a Right Alt hold: Right Alt (dictation) and Right Alt+V
    /// (paste last) are blocking hotkeys, Right Alt is tracked, and its key-down was swallowed
    /// at `since_ms` (its last event). Nothing here reads the real keyboard.
    fn right_alt_held_since(since_ms: u64) -> (HookContext, mpsc::Receiver<KeyEvent>) {
        use crate::types::Hotkey;
        let mut hotkeys = std::collections::HashSet::new();
        hotkeys.insert(Hotkey::new(Modifiers::OPT_RIGHT, None).unwrap());
        hotkeys.insert(Hotkey::new(Modifiers::OPT_RIGHT, Key::V).unwrap());
        let mut swallowed = SwallowedModifiers::default();
        assert!(swallow_modifier_event(
            &mut swallowed,
            Modifiers::OPT_RIGHT,
            true,
            true,
            since_ms
        ));
        let (tx, rx) = mpsc::channel();
        let ctx = HookContext {
            event_sender: tx,
            current_modifiers: Modifiers::OPT_RIGHT,
            blocking_hotkeys: Some(std::sync::Arc::new(std::sync::Mutex::new(hotkeys))),
            altgr_phantom_ctrl: false,
            menu_mask_sent: true,
            swallowed_modifiers: swallowed,
            last_key_down: None,
        };
        (ctx, rx)
    }

    /// The events a context has sent so far.
    fn sent(rx: &mpsc::Receiver<KeyEvent>) -> Vec<KeyEvent> {
        rx.try_iter().collect()
    }

    #[test]
    fn reconcile_keeps_a_modifier_held_swallowed() {
        // Right Alt is held with its key-down swallowed, so GetAsyncKeyState reports it up. The
        // next other key must not make reconciliation release it.
        let (mut ctx, rx) = right_alt_held_since(0);
        reconcile_modifiers_from(&mut ctx, Modifiers::empty());
        assert_eq!(ctx.current_modifiers, Modifiers::OPT_RIGHT);
        assert!(sent(&rx).is_empty(), "no synthetic release");
    }

    #[test]
    fn a_chord_on_a_swallowed_modifier_is_still_blocked() {
        // Right Alt (dictation) is held with its key-down swallowed, and Right Alt+V is a hotkey
        // too. Pressing V reconciles first (see keyboard_hook_proc); that must not drop Right
        // Alt, or the chord would never match and the V would reach the foreground app.
        let (mut ctx, _rx) = right_alt_held_since(0);
        reconcile_modifiers_from(&mut ctx, Modifiers::empty());
        assert!(should_block_hotkey(
            &ctx.blocking_hotkeys,
            ctx.current_modifiers,
            Some(Key::V)
        ));
    }

    #[test]
    fn a_session_change_forgets_swallowed_modifiers() {
        // The key-ups of swallowed keys may have gone to the secure desktop. The system reports
        // nothing held, as it does on the lock screen.
        let (mut ctx, rx) = right_alt_held_since(0);
        reconcile_after_session_change(&mut ctx, Modifiers::empty());
        assert_eq!(ctx.swallowed_modifiers.held(), Modifiers::empty());
        assert_eq!(ctx.current_modifiers, Modifiers::empty());
        let events = sent(&rx);
        assert_eq!(events.len(), 1, "{events:?}");
        assert_eq!(events[0].changed_modifier, Some(Modifiers::OPT_RIGHT));
        assert!(!events[0].is_key_down);
    }

    #[test]
    fn a_session_change_keeps_a_key_the_system_reports_held() {
        let (mut ctx, rx) = right_alt_held_since(0);
        reconcile_after_session_change(&mut ctx, Modifiers::OPT_RIGHT);
        assert_eq!(ctx.swallowed_modifiers.held(), Modifiers::empty());
        assert_eq!(ctx.current_modifiers, Modifiers::OPT_RIGHT);
        assert!(sent(&rx).is_empty());
    }

    #[test]
    fn a_fresh_swallowed_modifier_makes_the_next_key_a_chord() {
        // Right Alt+V: Right Alt repeats until V goes down, so its last event is recent.
        let (mut ctx, rx) = right_alt_held_since(1000);
        let outcome = handle_regular_key(&mut ctx, Some(Key::V), true, Modifiers::empty(), 1200);
        assert!(outcome.should_block, "Right Alt+V is a hotkey");
        assert_eq!(ctx.current_modifiers, Modifiers::OPT_RIGHT);
        assert_eq!(ctx.swallowed_modifiers.held(), Modifiers::OPT_RIGHT);
        let events = sent(&rx);
        assert_eq!(events.len(), 1, "{events:?}");
        assert_eq!(events[0].key, Some(Key::V));
        assert_eq!(events[0].modifiers, Modifiers::OPT_RIGHT);
    }

    #[test]
    fn a_stale_swallowed_modifier_is_dropped_before_the_next_key_is_matched() {
        // Right Alt's key-up was missed (an elevated window took focus, the hook timed out), so
        // it has been "held" for seconds with no event. A bare V is just a V: it must not match
        // Right Alt+V (PasteLast) and must not be swallowed.
        let (mut ctx, rx) = right_alt_held_since(1000);
        let now = 1000 + STALE_SWALLOW_MS + 1;
        let outcome = handle_regular_key(&mut ctx, Some(Key::V), true, Modifiers::empty(), now);
        assert!(!outcome.should_block);
        assert_eq!(ctx.swallowed_modifiers.held(), Modifiers::empty());
        assert_eq!(ctx.current_modifiers, Modifiers::empty());
        let events = sent(&rx);
        assert_eq!(events.len(), 2, "{events:?}");
        // First the synthetic release of the stale key, so consumers recover ...
        assert_eq!(events[0].changed_modifier, Some(Modifiers::OPT_RIGHT));
        assert!(!events[0].is_key_down);
        assert_eq!(events[0].modifiers, Modifiers::empty());
        // ... then the V as a bare key, which no hotkey matches.
        assert_eq!(events[1].key, Some(Key::V));
        assert_eq!(events[1].modifiers, Modifiers::empty());
    }

    #[test]
    fn the_staleness_boundary_is_exclusive() {
        for (age, chord) in [
            (STALE_SWALLOW_MS - 1, true),
            (STALE_SWALLOW_MS, true),
            (STALE_SWALLOW_MS + 1, false),
        ] {
            let (mut ctx, _rx) = right_alt_held_since(1000);
            let outcome =
                handle_regular_key(&mut ctx, Some(Key::V), true, Modifiers::empty(), 1000 + age);
            assert_eq!(outcome.should_block, chord, "age {age} ms");
        }
    }

    #[test]
    fn a_stale_swallowed_modifier_the_system_still_reports_held_stays() {
        let (mut ctx, rx) = right_alt_held_since(0);
        let now = STALE_SWALLOW_MS * 4;
        let outcome = handle_regular_key(&mut ctx, Some(Key::V), true, Modifiers::OPT_RIGHT, now);
        assert!(outcome.should_block);
        assert_eq!(ctx.swallowed_modifiers.held(), Modifiers::OPT_RIGHT);
        assert_eq!(sent(&rx).len(), 1);
    }

    #[test]
    fn only_a_key_press_drops_a_stale_swallowed_modifier() {
        // The release of a chord key arrives long after Right Alt's last event (Right Alt does
        // not repeat while V is down). It must still be part of the chord.
        let (mut ctx, _rx) = right_alt_held_since(0);
        let outcome = handle_regular_key(&mut ctx, Some(Key::V), true, Modifiers::empty(), 100);
        assert!(outcome.should_block);
        let late = STALE_SWALLOW_MS * 3;
        let outcome = handle_regular_key(&mut ctx, Some(Key::V), false, Modifiers::empty(), late);
        assert!(outcome.should_block, "the V release belongs to the chord");
        assert_eq!(ctx.swallowed_modifiers.held(), Modifiers::OPT_RIGHT);
    }

    #[test]
    fn holding_the_chord_key_down_keeps_the_chord_going() {
        // V is held for longer than the limit. Windows repeats V, not Right Alt, so Right Alt's
        // last event gets old; the repeats of the V that passed the check must not undo it.
        let (mut ctx, rx) = right_alt_held_since(900);
        let down = handle_regular_key(&mut ctx, Some(Key::V), true, Modifiers::empty(), 1000);
        assert!(down.should_block);
        for at in (1500..5000).step_by(30) {
            let repeat = handle_regular_key(&mut ctx, Some(Key::V), true, Modifiers::empty(), at);
            assert!(repeat.should_block, "repeat at {at} ms leaked");
        }
        assert_eq!(ctx.current_modifiers, Modifiers::OPT_RIGHT);
        assert!(
            sent(&rx).iter().all(|e| e.is_key_down && e.key.is_some()),
            "no synthetic release"
        );
        // After the V is released, the next new key press finds Right Alt silent for too long.
        let up = handle_regular_key(&mut ctx, Some(Key::V), false, Modifiers::empty(), 5100);
        assert!(up.should_block);
        let next = handle_regular_key(&mut ctx, Some(Key::X), true, Modifiers::empty(), 5200);
        assert!(!next.should_block);
        assert_eq!(ctx.swallowed_modifiers.held(), Modifiers::empty());
    }

    #[test]
    fn a_new_press_of_the_same_key_is_not_a_repeat() {
        let (mut ctx, _rx) = right_alt_held_since(0);
        let first = handle_regular_key(&mut ctx, Some(Key::V), true, Modifiers::empty(), 100);
        assert!(first.should_block);
        let up = handle_regular_key(&mut ctx, Some(Key::V), false, Modifiers::empty(), 150);
        assert!(up.should_block);
        // Pressed again after the limit, with Right Alt silent since the first press.
        let again = handle_regular_key(
            &mut ctx,
            Some(Key::V),
            true,
            Modifiers::empty(),
            STALE_SWALLOW_MS + 200,
        );
        assert!(!again.should_block);
    }

    #[test]
    fn phantom_flag_never_releases_a_tracked_ctrl() {
        // User really holds LCtrl, then AltGr: CTRL_LEFT is tracked from its
        // own event and physically down, so it must be neither stale nor
        // re-adopted.
        let tracked = Modifiers::CTRL_LEFT | Modifiers::OPT_RIGHT;
        let physical = Modifiers::CTRL_LEFT | Modifiers::OPT_RIGHT;
        assert_eq!(stale_modifiers(tracked, physical), Modifiers::empty());
        assert_eq!(
            adoptable_modifiers(tracked, physical, true),
            Modifiers::empty()
        );
    }
}
