//! Raphael operates the owner's own desktop: the shell's half, the hands.
//!
//! The core decides what to do (`raphael_core.computer`); it cannot reach UI Automation, so it
//! asks this side with a notification, `computer.request {id, op, args}`, which is answered with a
//! request of ours, `computer.reply {id, ok, result | error}`. The operations:
//!
//! - `apps`: the top-level windows of the apps on the desktop, each with its executable's file
//!   name (`excel.exe`), the name the app goes by, its title (kept in memory only), whether it is in
//!   front and whether it is on the always-allowed list. Raphael's own windows and the shell's
//!   (the taskbar, the desktop) are left out.
//! - `look {window}`: a compact accessibility snapshot of one window ([`tree`]), never with the
//!   value of a password field. Its elements are kept until the next look at that window, so that a
//!   step can name one by its id.
//! - `act {window, element, action, text, keys, direction, confirmed}`: one step. UI Automation's
//!   patterns come first (Invoke, Toggle, SelectionItem, ExpandCollapse for a click; Value for
//!   typing; Scroll), a mouse click at the element's centre or typed keys only when there is none.
//!   Keys come from an allowlist ([`keys`]). Typed keys break a line with Enter, which may send a
//!   message or a form, so text with a line break is typed only in a step the owner said yes to
//!   (`confirmed`); text with any other control character is refused. Keys and typed text never
//!   go to a sign-in, a payment field or a one-time code ([`policy::is_handoff_name`]): the live
//!   element with the focus is read again just before, and such a step fails with `handoff`.
//!   Keys and typed text for an element named go only to that element once it has the focus
//!   (`focus` otherwise: nothing is sent to what had the focus before), and keys that delete what
//!   is selected (Delete, Backspace, Ctrl+D) reach no live focus but a text field unless the step
//!   is `confirmed` (`unconfirmed` otherwise).
//! - `focus {window}`: the window is brought to the front.
//! - `check`: nothing is done; it fails as any request of the run would (`stopped`, `watch`), so
//!   that the core opens an app itself only for a run the shell has taken.
//! - `allow {exe, name}`: the app joins the always-allowed list of the device settings.
//!
//! What may never be touched is refused here whatever the core asks ([`policy`], [`Service`]): a
//! window of Raphael's own (`own_window`), an app Raphael never operates (a terminal, a password
//! manager: `refused_app`), an elevated window (an administrator's: `elevated`, which the core
//! hands to the owner), and a password field, which is never typed into or read (`password`). The
//! secure desktop of UAC cannot be reached from here at all.
//!
//! `computer.state {phase, line, run}` says where a run is: `begin` and `step` show the overlay's
//! 「操作中」 line and arm the watch on the owner's own input ([`watch`]), `pause` disarms it while
//! a question is on screen, `end` takes everything down, the elements kept with it, and shows the
//! line it brings (《了。已停止。》 after Esc, which hid the overlay). When the owner touches the
//! mouse or the keyboard, the core is told (`computer.interrupt`) and stops the run.
//!
//! A run is revocable ([`Runs`]). `begin` makes its id (`run`, which every request says too) the
//! shell's run; `end`, the owner's own input and the core going away revoke it at once, on the
//! thread that hears of it. Every request is given a [`Ticket`] for the run it says when it comes
//! in, and the desktop's thread checks it just before each thing that changes the desktop (a
//! window brought to the front, a pattern called, a value set, each batch of keys or of a mouse
//! click sent), so that a step asked for before the run ended, waiting behind a slow look or
//! halfway through its keys, does nothing more once the overlay says the run is over. A request
//! of a run that is not the shell's fails with `stopped`. A run whose watch on the owner's input
//! could not be started is refused as it begins ([`Runs::refuse`]): its requests fail with
//! `watch`, and the core ends it.
//!
//! Nothing here logs a window's title, an element's name or value, or what is typed: only
//! operations, kinds of failure and counts.

pub mod keys;
pub mod policy;
pub mod tree;
pub mod watch;

#[cfg(windows)]
mod app;
#[cfg(windows)]
mod uia;

#[cfg(windows)]
pub use app::spawn;

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

use serde_json::{Value, json};

use keys::Chord;
use tree::Entry;

/// The longest text one step may type, in characters (the core's limit too).
pub const MAX_TEXT_CHARS: usize = 1000;

