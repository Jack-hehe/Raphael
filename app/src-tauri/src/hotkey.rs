//! Global hotkeys: Right Alt dictates, Esc cancels, Right Alt+V pastes the last dictation.
//!
//! [`HotkeyService`] runs a `handy-keys` hotkey manager on its own thread and turns what it
//! reports into [`HotkeyEvent`]s on a channel. The manager works in blocking mode, so a
//! registered key never reaches the foreground app: Right Alt does not open a menu bar, and Esc
//! does not close a dialog while a dictation is active.
//!
//! `HotkeyManager` is `Send` but not `Sync`, so one thread owns it. The service talks to that
//! thread over a channel, and the thread looks for key events every 10 ms between commands. This
//! layout follows Handy's `shortcut/handy_keys.rs` (<https://github.com/cjpais/Handy>, MIT
//! licence).
//!
//! handy-keys is vendored in `vendor/handy-keys` (0.3.4, patched; the note at the top of its
//! `src/platform/windows/listener.rs` says what changed). Upstream had two gaps, found with
//! injected keys:
//!
//! - For a modifier-only hotkey such as Right Alt it swallowed only the first key-down. The
//!   auto-repeat key-downs and the key-up reached the foreground app, which could open its menu
//!   bar (or Office its KeyTips) after a long hold.
//! - The system's `GetAsyncKeyState` never sees a key whose down was swallowed, so the first
//!   other key or mouse click during the hold made handy-keys think the modifier was released.
//!   The hold ended early (`DictationUp` too soon) and a chord such as Right Alt+V never matched,
//!   so no `PasteLast` arrived.
//!
//! The patched hook also ignores injected keyboard events (`LLKHF_INJECTED`): Raphael's own paste
//! (key-ups sent with `SendInput`, the Ctrl+V chord) can not produce a `DictationDown`,
//! `DictationUp` or `PasteLast`, and the keys it sends are never swallowed.

use std::sync::mpsc::{self, Receiver, RecvTimeoutError, Sender};
use std::thread::{self, JoinHandle};
use std::time::Duration;

use handy_keys::{Hotkey, HotkeyId, HotkeyManager, HotkeyState, Key, Modifiers};
use tokio::sync::mpsc::UnboundedSender;

/// How often the hotkey thread looks for key events while it waits for commands.
const POLL_INTERVAL: Duration = Duration::from_millis(10);
/// How long [`HotkeyService::update_bindings`] waits for the hotkey thread to answer.
const REPLY_TIMEOUT: Duration = Duration::from_secs(2);
const NOT_RUNNING: &str = "the hotkey service is not running";

/// A key event the dictation controller reacts to.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum HotkeyEvent {
    /// A dictation key went down. One per press: key auto-repeat is not reported.
    DictationDown,
    /// A dictation key came up.
    DictationUp,
    /// Esc (or the dictation modifiers plus Esc). Only reported while cancel is enabled, see
    /// [`HotkeyService::set_cancel_enabled`].
    Cancel,
    /// A paste-last key went down. Right Alt+V reports `DictationDown` first, because Right Alt
    /// went down first.
    PasteLast,
}

/// The keys for each action. An entry is one key combination: see [`parse_binding`]. An action may
/// have several, and any of them triggers it.
#[derive(Clone, Debug, PartialEq)]
pub struct Bindings {
    pub dictation: Vec<String>,
    pub paste_last: Vec<String>,
}

impl Default for Bindings {
    fn default() -> Self {
        Self {
            dictation: vec!["AltRight".to_string()],
            paste_last: vec!["AltRight+V".to_string()],
        }
    }
}

/// Parses one key combination such as `"AltRight"`, `"AltRight+Space"` or `"AltRight+V"`.
///
/// This is `str::parse::<Hotkey>()` from handy-keys: case does not matter and the parts may come
/// in any order. On top of that the key names of the browser's `KeyboardEvent.code` are accepted
/// (`KeyV`, `Digit1`, `ArrowUp`, `Numpad5`, `Backquote`, ...), because the settings page records
/// keys in that spelling. A name with no modifier at all, such as `"V"`, parses too; it is up to
/// the caller not to offer such a binding, since it would block that key everywhere.
pub fn parse_binding(s: &str) -> Result<Hotkey, String> {
    let parts: Vec<String> = s.split('+').map(|part| handy_name(part.trim())).collect();
    parts
        .join("+")
        .parse::<Hotkey>()
        .map_err(|err| format!("invalid hotkey \"{s}\": {err}"))
}

/// handy-keys' name for a key or modifier given in the browser's `KeyboardEvent.code` spelling.
/// Names handy-keys already understands (`AltRight`, `Space`, `V`, `F9`, ...) come back unchanged.
fn handy_name(name: &str) -> String {
    let lower = name.to_ascii_lowercase();
    // KeyV -> v, Digit1 -> 1
    if let Some(letter) = lower
        .strip_prefix("key")
        .filter(|rest| rest.len() == 1 && rest.bytes().all(|b| b.is_ascii_lowercase()))
    {
        return letter.to_string();
    }
    if let Some(digit) = lower
        .strip_prefix("digit")
        .filter(|rest| rest.len() == 1 && rest.bytes().all(|b| b.is_ascii_digit()))
    {
        return digit.to_string();
    }
    if let Some(direction) = lower.strip_prefix("arrow") {
        return format!("{direction}arrow");
    }
    if let Some(pad) = lower.strip_prefix("numpad") {
        let pad = match pad {
            "add" => "plus",
            "subtract" => "minus",
            "equal" => "equals",
            other => other,
        };
        return format!("keypad{pad}");
    }
    match lower.as_str() {
        "backquote" => "grave",
        "bracketleft" => "leftbracket",
        "bracketright" => "rightbracket",
        // In handy-keys "delete" is the Backspace key; the browser's Delete is forward delete.
        "delete" => "forwarddelete",
        _ => name,
    }
    .to_string()
}

