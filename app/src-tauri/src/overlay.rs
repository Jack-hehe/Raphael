//! The overlay window: Raphael's emblem, reports, answers and questions in the bottom-right
//! corner.
//!
//! The window is created once, at start-up. It is transparent, never takes focus, lets the mouse
//! click through, stays on top and stays out of the taskbar and Alt+Tab. It is **never shown or
//! hidden again**: when tao shows a window it calls `ShowWindow(SW_SHOW)`, which focuses it, and
//! the next paste would go to the wrong place. "Hidden" means that the page (`overlay.html`)
//! draws nothing and stops its animation loop; the empty window stays where it is.
//!
//! The one time the window takes the mouse is while Raphael asks a YES/NO question
//! ([`OverlayApi::ask`]): its buttons must be clickable. Clicking is not activating: the window has
//! `WS_EX_NOACTIVATE` (tao gives it to a window that is not `focusable`, and
//! [`apply_passive_style`] makes sure of it), so a click on a button reaches the page without the
//! window becoming the foreground window, and the app the person was typing into keeps its focus.
//! [`takes_clicks`] says which events open and close that time.
//!
//! The page learns what to do from [`OverlayEvent`]s on a channel that it opens once with the
//! `overlay_subscribe` command, and answers a question with `overlay_confirm`. The rest of the app
//! drives the overlay through [`OverlayApi`].
//!
//! The page can draw the emblem in a window that nobody sees: something else may have hidden,
//! minimized or covered the window, or the shell may keep it on another virtual desktop. So each
//! time the emblem appears, and again as it thinks and counts, [`sight`] looks at the window from
//! the main thread, writes what it saw to the log, and puts right what it can, without ever
//! activating the window. In between, [`screen`] writes down what happens to the screen (the
//! display turning off and on, the session locking, sleep, the webview's GPU process exiting), so
//! that an emblem that nobody saw in a window that was in sight can be put next to it.

mod screen;
mod sight;

use std::{
    collections::VecDeque,
    sync::{
        Arc, Mutex, MutexGuard, PoisonError,
        atomic::{AtomicU32, AtomicU64, Ordering},
    },
    time::Instant,
};

use serde::Serialize;
use tauri::{
    AppHandle, Manager, PhysicalPosition, PhysicalRect, PhysicalSize, Webview, WebviewUrl,
    WebviewWindow, WebviewWindowBuilder, ipc::Channel,
};
use tokio::sync::mpsc::UnboundedSender;

/// What the emblem shows while it is on screen, as the shell sets it. The page has one more of its
/// own, "steady", which it picks by itself for a notice.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Mode {
    /// The white ring follows the user's microphone.
    Listening,
    /// Processing continues while its numeric percentage is shown.
    Thinking,
}

/// How far a dictation has come after the key was released: the speech that is left is
/// transcribed, then the brain cleans the text, then it is pasted (the core's
/// `dictation.progress`; `Done` is the stop's reply).
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Stage {
    Transcribing,
    Cleaning,
    Done,
}

/// What the percentage beside the emblem is made from: `done` of `total` segments transcribed
/// (while transcribing), and the length of the speech, which sets how long the cleanup is expected
/// to take.
#[derive(Clone, Copy, Debug, PartialEq, Serialize)]
pub struct Progress {
    pub stage: Stage,
    pub done: u32,
    pub total: u32,
    pub audio_seconds: f32,
}

impl Progress {
    /// The text is ready; delivery must still succeed before the UI reports 100%.
    pub const DONE: Progress = Progress {
        stage: Stage::Done,
        done: 0,
        total: 0,
        audio_seconds: 0.0,
    };
}

/// How much a question matters (`tier` of the core's confirmations): `T2` changes something that
/// can be undone, `T3` something that cannot. The page makes the YES of a `T3` question wait for a
/// long press.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
pub enum Tier {
    T2,
    T3,
}

impl Tier {
    /// The tier the core named. Anything else is taken for the more careful one.
    pub fn parse(tier: &str) -> Self {
        match tier {
            "T2" => Tier::T2,
            _ => Tier::T3,
        }
    }
}

/// A question for the person to answer YES or NO, with the words on its buttons, from the core's
/// confirmation. `id` comes back with the answer (`overlay_confirm`); `timeout_ms` is how long the
/// question stays open before it counts as a NO. `always`, sent only when there is one, is the
/// label of a third button (「一律允許」, the question about an app Raphael is to operate): a YES
/// that is also kept for next time.
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct AskPrompt {
    pub id: String,
    /// The recipient of the request, supplied by the controller rather than the model.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub member_id: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub conversation_id: Option<u64>,
    pub tier: Tier,
    pub prefix: String,
    pub text: String,
    pub yes: String,
    pub no: String,
    pub timeout_ms: u64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub always: Option<String>,
}

/// What the overlay page is told. Every text in it is in the interface language and in that one
/// alone, never a pair.
#[derive(Clone, Debug, Serialize)]
#[serde(tag = "type", rename_all = "lowercase")]
pub enum OverlayEvent {
    /// The emblem appears (the appear animation) and shows `mode`. A `line` (sent only when there
    /// is one) is Raphael's greeting to a question, written beside the emblem while he listens; a
    /// dictation shows the emblem alone.
    Show {
        mode: Mode,
        #[serde(skip_serializing_if = "Option::is_none")]
        line: Option<String>,
    },
    /// The emblem shows another mode. A `quiet` one (sent only when it is) shows no progress
    /// caption beside it: Raphael is thinking about a question, not working through a dictation.
    /// It may bring `lines` (sent only when there are some): Raphael's short reports on the
    /// analysis (《告。解析中……》), in the interface language, that the page changes in the
    /// caption's place until the thinking is over.
    Mode {
        mode: Mode,
        #[serde(skip_serializing_if = "std::ops::Not::not")]
        quiet: bool,
        #[serde(skip_serializing_if = "Vec::is_empty")]
        lines: Vec<String>,
    },
    /// Loudness from 0 to 1: the white ring's width while listening or speaking.
    Level { value: f32 },
    /// How far the dictation has come since the key was released, shown beside the glyph ring.
    Progress(Progress),
    /// Raphael's report: the emblem appears with `text` as its subtitle, then vanishes.
    Notice { text: String },
    /// Learned words are reported like a notice, but they never take the emblem from a dictation
    /// that is showing: the page tells them when it is over.
    Learned { text: String },
    /// Raphael answers: the emblem and one subtitle line, 《`prefix`。`text`》.
    Say {
        prefix: String,
        text: String,
        /// A voiced answer stays until the controller publishes Release or Hide.
        #[serde(skip_serializing_if = "std::ops::Not::not")]
        hold: bool,
    },
    /// Raphael asks a question: the subtitle line 《`prefix`。`text`》 and two buttons under it.
    /// The window takes the mouse until the question is over.
    Ask(AskPrompt),
    /// Delivery succeeded: show 100% briefly before the normal vanish animation.
    Complete,
    /// The voice ended: release the held answer and continue with any queued reports.
    Release,
    /// The vanish animation.
    Hide,
    /// Raphael operates the desktop: the emblem shows its 「操作中」 look, with `line` (the step,
    /// 《告。操作中：點擊「七」》) beside it until something else takes the stage. A question that
    /// was on screen goes; the corner is the apps' again (the window lets the mouse through).
    Operate { line: String },
    /// The emblem's diameter in logical pixels, whether the subtitles show (`"on"` or `"off"`)
    /// and the interface language (`"zh-TW"`, `"en"` or `"ja"`).
    Settings {
        px: u32,
        subtitles: String,
        language: String,
    },
    /// Raphael's picture changed (avatar.rs): the page asks for it again (`avatar_get`). A page
    /// that loads asks for it anyway, so the event is not kept for one that has not.
    Avatar,
}