/// Why a request could not be done, as the core is told it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Failure {
    /// The window is gone.
    Gone,
    /// The element is no longer there, or the id is of no look.
    Stale,
    /// A password field: the owner's.
    Password,
    /// An elevated window: the owner's.
    Elevated,
    /// An app Raphael never operates.
    RefusedApp,
    /// A window of Raphael's own.
    OwnWindow,
    /// Not a request of the expected shape.
    BadRequest,
    /// Keys outside the allowlist.
    KeysNotAllowed,
    /// The run the request is for is over (or was never the shell's): nothing more is done.
    Stopped,
    /// What the keys or the text would reach is a sign-in, a payment field or a code: the owner's.
    Handoff,
    /// The element named could not be given the keyboard focus, or the live focus is on another:
    /// nothing was pressed or typed.
    Focus,
    /// What the live focus is makes the step one that sends or deletes (Enter on a Send button or
    /// in a message box, a line break typed, Delete on a file), in a step the owner did not say
    /// yes to.
    Unconfirmed,
    /// The watch on the owner's own input could not be started: the run is revoked, and nothing
    /// is done for it.
    Watch,
    /// Anything else.
    Failed,
}

impl Failure {
    /// The word the core reads (`computer.bridge.KINDS`).
    pub fn kind(self) -> &'static str {
        match self {
            Failure::Gone => "gone",
            Failure::Stale => "stale",
            Failure::Password => "password",
            Failure::Elevated => "elevated",
            Failure::RefusedApp => "refused_app",
            Failure::OwnWindow => "own_window",
            Failure::BadRequest => "bad_request",
            Failure::KeysNotAllowed => "keys_not_allowed",
            Failure::Stopped => "stopped",
            Failure::Handoff => "handoff",
            Failure::Focus => "focus",
            Failure::Unconfirmed => "unconfirmed",
            Failure::Watch => "watch",
            Failure::Failed => "failed",
        }
    }
}

/// The run the shell works for, revocable at once.
///
/// The core names each run (`run`, 32 hexadecimal characters). `begin` makes it the shell's and
/// gives it a generation of its own; `end` of that run, or `revoke`, takes it away. A [`Ticket`]
/// is made for each request when it comes in, for the run the request says: it is live while that
/// run is the shell's, and never again after (a later run has another generation). Checking a
/// ticket is one atomic load, cheap enough to do before every input sent.
#[derive(Debug, Default)]
pub struct Runs {
    /// The id and the generation of the run under way.
    active: Mutex<Option<(String, u64)>>,
    /// The generation of the run under way, 0 when there is none.
    live: Arc<AtomicU64>,
    /// The last generation given.
    given: AtomicU64,
    /// The id and the generation of the last run refused because it could not be watched, until
    /// another begins.
    refused: Mutex<Option<(String, u64)>>,
    /// The generation of that run, 0 for none: its tickets fail with `watch`.
    unwatched: Arc<AtomicU64>,
}

impl Runs {
    pub fn new() -> Self {
        Self::default()
    }

    /// The run `run` begins: it is the shell's from now on, in place of any other.
    pub fn begin(&self, run: &str) {
        let generation = self.given.fetch_add(1, Ordering::SeqCst) + 1;
        let mut active = crate::rpc::lock(&self.active);
        *crate::rpc::lock(&self.refused) = None;
        *active = Some((run.to_owned(), generation));
        self.live.store(generation, Ordering::SeqCst);
    }

    /// Whether `run` is the shell's run.
    pub fn is_active(&self, run: &str) -> bool {
        matches!(crate::rpc::lock(&self.active).as_ref(), Some((id, _)) if id == run)
    }

    /// The run `run` ends: revoked at once. False, and nothing changes, when another run is the
    /// shell's (the late `end` of a run that a newer one replaced); true otherwise, a run the
    /// owner's own input revoked already included.
    pub fn end(&self, run: &str) -> bool {
        let mut active = crate::rpc::lock(&self.active);
        if matches!(active.as_ref(), Some((id, _)) if id != run) {
            return false;
        }
        self.live.store(0, Ordering::SeqCst);
        *active = None;
        true
    }

    /// Whatever run is under way is revoked at once: the owner's own input, the core gone.
    pub fn revoke(&self) {
        self.live.store(0, Ordering::SeqCst);
        *crate::rpc::lock(&self.active) = None;
    }

    /// The run `run` cannot be watched (the hooks on the owner's input did not go in): revoked
    /// at once, and every request of it fails with `watch`, so that the core ends it with the
    /// reason instead of operating a desktop the owner could not stop.
    /// A run that is not the shell's (replaced already) is left alone.
    pub fn refuse(&self, run: &str) {
        let mut active = crate::rpc::lock(&self.active);
        let Some((id, generation)) = active.take_if(|(id, _)| id.as_str() == run) else {
            return;
        };
        self.unwatched.store(generation, Ordering::SeqCst);
        self.live.store(0, Ordering::SeqCst);
        *crate::rpc::lock(&self.refused) = Some((id, generation));
    }