/// Lets the dictation controller turn the cancel keys on while a dictation is active and off
/// again afterwards. [`HotkeyService`] implements it; tests use a fake.
pub trait CancelSwitch: Send + Sync + 'static {
    fn set(&self, on: bool);
}

impl CancelSwitch for HotkeyService {
    fn set(&self, on: bool) {
        self.set_cancel_enabled(on);
    }
}

/// The global hotkeys. Dropping the service stops its thread and releases the keyboard hook.
pub struct HotkeyService {
    commands: Sender<Command>,
    thread: Option<JoinHandle<()>>,
}

impl HotkeyService {
    /// Installs the keyboard hook and registers the dictation and paste-last keys. Key events
    /// arrive on `events`; the service stops by itself if the receiving end is dropped.
    ///
    /// Fails if a binding is invalid or a key cannot be registered. A hook that Windows refuses
    /// to install is not an error here: handy-keys installs it on a thread of its own and only
    /// prints the failure to stderr.
    pub fn start(
        bindings: &Bindings,
        events: UnboundedSender<HotkeyEvent>,
    ) -> Result<Self, String> {
        let wanted = Wanted::from_bindings(bindings)?;
        // Blocking mode: a plain `HotkeyManager::new()` would watch the keys without swallowing
        // them.
        let manager = HotkeyManager::new_with_blocking()
            .map_err(|err| format!("could not install the keyboard hook: {err}"))?;
        let service = Self::start_with(manager, wanted, events)?;
        log::info!("global hotkeys are on: {bindings:?}");
        Ok(service)
    }

    fn start_with<B>(
        backend: B,
        wanted: Wanted,
        events: UnboundedSender<HotkeyEvent>,
    ) -> Result<Self, String>
    where
        B: Backend + Send + 'static,
    {
        let mut worker = Worker::new(backend, wanted, events);
        worker.sync()?;
        let (commands, inbox) = mpsc::channel();
        let thread = thread::Builder::new()
            .name("raphael-hotkeys".to_string())
            .spawn(move || worker.run(&inbox))
            .map_err(|err| format!("could not start the hotkey thread: {err}"))?;
        Ok(Self {
            commands,
            thread: Some(thread),
        })
    }

    /// Replaces the dictation and paste-last keys. Nothing changes if a binding is invalid (see
    /// [`parse_binding`]), is used twice, or is one of the cancel keys (Esc, and Esc with the
    /// dictation modifiers held). Keys that stay the same stay registered.
    pub fn update_bindings(&self, bindings: &Bindings) -> Result<(), String> {
        let wanted = Wanted::from_bindings(bindings)?;
        let (reply, answer) = mpsc::channel();
        self.commands
            .send(Command::Update { wanted, reply })
            .map_err(|_| NOT_RUNNING.to_string())?;
        answer
            .recv_timeout(REPLY_TIMEOUT)
            .map_err(|_| NOT_RUNNING.to_string())?
    }

    /// Turns the cancel keys on or off. They are registered only while a dictation is active,
    /// because while registered they are swallowed: Esc does nothing else. Esc works with the
    /// dictation modifiers still held (hold mode) and without them (tap mode).
    ///
    /// Takes effect within a few milliseconds, in order with the other calls on this service.
    pub fn set_cancel_enabled(&self, on: bool) {
        self.send(Command::SetCancel(on));
    }

    /// Pauses (`true`) or resumes (`false`) the dictation and paste-last keys. While paused they
    /// are not registered, so they reach the foreground app like any other key. Used by the
    /// hotkey recorder in the settings and by the tray's pause item. The cancel keys follow
    /// [`set_cancel_enabled`](Self::set_cancel_enabled) and are not affected.
    ///
    /// Takes effect within a few milliseconds, in order with the other calls on this service.
    pub fn set_paused(&self, paused: bool) {
        self.send(Command::SetPaused(paused));
    }

    fn send(&self, command: Command) {
        if self.commands.send(command).is_err() {
            log::warn!("hotkey command dropped: {NOT_RUNNING}");
        }
    }
}

impl Drop for HotkeyService {
    fn drop(&mut self) {
        // If the thread is already gone the send fails, which is fine.
        let _ = self.commands.send(Command::Shutdown);
        if let Some(thread) = self.thread.take()
            && thread.join().is_err()
        {
            log::error!("the hotkey thread panicked");
        }
    }
}

enum Command {
    Update {
        wanted: Wanted,
        reply: Sender<Result<(), String>>,
    },
    SetCancel(bool),
    SetPaused(bool),
    Shutdown,
}

/// What a registered key does.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Role {
    Dictation,
    PasteLast,
    Cancel,
}

/// The parsed dictation and paste-last keys.
#[derive(Debug, Clone, PartialEq)]
struct Wanted {
    dictation: Vec<Hotkey>,
    paste_last: Vec<Hotkey>,
}