/// How the rest of the app drives the overlay. Calls do not wait for the page and none of them
/// can fail: a problem with the window is logged, because the overlay must never get in the way
/// of dictation.
pub trait OverlayApi: Send + Sync + 'static {
    /// The emblem appears and shows `mode`. The window is put back in its corner first (the
    /// taskbar or the screen's scale may have changed).
    fn show(&self, mode: Mode);
    /// Raphael listens for a question: like `show(Mode::Listening)`, with `line` (his greeting, in
    /// the interface language) beside the emblem until he has heard it. It also greets on the
    /// emblem of a dictation that the question took over. No default: a wrapper that forgot it
    /// would show a question as a dictation.
    fn greet(&self, line: &str);
    /// Changes what the visible emblem shows.
    fn set_mode(&self, mode: Mode);
    /// The emblem thinks about a question: [`Mode::Thinking`] without the progress caption of a
    /// dictation. In its place the page changes the `lines` (his reports on the analysis, in the
    /// interface language) every second or so, until the answer or something else replaces them.
    /// No default, for the same reason as [`Self::greet`].
    fn think_quietly(&self, lines: &[&str]);
    /// Loudness from 0 to 1.
    fn level(&self, value: f32);
    /// Raphael reports `text` (in the interface language) as a subtitle for a time that depends on
    /// its length, then the emblem vanishes. Reports that arrive meanwhile wait their turn.
    fn notice(&self, text: &str);
    /// Report learned words in the usual corner. Fakes can keep the ordinary notice behavior.
    fn learned_notice(&self, text: &str) {
        self.notice(text);
    }
    /// Raphael answers: the emblem appears with 《`prefix`。`text`》 as one subtitle line, then
    /// vanishes after a time that depends on the length of `text`.
    fn say(&self, prefix: &str, text: &str);
    /// A voiced answer whose lifetime is owned by the dictation controller.
    fn say_held(&self, prefix: &str, text: &str) {
        self.say(prefix, text);
    }
    /// Release a voiced answer without dropping the reports queued behind it.
    fn release_say(&self) {
        self.hide();
    }
    /// Raphael asks a YES/NO question: the subtitle line and two buttons. From now on the window
    /// takes the mouse, without ever being activated, until another call puts something else on
    /// the stage (the reply to the answer, a notice, a hide). It keeps the mouse while the answer
    /// is being dealt with, so that a second click on a button that is still drawn does not land
    /// in the app below.
    fn ask(&self, prompt: &AskPrompt);
    /// The vanish animation plays; the window stays, empty, where it is.
    fn hide(&self);
    /// Finish successfully; simple implementations may retain their ordinary hide behavior.
    fn complete(&self) {
        self.hide();
    }
    /// How far the dictation has come since the key was released.
    fn progress(&self, progress: Progress);
    /// Raphael operates the desktop: the emblem's 「操作中」 look with `line`, the step, beside it
    /// (see [`OverlayEvent::Operate`]). A fake may show it as a notice.
    fn operate(&self, line: &str) {
        self.notice(line);
    }
}

// ---------- placement ----------

/// A rectangle in physical pixels: the work area, or the window.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Rect {
    pub x: i32,
    pub y: i32,
    pub w: i32,
    pub h: i32,
}

impl From<&PhysicalRect<i32, u32>> for Rect {
    fn from(rect: &PhysicalRect<i32, u32>) -> Self {
        Rect {
            x: rect.position.x,
            y: rect.position.y,
            w: rect.size.width as i32,
            h: rect.size.height as i32,
        }
    }
}

/// Width of the window in logical pixels: the subtitles use the room left of the emblem.
const WINDOW_WIDTH: f64 = 540.0;
/// Room above the canvas for the subtitles' upper lines, in logical pixels.
const SUBTITLE_ROOM: f64 = 40.0;
/// The emblem's circle is this far from the right edge of the work area, in logical pixels.
const RIGHT_GAP: f64 = 18.0;
/// The emblem's circle is this far above the taskbar, in logical pixels.
const BOTTOM_GAP: f64 = 12.0;
/// The square canvas is this much larger than the emblem so that the rays fit (the same ratio
/// as `CANVAS_RATIO` in emblem.ts).
const CANVAS_RATIO: f64 = 1.3;

/// Where the window goes: `work` is the work area (without the taskbar) of the primary monitor,
/// `emblem_px` the emblem's diameter in logical pixels and `scale` the monitor's scale factor.
///
/// The window is 540 wide and as tall as the canvas plus 40 (logical pixels, times `scale`). The
/// canvas sits in the window's bottom-right corner, so the window overhangs the emblem's circle
/// by the canvas margin on the right and at the bottom. That puts the circle 18 px from the right
/// edge and 12 px above the taskbar; the transparent margin may overlap the taskbar by a few
/// pixels, which is harmless because the window is click-through.
pub fn place(work: Rect, emblem_px: u32, scale: f64) -> Rect {
    let e = f64::from(emblem_px);
    let canvas = (CANVAS_RATIO * e).round();
    let margin = (canvas - e) / 2.0;
    let w = (WINDOW_WIDTH * scale).round() as i32;
    let h = ((canvas + SUBTITLE_ROOM) * scale).round() as i32;
    let right =
        (f64::from(work.x) + f64::from(work.w) + (margin - RIGHT_GAP) * scale).round() as i32;
    let bottom =
        (f64::from(work.y) + f64::from(work.h) + (margin - BOTTOM_GAP) * scale).round() as i32;
    Rect {
        x: right - w,
        y: bottom - h,
        w,
        h,
    }
}

/// Where the window was put, for the check that follows: the work area, the window's rectangle
/// in it ([`place`]) and the side of the emblem's square canvas, all in physical pixels.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct Placement {
    pub work: Rect,
    pub window: Rect,
    pub canvas: i32,
}

/// [`place`], with what the check needs to find the emblem in the window.
pub(crate) fn placement(work: Rect, emblem_px: u32, scale: f64) -> Placement {
    let canvas = (CANVAS_RATIO * f64::from(emblem_px)).round();
    Placement {
        work,
        window: place(work, emblem_px, scale),
        canvas: (canvas * scale).round() as i32,
    }
}

// ---------- the mouse ----------

/// What an event does to whether the window takes the mouse: `Some(true)` for the question that
/// needs its buttons clicked, `Some(false)` for everything that puts something else on the stage
/// (the reply to the answer among them, so the corner is the apps' again as soon as the question
/// is gone from the screen), `None` for what changes nothing on the stage.
pub(crate) fn takes_clicks(event: &OverlayEvent) -> Option<bool> {
    match event {
        OverlayEvent::Ask(_) => Some(true),
        OverlayEvent::Show { .. }
        | OverlayEvent::Say { .. }
        | OverlayEvent::Notice { .. }
        | OverlayEvent::Operate { .. }
        | OverlayEvent::Complete
        | OverlayEvent::Release
        | OverlayEvent::Hide => Some(false),
        // The loudness and progress are about the emblem, learned words wait behind whatever is
        // on the stage, and the settings are no scene.
        OverlayEvent::Mode { .. }
        | OverlayEvent::Level { .. }
        | OverlayEvent::Progress(_)
        | OverlayEvent::Learned { .. }
        | OverlayEvent::Settings { .. }
        | OverlayEvent::Avatar => None,
    }
}

// ---------- the pages that listen ----------

/// How many events wait for a page that has not subscribed yet.
const BACKLOG_MAX: usize = 16;

/// The pages that listen to the overlay, and what a page that comes late still needs to know.
struct Bus {
    /// One channel per webview: a page that is reloaded subscribes again and replaces its old
    /// channel (the old one would silently swallow events).
    subscribers: Vec<(String, Channel<OverlayEvent>)>,
    /// The latest `Settings`. Every new page hears it first, because `create` sends it before
    /// the page has loaded.
    settings: OverlayEvent,
    /// Events from before the first page subscribed. The webview takes a moment to load, and a
    /// report at start-up (say, "the core did not start") must not be lost.
    backlog: VecDeque<OverlayEvent>,
    /// Whether "no page is listening" has been logged since the last page subscribed. An event
    /// that waits is normal at start-up, but one during a dictation means the emblem cannot show.
    backlog_warned: bool,
}

impl OverlayEvent {
    /// The event's `type`, for the log.
    fn kind(&self) -> &'static str {
        match self {
            OverlayEvent::Show { .. } => "show",
            OverlayEvent::Mode { .. } => "mode",
            OverlayEvent::Level { .. } => "level",
            OverlayEvent::Progress(_) => "progress",
            OverlayEvent::Notice { .. } => "notice",
            OverlayEvent::Learned { .. } => "learned",
            OverlayEvent::Say { .. } => "say",
            OverlayEvent::Ask(_) => "ask",
            OverlayEvent::Complete => "complete",
            OverlayEvent::Release => "release",
            OverlayEvent::Hide => "hide",
            OverlayEvent::Operate { .. } => "operate",
            OverlayEvent::Settings { .. } => "settings",
            OverlayEvent::Avatar => "avatar",
        }
    }
}