    /// The ticket of a request for the run `run`: live while that run is the shell's.
    pub fn ticket(&self, run: Option<&str>) -> Ticket {
        let active = crate::rpc::lock(&self.active);
        let refused = crate::rpc::lock(&self.refused);
        let generation = match (active.as_ref(), refused.as_ref(), run) {
            (Some((id, generation)), _, Some(run)) if id == run => *generation,
            (None, Some((id, generation)), Some(run)) if id == run => *generation,
            _ => 0,
        };
        Ticket {
            live: self.live.clone(),
            unwatched: self.unwatched.clone(),
            generation,
        }
    }
}

/// What a request may do on the desktop: something only while its run is the shell's.
#[derive(Clone, Debug)]
pub struct Ticket {
    live: Arc<AtomicU64>,
    /// The generation of the run refused because it could not be watched ([`Runs::refuse`]).
    unwatched: Arc<AtomicU64>,
    /// The generation of its run; 0 for none, never live.
    generation: u64,
}

impl Ticket {
    /// Whether the run of the request is still under way.
    pub fn is_live(&self) -> bool {
        self.generation != 0 && self.live.load(Ordering::SeqCst) == self.generation
    }

    /// `Stopped` once the run of the request is over (`Watch` when it was refused because the
    /// owner's input could not be watched): checked just before every change to the desktop.
    pub fn check(&self) -> Result<(), Failure> {
        if self.is_live() {
            Ok(())
        } else if self.generation != 0 && self.unwatched.load(Ordering::SeqCst) == self.generation {
            Err(Failure::Watch)
        } else {
            Err(Failure::Stopped)
        }
    }
}

/// A top-level window of an app, as the desktop describes it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct WindowInfo {
    /// The window's handle, which is its id for the core.
    pub id: i64,
    pub pid: u32,
    /// The executable's file name in lower case.
    pub exe: String,
    /// The name the app goes by (its file description, or the file name without `.exe`).
    pub name: String,
    pub title: String,
    pub class: String,
    pub foreground: bool,
    pub elevated: bool,
}

/// One step.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Action {
    Click,
    Type(String),
    Keys(Chord),
    Scroll(Direction),
    Focus,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Direction {
    Up,
    Down,
    Left,
    Right,
}

/// A step in a window, on one of the elements of its latest look (or, for typing and keys, on
/// whatever has the focus). `confirmed`: the owner said yes to this very step.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Step {
    pub window: i64,
    pub element: Option<u32>,
    pub action: Action,
    pub confirmed: bool,
}

/// The desktop: UI Automation in the app, a fake in the tests. Whatever changes the desktop is
/// done only while `ticket` is live, checked again just before each change.
pub trait Desktop: Send + Sync + 'static {
    /// The top-level windows that may be apps, in front first.
    fn windows(&self) -> Vec<WindowInfo>;
    /// One window, if it is still there.
    fn window(&self, id: i64) -> Option<WindowInfo>;
    /// A snapshot of the window (its entries, and whether some were left out); its elements are
    /// kept for the steps that follow, if the run is still under way when it is done.
    fn look(&self, id: i64, ticket: &Ticket) -> Result<(Vec<Entry>, bool), Failure>;
    /// One step; the way it was done (`invoke`, `toggle`, `value`, `keyboard`, `mouse`...).
    fn act(&self, step: &Step, ticket: &Ticket) -> Result<&'static str, Failure>;
    /// Brings the window to the front.
    fn focus(&self, id: i64, ticket: &Ticket) -> Result<(), Failure>;
    /// Drops every element kept: the run is over.
    fn forget(&self);
}

/// The requests of the core, checked and handed to the desktop.
pub struct Service<D: Desktop> {
    desktop: D,
    /// This process: its windows are never operated.
    own_pid: u32,
}

impl<D: Desktop> Service<D> {
    pub fn new(desktop: D, own_pid: u32) -> Self {
        Self { desktop, own_pid }
    }

    pub fn desktop(&self) -> &D {
        &self.desktop
    }

    /// Does `op` with `args` for the run of `ticket`; `allowed` is the always-allowed list
    /// (executables). `allow` is not one of these: it changes the device settings, which the
    /// caller holds. Nothing is done for a run that is over (`Stopped`).
    pub fn handle(
        &self,
        op: &str,
        args: &Value,
        allowed: &[String],
        ticket: &Ticket,
    ) -> Result<Value, Failure> {
        if !matches!(op, "apps" | "look" | "act" | "focus" | "check") {
            return Err(Failure::BadRequest);
        }
        ticket.check()?;
        match op {
            "check" => Ok(json!({})),
            "apps" => Ok(self.apps(allowed)),
            "look" => {
                let window = self.checked(window_arg(args)?)?;
                let (entries, truncated) = self.desktop.look(window.id, ticket)?;
                Ok(json!({
                    "window": window.id,
                    "app": {"exe": window.exe, "name": window.name},
                    "title": window.title,
                    "elements": entries,
                    "truncated": truncated,
                    "always_allowed": allowed.contains(&window.exe),
                }))
            }
            "act" => {
                let step = parse_step(args)?;
                self.checked(step.window)?;
                let method = self.desktop.act(&step, ticket)?;
                Ok(json!({ "method": method }))
            }
            _ => {
                let window = self.checked(window_arg(args)?)?;
                self.desktop.focus(window.id, ticket)?;
                Ok(json!({}))
            }
        }
    }