impl Wanted {
    /// Parses `bindings`; refuses a key that two entries share or that is taken by cancel.
    fn from_bindings(bindings: &Bindings) -> Result<Self, String> {
        let dictation = parse_all(&bindings.dictation)?;
        let paste_last = parse_all(&bindings.paste_last)?;
        let cancel = cancel_hotkeys(&dictation);

        let entries = bindings
            .dictation
            .iter()
            .zip(&dictation)
            .chain(bindings.paste_last.iter().zip(&paste_last));
        let mut seen: Vec<(&str, Hotkey)> = Vec::new();
        for (text, hotkey) in entries {
            if cancel.contains(hotkey) {
                return Err(format!("\"{text}\" is reserved for cancelling a dictation"));
            }
            if let Some((other, _)) = seen.iter().find(|(_, seen_key)| seen_key == hotkey) {
                return Err(format!("\"{text}\" is already used by \"{other}\""));
            }
            seen.push((text, *hotkey));
        }
        Ok(Self {
            dictation,
            paste_last,
        })
    }
}

fn parse_all(bindings: &[String]) -> Result<Vec<Hotkey>, String> {
    bindings.iter().map(|text| parse_binding(text)).collect()
}

/// The keys that cancel a dictation: Esc alone (tap mode, nothing held) and Esc with the
/// modifiers of each dictation key (hold mode). A bare Esc does not fire while Right Alt is held,
/// because handy-keys matches the modifiers exactly.
fn cancel_hotkeys(dictation: &[Hotkey]) -> Vec<Hotkey> {
    let escape = |modifiers| Hotkey {
        modifiers,
        key: Some(Key::Escape),
    };
    let mut cancel = vec![escape(Modifiers::empty())];
    for hotkey in dictation {
        let with_modifiers = escape(hotkey.modifiers);
        if !cancel.contains(&with_modifiers) {
            cancel.push(with_modifiers);
        }
    }
    cancel
}

/// What the worker needs from the keyboard hook. `HotkeyManager` is the real one; the tests use a
/// fake that needs no hook.
trait Backend {
    type Id: Copy + Eq + Send + 'static;

    fn register(&self, hotkey: Hotkey) -> Result<Self::Id, String>;
    fn unregister(&self, id: Self::Id) -> Result<(), String>;
    fn try_recv(&self) -> Option<(Self::Id, HotkeyState)>;
}

impl Backend for HotkeyManager {
    type Id = HotkeyId;

    fn register(&self, hotkey: Hotkey) -> Result<HotkeyId, String> {
        HotkeyManager::register(self, hotkey).map_err(|err| err.to_string())
    }

    fn unregister(&self, id: HotkeyId) -> Result<(), String> {
        HotkeyManager::unregister(self, id).map_err(|err| err.to_string())
    }

    fn try_recv(&self) -> Option<(HotkeyId, HotkeyState)> {
        HotkeyManager::try_recv(self).map(|event| (event.id, event.state))
    }
}

struct Registration<Id> {
    hotkey: Hotkey,
    id: Id,
    role: Role,
}

/// The body of the hotkey thread.
struct Worker<B: Backend> {
    backend: B,
    events: UnboundedSender<HotkeyEvent>,
    wanted: Wanted,
    cancel_enabled: bool,
    paused: bool,
    registered: Vec<Registration<B::Id>>,
}

impl<B: Backend> Worker<B> {
    /// Nothing is registered until the first [`sync`](Self::sync).
    fn new(backend: B, wanted: Wanted, events: UnboundedSender<HotkeyEvent>) -> Self {
        Self {
            backend,
            events,
            wanted,
            cancel_enabled: false,
            paused: false,
            registered: Vec::new(),
        }
    }

    fn run(mut self, inbox: &Receiver<Command>) {
        loop {
            self.pump();
            if self.events.is_closed() {
                log::warn!("nobody listens to hotkey events any more; releasing the keyboard");
                break;
            }
            match inbox.recv_timeout(POLL_INTERVAL) {
                Ok(Command::Shutdown) | Err(RecvTimeoutError::Disconnected) => break,
                Ok(command) => self.handle(command),
                Err(RecvTimeoutError::Timeout) => {}
            }
        }
    }

    fn handle(&mut self, command: Command) {
        // Key events from before the command are reported first, under the bindings they
        // happened with. Otherwise a pause could swallow the release of a key that was held.
        self.pump();
        match command {
            Command::Update { wanted, reply } => {
                let previous = std::mem::replace(&mut self.wanted, wanted);
                let result = self.sync();
                if let Err(err) = &result {
                    log::error!("could not apply the new hotkey bindings: {err}");
                    // Go back to what worked before.
                    self.wanted = previous;
                    self.sync_logged();
                }
                // The caller may have given up waiting.
                let _ = reply.send(result);
            }
            Command::SetCancel(on) => {
                self.cancel_enabled = on;
                self.sync_logged();
            }
            Command::SetPaused(paused) => {
                self.paused = paused;
                self.sync_logged();
            }
            Command::Shutdown => {}
        }
    }

    /// Forwards the key events the backend has queued.
    fn pump(&self) {
        while let Some((id, state)) = self.backend.try_recv() {
            // An event can outlive its key: one queued just before the key was unregistered.
            let Some(role) = self.registered.iter().find(|r| r.id == id).map(|r| r.role) else {
                continue;
            };
            if let Some(event) = translate(role, state) {
                log::debug!("hotkey event: {event:?}");
                // A closed channel is noticed by `run`.
                let _ = self.events.send(event);
            }
        }
    }