impl Bus {
    fn new(emblem_px: u32, subtitles: &str, language: &str) -> Self {
        Bus {
            subscribers: Vec::new(),
            settings: OverlayEvent::Settings {
                px: emblem_px,
                subtitles: subtitles.to_owned(),
                language: language.to_owned(),
            },
            backlog: VecDeque::new(),
            backlog_warned: false,
        }
    }

    fn publish(&mut self, event: OverlayEvent) {
        if matches!(event, OverlayEvent::Settings { .. }) {
            self.settings = event.clone();
        }
        let kind = event.kind();
        if self.subscribers.is_empty() {
            match event {
                // Replayed to a new page as `self.settings`, and a loudness reading or a progress
                // report is stale by the time a page could use it.
                OverlayEvent::Settings { .. }
                | OverlayEvent::Avatar
                | OverlayEvent::Level { .. }
                | OverlayEvent::Progress(_) => {}
                _ => {
                    if self.backlog_warned {
                        log::debug!("overlay: no page is listening, \"{kind}\" waits");
                    } else {
                        self.backlog_warned = true;
                        log::warn!(
                            "overlay: no page is listening, the \"{kind}\" event waits for one \
                             (the overlay page has not loaded yet or could not subscribe)"
                        );
                    }
                    if self.backlog.len() >= BACKLOG_MAX {
                        self.backlog.pop_front();
                    }
                    self.backlog.push_back(event);
                }
            }
            return;
        }
        // A loudness reading comes many times a second.
        if matches!(event, OverlayEvent::Level { .. }) {
            log::trace!("overlay: \"{kind}\" to {} page(s)", self.subscribers.len());
        } else {
            log::debug!("overlay: \"{kind}\" to {} page(s)", self.subscribers.len());
        }
        self.subscribers
            .retain(|(label, channel)| match channel.send(event.clone()) {
                Ok(()) => true,
                Err(err) => {
                    log::warn!("the overlay page \"{label}\" does not take events any more: {err}");
                    false
                }
            });
    }

    fn subscribe(&mut self, label: &str, channel: Channel<OverlayEvent>) {
        self.subscribers.retain(|(known, _)| known != label);
        let mut alive = channel.send(self.settings.clone()).is_ok();
        while alive && let Some(event) = self.backlog.pop_front() {
            alive = channel.send(event).is_ok();
        }
        if alive {
            log::info!("overlay: the page \"{label}\" subscribed");
            self.subscribers.push((label.to_owned(), channel));
            self.backlog_warned = false;
        } else {
            log::warn!("the overlay page \"{label}\" went away while it subscribed");
        }
    }
}

// ---------- the overlay ----------

/// The overlay window and the pages that listen to it. Make it once with [`Overlay::create`],
/// keep it in the app state as `Arc<Overlay>`, and drive it through [`OverlayApi`].
pub struct Overlay {
    app: AppHandle,
    window: WebviewWindow,
    /// The window's handle, which never changes, for the checks that run on the main thread;
    /// `None` where there is none.
    hwnd: Option<isize>,
    /// The emblem's diameter in logical pixels, which sets the size of the window.
    emblem_px: AtomicU32,
    bus: Mutex<Bus>,
    /// Whether the window takes the mouse now (see [`takes_clicks`]); the window starts out
    /// letting it through. Held while the window is changed, so that two changes cannot cross.
    clicks: Mutex<bool>,
    /// When the window was created, and when it was last checked (ms after that): a long
    /// recording has no other event, and another window may come over the emblem meanwhile.
    born: Instant,
    last_sight_ms: AtomicU64,
}

/// How often the window is checked while the emblem is on screen with nothing else happening.
const SIGHT_EVERY_MS: u64 = 2_000;

fn lock<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(PoisonError::into_inner)
}

impl Overlay {
    /// Creates the overlay window, with an emblem `emblem_px` logical pixels wide, the subtitles
    /// `"on"` or `"off"` and the interface language (`"zh-TW"`, `"en"` or `"ja"`). Call it once,
    /// from `setup`.
    pub fn create(
        app: &AppHandle,
        emblem_px: u32,
        subtitles: &str,
        language: &str,
    ) -> tauri::Result<Arc<Self>> {
        // Visible from the start: see the module documentation for why it is never shown later.
        let window = WebviewWindowBuilder::new(
            app,
            crate::OVERLAY_WINDOW,
            WebviewUrl::App("overlay.html".into()),
        )
        .title("Raphael overlay")
        .transparent(true)
        .decorations(false)
        .shadow(false)
        .always_on_top(true)
        .skip_taskbar(true)
        .focusable(false)
        .focused(false)
        .resizable(false)
        .no_redirection_bitmap(true)
        .data_directory(crate::webview_data_dir(app)?)
        .build()?;

        // Let the mouse through (`WS_EX_TRANSPARENT | WS_EX_LAYERED`). A window that does not
        // do that would block clicks on whatever is under the corner, so do not keep it.
        if let Err(err) = window.set_ignore_cursor_events(true) {
            if let Err(err) = window.destroy() {
                log::warn!("could not close the overlay window either: {err}");
            }
            return Err(err);
        }
        apply_passive_style(&window);
        let hwnd = native_handle(&window);
        if let Some(hwnd) = hwnd {
            screen::watch(&window, hwnd);
        }

        let overlay = Arc::new(Overlay {
            app: app.clone(),
            window,
            hwnd,
            emblem_px: AtomicU32::new(emblem_px),
            bus: Mutex::new(Bus::new(emblem_px, subtitles, language)),
            clicks: Mutex::new(false),
            born: Instant::now(),
            last_sight_ms: AtomicU64::new(0),
        });
        overlay.place_window();
        Ok(overlay)
    }

    /// Raphael's picture changed: the page asks for the new one.
    pub fn avatar_changed(&self) {
        self.publish(OverlayEvent::Avatar);
    }

    /// A new emblem size changes the window's size, so it is put in its corner again.
    pub fn apply_settings(&self, emblem_px: u32, subtitles: &str, language: &str) {
        self.emblem_px.store(emblem_px, Ordering::Relaxed);
        self.place_window();
        self.publish(OverlayEvent::Settings {
            px: emblem_px,
            subtitles: subtitles.to_owned(),
            language: language.to_owned(),
        });
    }

    fn publish(&self, event: OverlayEvent) {
        // The window takes the mouse before the buttons appear, and lets it go as the page moves
        // on to something else.
        if let Some(on) = takes_clicks(&event) {
            self.set_clicks(on);
        }
        lock(&self.bus).publish(event);
    }

    /// Makes the window take the mouse (`true`) or let it through (`false`). It never takes focus
    /// either way: `WS_EX_NOACTIVATE` is part of the style that [`apply_passive_style`] puts
    /// back after tao rebuilt it for this change.
    fn set_clicks(&self, on: bool) {
        let mut current = lock(&self.clicks);
        if *current == on {
            return;
        }
        match self.window.set_ignore_cursor_events(!on) {
            Ok(()) => {
                *current = on;
                apply_passive_style(&self.window);
                log::debug!(
                    "overlay: the window {} the mouse",
                    if on { "takes" } else { "lets through" }
                );
            }
            Err(err) => log::warn!("could not change whether the overlay takes the mouse: {err}"),
        }
    }

    fn subscribe(&self, label: &str, channel: Channel<OverlayEvent>) {
        lock(&self.bus).subscribe(label, channel);
    }

    /// Puts the window in its corner of the primary monitor's work area, where the taskbar or the
    /// screen's scale may have moved it since the last time, and says where that is (`None` if it
    /// could not).
    fn place_window(&self) -> Option<Placement> {
        match self.try_place_window() {
            Ok(placement) => Some(placement),
            Err(err) => {
                log::warn!("could not place the overlay window: {err}");
                None
            }
        }
    }

