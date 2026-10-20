//! Whether the person can see the overlay window.
//!
//! The page can draw the emblem in a window that nobody sees: one that something hid or
//! minimized, that the shell cloaked because it stayed on another virtual desktop, that another
//! topmost window covers, or that is away from the work area. Just before the emblem appears,
//! [`check`] looks at the window from the main thread and writes what it saw to the log: at debug
//! level every time, at info level when something is wrong.
//!
//! Of the other windows, only how many cover the emblem is written down, never a title or the name
//! of an app.

use super::{Placement, Rect};

/// `DWMWA_CLOAKED`: who cloaked the window (dwmapi.h).
const CLOAKED_APP: u32 = 0x1;
const CLOAKED_SHELL: u32 = 0x2;
const CLOAKED_INHERITED: u32 = 0x4;

/// Where the window is among the virtual desktops, as the shell's `IVirtualDesktopManager` says.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) enum Desktop {
    /// On the desktop the person is looking at. A `tracked` window belongs to one desktop and is
    /// left behind when the person switches to another; one that the shell does not track (a tool
    /// window, as the overlay should be) shows on all of them.
    Current { tracked: bool },
    /// On another desktop, where the shell cloaks it from this one.
    Elsewhere,
    /// The shell could not say (the HRESULT): explorer may be restarting.
    Unknown(i32),
}

/// Where the foreground window is, next to the overlay.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) enum Foreground {
    /// There is none (the desktop is changing, or the screen is locked).
    None,
    /// The overlay itself: it took the focus, which it must never do.
    Overlay,
    /// Above the overlay in the z-order: a topmost window, or the overlay is not topmost.
    Above,
    Below,
    /// The walk up the z-order stopped before it could tell.
    Unknown,
}

/// The webview inside the window, as its WebView2 controller has it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) struct WebviewSight {
    /// `IsVisible`: a webview that is not visible renders nothing.
    pub visible: bool,
    /// Its bounds and the window that hosts it fill the window's client area, as wry sets them
    /// after every resize.
    pub fitted: bool,
}

/// What the window looks like, read on the main thread.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) struct Sight {
    /// `IsWindowVisible`.
    pub visible: bool,
    /// `IsIconic`.
    pub minimized: bool,
    /// `DWMWA_CLOAKED`: 0, or the flags of who cloaked it; `None` when DWM did not say.
    pub cloaked: Option<u32>,
    /// The extended window style, for the log.
    pub ex_style: u32,
    /// `WS_EX_TOPMOST`.
    pub topmost: bool,
    /// The extended style keeps the window out of Alt+Tab and from ever being activated
    /// (`passive_ex_style`).
    pub passive: bool,
    /// The alpha of the layered window, when someone set one. The overlay sets none: its
    /// transparency is the page's.
    pub alpha: Option<u8>,
    pub desktop: Desktop,
    /// Where the window is, in physical pixels.
    pub rect: Rect,
    /// Where `place_window` put it just before, and in which work area; `None` if it could not.
    pub placement: Option<Placement>,
    /// Visible windows above the overlay that cover the emblem's centre.
    pub over_emblem: u32,
    /// Those of them that let the mouse through: other apps' overlays, which are usually
    /// see-through, so they are counted apart and are not a problem.
    pub see_through: u32,
    pub foreground: Foreground,
    /// `None` when the controller could not say.
    pub webview: Option<WebviewSight>,
}

impl Sight {
    /// Whether the window is where it was placed, if it was.
    fn placed(&self) -> Option<bool> {
        self.placement
            .map(|placement| placement.window == self.rect)
    }

    /// Whether the emblem's centre is inside the work area it was placed in.
    fn emblem_in_work_area(&self) -> Option<bool> {
        let centre = emblem_centre(self.rect, self.placement.map(|p| p.canvas));
        self.placement
            .map(|placement| contains(placement.work, centre))
    }
}