    /// The keys that should be registered right now.
    fn active(&self) -> Vec<(Hotkey, Role)> {
        let mut active = Vec::new();
        if !self.paused {
            active.extend(self.wanted.dictation.iter().map(|k| (*k, Role::Dictation)));
            active.extend(self.wanted.paste_last.iter().map(|k| (*k, Role::PasteLast)));
        }
        if self.cancel_enabled {
            active.extend(
                cancel_hotkeys(&self.wanted.dictation)
                    .into_iter()
                    .map(|k| (k, Role::Cancel)),
            );
        }
        active
    }

    /// Makes the registrations match [`active`](Self::active). Unchanged keys are left alone.
    /// A failure does not stop the rest: every key is tried, and the first error is returned.
    fn sync(&mut self) -> Result<(), String> {
        let active = self.active();
        let mut first_error: Option<String> = None;
        let mut note = |err: String| {
            first_error.get_or_insert(err);
        };

        // Release first, so that a key which changes its role is free to be registered again.
        for registration in std::mem::take(&mut self.registered) {
            if active.contains(&(registration.hotkey, registration.role)) {
                self.registered.push(registration);
            } else if let Err(err) = self.backend.unregister(registration.id) {
                note(format!("could not release {}: {err}", registration.hotkey));
            }
        }
        for (hotkey, role) in active {
            if self.registered.iter().any(|r| r.hotkey == hotkey) {
                continue;
            }
            match self.backend.register(hotkey) {
                Ok(id) => self.registered.push(Registration { hotkey, id, role }),
                Err(err) => note(format!("could not register {hotkey}: {err}")),
            }
        }
        first_error.map_or(Ok(()), Err)
    }

    fn sync_logged(&mut self) {
        if let Err(err) = self.sync() {
            log::error!("hotkey registration failed: {err}");
        }
    }
}

/// The event to report for a key of the given role changing state, if any. Only dictation keys
/// report their release.
fn translate(role: Role, state: HotkeyState) -> Option<HotkeyEvent> {
    match (role, state) {
        (Role::Dictation, HotkeyState::Pressed) => Some(HotkeyEvent::DictationDown),
        (Role::Dictation, HotkeyState::Released) => Some(HotkeyEvent::DictationUp),
        (Role::PasteLast, HotkeyState::Pressed) => Some(HotkeyEvent::PasteLast),
        (Role::Cancel, HotkeyState::Pressed) => Some(HotkeyEvent::Cancel),
        (Role::PasteLast | Role::Cancel, HotkeyState::Released) => None,
    }
}

#[cfg(test)]
mod tests {
    use std::collections::{HashSet, VecDeque};
    use std::sync::{Arc, Mutex};
    use std::time::Instant;

    use tokio::sync::mpsc::{UnboundedReceiver, unbounded_channel};

    use super::*;

    fn hk(binding: &str) -> Hotkey {
        parse_binding(binding).unwrap_or_else(|err| panic!("{err}"))
    }

    fn hks(bindings: &[&str]) -> HashSet<Hotkey> {
        bindings.iter().map(|b| hk(b)).collect()
    }

    fn bindings(dictation: &[&str], paste_last: &[&str]) -> Bindings {
        Bindings {
            dictation: dictation.iter().map(|s| s.to_string()).collect(),
            paste_last: paste_last.iter().map(|s| s.to_string()).collect(),
        }
    }

    // ---- parse_binding ----

    #[test]
    fn parses_bindings() {
        assert!(parse_binding("AltRight").is_ok());
        assert!(parse_binding("AltRight+Space").is_ok());
        assert!(parse_binding("Nope+Q").is_err());
    }

    #[test]
    fn default_dictation_binding_is_right_alt_alone() {
        let defaults = Bindings::default();
        assert_eq!(defaults.dictation, vec!["AltRight"]);
        assert_eq!(
            parse_binding(&defaults.dictation[0]).unwrap(),
            Hotkey::new(Modifiers::OPT_RIGHT, None).unwrap()
        );
    }

    #[test]
    fn default_paste_last_binding_is_right_alt_and_v() {
        let defaults = Bindings::default();
        assert_eq!(defaults.paste_last, vec!["AltRight+V"]);
        assert_eq!(
            parse_binding(&defaults.paste_last[0]).unwrap(),
            Hotkey::new(Modifiers::OPT_RIGHT, Key::V).unwrap()
        );
    }

    #[test]
    fn right_alt_and_space_parses_to_right_alt_plus_space() {
        assert_eq!(
            parse_binding("AltRight+Space").unwrap(),
            Hotkey::new(Modifiers::OPT_RIGHT, Key::Space).unwrap()
        );
    }

    #[test]
    fn case_and_order_do_not_matter() {
        assert_eq!(hk("altright+v"), hk("AltRight+V"));
        assert_eq!(hk("V+AltRight"), hk("AltRight+V"));
        assert_eq!(hk(" AltRight + V "), hk("AltRight+V"));
    }

    #[test]
    fn browser_key_names_are_accepted() {
        // The settings page records `KeyboardEvent.code` values.
        assert_eq!(hk("AltRight+KeyV"), hk("AltRight+V"));
        assert_eq!(hk("keyv+altright"), hk("AltRight+V"));
        assert_eq!(hk("ControlLeft+Digit1"), hk("CtrlLeft+1"));
        assert_eq!(hk("ShiftRight+ArrowUp"), hk("ShiftRight+Up"));
        assert_eq!(hk("MetaLeft+Backquote"), hk("CmdLeft+Grave"));
        assert_eq!(hk("AltLeft+BracketLeft"), hk("AltLeft+["));
        assert_eq!(hk("Numpad5"), hk("Keypad5"));
        assert_eq!(hk("NumpadAdd"), hk("KeypadPlus"));
        assert_eq!(hk("NumpadSubtract"), hk("KeypadMinus"));
        assert_eq!(hk("NumpadEnter"), hk("KeypadEnter"));
        assert_eq!(hk("AltRight+Escape"), hk("AltRight+Esc"));
    }

