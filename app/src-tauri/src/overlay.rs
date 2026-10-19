//! The overlay window: Raphael's emblem and reports in the bottom-right corner.
//!
//! The window is created once, at start-up. It is transparent, never takes focus, lets the mouse
//! click through, stays on top and stays out of the taskbar and Alt+Tab. It is **never shown or
//! hidden again**: when tao shows a window it calls `ShowWindow(SW_SHOW)`, which focuses it, and
//! the next paste would go to the wrong place. "Hidden" means that the page (`overlay.html`)
//! draws nothing and stops its animation loop; the empty window stays where it is.
//!
//! The page learns what to do from [`OverlayEvent`]s on a channel that it opens once with the
//! `overlay_subscribe` command. The rest of the app drives the overlay through [`OverlayApi`].

use std::{
    collections::VecDeque,
    sync::{
        Arc, Mutex, MutexGuard, PoisonError,
        atomic::{AtomicU32, Ordering},
    },
};

use serde::Serialize;
use tauri::{
    AppHandle, Manager, PhysicalPosition, PhysicalRect, PhysicalSize, Webview, WebviewUrl,
    WebviewWindow, WebviewWindowBuilder, ipc::Channel,
};

/// What the emblem shows while it is on screen (spec §6.5).
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Mode {
    /// The white ring follows the user's microphone.
    Listening,
    /// Processing continues while its numeric percentage is shown.
    Thinking,
    /// The white ring follows Raphael's voice.
    #[cfg_attr(
        not(test),
        expect(dead_code, reason = "Raphael speaks from Phase 3 on (TTS)")
    )]
    Speaking,
    /// Just there, with a thin ring (used for notices).
    #[cfg_attr(
        not(test),
        expect(dead_code, reason = "the overlay page picks it for a notice by itself")
    )]
    Steady,
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

/// What the overlay page is told. Every text in it is in the interface language and in that one
/// alone, never a pair.
#[derive(Clone, Debug, Serialize)]
#[serde(tag = "type", rename_all = "lowercase")]
pub enum OverlayEvent {
    /// The emblem appears (the appear animation) and shows `mode`.
    Show { mode: Mode },
    /// The emblem shows another mode.
    Mode { mode: Mode },
    /// Loudness from 0 to 1: the white ring's width while listening or speaking.
    Level { value: f32 },
    /// How far the dictation has come since the key was released, shown beside the glyph ring.
    Progress(Progress),
    /// Raphael's report: the emblem appears with `text` as its subtitle, then vanishes.
    Notice { text: String },
    /// Learned words are reported like a notice, but they never take the emblem from a dictation
    /// that is showing: the page tells them when it is over.
    Learned { text: String },
    /// Delivery succeeded: show 100% briefly before the normal vanish animation.
    Complete,
    /// The vanish animation.
    Hide,
    /// The emblem's diameter in logical pixels, whether the subtitles show (`"on"` or `"off"`)
    /// and the interface language (`"zh-TW"`, `"en"` or `"ja"`).
    Settings {
        px: u32,
        subtitles: String,
        language: String,
    },
}

/// How the rest of the app drives the overlay. Calls do not wait for the page and none of them
/// can fail: a problem with the window is logged, because the overlay must never get in the way
/// of dictation.
pub trait OverlayApi: Send + Sync + 'static {
    /// The emblem appears and shows `mode`. The window is put back in its corner first (the
    /// taskbar or the screen's scale may have changed).
    fn show(&self, mode: Mode);
    /// Changes what the visible emblem shows.
    fn set_mode(&self, mode: Mode);
    /// Loudness from 0 to 1.
    fn level(&self, value: f32);
    /// Raphael reports `text` (in the interface language) as a subtitle for a time that depends on
    /// its length, then the emblem vanishes. Reports that arrive meanwhile wait their turn.
    fn notice(&self, text: &str);
    /// Report learned words in the usual corner. Fakes can keep the ordinary notice behavior.
    fn learned_notice(&self, text: &str) {
        self.notice(text);
    }
    /// The vanish animation plays; the window stays, empty, where it is.
    fn hide(&self);
    /// Finish successfully; simple implementations may retain their ordinary hide behavior.
    fn complete(&self) {
        self.hide();
    }
    /// How far the dictation has come since the key was released.
    fn progress(&self, progress: Progress);
}