/// The point of the window that has to be seen: the emblem's centre, which is the centre of its
/// square canvas in the window's bottom-right corner. Without a placement, the canvas is taken to
/// be as tall as the window.
pub(super) fn emblem_centre(rect: Rect, canvas: Option<i32>) -> (i32, i32) {
    let side = canvas.unwrap_or(rect.h);
    (rect.x + rect.w - side / 2, rect.y + rect.h - side / 2)
}

/// Whether `point` is inside `rect`, whose right and bottom edges are outside it as in Win32.
pub(super) fn contains(rect: Rect, (x, y): (i32, i32)) -> bool {
    x >= rect.x && x < rect.x + rect.w && y >= rect.y && y < rect.y + rect.h
}

// ---------- what is wrong ----------

/// Something that can keep the person from seeing the window.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) enum Problem {
    Hidden,
    Minimized,
    /// Who cloaked it (the `DWMWA_CLOAKED` flags).
    Cloaked(u32),
    NotTopmost,
    NotPassive,
    OnAnotherDesktop,
    /// Not where `place_window` put it a moment ago.
    Misplaced,
    /// The emblem's centre is outside the work area.
    OffWorkArea,
    /// How many windows cover the emblem.
    Covered(u32),
    /// The layered window's alpha.
    Faded(u8),
    /// The overlay is the foreground window.
    Focused,
    WebviewHidden,
    WebviewUnfitted,
}

/// Everything in `sight` that can keep the person from seeing the window. An unknown answer is
/// not a problem: nothing is known to be wrong.
pub(super) fn problems(sight: &Sight) -> Vec<Problem> {
    let mut found = Vec::new();
    if !sight.visible {
        found.push(Problem::Hidden);
    }
    if sight.minimized {
        found.push(Problem::Minimized);
    }
    if let Some(flags) = sight.cloaked
        && flags != 0
    {
        found.push(Problem::Cloaked(flags));
    }
    if !sight.topmost {
        found.push(Problem::NotTopmost);
    }
    if !sight.passive {
        found.push(Problem::NotPassive);
    }
    if sight.desktop == Desktop::Elsewhere {
        found.push(Problem::OnAnotherDesktop);
    }
    if sight.placed() == Some(false) {
        found.push(Problem::Misplaced);
    }
    if sight.emblem_in_work_area() == Some(false) {
        found.push(Problem::OffWorkArea);
    }
    if sight.over_emblem > 0 {
        found.push(Problem::Covered(sight.over_emblem));
    }
    if let Some(alpha) = sight.alpha
        && alpha < u8::MAX
    {
        found.push(Problem::Faded(alpha));
    }
    if sight.foreground == Foreground::Overlay {
        found.push(Problem::Focused);
    }
    if let Some(webview) = sight.webview {
        if !webview.visible {
            found.push(Problem::WebviewHidden);
        }
        // A minimized window has no client area to fill; wry fits the webview again when the
        // window is restored.
        if !webview.fitted && !sight.minimized {
            found.push(Problem::WebviewUnfitted);
        }
    }
    found
}

impl Problem {
    /// What the problem is, after "the window may be out of sight:".
    fn text(self) -> String {
        match self {
            Problem::Hidden => "hidden".into(),
            Problem::Minimized => "minimized".into(),
            Problem::Cloaked(flags) => format!("cloaked by {}", cloakers(flags)),
            Problem::NotTopmost => "not topmost".into(),
            Problem::NotPassive => "without its passive style".into(),
            Problem::OnAnotherDesktop => "on another virtual desktop".into(),
            Problem::Misplaced => "not where it was placed".into(),
            Problem::OffWorkArea => "with the emblem off the work area".into(),
            Problem::Covered(count) => format!("under {count} window(s) over the emblem"),
            Problem::Faded(alpha) => format!("faded to alpha {alpha}"),
            Problem::Focused => "the foreground window".into(),
            Problem::WebviewHidden => "with its webview hidden".into(),
            Problem::WebviewUnfitted => "with its webview not filling it".into(),
        }
    }
}