    fn try_place_window(&self) -> tauri::Result<Placement> {
        let monitor = self.app.primary_monitor()?.ok_or_else(|| {
            tauri::Error::Io(std::io::Error::new(
                std::io::ErrorKind::NotFound,
                "there is no primary monitor",
            ))
        })?;
        let placement = placement(
            Rect::from(monitor.work_area()),
            self.emblem_px.load(Ordering::Relaxed),
            monitor.scale_factor(),
        );
        let target = placement.window;
        // Compare with where the window really is: Windows itself moves windows when the
        // screens change.
        let position = self.window.outer_position()?;
        let size = self.window.outer_size()?;
        if (position.x, position.y, size.width, size.height)
            == (target.x, target.y, target.w as u32, target.h as u32)
        {
            return Ok(placement);
        }
        self.window
            .set_size(PhysicalSize::new(target.w as u32, target.h as u32))?;
        self.window
            .set_position(PhysicalPosition::new(target.x, target.y))?;
        // tao rebuilds the extended window style whenever it changes flags or the size.
        apply_passive_style(&self.window);
        Ok(placement)
    }

    /// Checks on the main thread that the person can see the window that the emblem is about to
    /// appear in (see [`sight`]). It is queued behind the placing that came before it, so it sees
    /// the window where it was just put; the caller does not wait for it.
    fn keep_in_sight(&self, placement: Option<Placement>) {
        self.last_sight_ms.store(self.elapsed_ms(), Ordering::Relaxed);
        if let Some(hwnd) = self.hwnd {
            sight::check(&self.window, hwnd, placement);
        }
    }

    /// [`Self::keep_in_sight`], at most every [`SIGHT_EVERY_MS`]: for the events that keep coming
    /// while the emblem is on screen (the microphone's level, an answer being said), so that a
    /// window that came over it during a long recording is put back under it.
    fn keep_in_sight_now_and_then(&self) {
        let now = self.elapsed_ms();
        let last = self.last_sight_ms.load(Ordering::Relaxed);
        if now.saturating_sub(last) >= SIGHT_EVERY_MS
            && self
                .last_sight_ms
                .compare_exchange(last, now, Ordering::Relaxed, Ordering::Relaxed)
                .is_ok()
        {
            self.keep_in_sight(None);
        }
    }

    fn elapsed_ms(&self) -> u64 {
        u64::try_from(self.born.elapsed().as_millis()).unwrap_or(u64::MAX)
    }
}

impl OverlayApi for Overlay {
    fn show(&self, mode: Mode) {
        let placement = self.place_window();
        self.keep_in_sight(placement);
        self.publish(OverlayEvent::Show { mode, line: None });
    }

    fn greet(&self, line: &str) {
        let placement = self.place_window();
        self.keep_in_sight(placement);
        self.publish(OverlayEvent::Show {
            mode: Mode::Listening,
            line: Some(line.to_owned()),
        });
    }

    fn set_mode(&self, mode: Mode) {
        // Another topmost window may have come over the emblem since it appeared: the person
        // must see it think and count up to 100 % (owner, 2026-10-23).
        self.keep_in_sight(self.place_window());
        self.publish(OverlayEvent::Mode {
            mode,
            quiet: false,
            lines: Vec::new(),
        });
    }

    fn think_quietly(&self, lines: &[&str]) {
        self.publish(OverlayEvent::Mode {
            mode: Mode::Thinking,
            quiet: true,
            lines: lines.iter().map(|&line| line.to_owned()).collect(),
        });
    }

    fn level(&self, value: f32) {
        self.keep_in_sight_now_and_then();
        self.publish(OverlayEvent::Level { value });
    }

    fn notice(&self, text: &str) {
        self.publish(OverlayEvent::Notice {
            text: text.to_owned(),
        });
    }

    fn learned_notice(&self, text: &str) {
        self.publish(OverlayEvent::Learned {
            text: text.to_owned(),
        });
    }

    fn say(&self, prefix: &str, text: &str) {
        self.keep_in_sight_now_and_then();
        self.publish(OverlayEvent::Say {
            hold: false,
            prefix: prefix.to_owned(),
            text: text.to_owned(),
        });
    }

    fn say_held(&self, prefix: &str, text: &str) {
        self.keep_in_sight_now_and_then();
        self.publish(OverlayEvent::Say {
            prefix: prefix.to_owned(),
            text: text.to_owned(),
            hold: true,
        });
    }

    fn release_say(&self) {
        self.publish(OverlayEvent::Release);
    }

    fn ask(&self, prompt: &AskPrompt) {
        self.keep_in_sight_now_and_then();
        self.publish(OverlayEvent::Ask(prompt.clone()));
    }

    fn operate(&self, line: &str) {
        self.publish(OverlayEvent::Operate {
            line: line.to_owned(),
        });
    }

    fn hide(&self) {
        self.publish(OverlayEvent::Hide);
    }

    fn complete(&self) {
        self.publish(OverlayEvent::Complete);
    }

    fn progress(&self, progress: Progress) {
        self.keep_in_sight(self.place_window());
        self.publish(OverlayEvent::Progress(progress));
    }
}

/// The overlay page calls this once and receives all its events on `channel`. Only the overlay
/// page may: the events carry Raphael's answers and his questions.
#[tauri::command]
pub fn overlay_subscribe(
    app: AppHandle,
    webview: Webview,
    channel: Channel<OverlayEvent>,
) -> Result<(), String> {
    crate::commands::ensure_caller(webview.label(), crate::OVERLAY_WINDOW)?;
    let Some(overlay) = app.try_state::<Arc<Overlay>>() else {
        log::warn!(
            "overlay: the page \"{}\" subscribed before the overlay was running",
            webview.label()
        );
        return Err("the overlay is not running".into());
    };
    overlay.subscribe(webview.label(), channel);
    Ok(())
}

/// An answer to the question on screen: whether the YES button (`approved`) or the NO button was
/// clicked, for the question `id`, on the overlay or (`in_app`) on the card of the main window's
/// 拉斐爾 page (`agent_question_answer` in commands.rs).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Confirmation {
    pub id: String,
    pub approved: bool,
    pub in_app: bool,
    /// The third answer of a question that has one (「一律允許」): a YES, kept for next time.
    pub always: bool,
}

/// Where the page's answers go: the dictation controller's task takes them from the other end.
/// Kept in the app state.
pub struct ConfirmSender(pub UnboundedSender<Confirmation>);

/// The overlay page answers the question on screen: `approved` is true for its YES button. The
/// answer goes to the controller, which takes it only for the question that is open (`id`). Only
/// the overlay page may call it; the main window answers through its own command
/// (`agent_question_answer`), which says so, and nothing else may approve what Raphael was about
/// to do.
///
/// It needs no capability: the commands of the app itself are not behind the capabilities (the
/// build declares no app manifest), and the window is checked here, like `overlay_log` does.
#[tauri::command]
pub fn overlay_confirm(
    app: AppHandle,
    webview: Webview,
    id: String,
    approved: bool,
    always: Option<bool>,
) -> Result<(), String> {
    crate::commands::ensure_caller(webview.label(), crate::OVERLAY_WINDOW)?;
    let Some(answers) = app.try_state::<ConfirmSender>() else {
        return Err("nothing is listening to the overlay's answers".into());
    };
    answers
        .0
        .send(Confirmation {
            id,
            approved,
            in_app: false,
            always: approved && always == Some(true),
        })
        .map_err(|_| "nothing is listening to the overlay's answers".to_owned())
}

/// The longest page message that is logged, in characters.
const PAGE_LOG_MAX: usize = 2000;

/// The overlay page reports a problem (an event it could not handle, an uncaught error, a
/// subscription that failed, animation frames that stopped) or, at `info` and `debug`, what it
/// is doing. It has no console anyone can see, so this is the only trace. `level` is `"error"`,
/// `"warn"`, `"info"` or `"debug"`; errors are logged as warnings, because the overlay never
/// stops dictation. Only the overlay page may call it, and what it sends is event kinds and
/// metadata, never the words of a report (see diagnostics.ts): the log is not a place for them.
#[tauri::command]
pub fn overlay_log(webview: Webview, level: String, message: String) -> Result<(), String> {
    crate::commands::ensure_caller(webview.label(), crate::OVERLAY_WINDOW)?;
    let label = webview.label();
    let message = page_message(&message);
    match page_log_level(&level) {
        log::Level::Info => log::info!("overlay page \"{label}\": {message}"),
        log::Level::Debug => log::debug!("overlay page \"{label}\": {message}"),
        _ => log::warn!("overlay page \"{label}\": {message}"),
    }
    Ok(())
}