// ---------- placement (spec §6.1) ----------

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
            OverlayEvent::Complete => "complete",
            OverlayEvent::Hide => "hide",
            OverlayEvent::Settings { .. } => "settings",
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
    /// The emblem's diameter in logical pixels, which sets the size of the window.
    emblem_px: AtomicU32,
    bus: Mutex<Bus>,
}

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
        .build()?;

        // Let the mouse through (`WS_EX_TRANSPARENT | WS_EX_LAYERED`). A window that does not
        // do that would block clicks on whatever is under the corner, so do not keep it.
        if let Err(err) = window.set_ignore_cursor_events(true) {
            if let Err(err) = window.destroy() {
                log::warn!("could not close the overlay window either: {err}");
            }
            return Err(err);
        }
        hide_from_alt_tab(&window);

        let overlay = Arc::new(Overlay {
            app: app.clone(),
            window,
            emblem_px: AtomicU32::new(emblem_px),
            bus: Mutex::new(Bus::new(emblem_px, subtitles, language)),
        });
        overlay.place_window();
        Ok(overlay)
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
        lock(&self.bus).publish(event);
    }

    fn subscribe(&self, label: &str, channel: Channel<OverlayEvent>) {
        lock(&self.bus).subscribe(label, channel);
    }

    /// Puts the window in its corner of the primary monitor's work area, where the taskbar or the
    /// screen's scale may have moved it since the last time.
    fn place_window(&self) {
        if let Err(err) = self.try_place_window() {
            log::warn!("could not place the overlay window: {err}");
        }
    }

    fn try_place_window(&self) -> tauri::Result<()> {
        let monitor = self.app.primary_monitor()?.ok_or_else(|| {
            tauri::Error::Io(std::io::Error::new(
                std::io::ErrorKind::NotFound,
                "there is no primary monitor",
            ))
        })?;
        let target = place(
            Rect::from(monitor.work_area()),
            self.emblem_px.load(Ordering::Relaxed),
            monitor.scale_factor(),
        );
        // Compare with where the window really is: Windows itself moves windows when the
        // screens change.
        let position = self.window.outer_position()?;
        let size = self.window.outer_size()?;
        if (position.x, position.y, size.width, size.height)
            == (target.x, target.y, target.w as u32, target.h as u32)
        {
            return Ok(());
        }
        self.window
            .set_size(PhysicalSize::new(target.w as u32, target.h as u32))?;
        self.window
            .set_position(PhysicalPosition::new(target.x, target.y))?;
        // tao rebuilds the extended window style whenever it changes flags or the size.
        hide_from_alt_tab(&self.window);
        Ok(())
    }
}

impl OverlayApi for Overlay {
    fn show(&self, mode: Mode) {
        self.place_window();
        self.publish(OverlayEvent::Show { mode });
    }

    fn set_mode(&self, mode: Mode) {
        self.publish(OverlayEvent::Mode { mode });
    }