/// Who cloaked the window, from the `DWMWA_CLOAKED` flags.
fn cloakers(flags: u32) -> String {
    let mut who = Vec::new();
    if flags & CLOAKED_SHELL != 0 {
        who.push("the shell".to_owned());
    }
    if flags & CLOAKED_APP != 0 {
        who.push("the app".to_owned());
    }
    if flags & CLOAKED_INHERITED != 0 {
        who.push("its owner".to_owned());
    }
    let other = flags & !(CLOAKED_SHELL | CLOAKED_APP | CLOAKED_INHERITED);
    if other != 0 {
        who.push(format!("0x{other:X}"));
    }
    who.join(" and ")
}

// ---------- what the log says ----------

fn yes_no(value: bool) -> &'static str {
    if value { "yes" } else { "no" }
}

fn known(value: Option<bool>) -> &'static str {
    value.map_or("unknown", yes_no)
}

fn rect_text(rect: Rect) -> String {
    format!("{},{} {}x{}", rect.x, rect.y, rect.w, rect.h)
}

/// The window's state on one line, as `key=value` pairs, made of numbers and fixed words only.
pub(super) fn state_line(sight: &Sight) -> String {
    let cloaked = match sight.cloaked {
        None => "unknown".to_owned(),
        Some(0) => "no".to_owned(),
        Some(flags) => {
            let mut who = Vec::new();
            for (flag, name) in [
                (CLOAKED_SHELL, "shell"),
                (CLOAKED_APP, "app"),
                (CLOAKED_INHERITED, "inherited"),
            ] {
                if flags & flag != 0 {
                    who.push(name.to_owned());
                }
            }
            let other = flags & !(CLOAKED_SHELL | CLOAKED_APP | CLOAKED_INHERITED);
            if other != 0 {
                who.push(format!("0x{other:X}"));
            }
            who.join("+")
        }
    };
    let alpha = sight
        .alpha
        .map_or_else(|| "none".to_owned(), |alpha| alpha.to_string());
    let desktop = match sight.desktop {
        Desktop::Current { tracked: true } => "current(tracked)".to_owned(),
        Desktop::Current { tracked: false } => "current(untracked)".to_owned(),
        Desktop::Elsewhere => "elsewhere".to_owned(),
        Desktop::Unknown(code) => format!("unknown(0x{:08X})", code as u32),
    };
    let work = sight.placement.map_or_else(
        || "unknown".to_owned(),
        |placement| rect_text(placement.work),
    );
    let foreground = match sight.foreground {
        Foreground::None => "none",
        Foreground::Overlay => "overlay",
        Foreground::Above => "above",
        Foreground::Below => "below",
        Foreground::Unknown => "unknown",
    };
    let webview = match sight.webview {
        None => "unknown".to_owned(),
        Some(webview) => format!(
            "{},{}",
            if webview.visible { "visible" } else { "hidden" },
            if webview.fitted { "fitted" } else { "unfitted" }
        ),
    };
    format!(
        "visible={} minimized={} cloaked={cloaked} topmost={} passive={} ex_style=0x{:08X} \
         alpha={alpha} desktop={desktop} rect={} placed={} work={work} emblem_in_work={} \
         over_emblem={} see_through={} foreground={foreground} webview={webview}",
        yes_no(sight.visible),
        yes_no(sight.minimized),
        yes_no(sight.topmost),
        yes_no(sight.passive),
        sight.ex_style,
        rect_text(sight.rect),
        known(sight.placed()),
        known(sight.emblem_in_work_area()),
        sight.over_emblem,
        sight.see_through,
    )
}

/// The problems as a list in words.
pub(super) fn problems_text(problems: &[Problem]) -> String {
    problems
        .iter()
        .map(|problem| problem.text())
        .collect::<Vec<_>>()
        .join(", ")
}

/// How long a check took, for the log.
fn took(elapsed: std::time::Duration) -> String {
    format!("{:.1} ms", elapsed.as_secs_f64() * 1000.0)
}

// ---------- the window itself ----------

#[cfg(windows)]
pub(super) use native::check;

#[cfg(not(windows))]
pub(super) fn check(_window: &tauri::WebviewWindow, _hwnd: isize, _placement: Option<Placement>) {}