    #[test]
    fn browser_delete_is_forward_delete_and_backspace_is_not() {
        assert_eq!(
            hk("Delete"),
            Hotkey::new(Modifiers::empty(), Key::ForwardDelete).unwrap()
        );
        assert_eq!(
            hk("Backspace"),
            Hotkey::new(Modifiers::empty(), Key::Delete).unwrap()
        );
    }

    #[test]
    fn every_browser_key_name_we_translate_exists_in_handy_keys() {
        let mut names: Vec<String> = Vec::new();
        names.extend(('A'..='Z').map(|c| format!("Key{c}")));
        names.extend((0..=9).map(|d| format!("Digit{d}")));
        names.extend((0..=9).map(|d| format!("Numpad{d}")));
        names.extend(["Left", "Right", "Up", "Down"].map(|d| format!("Arrow{d}")));
        names.extend(
            [
                "Add", "Subtract", "Multiply", "Divide", "Decimal", "Enter", "Equal", "Comma",
            ]
            .map(|n| format!("Numpad{n}")),
        );
        names.extend(
            [
                "Backquote",
                "BracketLeft",
                "BracketRight",
                "Delete",
                "Backspace",
                "Minus",
                "Equal",
            ]
            .map(String::from),
        );
        for name in names {
            assert!(parse_binding(&name).is_ok(), "{name} does not parse");
            assert!(
                parse_binding(&format!("AltRight+{name}")).is_ok(),
                "AltRight+{name} does not parse"
            );
        }
    }

    #[test]
    fn bad_bindings_are_errors_that_name_the_binding() {
        // "Key1" and "DigitA" are not browser key names.
        for bad in [
            "",
            "+",
            " ",
            "Nope+Q",
            "A+B",
            "AltRight+Nope",
            "KeyAB",
            "Key1",
            "DigitA",
        ] {
            let err = parse_binding(bad).expect_err(bad);
            assert!(err.contains(&format!("\"{bad}\"")), "{err}");
        }
    }

    // ---- Wanted and the cancel keys ----

    #[test]
    fn default_bindings_are_accepted() {
        let wanted = Wanted::from_bindings(&Bindings::default()).unwrap();
        assert_eq!(wanted.dictation, vec![hk("AltRight")]);
        assert_eq!(wanted.paste_last, vec![hk("AltRight+V")]);
    }

    #[test]
    fn a_key_can_be_bound_only_once() {
        // Same action, different spelling.
        assert!(Wanted::from_bindings(&bindings(&["AltRight", "altright"], &[])).is_err());
        // Two actions.
        let err =
            Wanted::from_bindings(&bindings(&["AltRight+V"], &["AltRight+KeyV"])).unwrap_err();
        assert!(
            err.contains("AltRight+KeyV") && err.contains("AltRight+V"),
            "{err}"
        );
    }

    #[test]
    fn escape_belongs_to_cancel() {
        for taken in ["Escape", "AltRight+Escape", "AltRight+Esc"] {
            assert!(
                Wanted::from_bindings(&bindings(&["AltRight"], &[taken])).is_err(),
                "{taken}"
            );
        }
        // With another dictation key, Right Alt+Esc is no longer a cancel key.
        assert!(Wanted::from_bindings(&bindings(&["F9"], &["AltRight+Escape"])).is_ok());
        // A dictation key cannot be a cancel key either.
        assert!(Wanted::from_bindings(&bindings(&["AltRight+Escape"], &[])).is_err());
    }

    #[test]
    fn an_invalid_binding_rejects_everything() {
        assert!(Wanted::from_bindings(&bindings(&["AltRight", "Nope"], &["AltRight+V"])).is_err());
        assert!(Wanted::from_bindings(&bindings(&["AltRight"], &["Nope"])).is_err());
    }

    #[test]
    fn an_action_may_have_no_keys() {
        let wanted = Wanted::from_bindings(&bindings(&[], &[])).unwrap();
        assert!(wanted.dictation.is_empty() && wanted.paste_last.is_empty());
    }

    #[test]
    fn cancel_keys_follow_the_dictation_modifiers() {
        assert_eq!(
            cancel_hotkeys(&[hk("AltRight")]),
            vec![hk("Escape"), hk("AltRight+Escape")]
        );
        // A key without modifiers needs no second Esc.
        assert_eq!(cancel_hotkeys(&[hk("F9")]), vec![hk("Escape")]);
        assert_eq!(cancel_hotkeys(&[]), vec![hk("Escape")]);
        assert_eq!(
            cancel_hotkeys(&[
                hk("AltRight"),
                hk("ControlRight+Space"),
                hk("AltRight+Space")
            ]),
            vec![
                hk("Escape"),
                hk("AltRight+Escape"),
                hk("ControlRight+Escape")
            ]
        );
    }

    // ---- translate ----

    #[test]
    fn dictation_reports_both_edges() {
        assert_eq!(
            translate(Role::Dictation, HotkeyState::Pressed),
            Some(HotkeyEvent::DictationDown)
        );
        assert_eq!(
            translate(Role::Dictation, HotkeyState::Released),
            Some(HotkeyEvent::DictationUp)
        );
    }