    fn level(&self, value: f32) {
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

    fn hide(&self) {
        self.publish(OverlayEvent::Hide);
    }

    fn complete(&self) {
        self.publish(OverlayEvent::Complete);
    }

    fn progress(&self, progress: Progress) {
        self.publish(OverlayEvent::Progress(progress));
    }
}

/// The overlay page calls this once and receives all its events on `channel`.
#[tauri::command]
pub fn overlay_subscribe(
    app: AppHandle,
    webview: Webview,
    channel: Channel<OverlayEvent>,
) -> Result<(), String> {
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

// ---------- keeping the window out of Alt+Tab ----------

/// Adds `WS_EX_TOOLWINDOW` and removes `WS_EX_APPWINDOW`, which keeps the window out of Alt+Tab
/// (`skip_taskbar` only keeps it off the taskbar). tao rebuilds the whole extended style from
/// its own flags whenever one of them changes, so this must be done again after such a change.
///
/// The change is made on the main thread, which owns the window: from any other thread
/// `SetWindowLongPtrW` would wait for the main thread, and that is a deadlock waiting to happen.
#[cfg(windows)]
fn hide_from_alt_tab(window: &WebviewWindow) {
    use windows::Win32::{
        Foundation::HWND,
        UI::WindowsAndMessaging::{
            GWL_EXSTYLE, GetWindowLongPtrW, SetWindowLongPtrW, WS_EX_APPWINDOW, WS_EX_TOOLWINDOW,
        },
    };

    let hwnd = match window.hwnd() {
        Ok(hwnd) => hwnd.0 as isize,
        Err(err) => {
            log::warn!("no window handle for the overlay, it may show in Alt+Tab: {err}");
            return;
        }
    };
    let change = window.run_on_main_thread(move || {
        let hwnd = HWND(hwnd as *mut _);
        // SAFETY: `hwnd` is the overlay window, which lives as long as the app, and this runs on
        // the thread that owns it.
        unsafe {
            let style = GetWindowLongPtrW(hwnd, GWL_EXSTYLE);
            let wanted = (style | WS_EX_TOOLWINDOW.0 as isize) & !(WS_EX_APPWINDOW.0 as isize);
            if wanted != style {
                SetWindowLongPtrW(hwnd, GWL_EXSTYLE, wanted);
            }
        }
    });
    if let Err(err) = change {
        log::warn!("could not keep the overlay out of Alt+Tab: {err}");
    }
}

#[cfg(not(windows))]
fn hide_from_alt_tab(_window: &WebviewWindow) {}

#[cfg(test)]
mod tests {
    use super::*;
    use tauri::ipc::InvokeResponseBody;

    #[test]
    fn only_the_overlay_page_writes_to_the_log() {
        // The command cannot be called here without a running app, so read its source.
        let source = include_str!("overlay.rs").replace("\r\n", "\n");
        let production = source.split("#[cfg(test)]").next().unwrap();
        let command = production
            .split("#[tauri::command]")
            .find(|command| command.contains("pub fn overlay_log("))
            .unwrap();
        let body = command.split("\n}\n").next().unwrap();
        assert!(body.contains("ensure_caller(webview.label(), crate::OVERLAY_WINDOW)"));
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
                mode: Mode::Listening
            }),
            r#"{"type":"show","mode":"listening"}"#
        );
        assert_eq!(
            json(OverlayEvent::Mode {
                mode: Mode::Thinking
            }),
            r#"{"type":"mode","mode":"thinking"}"#
        );
        assert_eq!(
            json(OverlayEvent::Mode {
                mode: Mode::Speaking
            }),
            r#"{"type":"mode","mode":"speaking"}"#
        );
        assert_eq!(
            json(OverlayEvent::Mode { mode: Mode::Steady }),
            r#"{"type":"mode","mode":"steady"}"#
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
        bus.publish(OverlayEvent::Show { mode: Mode::Steady });
        bus.publish(OverlayEvent::Level { value: 1.0 });
        bus.publish(OverlayEvent::Hide);
        let expected = [
            SETTINGS_150_ON,
            r#"{"type":"show","mode":"steady"}"#,
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
            OverlayEvent::Show { mode: Mode::Steady },
            OverlayEvent::Mode { mode: Mode::Steady },
            OverlayEvent::Level { value: 0.0 },
            OverlayEvent::Progress(Progress::DONE),
            notice(0),
            OverlayEvent::Learned {
                text: String::new(),
            },
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