#[cfg(windows)]
mod native {
    use std::{
        cell::{Cell, RefCell},
        ffi::c_void,
        time::Instant,
    };

    use tauri::WebviewWindow;
    use webview2_com::Microsoft::Web::WebView2::Win32::ICoreWebView2Controller;
    use windows::{
        Win32::{
            Foundation::{HWND, POINT, RECT},
            Graphics::{
                Dwm::{DWMWA_CLOAKED, DwmGetWindowAttribute},
                Gdi::ClientToScreen,
            },
            System::Com::{CLSCTX_ALL, CoCreateInstance},
            UI::{
                Shell::{IVirtualDesktopManager, VirtualDesktopManager},
                WindowsAndMessaging::{
                    GW_HWNDPREV, GWL_EXSTYLE, GWL_STYLE, GetClientRect, GetForegroundWindow,
                    GetLayeredWindowAttributes, GetWindow, GetWindowLongPtrW, GetWindowRect,
                    IsIconic, IsWindowVisible, LAYERED_WINDOW_ATTRIBUTES_FLAGS, LWA_ALPHA,
                    WS_EX_TOPMOST, WS_EX_TRANSPARENT, WS_VISIBLE,
                },
            },
        },
        core::{BOOL, GUID},
    };

    use super::{
        Desktop, Foreground, Sight, WebviewSight, contains, emblem_centre, problems, problems_text,
        state_line, took,
    };
    use crate::overlay::{Placement, Rect, passive_ex_style};

    /// How many windows above the overlay are looked at, at most. There are rarely more than a
    /// few dozen; the bound keeps a z-order that changes under the walk from making it endless.
    const WALK_MAX: usize = 1024;

    thread_local! {
        /// The shell's virtual desktop manager. It is made on the main thread, which COM runs as
        /// a single-threaded apartment (tao joins one there before it hides a window from the
        /// taskbar, and wry before it makes a webview), and it is used there only. After a call
        /// that fails it is made again, because explorer may have restarted.
        static DESKTOPS: RefCell<Option<IVirtualDesktopManager>> = const { RefCell::new(None) };
        /// Whether a check is running on this thread. A call into the shell may let window
        /// messages in while it waits, and with them the next check, which then gives way.
        static CHECKING: Cell<bool> = const { Cell::new(false) };
    }

    /// Marks a check as running until it is dropped.
    struct Checking;

    impl Checking {
        fn start() -> Option<Self> {
            (!CHECKING.replace(true)).then_some(Checking)
        }
    }

    impl Drop for Checking {
        fn drop(&mut self) {
            CHECKING.set(false);
        }
    }

    /// Looks at the overlay window `hwnd`, which `placement` says where it was just put, and
    /// writes what it saw to the log. It runs on the main thread, which owns the window and the
    /// webview's controller (wry's `with_webview` hands it over there); the caller does not wait.
    pub(in crate::overlay) fn check(
        window: &WebviewWindow,
        hwnd: isize,
        placement: Option<Placement>,
    ) {
        let posted = window.with_webview(move |webview| {
            let Some(_checking) = Checking::start() else {
                log::debug!("overlay: a window check is already running");
                return;
            };
            let started = Instant::now();
            let controller = webview.controller();
            // SAFETY: `hwnd` is the overlay window, which lives as long as the app, and this runs
            // on the main thread, which owns it and the controller.
            let sight = unsafe { look(HWND(hwnd as *mut c_void), placement, &controller) };
            let wrong = problems(&sight);
            let elapsed = took(started.elapsed());
            if wrong.is_empty() {
                log::debug!("overlay: window check in {elapsed}: {}", state_line(&sight));
            } else {
                log::info!(
                    "overlay: the window may be out of sight ({}): {} (in {elapsed})",
                    problems_text(&wrong),
                    state_line(&sight)
                );
            }
        });
        if let Err(err) = posted {
            log::warn!("could not check whether the overlay window can be seen: {err}");
        }
    }