    fn apps(&self, allowed: &[String]) -> Value {
        let windows: Vec<Value> = self
            .desktop
            .windows()
            .into_iter()
            .filter(|w| {
                w.pid != self.own_pid && !w.exe.is_empty() && !policy::is_shell_class(&w.class)
            })
            .map(|w| {
                json!({
                    "id": w.id,
                    "exe": w.exe,
                    "name": w.name,
                    "title": w.title,
                    "foreground": w.foreground,
                    "always_allowed": allowed.contains(&w.exe),
                })
            })
            .collect();
        json!({ "windows": windows })
    }

    /// The window `id`, if a run may touch it.
    fn checked(&self, id: i64) -> Result<WindowInfo, Failure> {
        let window = self.desktop.window(id).ok_or(Failure::Gone)?;
        if window.pid == self.own_pid {
            return Err(Failure::OwnWindow);
        }
        if policy::is_refused(&window.exe) {
            return Err(Failure::RefusedApp);
        }
        if window.elevated {
            return Err(Failure::Elevated);
        }
        Ok(window)
    }
}

fn window_arg(args: &Value) -> Result<i64, Failure> {
    args.get("window")
        .and_then(Value::as_i64)
        .filter(|id| *id > 0)
        .ok_or(Failure::BadRequest)
}

/// Whether typing `text` breaks a line: a line feed or a carriage return, which typed keys press
/// as Enter.
pub fn breaks_lines(text: &str) -> bool {
    text.contains(['\n', '\r'])
}

/// The `act` request as a step: `{window, element, action, text, keys, direction, confirmed}`.
/// Typed text may hold no control character but a tab and line breaks: one typed as a character
/// may be taken for the key it is (a carriage return for Enter, an escape, a backspace).
pub fn parse_step(args: &Value) -> Result<Step, Failure> {
    let window = window_arg(args)?;
    let element = match args.get("element") {
        None | Some(Value::Null) => None,
        Some(value) => Some(
            value
                .as_u64()
                .and_then(|id| u32::try_from(id).ok())
                .filter(|id| *id > 0)
                .ok_or(Failure::BadRequest)?,
        ),
    };
    let text = |name: &str| args.get(name).and_then(Value::as_str);
    let action = match text("action") {
        Some("click") => Action::Click,
        Some("focus") => Action::Focus,
        Some("type") => {
            let typed = text("text")
                .filter(|t| !t.is_empty())
                .ok_or(Failure::BadRequest)?;
            if typed.chars().count() > MAX_TEXT_CHARS
                || typed
                    .chars()
                    .any(|c| c.is_control() && !matches!(c, '\t' | '\n' | '\r'))
            {
                return Err(Failure::BadRequest);
            }
            Action::Type(typed.to_owned())
        }
        Some("keys") => {
            let chord = text("keys").ok_or(Failure::BadRequest)?;
            Action::Keys(keys::parse_chord(chord).map_err(|_| Failure::KeysNotAllowed)?)
        }
        Some("scroll") => Action::Scroll(match text("direction").unwrap_or("down") {
            "up" => Direction::Up,
            "down" => Direction::Down,
            "left" => Direction::Left,
            "right" => Direction::Right,
            _ => return Err(Failure::BadRequest),
        }),
        _ => return Err(Failure::BadRequest),
    };
    if element.is_none() && matches!(action, Action::Click | Action::Scroll(_) | Action::Focus) {
        return Err(Failure::BadRequest);
    }
    Ok(Step {
        window,
        element,
        action,
        confirmed: args.get("confirmed") == Some(&Value::Bool(true)),
    })
}

/// An id of the core's (a request's, a run's): 32 hexadecimal characters.
fn core_id(value: Option<&Value>) -> Option<String> {
    let id = value?.as_str()?;
    (id.len() == 32 && id.bytes().all(|b| b.is_ascii_hexdigit())).then(|| id.to_owned())
}

/// A `computer.request` notification.
#[derive(Clone, Debug, PartialEq)]
pub struct Request {
    pub id: String,
    pub op: String,
    pub args: Value,
    /// The run it is for; None when it says none (nothing is done for it on the desktop).
    pub run: Option<String>,
}