    #[test]
    fn paste_last_and_cancel_report_only_the_press() {
        assert_eq!(
            translate(Role::PasteLast, HotkeyState::Pressed),
            Some(HotkeyEvent::PasteLast)
        );
        assert_eq!(translate(Role::PasteLast, HotkeyState::Released), None);
        assert_eq!(
            translate(Role::Cancel, HotkeyState::Pressed),
            Some(HotkeyEvent::Cancel)
        );
        assert_eq!(translate(Role::Cancel, HotkeyState::Released), None);
    }

    // ---- Worker, with a fake keyboard hook ----

    /// Stands in for `HotkeyManager`: records registrations and replays queued key events. Clones
    /// share their state, so a test can keep one after handing the other to a worker.
    #[derive(Clone, Default)]
    struct FakeBackend(Arc<Mutex<FakeState>>);

    #[derive(Default)]
    struct FakeState {
        next_id: u32,
        registered: Vec<(u32, Hotkey)>,
        queue: VecDeque<(u32, HotkeyState)>,
        /// Registering this key fails.
        refuse: Option<Hotkey>,
    }

    impl Backend for FakeBackend {
        type Id = u32;

        fn register(&self, hotkey: Hotkey) -> Result<u32, String> {
            let mut state = self.0.lock().unwrap();
            if state.registered.iter().any(|(_, h)| *h == hotkey) || state.refuse == Some(hotkey) {
                return Err("already registered".to_string());
            }
            state.next_id += 1;
            let id = state.next_id;
            state.registered.push((id, hotkey));
            Ok(id)
        }

        fn unregister(&self, id: u32) -> Result<(), String> {
            let mut state = self.0.lock().unwrap();
            let before = state.registered.len();
            state.registered.retain(|(i, _)| *i != id);
            if state.registered.len() == before {
                return Err("not found".to_string());
            }
            Ok(())
        }

        fn try_recv(&self) -> Option<(u32, HotkeyState)> {
            self.0.lock().unwrap().queue.pop_front()
        }
    }

    impl FakeBackend {
        fn registered(&self) -> HashSet<Hotkey> {
            self.0
                .lock()
                .unwrap()
                .registered
                .iter()
                .map(|(_, h)| *h)
                .collect()
        }

        fn id_of(&self, binding: &str) -> u32 {
            let hotkey = hk(binding);
            let state = self.0.lock().unwrap();
            state
                .registered
                .iter()
                .find(|(_, h)| *h == hotkey)
                .map(|(i, _)| *i)
                .unwrap_or_else(|| panic!("{binding} is not registered"))
        }

        fn queue(&self, id: u32, state: HotkeyState) {
            self.0.lock().unwrap().queue.push_back((id, state));
        }

        fn press(&self, binding: &str) {
            self.queue(self.id_of(binding), HotkeyState::Pressed);
        }

        fn release(&self, binding: &str) {
            self.queue(self.id_of(binding), HotkeyState::Released);
        }

        fn refuse(&self, binding: &str) {
            self.0.lock().unwrap().refuse = Some(hk(binding));
        }
    }

    fn worker(
        fake: &FakeBackend,
        b: &Bindings,
    ) -> (Worker<FakeBackend>, UnboundedReceiver<HotkeyEvent>) {
        let (events, rx) = unbounded_channel();
        let wanted = Wanted::from_bindings(b).unwrap();
        (Worker::new(fake.clone(), wanted, events), rx)
    }

    fn drain(rx: &mut UnboundedReceiver<HotkeyEvent>) -> Vec<HotkeyEvent> {
        std::iter::from_fn(|| rx.try_recv().ok()).collect()
    }

    #[test]
    fn nothing_is_registered_before_the_first_sync() {
        let fake = FakeBackend::default();
        let (_worker, _rx) = worker(&fake, &Bindings::default());
        assert!(fake.registered().is_empty());
    }

    #[test]
    fn sync_registers_the_dictation_and_paste_last_keys() {
        let fake = FakeBackend::default();
        let (mut w, _rx) = worker(&fake, &Bindings::default());
        w.sync().unwrap();
        assert_eq!(fake.registered(), hks(&["AltRight", "AltRight+V"]));
    }

    #[test]
    fn cancel_keys_are_registered_only_while_enabled() {
        let fake = FakeBackend::default();
        let (mut w, _rx) = worker(&fake, &Bindings::default());
        w.sync().unwrap();

        w.handle(Command::SetCancel(true));
        assert_eq!(
            fake.registered(),
            hks(&["AltRight", "AltRight+V", "Escape", "AltRight+Escape"])
        );

        w.handle(Command::SetCancel(false));
        assert_eq!(fake.registered(), hks(&["AltRight", "AltRight+V"]));
    }

    #[test]
    fn pausing_frees_the_dictation_and_paste_last_keys_but_not_cancel() {
        let fake = FakeBackend::default();
        let (mut w, _rx) = worker(&fake, &Bindings::default());
        w.sync().unwrap();
        w.handle(Command::SetCancel(true));

        w.handle(Command::SetPaused(true));
        assert_eq!(fake.registered(), hks(&["Escape", "AltRight+Escape"]));

        w.handle(Command::SetPaused(false));
        assert_eq!(
            fake.registered(),
            hks(&["AltRight", "AltRight+V", "Escape", "AltRight+Escape"])
        );

        w.handle(Command::SetCancel(false));
        w.handle(Command::SetPaused(true));
        assert!(fake.registered().is_empty());
    }