    /// What the window looks like now. It only reads.
    ///
    /// # Safety
    ///
    /// `hwnd` must be the overlay window, and the caller its thread.
    unsafe fn look(
        hwnd: HWND,
        placement: Option<Placement>,
        controller: &ICoreWebView2Controller,
    ) -> Sight {
        // SAFETY: the caller owns `hwnd`; the other windows are only read, and a window that goes
        // away during the walk makes a call fail, which ends the walk.
        unsafe {
            let ex_style = GetWindowLongPtrW(hwnd, GWL_EXSTYLE);
            let rect = window_rect(hwnd).unwrap_or(Rect {
                x: 0,
                y: 0,
                w: 0,
                h: 0,
            });
            let above = look_above(hwnd, emblem_centre(rect, placement.map(|p| p.canvas)));
            Sight {
                visible: IsWindowVisible(hwnd).as_bool(),
                minimized: IsIconic(hwnd).as_bool(),
                cloaked: cloaked(hwnd),
                ex_style: ex_style as u32,
                topmost: ex_style & WS_EX_TOPMOST.0 as isize != 0,
                passive: passive_ex_style(ex_style) == ex_style,
                alpha: layered_alpha(hwnd),
                desktop: desktop(hwnd),
                rect,
                placement,
                over_emblem: above.covering,
                see_through: above.see_through,
                foreground: above.foreground,
                webview: webview_sight(hwnd, controller),
            }
        }
    }

    /// What lies above the overlay in the z-order.
    struct Above {
        covering: u32,
        see_through: u32,
        foreground: Foreground,
    }

    /// Walks up the z-order from the overlay: the visible windows over the emblem's `centre`
    /// (those that let the mouse through counted apart), and whether the foreground window is one
    /// of the windows above.
    unsafe fn look_above(hwnd: HWND, centre: (i32, i32)) -> Above {
        // SAFETY: only reads; a handle that dies during the walk makes `GetWindow` fail.
        unsafe {
            let foreground = GetForegroundWindow();
            let mut covering = 0;
            let mut see_through = 0;
            let mut foreground_above = false;
            let mut walked_all = false;
            let mut next = GetWindow(hwnd, GW_HWNDPREV);
            for _ in 0..WALK_MAX {
                let Ok(other) = next else {
                    walked_all = true;
                    break;
                };
                if other == foreground {
                    foreground_above = true;
                }
                if IsWindowVisible(other).as_bool()
                    && cloaked(other).unwrap_or(0) == 0
                    && window_rect(other).is_some_and(|rect| contains(rect, centre))
                {
                    if GetWindowLongPtrW(other, GWL_EXSTYLE) & WS_EX_TRANSPARENT.0 as isize != 0 {
                        see_through += 1;
                    } else {
                        covering += 1;
                    }
                }
                next = GetWindow(other, GW_HWNDPREV);
            }
            let foreground = if foreground.is_invalid() {
                Foreground::None
            } else if foreground == hwnd {
                Foreground::Overlay
            } else if foreground_above {
                Foreground::Above
            } else if walked_all {
                Foreground::Below
            } else {
                Foreground::Unknown
            };
            Above {
                covering,
                see_through,
                foreground,
            }
        }
    }

    unsafe fn window_rect(hwnd: HWND) -> Option<Rect> {
        let mut rect = RECT::default();
        // SAFETY: `rect` is a valid place for the answer.
        unsafe { GetWindowRect(hwnd, &mut rect) }.ok()?;
        Some(Rect {
            x: rect.left,
            y: rect.top,
            w: rect.right - rect.left,
            h: rect.bottom - rect.top,
        })
    }

    /// `DWMWA_CLOAKED`, or `None` if DWM does not say.
    unsafe fn cloaked(hwnd: HWND) -> Option<u32> {
        let mut cloaked = 0u32;
        // SAFETY: the attribute is a DWORD, and `cloaked` has room for one.
        unsafe {
            DwmGetWindowAttribute(
                hwnd,
                DWMWA_CLOAKED,
                (&raw mut cloaked).cast(),
                size_of::<u32>() as u32,
            )
        }
        .ok()?;
        Some(cloaked)
    }