/// A `computer.request` notification: `{id, op, args, run}`.
pub fn parse_request(params: &Value) -> Option<Request> {
    let id = core_id(params.get("id"))?;
    let op = params.get("op")?.as_str()?;
    let args = params.get("args").cloned().unwrap_or_else(|| json!({}));
    args.is_object().then(|| Request {
        id,
        op: op.to_owned(),
        args,
        run: core_id(params.get("run")),
    })
}

/// Where a run is (`computer.state`).
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Phase {
    Begin(String),
    Step(String),
    Pause,
    End(Option<String>),
}

/// The longest line the overlay is given, in characters.
const MAX_LINE_CHARS: usize = 120;

/// A `computer.state` notification: `{phase, line, run}`, as the phase and the run it is of.
pub fn parse_state(params: &Value) -> Option<(Phase, String)> {
    let line = params
        .get("line")
        .and_then(Value::as_str)
        .map(|line| policy::clean(line, MAX_LINE_CHARS))
        .filter(|line| !line.is_empty());
    let run = core_id(params.get("run"))?;
    let phase = match params.get("phase")?.as_str()? {
        "begin" => Phase::Begin(line?),
        "step" => Phase::Step(line?),
        "pause" => Phase::Pause,
        "end" => Phase::End(line),
        _ => return None,
    };
    Some((phase, run))
}

