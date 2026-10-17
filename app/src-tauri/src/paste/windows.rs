//! [`WindowsPaster`]: finds the focused window, and pastes into it on Windows.
//!
//! Each paste runs on a thread of its own, which owns the [`ClipboardSession`]: snapshot the
//! user's clipboard, offer the text, check the focus once more, release the modifier keys the
//! user still holds, send the paste chord, then wait for the target to read the text and put the
//! user's clipboard back. If the focus check says not to paste, the text stays on the clipboard.
//!
//! The chord is sent as Handy does it in `src-tauri/src/input.rs` (MIT, Copyright (c) 2025
//! CJ Pais; the notice is in `clipboard.rs`): modifiers down, key click, a 100 ms hold, modifiers
//! up. Releasing held modifiers first and the per-program chord ([`shortcut_for`]) follow
//! OpenWhispr (spec section 5).
//!
//! [`shortcut_for`]: super::shortcut_for

use std::sync::{Mutex, PoisonError};
use std::thread;
use std::time::Duration;

use enigo::{Direction, Enigo, Key, Keyboard, Settings};
use windows::Win32::Foundation::{ERROR_ACCESS_DENIED, HANDLE};
use windows::Win32::Security::{GetTokenInformation, TOKEN_ELEVATION, TOKEN_QUERY, TokenElevation};
use windows::Win32::System::Threading::{
    GetCurrentProcess, OpenProcess, OpenProcessToken, PROCESS_NAME_WIN32,
    PROCESS_QUERY_LIMITED_INFORMATION, QueryFullProcessImageNameW,
};
use windows::Win32::UI::Input::KeyboardAndMouse::{
    GetAsyncKeyState, INPUT, INPUT_0, INPUT_KEYBOARD, KEYBD_EVENT_FLAGS, KEYBDINPUT,
    KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, MAPVK_VK_TO_VSC, MapVirtualKeyW, SendInput,
    VIRTUAL_KEY, VK_LCONTROL, VK_LMENU, VK_LSHIFT, VK_LWIN, VK_RCONTROL, VK_RMENU, VK_RSHIFT,
    VK_RWIN,
};
use windows::Win32::UI::WindowsAndMessaging::{GetForegroundWindow, GetWindowThreadProcessId};
use windows::core::{HRESULT, Owned, PWSTR};

use super::clipboard::ClipboardSession;
use super::{CopyReason, Decision, PasteOutcome, Paster, Shortcut, Target, decide};

/// How long the chord's modifiers stay down after the key click. Most programs read them from
/// the key's message, but some look at the keyboard's state while handling it.
const CHORD_HOLD: Duration = Duration::from_millis(100);

/// Modifier keys the user may still be holding, each with whether it is an extended key.
const MODIFIERS: [(VIRTUAL_KEY, bool); 8] = [
    (VK_LSHIFT, false),
    (VK_RSHIFT, false),
    (VK_LCONTROL, false),
    (VK_RCONTROL, true),
    (VK_LMENU, false),
    (VK_RMENU, true),
    (VK_LWIN, true),
    (VK_RWIN, true),
];

/// An unassigned virtual key, tapped before Alt or Win is released so that the release alone
/// does not open the window's menu bar or the Start menu (AutoHotkey's "menu mask key").
const VK_MASK: VIRTUAL_KEY = VIRTUAL_KEY(0xE8);

/// One paste at a time: a second paste while the first waits would snapshot the first one's
/// text as the user's clipboard.
static ONE_AT_A_TIME: Mutex<()> = Mutex::new(());

/// The [`Paster`] for Windows.
pub struct WindowsPaster {
    /// Whether this app runs elevated, which lets it paste into elevated windows.
    self_elevated: bool,
}

impl WindowsPaster {
    /// Looks up, once, whether this app runs elevated.
    pub fn new() -> Self {
        // SAFETY: the pseudo handle of our own process needs no closing.
        let self_elevated = process_is_elevated(unsafe { GetCurrentProcess() }).unwrap_or(false);
        Self { self_elevated }
    }
}

impl Default for WindowsPaster {
    fn default() -> Self {
        Self::new()
    }
}

impl Paster for WindowsPaster {
    fn foreground(&self) -> Option<Target> {
        foreground_target()
    }