    /// The layered window's alpha, if someone set one with `SetLayeredWindowAttributes`.
    unsafe fn layered_alpha(hwnd: HWND) -> Option<u8> {
        let mut alpha = 0u8;
        let mut flags = LAYERED_WINDOW_ATTRIBUTES_FLAGS::default();
        // SAFETY: both out-parameters point to valid places; the colour key is not asked for.
        unsafe {
            GetLayeredWindowAttributes(hwnd, None, Some(&raw mut alpha), Some(&raw mut flags))
        }
        .ok()?;
        (flags.0 & LWA_ALPHA.0 != 0).then_some(alpha)
    }

    /// The shell's virtual desktop manager, made once on this thread (see `DESKTOPS`).
    fn desktops() -> windows::core::Result<IVirtualDesktopManager> {
        if let Some(manager) = DESKTOPS.with_borrow(Clone::clone) {
            return Ok(manager);
        }
        // SAFETY: COM runs on this thread (see `DESKTOPS`).
        let manager: IVirtualDesktopManager =
            unsafe { CoCreateInstance(&VirtualDesktopManager, None, CLSCTX_ALL) }?;
        DESKTOPS.set(Some(manager.clone()));
        Ok(manager)
    }

    /// Where the window is among the virtual desktops.
    fn desktop(hwnd: HWND) -> Desktop {
        let manager = match desktops() {
            Ok(manager) => manager,
            Err(err) => return Desktop::Unknown(err.code().0),
        };
        // SAFETY: the manager is used on the thread that made it, for a top-level window.
        match unsafe { manager.IsWindowOnCurrentVirtualDesktop(hwnd) } {
            Ok(on) if on.as_bool() => Desktop::Current {
                tracked: desktop_id(&manager, hwnd).is_some(),
            },
            Ok(_) => Desktop::Elsewhere,
            Err(err) => {
                DESKTOPS.set(None);
                Desktop::Unknown(err.code().0)
            }
        }
    }

    /// The desktop that `hwnd` belongs to. A window that the shell does not track (a tool window,
    /// the taskbar, the desktop) has none: the call fails with TYPE_E_ELEMENTNOTFOUND, or gives
    /// the all-zero id.
    fn desktop_id(manager: &IVirtualDesktopManager, hwnd: HWND) -> Option<GUID> {
        // SAFETY: as in `desktop`.
        unsafe { manager.GetWindowDesktopId(hwnd) }
            .ok()
            .filter(|id| *id != GUID::zeroed())
    }

    /// The window that WebView2 draws in, which wry made inside the overlay window.
    unsafe fn host_window(controller: &ICoreWebView2Controller) -> Option<HWND> {
        let mut host = HWND::default();
        // SAFETY: `host` is a valid place for the answer.
        unsafe { controller.ParentWindow(&mut host) }.ok()?;
        (!host.is_invalid()).then_some(host)
    }

    unsafe fn client_size(hwnd: HWND) -> Option<(i32, i32)> {
        let mut client = RECT::default();
        // SAFETY: `client` is a valid place for the answer.
        unsafe { GetClientRect(hwnd, &mut client) }.ok()?;
        Some((client.right - client.left, client.bottom - client.top))
    }