/// The reply to the request `id`, as the core's `computer.reply` takes it.
pub fn reply(id: &str, outcome: Result<Value, Failure>) -> Value {
    match outcome {
        Ok(result) => json!({ "id": id, "ok": true, "result": result }),
        Err(failure) => json!({ "id": id, "ok": false, "error": failure.kind() }),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const OWN: u32 = 100;
    const RUN: &str = "0123456789abcdef0123456789abcdef";
    const OTHER_RUN: &str = "fedcba9876543210fedcba9876543210";

    /// The ticket of a request of a run that is under way.
    fn live() -> Ticket {
        let runs = Runs::new();
        runs.begin(RUN);
        runs.ticket(Some(RUN))
    }

    fn window(id: i64, pid: u32, exe: &str) -> WindowInfo {
        WindowInfo {
            id,
            pid,
            exe: exe.to_owned(),
            name: policy::fallback_name(exe),
            title: format!("title of {id}"),
            class: "AppWindow".to_owned(),
            foreground: id == 1,
            elevated: false,
        }
    }

    #[derive(Default)]
    struct FakeDesktop {
        windows: Vec<WindowInfo>,
        steps: Mutex<Vec<Step>>,
        looks: Mutex<Vec<i64>>,
        focused: Mutex<Vec<i64>>,
    }

    impl Desktop for FakeDesktop {
        fn windows(&self) -> Vec<WindowInfo> {
            self.windows.clone()
        }
        fn window(&self, id: i64) -> Option<WindowInfo> {
            self.windows.iter().find(|w| w.id == id).cloned()
        }
        fn look(&self, id: i64, _ticket: &Ticket) -> Result<(Vec<Entry>, bool), Failure> {
            self.looks.lock().unwrap().push(id);
            let shot = tree::build(tree::tests::calculator(), tree::Caps::default(), || false);
            Ok((shot.entries, shot.truncated))
        }
        fn act(&self, step: &Step, _ticket: &Ticket) -> Result<&'static str, Failure> {
            self.steps.lock().unwrap().push(step.clone());
            Ok("invoke")
        }
        fn focus(&self, id: i64, _ticket: &Ticket) -> Result<(), Failure> {
            self.focused.lock().unwrap().push(id);
            Ok(())
        }
        fn forget(&self) {}
    }

    fn service() -> Service<FakeDesktop> {
        let mut shell = window(5, 7, "explorer.exe");
        shell.class = "Shell_TrayWnd".to_owned();
        let mut admin = window(6, 8, "mmcadmin.exe");
        admin.elevated = true;
        Service::new(
            FakeDesktop {
                windows: vec![
                    window(1, 10, "calculatorapp.exe"),
                    window(2, OWN, "raphael.exe"),
                    window(3, 11, "cmd.exe"),
                    shell,
                    admin,
                ],
                ..FakeDesktop::default()
            },
            OWN,
        )
    }

    #[test]
    fn the_listing_leaves_out_raphaels_own_windows_and_the_shells() {
        let service = service();
        let listed = service
            .handle(
                "apps",
                &json!({}),
                &["calculatorapp.exe".to_owned()],
                &live(),
            )
            .unwrap();
        let ids: Vec<i64> = listed["windows"]
            .as_array()
            .unwrap()
            .iter()
            .map(|w| w["id"].as_i64().unwrap())
            .collect();
        assert_eq!(ids, [1, 3, 6]);
        assert_eq!(listed["windows"][0]["always_allowed"], true);
        assert_eq!(listed["windows"][1]["always_allowed"], false);
        assert_eq!(listed["windows"][0]["title"], "title of 1");
    }

    #[test]
    fn a_look_is_sent_without_any_password_and_with_the_app() {
        let service = service();
        let look = service
            .handle("look", &json!({"window": 1}), &[], &live())
            .unwrap();
        assert_eq!(look["window"], 1);
        assert_eq!(
            look["app"],
            json!({"exe": "calculatorapp.exe", "name": "calculatorapp"})
        );
        assert_eq!(look["always_allowed"], false);
        assert!(!look.to_string().contains("hunter2"));
        assert!(look["elements"].as_array().unwrap().len() >= 4);
    }

    #[test]
    fn own_refused_elevated_and_gone_windows_are_never_touched() {
        let service = service();
        for (window, failure) in [
            (2, Failure::OwnWindow),
            (3, Failure::RefusedApp),
            (6, Failure::Elevated),
            (99, Failure::Gone),
        ] {
            for (op, args) in [
                ("look", json!({"window": window})),
                ("focus", json!({"window": window})),
                (
                    "act",
                    json!({"window": window, "element": 1, "action": "click"}),
                ),
            ] {
                assert_eq!(
                    service.handle(op, &args, &[], &live()),
                    Err(failure),
                    "{op} {window}"
                );
            }
        }
        assert!(service.desktop().steps.lock().unwrap().is_empty());
        assert!(service.desktop().looks.lock().unwrap().is_empty());
    }

    #[test]
    fn a_step_is_read_and_checked_before_it_reaches_the_desktop() {
        let service = service();
        let done = service
            .handle(
                "act",
                &json!({"window": 1, "element": 3, "action": "click"}),
                &[],
                &live(),
            )
            .unwrap();
        assert_eq!(done, json!({"method": "invoke"}));
        let keys = parse_step(&json!({"window": 1, "action": "keys", "keys": "Ctrl+S"})).unwrap();
        assert!(matches!(keys.action, Action::Keys(chord) if chord.ctrl));
        let typed =
            parse_step(&json!({"window": 1, "element": null, "action": "type", "text": "12*34"}))
                .unwrap();
        assert_eq!(typed.action, Action::Type("12*34".into()));
        let scroll = parse_step(&json!({"window": 1, "element": 2, "action": "scroll"})).unwrap();
        assert_eq!(scroll.action, Action::Scroll(Direction::Down));
    }

    #[test]
    fn a_step_of_the_wrong_shape_or_with_keys_outside_the_allowlist_is_refused() {
        for (args, failure) in [
            (
                json!({"window": 1, "action": "keys", "keys": "Win+L"}),
                Failure::KeysNotAllowed,
            ),
            (
                json!({"window": 1, "action": "keys", "keys": "Alt+Tab"}),
                Failure::KeysNotAllowed,
            ),
            (json!({"window": 1, "action": "click"}), Failure::BadRequest),
            (
                json!({"window": 1, "element": 0, "action": "click"}),
                Failure::BadRequest,
            ),
            (
                json!({"window": 1, "element": -2, "action": "click"}),
                Failure::BadRequest,
            ),
            (
                json!({"window": 1, "action": "type", "text": ""}),
                Failure::BadRequest,
            ),
            (
                json!({"window": 1, "action": "type", "text": "字".repeat(1001)}),
                Failure::BadRequest,
            ),
            (
                json!({"window": 1, "element": 2, "action": "scroll", "direction": "sideways"}),
                Failure::BadRequest,
            ),
            (
                json!({"window": 1, "element": 2, "action": "drag"}),
                Failure::BadRequest,
            ),
            (
                json!({"window": "1", "action": "focus"}),
                Failure::BadRequest,
            ),
            (
                json!({"action": "click", "element": 1}),
                Failure::BadRequest,
            ),
        ] {
            assert_eq!(parse_step(&args), Err(failure), "{args}");
        }
        assert_eq!(
            service().handle("teleport", &json!({}), &[], &live()),
            Err(Failure::BadRequest)
        );
    }

    #[test]
    fn requests_and_states_are_read_with_suspicion() {
        let id = "0123456789abcdef0123456789abcdef";
        assert_eq!(
            parse_request(&json!({"id": id, "op": "apps", "args": {}, "run": RUN})),
            Some(Request {
                id: id.to_owned(),
                op: "apps".to_owned(),
                args: json!({}),
                run: Some(RUN.to_owned()),
            })
        );
        // a request that says no run, or no run of the core's, is read, for no run
        for run in [json!(null), json!("short"), json!(7)] {
            let request = parse_request(&json!({"id": id, "op": "apps", "run": run})).unwrap();
            assert_eq!(request.run, None, "{run}");
        }
        for bad in [
            json!({"id": "short", "op": "apps"}),
            json!({"id": id, "op": 3}),
            json!({"id": id, "op": "apps", "args": [1]}),
            json!({"op": "apps"}),
        ] {
            assert_eq!(parse_request(&bad), None, "{bad}");
        }
        let state = |phase: Phase| Some((phase, RUN.to_owned()));
        assert_eq!(
            parse_state(&json!({"phase": "begin", "line": "《告。操作中：開始》", "run": RUN})),
            state(Phase::Begin("《告。操作中：開始》".into()))
        );
        assert_eq!(
            parse_state(&json!({"phase": "step", "line": "a\nb", "run": RUN})),
            state(Phase::Step("a b".into()))
        );
        assert_eq!(
            parse_state(&json!({"phase": "pause", "line": null, "run": RUN})),
            state(Phase::Pause)
        );
        assert_eq!(
            parse_state(&json!({"phase": "end", "line": null, "run": RUN})),
            state(Phase::End(None))
        );
        assert_eq!(parse_state(&json!({"phase": "step", "run": RUN})), None);
        assert_eq!(
            parse_state(&json!({"phase": "dance", "line": "x", "run": RUN})),
            None
        );
        // a state of no run is of nothing the shell can tell apart
        assert_eq!(parse_state(&json!({"phase": "end", "line": null})), None);
    }

    #[test]
    fn a_reply_says_the_result_or_the_kind() {
        assert_eq!(
            reply("a", Ok(json!({"method": "invoke"}))),
            json!({"id": "a", "ok": true, "result": {"method": "invoke"}})
        );
        assert_eq!(
            reply("a", Err(Failure::Password)),
            json!({"id": "a", "ok": false, "error": "password"})
        );
        for failure in [
            Failure::Gone,
            Failure::Stale,
            Failure::Password,
            Failure::Elevated,
            Failure::RefusedApp,
            Failure::OwnWindow,
            Failure::BadRequest,
            Failure::KeysNotAllowed,
            Failure::Stopped,
            Failure::Handoff,
            Failure::Focus,
            Failure::Unconfirmed,
            Failure::Watch,
            Failure::Failed,
        ] {
            assert!(
                failure
                    .kind()
                    .chars()
                    .all(|c| c.is_ascii_lowercase() || c == '_')
            );
        }
    }

    // --- a run is revocable ----------------------------------------------------------------------

    #[test]
    fn a_ticket_is_live_only_while_its_run_is_the_shells() {
        let runs = Runs::new();
        assert!(!runs.ticket(Some(RUN)).is_live(), "before any begin");
        runs.begin(RUN);
        let ticket = runs.ticket(Some(RUN));
        assert!(ticket.is_live() && ticket.check().is_ok());
        assert!(!runs.ticket(Some(OTHER_RUN)).is_live(), "another run's");
        assert!(!runs.ticket(None).is_live(), "of no run");
        assert!(runs.end(RUN));
        assert_eq!(ticket.check(), Err(Failure::Stopped));
        // the same id begun again is a run of its own: the old ticket stays dead
        runs.begin(RUN);
        assert!(!ticket.is_live());
        assert!(runs.ticket(Some(RUN)).is_live());
    }

    #[test]
    fn the_late_end_of_a_replaced_run_leaves_the_newer_one_alone() {
        let runs = Runs::new();
        runs.begin(RUN);
        let old = runs.ticket(Some(RUN));
        runs.begin(OTHER_RUN);
        let new = runs.ticket(Some(OTHER_RUN));
        assert!(!old.is_live(), "replaced");
        assert!(!runs.end(RUN));
        assert!(new.is_live() && runs.is_active(OTHER_RUN));
        assert!(runs.end(OTHER_RUN));
        assert!(!new.is_live() && !runs.is_active(OTHER_RUN));
    }

    #[test]
    fn the_owners_own_input_revokes_the_run_at_once() {
        let runs = Runs::new();
        runs.begin(RUN);
        let ticket = runs.ticket(Some(RUN));
        runs.revoke();
        assert!(!ticket.is_live());
        assert!(
            !runs.ticket(Some(RUN)).is_live(),
            "no new ticket for it either"
        );
        assert!(!runs.is_active(RUN));
        // the core's own end of it, which comes later, still takes the run down
        assert!(runs.end(RUN));
    }

    #[test]
    fn a_request_of_a_run_that_ended_never_reaches_the_desktop() {
        let service = service();
        let runs = Runs::new();
        runs.begin(RUN);
        // asked for while the run went on, then the run ended (Esc) before it was done
        let ticket = runs.ticket(Some(RUN));
        runs.end(RUN);
        for (op, args) in [
            ("apps", json!({})),
            ("look", json!({"window": 1})),
            ("focus", json!({"window": 1})),
            ("act", json!({"window": 1, "element": 3, "action": "click"})),
            (
                "act",
                json!({"window": 1, "action": "keys", "keys": "Enter", "confirmed": true}),
            ),
        ] {
            assert_eq!(
                service.handle(op, &args, &[], &ticket),
                Err(Failure::Stopped),
                "{op}"
            );
        }
        let desktop = service.desktop();
        assert!(desktop.steps.lock().unwrap().is_empty());
        assert!(desktop.looks.lock().unwrap().is_empty());
        assert!(desktop.focused.lock().unwrap().is_empty());
        assert_eq!(Failure::Stopped.kind(), "stopped");
    }

    #[test]
    fn a_check_says_whether_the_run_is_the_shells_and_does_nothing() {
        let service = service();
        let runs = Runs::new();
        runs.begin(RUN);
        assert_eq!(
            service.handle("check", &json!({}), &[], &runs.ticket(Some(RUN))),
            Ok(json!({}))
        );
        assert_eq!(
            service.handle("check", &json!({}), &[], &runs.ticket(Some(OTHER_RUN))),
            Err(Failure::Stopped)
        );
        runs.refuse(RUN);
        assert_eq!(
            service.handle("check", &json!({}), &[], &runs.ticket(Some(RUN))),
            Err(Failure::Watch)
        );
        let desktop = service.desktop();
        assert!(desktop.steps.lock().unwrap().is_empty());
        assert!(desktop.looks.lock().unwrap().is_empty());
        assert!(desktop.focused.lock().unwrap().is_empty());
    }

    #[test]
    fn a_run_that_cannot_be_watched_does_nothing_and_says_why() {
        let service = service();
        let runs = Runs::new();
        runs.begin(RUN);
        let early = runs.ticket(Some(RUN));
        runs.refuse(RUN);
        assert!(
            !runs.is_active(RUN),
            "no state of it shows 「操作中」 again"
        );
        for ticket in [early, runs.ticket(Some(RUN))] {
            assert!(!ticket.is_live());
            for (op, args) in [
                ("apps", json!({})),
                ("look", json!({"window": 1})),
                (
                    "act",
                    json!({"window": 1, "element": 3, "action": "click", "confirmed": true}),
                ),
            ] {
                assert_eq!(
                    service.handle(op, &args, &[], &ticket),
                    Err(Failure::Watch),
                    "{op}"
                );
            }
        }
        assert!(service.desktop().steps.lock().unwrap().is_empty());
        assert!(service.desktop().looks.lock().unwrap().is_empty());
        // another run's requests are not told of it, and the next run is watched afresh
        assert_eq!(runs.ticket(Some(OTHER_RUN)).check(), Err(Failure::Stopped));
        runs.begin(OTHER_RUN);
        assert!(runs.ticket(Some(OTHER_RUN)).is_live());
        assert_eq!(runs.ticket(Some(RUN)).check(), Err(Failure::Stopped));
        assert_eq!(Failure::Watch.kind(), "watch");
    }

    // --- typing: line breaks only with a yes, no other control characters -------------------------

    #[test]
    fn a_step_says_whether_the_owner_said_yes_to_it() {
        let step = |extra: Value| {
            let mut args = json!({"window": 1, "action": "type", "text": "a\nb"});
            args.as_object_mut()
                .unwrap()
                .extend(extra.as_object().unwrap().clone());
            parse_step(&args).unwrap().confirmed
        };
        assert!(step(json!({"confirmed": true})));
        assert!(!step(json!({})));
        assert!(!step(json!({"confirmed": "true"})));
        assert!(!step(json!({"confirmed": 1})));
        assert!(breaks_lines("a\nb") && breaks_lines("a\r") && !breaks_lines("a\tb"));
    }

    #[test]
    fn typed_text_with_a_control_character_that_could_be_a_key_is_refused() {
        for text in ["a\u{1b}b", "a\u{8}", "\u{7f}", "a\u{0}", "\u{85}"] {
            assert_eq!(
                parse_step(&json!({"window": 1, "action": "type", "text": text})),
                Err(Failure::BadRequest),
                "{text:?}"
            );
        }
        for text in ["a\tb", "a\nb", "a\r\nb", "七 seven"] {
            assert!(
                parse_step(&json!({"window": 1, "action": "type", "text": text})).is_ok(),
                "{text:?}"
            );
        }
    }
}