    #[test]
    fn an_update_while_paused_takes_effect_on_resume() {
        let fake = FakeBackend::default();
        let (mut w, _rx) = worker(&fake, &Bindings::default());
        w.sync().unwrap();
        w.handle(Command::SetPaused(true));

        let (reply, answer) = mpsc::channel();
        let wanted = Wanted::from_bindings(&bindings(&["AltRight+Space"], &["F9"])).unwrap();
        w.handle(Command::Update { wanted, reply });
        assert_eq!(answer.recv().unwrap(), Ok(()));
        assert!(fake.registered().is_empty(), "still paused");

        w.handle(Command::SetPaused(false));
        assert_eq!(fake.registered(), hks(&["AltRight+Space", "F9"]));
    }

    #[test]
    fn an_update_leaves_unchanged_keys_registered() {
        let fake = FakeBackend::default();
        let (mut w, _rx) = worker(&fake, &Bindings::default());
        w.sync().unwrap();
        let paste_id = fake.id_of("AltRight+V");

        let (reply, answer) = mpsc::channel();
        let wanted =
            Wanted::from_bindings(&bindings(&["AltRight+Space", "F9"], &["AltRight+V"])).unwrap();
        w.handle(Command::Update { wanted, reply });

        assert_eq!(answer.recv().unwrap(), Ok(()));
        assert_eq!(
            fake.registered(),
            hks(&["AltRight+Space", "F9", "AltRight+V"])
        );
        assert_eq!(fake.id_of("AltRight+V"), paste_id, "was not re-registered");
    }

    #[test]
    fn an_update_can_swap_the_roles_of_two_keys() {
        let fake = FakeBackend::default();
        let (mut w, mut rx) = worker(&fake, &bindings(&["F8"], &["F9"]));
        w.sync().unwrap();

        let (reply, answer) = mpsc::channel();
        let wanted = Wanted::from_bindings(&bindings(&["F9"], &["F8"])).unwrap();
        w.handle(Command::Update { wanted, reply });
        assert_eq!(answer.recv().unwrap(), Ok(()));

        fake.press("F9");
        fake.press("F8");
        w.pump();
        assert_eq!(
            drain(&mut rx),
            vec![HotkeyEvent::DictationDown, HotkeyEvent::PasteLast]
        );
    }

    #[test]
    fn a_failed_update_restores_the_old_keys() {
        let fake = FakeBackend::default();
        let (mut w, _rx) = worker(&fake, &Bindings::default());
        w.sync().unwrap();
        fake.refuse("F9");

        let (reply, answer) = mpsc::channel();
        let wanted = Wanted::from_bindings(&bindings(&["AltRight+Space", "F9"], &[])).unwrap();
        w.handle(Command::Update { wanted, reply });

        let err = answer.recv().unwrap().unwrap_err();
        assert!(err.contains("F9"), "{err}");
        assert_eq!(fake.registered(), hks(&["AltRight", "AltRight+V"]));
        assert_eq!(
            w.wanted,
            Wanted::from_bindings(&Bindings::default()).unwrap()
        );
    }

    #[test]
    fn key_events_become_hotkey_events() {
        let fake = FakeBackend::default();
        let (mut w, mut rx) = worker(&fake, &Bindings::default());
        w.sync().unwrap();
        w.handle(Command::SetCancel(true));

        // Right Alt+V: Right Alt goes down first, so the dictation key reports before paste-last.
        fake.press("AltRight");
        fake.press("AltRight+V");
        fake.release("AltRight+V");
        fake.release("AltRight");
        fake.press("Escape");
        fake.release("Escape");
        fake.press("AltRight+Escape");
        w.pump();

        assert_eq!(
            drain(&mut rx),
            vec![
                HotkeyEvent::DictationDown,
                HotkeyEvent::PasteLast,
                HotkeyEvent::DictationUp,
                HotkeyEvent::Cancel,
                HotkeyEvent::Cancel,
            ]
        );
    }

    #[test]
    fn key_events_from_before_a_command_are_reported_before_it_takes_effect() {
        let fake = FakeBackend::default();
        let (mut w, mut rx) = worker(&fake, &Bindings::default());
        w.sync().unwrap();

        // Right Alt went down and came up, then the pause arrived.
        fake.press("AltRight");
        fake.release("AltRight");
        w.handle(Command::SetPaused(true));

        assert_eq!(
            drain(&mut rx),
            vec![HotkeyEvent::DictationDown, HotkeyEvent::DictationUp]
        );
        assert!(fake.registered().is_empty());
    }

    #[test]
    fn events_of_a_key_that_is_no_longer_registered_are_dropped() {
        let fake = FakeBackend::default();
        let (mut w, mut rx) = worker(&fake, &Bindings::default());
        w.sync().unwrap();
        let right_alt = fake.id_of("AltRight");
        w.handle(Command::SetPaused(true));

        // An event that was produced just before the key was unregistered arrives late.
        fake.queue(right_alt, HotkeyState::Pressed);
        // And an id nobody ever had.
        fake.queue(9999, HotkeyState::Pressed);
        w.pump();
        assert!(drain(&mut rx).is_empty());
    }

    // ---- HotkeyService, with real threads and the fake keyboard hook ----

    fn service(
        fake: &FakeBackend,
        b: &Bindings,
    ) -> (HotkeyService, UnboundedReceiver<HotkeyEvent>) {
        let (events, rx) = unbounded_channel();
        let wanted = Wanted::from_bindings(b).unwrap();
        (
            HotkeyService::start_with(fake.clone(), wanted, events).unwrap(),
            rx,
        )
    }