    /// Whether the webview is shown and fills the window, as wry leaves it after a resize: its
    /// bounds are the whole client area, and the window that hosts it is visible, at the client
    /// area's corner and of its size.
    unsafe fn webview_sight(
        hwnd: HWND,
        controller: &ICoreWebView2Controller,
    ) -> Option<WebviewSight> {
        // SAFETY: the controller is used on its thread; every out-parameter is a valid place.
        unsafe {
            let mut visible = BOOL::default();
            controller.IsVisible(&mut visible).ok()?;
            let mut bounds = RECT::default();
            controller.Bounds(&mut bounds).ok()?;
            let (width, height) = client_size(hwnd)?;
            let host = host_window(controller)?;
            let mut corner = POINT::default();
            if !ClientToScreen(hwnd, &mut corner).as_bool() {
                return None;
            }
            let host_rect = window_rect(host)?;
            let shown = GetWindowLongPtrW(host, GWL_STYLE) & WS_VISIBLE.0 as isize != 0;
            let fitted = bounds
                == RECT {
                    left: 0,
                    top: 0,
                    right: width,
                    bottom: height,
                }
                && host_rect
                    == Rect {
                        x: corner.x,
                        y: corner.y,
                        w: width,
                        h: height,
                    }
                && shown;
            Some(WebviewSight {
                visible: visible.as_bool(),
                fitted,
            })
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The overlay on the owner's screen (work area 1920x1032, scale 1, emblem 150): as it
    /// should be.
    fn in_sight() -> Sight {
        let placement = super::super::placement(
            Rect {
                x: 0,
                y: 0,
                w: 1920,
                h: 1032,
            },
            150,
            1.0,
        );
        Sight {
            visible: true,
            minimized: false,
            cloaked: Some(0),
            ex_style: 0x0808_00AC,
            topmost: true,
            passive: true,
            alpha: None,
            desktop: Desktop::Current { tracked: false },
            rect: placement.window,
            placement: Some(placement),
            over_emblem: 0,
            see_through: 0,
            foreground: Foreground::Below,
            webview: Some(WebviewSight {
                visible: true,
                fitted: true,
            }),
        }
    }

    #[test]
    fn a_window_in_sight_has_nothing_wrong() {
        assert_eq!(problems(&in_sight()), []);
    }

    #[test]
    fn the_emblem_centre_is_the_middle_of_the_canvas_in_the_corner() {
        let sight = in_sight();
        // The window is 1385,808 540x235 and its 195 px canvas sits in the bottom-right corner.
        assert_eq!(emblem_centre(sight.rect, Some(195)), (1828, 946));
        assert_eq!(sight.emblem_in_work_area(), Some(true));
        // without a placement, a canvas as tall as the window
        assert_eq!(emblem_centre(sight.rect, None), (1925 - 117, 1043 - 117));
    }

    #[test]
    fn a_point_on_the_right_or_bottom_edge_is_outside() {
        let rect = Rect {
            x: 10,
            y: 20,
            w: 100,
            h: 50,
        };
        assert!(contains(rect, (10, 20)));
        assert!(contains(rect, (109, 69)));
        assert!(!contains(rect, (110, 40)));
        assert!(!contains(rect, (50, 70)));
        assert!(!contains(rect, (9, 40)));
    }

    /// Changes a window in sight into one that is not.
    type Spoil = fn(&mut Sight);

    #[test]
    fn each_way_of_being_out_of_sight_is_found() {
        let cases: [(Spoil, Problem); 12] = [
            (|s| s.visible = false, Problem::Hidden),
            (|s| s.minimized = true, Problem::Minimized),
            (
                |s| s.cloaked = Some(CLOAKED_SHELL),
                Problem::Cloaked(CLOAKED_SHELL),
            ),
            (|s| s.topmost = false, Problem::NotTopmost),
            (|s| s.passive = false, Problem::NotPassive),
            (
                |s| s.desktop = Desktop::Elsewhere,
                Problem::OnAnotherDesktop,
            ),
            (|s| s.rect.x -= 300, Problem::Misplaced),
            (|s| s.over_emblem = 2, Problem::Covered(2)),
            (|s| s.alpha = Some(0), Problem::Faded(0)),
            (|s| s.foreground = Foreground::Overlay, Problem::Focused),
            (
                |s| {
                    s.webview = Some(WebviewSight {
                        visible: false,
                        fitted: true,
                    })
                },
                Problem::WebviewHidden,
            ),
            (
                |s| {
                    s.webview = Some(WebviewSight {
                        visible: true,
                        fitted: false,
                    })
                },
                Problem::WebviewUnfitted,
            ),
        ];
        for (spoil, problem) in cases {
            let mut sight = in_sight();
            spoil(&mut sight);
            assert_eq!(problems(&sight), [problem], "{sight:?}");
        }
    }

    #[test]
    fn a_window_moved_off_the_screen_is_misplaced_and_off_the_work_area() {
        let mut sight = in_sight();
        sight.rect.x = 2400; // where a monitor that went away was
        assert_eq!(problems(&sight), [Problem::Misplaced, Problem::OffWorkArea]);
    }

    #[test]
    fn what_cannot_be_known_is_not_a_problem() {
        let mut sight = in_sight();
        sight.cloaked = None;
        sight.desktop = Desktop::Unknown(0x8007_06BA_u32 as i32);
        sight.placement = None;
        sight.foreground = Foreground::Unknown;
        sight.webview = None;
        assert_eq!(problems(&sight), []);
    }

    #[test]
    fn see_through_overlays_above_and_an_opaque_alpha_are_not_a_problem() {
        let mut sight = in_sight();
        sight.see_through = 3;
        sight.alpha = Some(255);
        sight.foreground = Foreground::Above;
        assert_eq!(problems(&sight), []);
    }

    #[test]
    fn a_minimized_window_does_not_also_complain_that_its_webview_does_not_fill_it() {
        let mut sight = in_sight();
        sight.minimized = true;
        sight.rect = Rect {
            x: -32000,
            y: -32000,
            w: 160,
            h: 28,
        };
        sight.webview = Some(WebviewSight {
            visible: true,
            fitted: false,
        });
        assert_eq!(
            problems(&sight),
            [Problem::Minimized, Problem::Misplaced, Problem::OffWorkArea]
        );
    }

    #[test]
    fn the_state_line_is_numbers_and_fixed_words() {
        assert_eq!(
            state_line(&in_sight()),
            "visible=yes minimized=no cloaked=no topmost=yes passive=yes ex_style=0x080800AC \
             alpha=none desktop=current(untracked) rect=1385,808 540x235 placed=yes \
             work=0,0 1920x1032 emblem_in_work=yes over_emblem=0 see_through=0 \
             foreground=below webview=visible,fitted"
        );
        let mut sight = in_sight();
        sight.cloaked = Some(CLOAKED_SHELL | CLOAKED_APP | 0x10);
        sight.alpha = Some(0);
        sight.desktop = Desktop::Unknown(0x8002_802B_u32 as i32);
        sight.placement = None;
        sight.foreground = Foreground::Overlay;
        sight.webview = None;
        assert_eq!(
            state_line(&sight),
            "visible=yes minimized=no cloaked=shell+app+0x10 topmost=yes passive=yes \
             ex_style=0x080800AC alpha=0 desktop=unknown(0x8002802B) rect=1385,808 540x235 \
             placed=unknown work=unknown emblem_in_work=unknown over_emblem=0 see_through=0 \
             foreground=overlay webview=unknown"
        );
    }

    #[test]
    fn the_problems_read_as_a_list() {
        assert_eq!(
            problems_text(&[
                Problem::OnAnotherDesktop,
                Problem::Cloaked(CLOAKED_SHELL | CLOAKED_INHERITED),
                Problem::Covered(1),
            ]),
            "on another virtual desktop, cloaked by the shell and its owner, \
             under 1 window(s) over the emblem"
        );
        assert_eq!(
            problems_text(&[Problem::Cloaked(CLOAKED_APP | 0x20)]),
            "cloaked by the app and 0x20"
        );
    }

    #[test]
    fn the_check_never_reads_a_title_or_the_name_of_an_app() {
        // Only numbers and fixed words reach the log (the state has no text in it); this keeps
        // the calls that would read names out of the check.
        let source = include_str!("sight.rs").replace("\r\n", "\n");
        let production = source.split("#[cfg(test)]").next().unwrap();
        for call in [
            "GetWindowText",
            "InternalGetWindowText",
            "GetClassName",
            "RealGetWindowClass",
            "GetWindowModuleFileName",
            "GetModuleFileName",
            "QueryFullProcessImageName",
            "GetProcessImageFileName",
            "GetWindowThreadProcessId",
        ] {
            assert!(!production.contains(call), "the check calls {call}");
        }
    }
}