    fn deliver(&self, text: &str, at_stop: Option<&Target>) -> PasteOutcome {
        let _turn = ONE_AT_A_TIME.lock().unwrap_or_else(PoisonError::into_inner);
        let (text, target, self_elevated) = (text.to_owned(), at_stop.cloned(), self.self_elevated);
        // The session's window is answered only while its thread waits for it, so it gets a
        // thread that does nothing else.
        let worker = thread::Builder::new()
            .name("raphael-paste".to_owned())
            .spawn(move || paste(&text, target.as_ref(), self_elevated));
        let failure = match worker.map(thread::JoinHandle::join) {
            Ok(Ok(outcome)) => return outcome,
            Ok(Err(_)) => "the paste thread panicked".to_owned(),
            Err(e) => format!("could not start the paste thread: {e}"),
        };
        log::error!("paste: {failure}");
        outcome_of(decide(at_stop, foreground_target().as_ref(), self_elevated))
    }

    fn copy(&self, text: &str) {
        // With no target, `decide` always says copy only: the text is published and kept.
        let outcome = self.deliver(text, None);
        debug_assert_eq!(outcome, PasteOutcome::CopiedOnly(CopyReason::NoTarget));
    }
}

/// One paste, on the current thread.
fn paste(text: &str, at_stop: Option<&Target>, self_elevated: bool) -> PasteOutcome {
    let session = ClipboardSession::begin()
        .and_then(|mut session| session.publish_text(text).map(|()| session));
    // Decided once the text is on the clipboard: as late as possible before the chord.
    let decision = decide(at_stop, foreground_target().as_ref(), self_elevated);
    let session = match session {
        Ok(session) => session,
        Err(e) => {
            // Nothing was pasted or copied. PasteOutcome has no word for that; the dictation is
            // still in the history, for "paste last".
            log::error!("paste: could not use the clipboard: {e}");
            return outcome_of(decision);
        }
    };
    match decision {
        Decision::Paste(shortcut) => {
            let program = at_stop.map_or("", |target| target.process_name.as_str());
            log::info!("paste: {shortcut:?} into {program}");
            release_held_modifiers();
            let chord_sent = send_chord(shortcut, || session.pump_for(CHORD_HOLD));
            session.wait_and_restore(chord_sent);
        }
        Decision::CopyOnly(reason) => {
            log::info!("paste: not pasting ({reason:?}); the text stays on the clipboard");
            session.keep();
        }
    }
    outcome_of(decision)
}

fn outcome_of(decision: Decision) -> PasteOutcome {
    match decision {
        Decision::Paste(_) => PasteOutcome::Pasted,
        Decision::CopyOnly(reason) => PasteOutcome::CopiedOnly(reason),
    }
}

/// Presses the chord's modifiers, clicks its key, calls `hold` with the modifiers still down,
/// then releases them. Returns whether the key click went out.
fn send_chord(shortcut: Shortcut, hold: impl FnOnce()) -> bool {
    let (modifiers, key): (&[Key], Key) = match shortcut {
        Shortcut::CtrlV => (&[Key::Control], Key::V),
        Shortcut::CtrlShiftV => (&[Key::Control, Key::Shift], Key::V),
        Shortcut::ShiftInsert => (&[Key::Shift], Key::Insert),
    };
    let mut enigo = match Enigo::new(&Settings::default()) {
        Ok(enigo) => enigo,
        Err(e) => {
            log::error!("paste: could not send keys: {e}");
            return false;
        }
    };
    let mut pressed = 0;
    for &modifier in modifiers {
        if let Err(e) = enigo.key(modifier, Direction::Press) {
            log::error!("paste: could not press {modifier:?}: {e}");
            break;
        }
        pressed += 1;
    }
    let mut clicked = false;
    if pressed == modifiers.len() {
        match enigo.key(key, Direction::Click) {
            Ok(()) => {
                clicked = true;
                hold();
            }
            Err(e) => log::error!("paste: could not send {shortcut:?}: {e}"),
        }
    }
    for &modifier in modifiers[..pressed].iter().rev() {
        if let Err(e) = enigo.key(modifier, Direction::Release) {
            log::error!("paste: could not release {modifier:?}: {e}");
        }
    }
    clicked
}