/// How a page message of `level` is logged: unknown levels count as warnings.
fn page_log_level(level: &str) -> log::Level {
    match level {
        "info" => log::Level::Info,
        "debug" => log::Level::Debug,
        _ => log::Level::Warn,
    }
}

/// `message` on one line, cut to [`PAGE_LOG_MAX`] characters.
fn page_message(message: &str) -> String {
    let mut out: String = message
        .chars()
        .take(PAGE_LOG_MAX)
        .map(|c| if c.is_control() { ' ' } else { c })
        .collect();
    if message.chars().count() > PAGE_LOG_MAX {
        out.push('\u{2026}');
    }
    out
}

// ---------- a window that is only ever there ----------

/// The extended style the overlay window must have, from the `style` it has: out of Alt+Tab
/// (`WS_EX_TOOLWINDOW` added and `WS_EX_APPWINDOW` removed, because `skip_taskbar` only keeps it off
/// the taskbar) and never activated, not even by a click (`WS_EX_NOACTIVATE`). The rest, the
/// mouse flags above all, is left as it is.
#[cfg(windows)]
fn passive_ex_style(style: isize) -> isize {
    use windows::Win32::UI::WindowsAndMessaging::{
        WS_EX_APPWINDOW, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW,
    };

    (style | WS_EX_TOOLWINDOW.0 as isize | WS_EX_NOACTIVATE.0 as isize)
        & !(WS_EX_APPWINDOW.0 as isize)
}

/// Gives the window [`passive_ex_style`]. tao already makes a window that is not `focusable`
/// `WS_EX_NOACTIVATE`, but it rebuilds the whole extended style from its own flags whenever one
/// of them changes (and when the size does), which drops the tool window style, so this is done
/// again after every such change, and it makes sure of `WS_EX_NOACTIVATE` once more.
///
/// The change is made on the main thread, which owns the window: from any other thread
/// `SetWindowLongPtrW` would wait for the main thread, and that is a deadlock waiting to happen.
#[cfg(windows)]
fn apply_passive_style(window: &WebviewWindow) {
    use windows::Win32::Foundation::HWND;

    let hwnd = match window.hwnd() {
        Ok(hwnd) => hwnd.0 as isize,
        Err(err) => {
            log::warn!("no window handle for the overlay, it may show in Alt+Tab: {err}");
            return;
        }
    };
    let change = window.run_on_main_thread(move || {
        // SAFETY: `hwnd` is the overlay window, which lives as long as the app, and this runs on
        // the thread that owns it.
        unsafe { restore_passive_style(HWND(hwnd as *mut _)) };
    });
    if let Err(err) = change {
        log::warn!("could not keep the overlay out of Alt+Tab and out of focus: {err}");
    }
}

#[cfg(not(windows))]
fn apply_passive_style(_window: &WebviewWindow) {}

/// Gives the window [`passive_ex_style`] if it lost it, and says whether it had.
///
/// # Safety
///
/// `hwnd` must be a window that the calling thread owns (see [`apply_passive_style`]).
#[cfg(windows)]
unsafe fn restore_passive_style(hwnd: windows::Win32::Foundation::HWND) -> bool {
    use windows::Win32::UI::WindowsAndMessaging::{
        GWL_EXSTYLE, GetWindowLongPtrW, SetWindowLongPtrW,
    };

    // SAFETY: the caller owns `hwnd`, so the style cannot change between the read and the write.
    unsafe {
        let style = GetWindowLongPtrW(hwnd, GWL_EXSTYLE);
        let wanted = passive_ex_style(style);
        if wanted == style {
            return false;
        }
        SetWindowLongPtrW(hwnd, GWL_EXSTYLE, wanted);
    }
    true
}

/// The window's handle as a number that can go to the main thread, or `None` (logged) if there is
/// none. It never changes: the window lives as long as the app.
#[cfg(windows)]
fn native_handle(window: &WebviewWindow) -> Option<isize> {
    match window.hwnd() {
        Ok(hwnd) => Some(hwnd.0 as isize),
        Err(err) => {
            log::warn!(
                "no window handle for the overlay, so it is not checked when it shows: {err}"
            );
            None
        }
    }
}

#[cfg(not(windows))]
fn native_handle(_window: &WebviewWindow) -> Option<isize> {
    None
}

/// A Windows error as its HRESULT, for the log: the system's message for it is in the user's
/// language, and the code is what can be looked up.
#[cfg(windows)]
fn hresult(err: &windows::core::Error) -> String {
    format!("0x{:08X}", err.code().0 as u32)
}

#[cfg(test)]
mod tests {
    use super::*;
    use tauri::ipc::InvokeResponseBody;

    #[test]
    fn only_the_overlay_page_hears_the_overlay_writes_to_its_log_and_answers_its_questions() {
        // The commands cannot be called here without a running app, so read their source.
        let source = include_str!("overlay.rs").replace("\r\n", "\n");
        let production = source.split("#[cfg(test)]").next().unwrap();
        for name in ["overlay_subscribe", "overlay_log", "overlay_confirm"] {
            let command = production
                .split("#[tauri::command]")
                .find(|command| command.contains(&format!("pub fn {name}(")))
                .unwrap();
            let body = command.split("\n}\n").next().unwrap();
            assert!(
                body.contains("ensure_caller(webview.label(), crate::OVERLAY_WINDOW)"),
                "{name} does not check who calls it"
            );
            // the check comes before anything is done
            let first = body
                .split("-> Result<(), String> {")
                .nth(1)
                .unwrap()
                .trim_start();
            assert!(
                first.starts_with("crate::commands::ensure_caller("),
                "{name}: the check must come first"
            );
        }
    }

    #[test]
    fn every_command_of_the_overlay_is_known_to_this_list() {
        // A new command of the overlay has to decide who may call it, so it has to be added here.
        let source = include_str!("overlay.rs").replace("\r\n", "\n");
        let production = source.split("#[cfg(test)]").next().unwrap();
        let mut commands: Vec<&str> = production
            .split("#[tauri::command]")
            .skip(1)
            .map(|command| {
                command
                    .split("pub fn ")
                    .nth(1)
                    .and_then(|rest| rest.split('(').next())
                    .unwrap()
            })
            .collect();
        commands.sort_unstable();
        assert_eq!(
            commands,
            ["overlay_confirm", "overlay_log", "overlay_subscribe"]
        );
    }

    #[test]
    fn emblem_sits_18px_from_the_right_and_12px_above_the_taskbar() {
        for (scale, e) in [(1.0, 150u32), (1.5, 194u32)] {
            let work = Rect {
                x: 0,
                y: 0,
                w: 2560,
                h: 1392,
            };
            let r = place(work, e, scale);
            let c = (1.3 * e as f64).round() * scale; // canvas size in physical px
            let center_x = (r.x + r.w) as f64 - c / 2.0;
            let center_y = (r.y + r.h) as f64 - c / 2.0;
            let radius = e as f64 * scale / 2.0;
            assert!(((work.x + work.w) as f64 - center_x - radius - 18.0 * scale).abs() <= 1.0);
            assert!(((work.y + work.h) as f64 - center_y - radius - 12.0 * scale).abs() <= 1.0);
            assert_eq!(r.w, (540.0 * scale).round() as i32);
        }
    }

    #[test]
    fn window_is_the_canvas_plus_40px_tall() {
        for (scale, e, canvas) in [(1.0, 120u32, 156.0), (1.0, 150, 195.0), (1.25, 194, 252.0)] {
            let r = place(
                Rect {
                    x: 0,
                    y: 0,
                    w: 1920,
                    h: 1032,
                },
                e,
                scale,
            );
            assert_eq!(
                r.h,
                ((canvas + 40.0) * scale).round() as i32,
                "emblem {e} at {scale}"
            );
        }
    }

