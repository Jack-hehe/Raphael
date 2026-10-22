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
//! - `act {window, element, action, text, keys, direction}`: one step. UI Automation's patterns
//!   come first (Invoke, Toggle, SelectionItem, ExpandCollapse for a click; Value for typing;
//!   Scroll), a mouse click at the element's centre or typed keys only when there is none. Keys
//!   come from an allowlist ([`keys`]).
//! - `focus {window}`: the window is brought to the front.
//! - `allow {exe, name}`: the app joins the always-allowed list of the device settings.
//!
//! What may never be touched is refused here whatever the core asks ([`policy`], [`Service`]): a
//! window of Raphael's own (`own_window`), an app Raphael never operates (a terminal, a password
//! manager: `refused_app`), an elevated window (an administrator's: `elevated`, which the core
//! hands to the owner), and a password field, which is never typed into or read (`password`). The
//! secure desktop of UAC cannot be reached from here at all.
//!
//! `computer.state {phase, line}` says where a run is: `begin` and `step` show the overlay's
//! 「操作中」 line and arm the watch on the owner's own input ([`watch`]), `pause` disarms it while
//! a question is on screen, `end` takes everything down, the elements kept with it, and shows the
//! line it brings (《了。已停止。》 after Esc, which hid the overlay). When the owner touches the
//! mouse or the keyboard, the core is told (`computer.interrupt`) and stops the run.
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
            Failure::Failed => "failed",
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
/// whatever has the focus).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Step {
    pub window: i64,
    pub element: Option<u32>,
    pub action: Action,
}

/// The desktop: UI Automation in the app, a fake in the tests.
pub trait Desktop: Send + Sync + 'static {
    /// The top-level windows that may be apps, in front first.
    fn windows(&self) -> Vec<WindowInfo>;
    /// One window, if it is still there.
    fn window(&self, id: i64) -> Option<WindowInfo>;
    /// A snapshot of the window (its entries, and whether some were left out); its elements are
    /// kept for the steps that follow.
    fn look(&self, id: i64) -> Result<(Vec<Entry>, bool), Failure>;
    /// One step; the way it was done (`invoke`, `toggle`, `value`, `keyboard`, `mouse`...).
    fn act(&self, step: &Step) -> Result<&'static str, Failure>;
    /// Brings the window to the front.
    fn focus(&self, id: i64) -> Result<(), Failure>;
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

    /// Does `op` with `args`; `allowed` is the always-allowed list (executables). `allow` is not
    /// one of these: it changes the device settings, which the caller holds.
    pub fn handle(&self, op: &str, args: &Value, allowed: &[String]) -> Result<Value, Failure> {
        match op {
            "apps" => Ok(self.apps(allowed)),
            "look" => {
                let window = self.checked(window_arg(args)?)?;
                let (entries, truncated) = self.desktop.look(window.id)?;
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
                let method = self.desktop.act(&step)?;
                Ok(json!({ "method": method }))
            }
            "focus" => {
                let window = self.checked(window_arg(args)?)?;
                self.desktop.focus(window.id)?;
                Ok(json!({}))
            }
            _ => Err(Failure::BadRequest),
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

/// The `act` request as a step: `{window, element, action, text, keys, direction}`.
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
            if typed.chars().count() > MAX_TEXT_CHARS {
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
    })
}

/// A `computer.request` notification: its id (32 hexadecimal characters), its operation and its
/// arguments.
pub fn parse_request(params: &Value) -> Option<(String, String, Value)> {
    let id = params.get("id")?.as_str()?;
    if id.len() != 32 || !id.bytes().all(|b| b.is_ascii_hexdigit()) {
        return None;
    }
    let op = params.get("op")?.as_str()?;
    let args = params.get("args").cloned().unwrap_or_else(|| json!({}));
    args.is_object()
        .then(|| (id.to_owned(), op.to_owned(), args))
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

/// A `computer.state` notification: `{phase, line}`.
pub fn parse_state(params: &Value) -> Option<Phase> {
    let line = params
        .get("line")
        .and_then(Value::as_str)
        .map(|line| policy::clean(line, MAX_LINE_CHARS))
        .filter(|line| !line.is_empty());
    match params.get("phase")?.as_str()? {
        "begin" => Some(Phase::Begin(line?)),
        "step" => Some(Phase::Step(line?)),
        "pause" => Some(Phase::Pause),
        "end" => Some(Phase::End(line)),
        _ => None,
    }
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
    use std::sync::Mutex;

    const OWN: u32 = 100;

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
    }

    impl Desktop for FakeDesktop {
        fn windows(&self) -> Vec<WindowInfo> {
            self.windows.clone()
        }
        fn window(&self, id: i64) -> Option<WindowInfo> {
            self.windows.iter().find(|w| w.id == id).cloned()
        }
        fn look(&self, id: i64) -> Result<(Vec<Entry>, bool), Failure> {
            self.looks.lock().unwrap().push(id);
            let shot = tree::build(tree::tests::calculator(), tree::Caps::default(), || false);
            Ok((shot.entries, shot.truncated))
        }
        fn act(&self, step: &Step) -> Result<&'static str, Failure> {
            self.steps.lock().unwrap().push(step.clone());
            Ok("invoke")
        }
        fn focus(&self, _id: i64) -> Result<(), Failure> {
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
            .handle("apps", &json!({}), &["calculatorapp.exe".to_owned()])
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
        let look = service.handle("look", &json!({"window": 1}), &[]).unwrap();
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
                    service.handle(op, &args, &[]),
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
            service().handle("teleport", &json!({}), &[]),
            Err(Failure::BadRequest)
        );
    }

    #[test]
    fn requests_and_states_are_read_with_suspicion() {
        let id = "0123456789abcdef0123456789abcdef";
        assert_eq!(
            parse_request(&json!({"id": id, "op": "apps", "args": {}})),
            Some((id.to_owned(), "apps".to_owned(), json!({})))
        );
        for bad in [
            json!({"id": "short", "op": "apps"}),
            json!({"id": id, "op": 3}),
            json!({"id": id, "op": "apps", "args": [1]}),
            json!({"op": "apps"}),
        ] {
            assert_eq!(parse_request(&bad), None, "{bad}");
        }
        assert_eq!(
            parse_state(&json!({"phase": "begin", "line": "《告。操作中：開始》"})),
            Some(Phase::Begin("《告。操作中：開始》".into()))
        );
        assert_eq!(
            parse_state(&json!({"phase": "step", "line": "a\nb"})),
            Some(Phase::Step("a b".into()))
        );
        assert_eq!(
            parse_state(&json!({"phase": "pause", "line": null})),
            Some(Phase::Pause)
        );
        assert_eq!(
            parse_state(&json!({"phase": "end", "line": null})),
            Some(Phase::End(None))
        );
        assert_eq!(parse_state(&json!({"phase": "step"})), None);
        assert_eq!(parse_state(&json!({"phase": "dance", "line": "x"})), None);
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
}