    /// Waits until the hotkey thread has handled everything sent before this call, and has looked
    /// for key events twice since.
    fn settle(svc: &HotkeyService, b: &Bindings) {
        svc.update_bindings(b).unwrap();
        svc.update_bindings(b).unwrap();
    }

    fn next_event(rx: &mut UnboundedReceiver<HotkeyEvent>) -> HotkeyEvent {
        let deadline = Instant::now() + Duration::from_secs(5);
        while Instant::now() < deadline {
            if let Ok(event) = rx.try_recv() {
                return event;
            }
            thread::sleep(Duration::from_millis(2));
        }
        panic!("no hotkey event within 5 s");
    }

    #[test]
    fn the_service_registers_at_start_and_reports_events() {
        let fake = FakeBackend::default();
        let (svc, mut rx) = service(&fake, &Bindings::default());
        assert_eq!(fake.registered(), hks(&["AltRight", "AltRight+V"]));

        fake.press("AltRight");
        assert_eq!(next_event(&mut rx), HotkeyEvent::DictationDown);
        fake.release("AltRight");
        assert_eq!(next_event(&mut rx), HotkeyEvent::DictationUp);
        drop(svc);
    }

    #[test]
    fn the_service_applies_commands_in_order() {
        let fake = FakeBackend::default();
        let b = Bindings::default();
        let (svc, _rx) = service(&fake, &b);

        svc.set_cancel_enabled(true);
        settle(&svc, &b);
        assert_eq!(
            fake.registered(),
            hks(&["AltRight", "AltRight+V", "Escape", "AltRight+Escape"])
        );

        svc.set_paused(true);
        settle(&svc, &b);
        assert_eq!(fake.registered(), hks(&["Escape", "AltRight+Escape"]));

        svc.set_paused(false);
        svc.set_cancel_enabled(false);
        settle(&svc, &b);
        assert_eq!(fake.registered(), hks(&["AltRight", "AltRight+V"]));
    }

    #[test]
    fn update_bindings_replaces_the_keys_and_reports_bad_ones() {
        let fake = FakeBackend::default();
        let (svc, _rx) = service(&fake, &Bindings::default());

        svc.update_bindings(&bindings(&["AltRight+Space"], &["AltRight+KeyV"]))
            .unwrap();
        assert_eq!(fake.registered(), hks(&["AltRight+Space", "AltRight+V"]));

        let err = svc.update_bindings(&bindings(&["Nope"], &[])).unwrap_err();
        assert!(err.contains("Nope"), "{err}");
        let err = svc
            .update_bindings(&bindings(&["F9"], &["F9"]))
            .unwrap_err();
        assert!(err.contains("F9"), "{err}");
        assert_eq!(
            fake.registered(),
            hks(&["AltRight+Space", "AltRight+V"]),
            "unchanged"
        );
    }

    #[test]
    fn the_cancel_switch_turns_the_cancel_keys_on_and_off() {
        let fake = FakeBackend::default();
        let b = Bindings::default();
        let (svc, mut rx) = service(&fake, &b);
        let switch: &dyn CancelSwitch = &svc;

        switch.set(true);
        settle(&svc, &b);
        fake.press("Escape");
        assert_eq!(next_event(&mut rx), HotkeyEvent::Cancel);

        switch.set(false);
        settle(&svc, &b);
        assert_eq!(fake.registered(), hks(&["AltRight", "AltRight+V"]));
    }

    #[test]
    fn dropping_the_service_releases_the_hook() {
        let fake = FakeBackend::default();
        let (svc, _rx) = service(&fake, &Bindings::default());
        assert_eq!(
            Arc::strong_count(&fake.0),
            2,
            "the worker holds the other handle"
        );
        drop(svc);
        assert_eq!(
            Arc::strong_count(&fake.0),
            1,
            "the thread is gone and dropped its backend"
        );
    }

    #[test]
    fn the_service_stops_when_nobody_listens_to_its_events() {
        let fake = FakeBackend::default();
        let (svc, rx) = service(&fake, &Bindings::default());
        drop(rx);

        let deadline = Instant::now() + Duration::from_secs(5);
        while svc.update_bindings(&Bindings::default()).is_ok() {
            assert!(Instant::now() < deadline, "the thread is still running");
            thread::sleep(Duration::from_millis(5));
        }
        assert_eq!(
            svc.update_bindings(&Bindings::default()),
            Err(NOT_RUNNING.to_string())
        );
        assert_eq!(Arc::strong_count(&fake.0), 1);
    }

    #[test]
    fn starting_fails_when_a_key_cannot_be_registered() {
        let fake = FakeBackend::default();
        fake.refuse("AltRight+V");
        let (events, _rx) = unbounded_channel();
        let wanted = Wanted::from_bindings(&Bindings::default()).unwrap();
        let err = HotkeyService::start_with(fake.clone(), wanted, events)
            .err()
            .unwrap();
        assert!(err.contains("could not register"), "{err}");
        assert_eq!(Arc::strong_count(&fake.0), 1, "nothing is left running");
    }

    #[test]
    fn starting_with_a_bad_binding_installs_no_hook() {
        let (events, _rx) = unbounded_channel();
        let err = HotkeyService::start(&bindings(&["Nope"], &[]), events)
            .err()
            .unwrap();
        assert!(err.contains("Nope"), "{err}");
    }
}