    #[test]
    fn placement_follows_the_work_area() {
        let at_zero = place(
            Rect {
                x: 0,
                y: 0,
                w: 1920,
                h: 1032,
            },
            150,
            1.0,
        );
        let moved = place(
            Rect {
                x: 100,
                y: 40,
                w: 1920,
                h: 1032,
            },
            150,
            1.0,
        );
        assert_eq!((moved.x - at_zero.x, moved.y - at_zero.y), (100, 40));
        assert_eq!((moved.w, moved.h), (at_zero.w, at_zero.h));
    }

    #[test]
    fn a_taller_taskbar_moves_the_emblem_up() {
        let thin = place(
            Rect {
                x: 0,
                y: 0,
                w: 1920,
                h: 1040,
            },
            150,
            1.0,
        );
        let thick = place(
            Rect {
                x: 0,
                y: 0,
                w: 1920,
                h: 1000,
            },
            150,
            1.0,
        );
        assert_eq!(thin.y - thick.y, 40);
        assert_eq!(thin.x, thick.x);
    }

    #[test]
    fn the_placement_says_where_the_canvas_is_in_the_window() {
        let work = Rect {
            x: 0,
            y: 0,
            w: 1920,
            h: 1032,
        };
        for (scale, e, canvas) in [(1.0, 150u32, 195), (1.25, 194, 315), (1.5, 120, 234)] {
            let placement = placement(work, e, scale);
            assert_eq!(placement.work, work);
            assert_eq!(placement.window, place(work, e, scale));
            assert_eq!(placement.canvas, canvas, "emblem {e} at {scale}");
            // the canvas and the 40 logical pixels above it make the window's height
            assert!((placement.window.h - placement.canvas - (40.0 * scale) as i32).abs() <= 1);
        }
    }

    #[test]
    fn overlay_api_can_be_held_as_a_trait_object() {
        // The dictation controller keeps `Arc<dyn OverlayApi>` so that it can be tested with a fake.
        fn assert_usable(_: Option<Arc<dyn OverlayApi>>) {}
        fn assert_implemented<T: OverlayApi>() {}
        assert_usable(None);
        assert_implemented::<Overlay>();
    }

    #[test]
    fn work_area_from_tauri_keeps_position_and_size() {
        let work = tauri::PhysicalRect {
            position: tauri::PhysicalPosition::new(-1920, 10),
            size: tauri::PhysicalSize::new(1920u32, 1040u32),
        };
        assert_eq!(
            Rect::from(&work),
            Rect {
                x: -1920,
                y: 10,
                w: 1920,
                h: 1040
            }
        );
    }

    // ---- what the page receives ----