/// Releases the modifier keys the user is still holding, which would otherwise turn Ctrl+V
/// into Ctrl+Alt+V and the like.
fn release_held_modifiers() {
    // SAFETY: plain queries.
    let held: Vec<(VIRTUAL_KEY, bool)> = MODIFIERS
        .into_iter()
        .filter(|(vk, _)| unsafe { GetAsyncKeyState(i32::from(vk.0)) } < 0)
        .collect();
    if held.is_empty() {
        return;
    }
    let mut inputs = Vec::new();
    if held
        .iter()
        .any(|(vk, _)| [VK_LMENU, VK_RMENU, VK_LWIN, VK_RWIN].contains(vk))
    {
        inputs.push(key_event(VK_MASK, false, false));
        inputs.push(key_event(VK_MASK, false, true));
    }
    inputs.extend(
        held.iter()
            .map(|&(vk, extended)| key_event(vk, extended, true)),
    );
    // SAFETY: `inputs` are initialized keyboard inputs of the size given.
    let sent = unsafe { SendInput(&inputs, size_of::<INPUT>() as i32) };
    if sent as usize != inputs.len() {
        log::warn!("paste: could not release the modifier keys the user holds");
    }
}

/// A keyboard input for `SendInput`.
fn key_event(vk: VIRTUAL_KEY, extended: bool, up: bool) -> INPUT {
    let mut flags = KEYBD_EVENT_FLAGS::default();
    if extended {
        flags |= KEYEVENTF_EXTENDEDKEY;
    }
    if up {
        flags |= KEYEVENTF_KEYUP;
    }
    // SAFETY: a plain query; scan codes fit in 16 bits.
    let scan = unsafe { MapVirtualKeyW(u32::from(vk.0), MAPVK_VK_TO_VSC) } as u16;
    INPUT {
        r#type: INPUT_KEYBOARD,
        Anonymous: INPUT_0 {
            ki: KEYBDINPUT {
                wVk: vk,
                wScan: scan,
                dwFlags: flags,
                time: 0,
                dwExtraInfo: 0,
            },
        },
    }
}

/// The window that has focus now, with its process's executable name and elevation.
fn foreground_target() -> Option<Target> {
    // SAFETY: plain queries.
    let hwnd = unsafe { GetForegroundWindow() };
    if hwnd.is_invalid() {
        return None;
    }
    let mut pid = 0;
    unsafe { GetWindowThreadProcessId(hwnd, Some(&raw mut pid)) };
    if pid == 0 {
        return None;
    }
    let (process_name, is_elevated) = describe_process(pid);
    Some(Target {
        hwnd: hwnd.0 as isize,
        pid,
        process_name,
        is_elevated,
    })
}

/// A process's executable file name and whether it runs elevated. A process that we may not
/// even look at counts as elevated: it is out of our reach in the same way.
fn describe_process(pid: u32) -> (String, bool) {
    let access_denied = HRESULT::from_win32(ERROR_ACCESS_DENIED.0);
    // SAFETY: `Owned` closes the handle.
    let process = match unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid) } {
        Ok(handle) => unsafe { Owned::new(handle) },
        Err(e) if e.code() == access_denied => return (String::new(), true),
        Err(e) => {
            log::warn!("paste: could not look at process {pid}: {e}");
            return (String::new(), false);
        }
    };
    let name = image_file_name(*process).unwrap_or_default();
    let elevated = process_is_elevated(*process).unwrap_or_else(|e| e.code() == access_denied);
    (name, elevated)
}

/// The file name of a process's executable, such as `notepad.exe`.
fn image_file_name(process: HANDLE) -> Option<String> {
    let mut path = vec![0u16; 32_768];
    let mut len = path.len() as u32;
    // SAFETY: `path` has room for `len` UTF-16 units.
    unsafe {
        QueryFullProcessImageNameW(
            process,
            PROCESS_NAME_WIN32,
            PWSTR(path.as_mut_ptr()),
            &mut len,
        )
    }
    .ok()?;
    let path = String::from_utf16_lossy(&path[..len as usize]);
    path.rsplit('\\').next().map(str::to_owned)
}

/// Whether a process runs elevated.
fn process_is_elevated(process: HANDLE) -> windows::core::Result<bool> {
    let mut token = HANDLE::default();
    let mut elevation = TOKEN_ELEVATION::default();
    let mut len = 0;
    // SAFETY: `Owned` closes the token; `elevation` has the size passed.
    unsafe {
        OpenProcessToken(process, TOKEN_QUERY, &mut token)?;
        let token = Owned::new(token);
        GetTokenInformation(
            *token,
            TokenElevation,
            Some((&raw mut elevation).cast()),
            size_of::<TOKEN_ELEVATION>() as u32,
            &mut len,
        )?;
    }
    Ok(elevation.TokenIsElevated != 0)
}