    #[test]
    fn events_have_the_shape_the_page_expects() {
        let json = |e: OverlayEvent| serde_json::to_string(&e).unwrap();
        assert_eq!(
            json(OverlayEvent::Show {
                mode: Mode::Listening,
                line: None
            }),
            r#"{"type":"show","mode":"listening"}"#
        );
        assert_eq!(
            json(OverlayEvent::Show {
                mode: Mode::Listening,
                line: Some("《告。主人，請下達指示。》".into())
            }),
            r#"{"type":"show","mode":"listening","line":"《告。主人，請下達指示。》"}"#
        );
        assert_eq!(
            json(OverlayEvent::Mode {
                mode: Mode::Thinking,
                quiet: false,
                lines: Vec::new(),
            }),
            r#"{"type":"mode","mode":"thinking"}"#
        );

        // a question being thought about: no progress caption, and the waiting lines in its place
        assert_eq!(
            json(OverlayEvent::Mode {
                mode: Mode::Thinking,
                quiet: true,
                lines: Vec::new(),
            }),
            r#"{"type":"mode","mode":"thinking","quiet":true}"#
        );
        assert_eq!(
            json(OverlayEvent::Mode {
                mode: Mode::Thinking,
                quiet: true,
                lines: vec!["《告。解析中……》".into(), "《告。並列演算中……》".into()],
            }),
            r#"{"type":"mode","mode":"thinking","quiet":true,"lines":["《告。解析中……》","《告。並列演算中……》"]}"#
        );
        assert_eq!(
            json(OverlayEvent::Level { value: 0.5 }),
            r#"{"type":"level","value":0.5}"#
        );
        assert_eq!(
            json(OverlayEvent::Notice {
                text: "《了。》".into()
            }),
            r#"{"type":"notice","text":"《了。》"}"#
        );
        assert_eq!(json(OverlayEvent::Hide), r#"{"type":"hide"}"#);
        assert_eq!(json(OverlayEvent::Complete), r#"{"type":"complete"}"#);
        assert_eq!(json(OverlayEvent::Release), r#"{"type":"release"}"#);
        assert_eq!(
            json(OverlayEvent::Learned {
                text: "《了。「手機」已加入詞典。》".into()
            }),
            r#"{"type":"learned","text":"《了。「手機」已加入詞典。》"}"#
        );
        assert_eq!(
            json(OverlayEvent::Progress(Progress {
                stage: Stage::Transcribing,
                done: 1,
                total: 3,
                audio_seconds: 4.5
            })),
            r#"{"type":"progress","stage":"transcribing","done":1,"total":3,"audio_seconds":4.5}"#
        );
        assert_eq!(
            json(OverlayEvent::Progress(Progress {
                stage: Stage::Cleaning,
                ..Progress::DONE
            })),
            r#"{"type":"progress","stage":"cleaning","done":0,"total":0,"audio_seconds":0.0}"#
        );
        assert_eq!(
            json(OverlayEvent::Progress(Progress::DONE)),
            r#"{"type":"progress","stage":"done","done":0,"total":0,"audio_seconds":0.0}"#
        );
        assert_eq!(
            json(OverlayEvent::Settings {
                px: 150,
                subtitles: "on".into(),
                language: "zh-TW".into()
            }),
            r#"{"type":"settings","px":150,"subtitles":"on","language":"zh-TW"}"#
        );
    }

    fn prompt(tier: Tier) -> AskPrompt {
        AskPrompt {
            member_id: None,
            conversation_id: None,
            always: None,
            id: "c-7".into(),
            tier,
            prefix: "問".into(),
            text: "要把這段話複製到剪貼簿嗎？".into(),
            yes: "是".into(),
            no: "否".into(),
            timeout_ms: 30_000,
        }
    }

    #[test]
    fn say_and_ask_have_the_shape_the_page_expects() {
        let json = |e: OverlayEvent| serde_json::to_string(&e).unwrap();
        assert_eq!(
            json(OverlayEvent::Say {
                prefix: "Answer".into(),
                text: "Hello.".into(),
                hold: true,
            }),
            r#"{"type":"say","prefix":"Answer","text":"Hello.","hold":true}"#
        );
        assert_eq!(
            json(OverlayEvent::Say {
                hold: false,
                prefix: "答".into(),
                text: "台北現在二十四度，多雲。".into()
            }),
            r#"{"type":"say","prefix":"答","text":"台北現在二十四度，多雲。"}"#
        );
        assert_eq!(
            json(OverlayEvent::Ask(prompt(Tier::T2))),
            concat!(
                r#"{"type":"ask","id":"c-7","tier":"T2","prefix":"問","#,
                r#""text":"要把這段話複製到剪貼簿嗎？","yes":"是","no":"否","timeout_ms":30000}"#
            )
        );
        assert_eq!(
            serde_json::to_value(OverlayEvent::Ask(prompt(Tier::T3))).unwrap()["tier"],
            "T3"
        );
    }

    #[test]
    fn the_tier_the_core_names_is_kept_and_anything_else_is_the_careful_one() {
        assert_eq!(Tier::parse("T2"), Tier::T2);
        assert_eq!(Tier::parse("T3"), Tier::T3);
        for odd in ["", "T1", "T4", "t2", "T2 ", "high"] {
            assert_eq!(Tier::parse(odd), Tier::T3, "{odd:?}");
        }
    }

    // ---- the mouse ----

    #[test]
    fn only_a_question_makes_the_window_take_the_mouse() {
        let ask = OverlayEvent::Ask(prompt(Tier::T2));
        assert_eq!(takes_clicks(&ask), Some(true));
        // everything that puts something else on the stage lets the mouse through again
        for ends in [
            OverlayEvent::Show {
                mode: Mode::Listening,
                line: None,
            },
            OverlayEvent::Say {
                hold: false,
                prefix: "答".into(),
                text: String::new(),
            },
            notice(1),
            OverlayEvent::Complete,
            OverlayEvent::Release,
            OverlayEvent::Hide,
        ] {
            assert_eq!(takes_clicks(&ends), Some(false), "{ends:?}");
        }
        // and what is not about the stage changes nothing
        for neutral in [
            OverlayEvent::Mode {
                mode: Mode::Thinking,
                quiet: false,
                lines: Vec::new(),
            },
            OverlayEvent::Level { value: 0.5 },
            OverlayEvent::Progress(Progress::DONE),
            OverlayEvent::Learned {
                text: String::new(),
            },
            OverlayEvent::Settings {
                px: 150,
                subtitles: "on".into(),
                language: "zh-TW".into(),
            },
        ] {
            assert_eq!(takes_clicks(&neutral), None, "{neutral:?}");
        }
    }

    #[test]
    fn a_question_that_nobody_answers_is_over_with_the_next_thing_shown() {
        // The window takes the mouse from the question until an event that ends it: replay the
        // events of a few scenes the way `Overlay::publish` applies them.
        let replay = |events: &[OverlayEvent]| {
            let mut takes = false;
            let mut seen = Vec::new();
            for event in events {
                if let Some(on) = takes_clicks(event) {
                    takes = on;
                }
                seen.push(takes);
            }
            seen
        };
        let listening = OverlayEvent::Show {
            mode: Mode::Listening,
            line: None,
        };
        let thinking = OverlayEvent::Mode {
            mode: Mode::Thinking,
            quiet: false,
            lines: Vec::new(),
        };
        let ask = OverlayEvent::Ask(prompt(Tier::T3));
        let say = OverlayEvent::Say {
            hold: false,
            prefix: "了".into(),
            text: "好了".into(),
        };
        let level = OverlayEvent::Level { value: 0.2 };
        assert_eq!(
            replay(&[listening, thinking, ask.clone(), level.clone(), say.clone()]),
            [false, false, true, true, false]
        );
        // the loudness of the room does not close the question; a hide does
        assert_eq!(
            replay(&[ask, level, OverlayEvent::Hide]),
            [true, true, false]
        );
    }

    #[test]
    fn the_overlay_window_is_built_to_never_take_focus() {
        // The window cannot be built here without a running app, so read its source: tao gives a
        // window that is not focusable `WS_EX_NOACTIVATE`, and `focused(false)` keeps its first
        // show from activating it.
        let source = include_str!("overlay.rs").replace("\r\n", "\n");
        let production = source.split("#[cfg(test)]").next().unwrap();
        let build = production
            .split("WebviewWindowBuilder::new(")
            .nth(1)
            .and_then(|rest| rest.split(".build()").next())
            .unwrap();
        assert!(build.contains(".focusable(false)"));
        assert!(build.contains(".focused(false)"));
    }

    #[test]
    fn the_window_is_kept_in_sight_when_the_emblem_appears_thinks_and_counts() {
        // Another topmost window can cover the corner after `show`, while the user speaks or
        // while transcription runs, so `set_mode` and `progress` must also raise the window.
        let source = include_str!("overlay.rs").replace("\r\n", "\n");
        let production = source.split("#[cfg(test)]").next().unwrap();
        let api = production
            .split("impl OverlayApi for Overlay {")
            .nth(1)
            .unwrap();
        for method in [
            "fn show(",
            "fn greet(",
            "fn set_mode(",
            "fn progress(",
        ] {
            let body = api
                .split(method)
                .nth(1)
                .and_then(|rest| rest.split("\n    }\n").next())
                .unwrap();
            assert!(
                body.contains("self.place_window()") && body.contains("self.keep_in_sight("),
                "{method} must place the window and keep it in sight"
            );
        }
    }

    #[cfg(windows)]
    #[test]
    fn the_style_of_the_window_is_never_activated_and_out_of_alt_tab() {
        use windows::Win32::UI::WindowsAndMessaging::{
            WS_EX_ACCEPTFILES, WS_EX_APPWINDOW, WS_EX_LAYERED, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW,
            WS_EX_TOPMOST, WS_EX_TRANSPARENT, WS_EX_WINDOWEDGE,
        };

        let has = |style: isize, flag: windows::Win32::UI::WindowsAndMessaging::WINDOW_EX_STYLE| {
            style & flag.0 as isize != 0
        };
        let base = (WS_EX_WINDOWEDGE.0 | WS_EX_ACCEPTFILES.0 | WS_EX_TOPMOST.0) as isize;
        // what tao builds, with the mouse going through, and with the mouse taken
        for mouse in [(WS_EX_TRANSPARENT.0 | WS_EX_LAYERED.0) as isize, 0] {
            for tao in [
                base | mouse | WS_EX_APPWINDOW.0 as isize,
                base | mouse | WS_EX_NOACTIVATE.0 as isize,
                base | mouse,
            ] {
                let fixed = passive_ex_style(tao);
                assert!(has(fixed, WS_EX_NOACTIVATE), "{tao:#x}");
                assert!(has(fixed, WS_EX_TOOLWINDOW), "{tao:#x}");
                assert!(!has(fixed, WS_EX_APPWINDOW), "{tao:#x}");
                // the rest is what it was, the mouse flags above all
                let touched =
                    (WS_EX_NOACTIVATE.0 | WS_EX_TOOLWINDOW.0 | WS_EX_APPWINDOW.0) as isize;
                assert_eq!(fixed & !touched, tao & !touched, "{tao:#x}");
                assert_eq!(passive_ex_style(fixed), fixed, "it is done once");
            }
        }
    }

    /// A page that records what it receives, as JSON text.
    fn page() -> (Channel<OverlayEvent>, Arc<Mutex<Vec<String>>>) {
        let received = Arc::new(Mutex::new(Vec::new()));
        let sink = received.clone();
        let channel = Channel::new(move |body| {
            if let InvokeResponseBody::Json(json) = body {
                sink.lock().unwrap().push(json);
            }
            Ok(())
        });
        (channel, received)
    }

    fn received(got: &Arc<Mutex<Vec<String>>>) -> Vec<String> {
        got.lock().unwrap().clone()
    }

    fn notice(n: usize) -> OverlayEvent {
        OverlayEvent::Notice {
            text: n.to_string(),
        }
    }

    const SETTINGS_150_ON: &str =
        r#"{"type":"settings","px":150,"subtitles":"on","language":"zh-TW"}"#;

    #[test]
    fn a_new_page_gets_the_settings_first() {
        let mut bus = Bus::new(150, "off", "ja");
        let (channel, got) = page();
        bus.subscribe("overlay", channel);
        assert_eq!(
            received(&got),
            [r#"{"type":"settings","px":150,"subtitles":"off","language":"ja"}"#]
        );
    }

    #[test]
    fn only_the_latest_settings_reach_a_page_that_loads_late() {
        let mut bus = Bus::new(150, "on", "zh-TW");
        bus.publish(OverlayEvent::Settings {
            px: 194,
            subtitles: "off".into(),
            language: "en".into(),
        });
        let (channel, got) = page();
        bus.subscribe("overlay", channel);
        assert_eq!(
            received(&got),
            [r#"{"type":"settings","px":194,"subtitles":"off","language":"en"}"#]
        );
    }

    #[test]
    fn events_from_before_the_page_loaded_arrive_in_order_after_the_settings() {
        let mut bus = Bus::new(150, "on", "zh-TW");
        bus.publish(OverlayEvent::Show {
            mode: Mode::Listening,
            line: None,
        });
        bus.publish(OverlayEvent::Level { value: 0.3 }); // a loudness reading is stale by then
        bus.publish(OverlayEvent::Progress(Progress::DONE)); // and so is a progress report
        bus.publish(OverlayEvent::Notice { text: "了".into() });
        let (channel, got) = page();
        bus.subscribe("overlay", channel);
        assert_eq!(
            received(&got),
            [
                SETTINGS_150_ON,
                r#"{"type":"show","mode":"listening"}"#,
                r#"{"type":"notice","text":"了"}"#,
            ]
        );
    }

    #[test]
    fn the_backlog_is_delivered_once() {
        let mut bus = Bus::new(150, "on", "zh-TW");
        bus.publish(notice(1));
        let (first, got_first) = page();
        bus.subscribe("overlay", first);
        let (second, got_second) = page();
        bus.subscribe("other", second);
        assert_eq!(received(&got_first).len(), 2); // settings and the notice
        assert_eq!(received(&got_second), [SETTINGS_150_ON]);
    }

    #[test]
    fn the_backlog_keeps_the_newest_events() {
        let mut bus = Bus::new(150, "on", "zh-TW");
        for n in 0..BACKLOG_MAX + 5 {
            bus.publish(notice(n));
        }
        let (channel, got) = page();
        bus.subscribe("overlay", channel);
        let got = received(&got);
        assert_eq!(got.len(), 1 + BACKLOG_MAX);
        assert_eq!(got[1], r#"{"type":"notice","text":"5"}"#);
        assert_eq!(
            got.last().unwrap(),
            &format!(r#"{{"type":"notice","text":"{}"}}"#, BACKLOG_MAX + 4)
        );
    }

    #[test]
    fn events_reach_every_page_in_order() {
        let mut bus = Bus::new(150, "on", "zh-TW");
        let (a, got_a) = page();
        let (b, got_b) = page();
        bus.subscribe("a", a);
        bus.subscribe("b", b);
        bus.publish(OverlayEvent::Show {
            mode: Mode::Listening,
            line: None,
        });
        bus.publish(OverlayEvent::Level { value: 1.0 });
        bus.publish(OverlayEvent::Hide);
        let expected = [
            SETTINGS_150_ON,
            r#"{"type":"show","mode":"listening"}"#,
            r#"{"type":"level","value":1.0}"#,
            r#"{"type":"hide"}"#,
        ];
        assert_eq!(received(&got_a), expected);
        assert_eq!(received(&got_b), expected);
    }

    #[test]
    fn a_page_that_subscribes_again_replaces_its_old_channel() {
        let mut bus = Bus::new(150, "on", "zh-TW");
        let (old, got_old) = page();
        bus.subscribe("overlay", old);
        let (new, got_new) = page();
        bus.subscribe("overlay", new); // the page was reloaded
        bus.publish(OverlayEvent::Hide);
        assert_eq!(bus.subscribers.len(), 1);
        assert_eq!(received(&got_old), [SETTINGS_150_ON]);
        assert_eq!(received(&got_new), [SETTINGS_150_ON, r#"{"type":"hide"}"#]);
    }

    #[test]
    fn a_page_that_is_gone_is_dropped_and_the_others_still_hear() {
        let mut bus = Bus::new(150, "on", "zh-TW");
        let gone = Channel::new(|_| Err(tauri::Error::WebviewNotFound));
        let (alive, got) = page();
        bus.subscribers.push(("gone".into(), gone));
        bus.subscribe("alive", alive);
        bus.publish(OverlayEvent::Hide);
        assert_eq!(bus.subscribers.len(), 1);
        assert_eq!(bus.subscribers[0].0, "alive");
        assert_eq!(received(&got), [SETTINGS_150_ON, r#"{"type":"hide"}"#]);
    }

    #[test]
    fn page_messages_are_logged_at_their_level_and_errors_as_warnings() {
        assert_eq!(page_log_level("info"), log::Level::Info);
        assert_eq!(page_log_level("debug"), log::Level::Debug);
        assert_eq!(page_log_level("warn"), log::Level::Warn);
        assert_eq!(page_log_level("error"), log::Level::Warn);
        assert_eq!(page_log_level("whatever"), log::Level::Warn);
    }

    #[test]
    fn page_messages_are_one_line_and_cut_when_long() {
        assert_eq!(page_message("a\nb\tc"), "a b c");
        let long = "x".repeat(PAGE_LOG_MAX + 10);
        let cut = page_message(&long);
        assert_eq!(cut.chars().count(), PAGE_LOG_MAX + 1);
        assert!(cut.ends_with('\u{2026}'));
        assert_eq!(
            page_message(&"x".repeat(PAGE_LOG_MAX)).chars().count(),
            PAGE_LOG_MAX
        );
    }

    #[test]
    fn waiting_without_a_page_is_warned_about_once_until_a_page_subscribes() {
        let mut bus = Bus::new(150, "on", "zh-TW");
        bus.publish(OverlayEvent::Level { value: 0.2 }); // dropped, not worth a warning
        bus.publish(OverlayEvent::Settings {
            px: 150,
            subtitles: "on".into(),
            language: "zh-TW".into(),
        });
        assert!(!bus.backlog_warned);
        bus.publish(OverlayEvent::Show {
            mode: Mode::Listening,
            line: None,
        });
        assert!(bus.backlog_warned);
        bus.publish(OverlayEvent::Hide);
        assert!(bus.backlog_warned);
        let (channel, _got) = page();
        bus.subscribe("overlay", channel);
        assert!(
            !bus.backlog_warned,
            "a page that comes and goes is worth a new warning"
        );
    }

    #[test]
    fn every_event_has_its_kind_for_the_log() {
        let events = [
            OverlayEvent::Show {
                mode: Mode::Listening,
                line: None,
            },
            OverlayEvent::Mode {
                mode: Mode::Thinking,
                quiet: false,
                lines: Vec::new(),
            },
            OverlayEvent::Level { value: 0.0 },
            OverlayEvent::Progress(Progress::DONE),
            notice(0),
            OverlayEvent::Learned {
                text: String::new(),
            },
            OverlayEvent::Say {
                hold: false,
                prefix: "答".into(),
                text: String::new(),
            },
            OverlayEvent::Ask(prompt(Tier::T2)),
            OverlayEvent::Complete,
            OverlayEvent::Release,
            OverlayEvent::Hide,
            OverlayEvent::Settings {
                px: 150,
                subtitles: "on".into(),
                language: "zh-TW".into(),
            },
        ];
        for event in events {
            let json = serde_json::to_value(&event).unwrap();
            assert_eq!(json["type"], event.kind());
        }
    }

    #[test]
    fn an_answer_and_a_question_from_before_the_page_loaded_wait_for_it() {
        let mut bus = Bus::new(150, "on", "zh-TW");
        bus.publish(OverlayEvent::Say {
            hold: false,
            prefix: "答".into(),
            text: "好".into(),
        });
        bus.publish(OverlayEvent::Ask(prompt(Tier::T2)));
        let (channel, got) = page();
        bus.subscribe("overlay", channel);
        let got = received(&got);
        assert_eq!(got.len(), 3);
        assert_eq!(got[0], SETTINGS_150_ON);
        assert_eq!(got[1], r#"{"type":"say","prefix":"答","text":"好"}"#);
        assert!(
            got[2].starts_with(r#"{"type":"ask","id":"c-7""#),
            "{}",
            got[2]
        );
    }

    #[test]
    fn the_window_on_a_1920x1080_screen_overhangs_only_by_the_transparent_margin() {
        // What GetWindowRect showed on the user's screen (work area 1920x1032, scale 1). The
        // window has no frame, so this is the window itself, and the overhang is the canvas's
        // transparent margin.
        let work = Rect {
            x: 0,
            y: 0,
            w: 1920,
            h: 1032,
        };
        let r = place(work, 150, 1.0);
        assert_eq!((r.x, r.y, r.x + r.w, r.y + r.h), (1385, 808, 1925, 1043));
        // The emblem's circle (the 195 px canvas sits in the bottom-right corner) is inside the
        // work area, about 18 px from its right edge and 12 px above the taskbar.
        let center = (f64::from(r.x + r.w) - 97.5, f64::from(r.y + r.h) - 97.5);
        assert!(center.0 + 75.0 <= 1920.0 - 17.0);
        assert!(center.1 + 75.0 <= 1032.0 - 11.0);
        assert!(center.0 - 75.0 >= 0.0 && center.1 - 75.0 >= 0.0);
    }

    #[test]
    fn events_after_the_last_page_is_gone_wait_for_the_next_one() {
        let mut bus = Bus::new(150, "on", "zh-TW");
        let gone = Channel::new(|_| Err(tauri::Error::WebviewNotFound));
        bus.subscribers.push(("gone".into(), gone));
        bus.publish(OverlayEvent::Hide); // dropped with the page that failed to take it
        bus.publish(notice(7)); // nobody is listening any more
        let (channel, got) = page();
        bus.subscribe("overlay", channel);
        assert_eq!(
            received(&got),
            [SETTINGS_150_ON, r#"{"type":"notice","text":"7"}"#]
        );
    }
}
