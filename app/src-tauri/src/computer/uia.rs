//! The desktop on Windows: UI Automation to read windows and act on their elements, `SendInput`
//! where an element has no pattern for what is wanted.
//!
//! Every UI Automation object lives on one thread of its own ([`UiaDesktop`]), in the
//! multithreaded apartment like the learning module's client (`learn/uia.rs`), with its calls
//! bounded (a busy app makes UI Automation wait up to 20 s otherwise). The rest of the app hands it
//! jobs and waits for each, for a bounded time.
//!
//! - The listing (`EnumWindows`) keeps the visible, uncloaked, titled windows that are not tool
//!   windows. A UWP app's window belongs to `ApplicationFrameHost.exe`: the app is the process of
//!   its `CoreWindow` child, and a frame without one (a suspended app) is left out. An app goes by
//!   its executable's file description (`Microsoft Excel`), or its file name.
//! - A look builds one cache request for the properties and patterns a snapshot needs, and walks
//!   the control view one level at a time (`FindAllBuildCache` with `TreeScope_Children`), within
//!   the caps and four seconds ([`tree::build`]). The elements are kept until the next look at the
//!   window, or the end of the run.
//! - A step checks the element again first (a password field is refused, whatever the look said,
//!   and so is a click on or text for an element now called like a sign-in, a payment field or a
//!   code), brings the window to the front, and uses UI Automation's patterns; a click at the
//!   element's centre only when there is none, and only when the point is the window's (nothing,
//!   the overlay least of all, covers it). Typing into an edit field sets its value; elsewhere the
//!   text is typed at the caret, as Unicode, in batches, each after the element with the focus is
//!   read once more (it must be in the window, and neither a password field nor a sign-in, a
//!   payment field or a code); a line break is Enter, typed only in a step the owner said yes to.
//!   Keys go the same way. Keys or typed text for an element named go only once that element has
//!   taken the focus and the live focus is it (or the edit field of the combo box named): one
//!   that cannot take it (a text, a pane, a disabled button) fails with `focus`, and nothing goes
//!   to whatever had the focus before. Without the owner's yes (`unconfirmed`), nothing that
//!   sends or deletes is judged anew on the live focus and refused: Enter or Space on an element
//!   called like Send, Delete or Buy, Enter in a message box, the chords that send, a typed line
//!   break, and Delete, Backspace or Ctrl+D anywhere but in a text field (on a file or a mail they
//!   delete it) ([`policy::may_reach`]). Every input it sends carries Raphael's marker
//!   (`handy_keys::RAPHAEL_INPUT_MARKER`) and is injected, so the watch on the owner's input and
//!   the hotkeys never take it for the owner's.
//! - The window is in front only when the window Windows has in front is the step's own, or one
//!   it owns (a dialog of its): another window of the same process (a second Chrome window) is
//!   not, and the step brings its own to the front first.
//! - Every job is for a run ([`Ticket`]): one whose run is over by the time it comes up does
//!   nothing, and one under way checks again just before each change it makes (the window
//!   brought to the front, a pattern called, a value set, each batch of input sent), so that once
//!   the run is stopped nothing more reaches the desktop.
//!
//! Nothing here logs a title, a name, a value or what is typed.

use std::collections::HashMap;
use std::ffi::c_void;
use std::sync::{Mutex, mpsc};
use std::time::{Duration, Instant};

use windows::Win32::Foundation::{HWND, LPARAM, POINT, RECT};
use windows::Win32::Graphics::Dwm::{DWMWA_CLOAKED, DwmGetWindowAttribute};
use windows::Win32::Storage::FileSystem::{
    GetFileVersionInfoSizeW, GetFileVersionInfoW, VerQueryValueW,
};
use windows::Win32::System::Com::{
    CLSCTX_INPROC_SERVER, COINIT_MULTITHREADED, CoCreateInstance, CoInitializeEx, CoUninitialize,
};
use windows::Win32::System::Threading::{
    OpenProcess, PROCESS_NAME_WIN32, PROCESS_QUERY_LIMITED_INFORMATION, QueryFullProcessImageNameW,
};
use windows::Win32::UI::Accessibility::{
    CUIAutomation8, ExpandCollapseState_Collapsed, ExpandCollapseState_Expanded,
    ExpandCollapseState_PartiallyExpanded, IUIAutomation, IUIAutomation2,
    IUIAutomationCacheRequest, IUIAutomationCondition, IUIAutomationElement,
    IUIAutomationExpandCollapsePattern, IUIAutomationInvokePattern, IUIAutomationScrollPattern,
    IUIAutomationSelectionItemPattern, IUIAutomationTogglePattern, IUIAutomationValuePattern,
    ScrollAmount, ScrollAmount_LargeDecrement, ScrollAmount_LargeIncrement, ScrollAmount_NoAmount,
    ToggleState_Indeterminate, ToggleState_Off, ToggleState_On, TreeScope_Children,
    UIA_BoundingRectanglePropertyId, UIA_ComboBoxControlTypeId, UIA_ControlTypePropertyId,
    UIA_DocumentControlTypeId, UIA_E_TIMEOUT, UIA_EditControlTypeId,
    UIA_ExpandCollapseExpandCollapseStatePropertyId, UIA_ExpandCollapsePatternId,
    UIA_HasKeyboardFocusPropertyId, UIA_InvokePatternId, UIA_IsEnabledPropertyId,
    UIA_IsOffscreenPropertyId, UIA_IsPasswordPropertyId, UIA_NamePropertyId, UIA_ScrollPatternId,
    UIA_SelectionItemIsSelectedPropertyId, UIA_SelectionItemPatternId, UIA_SpinnerControlTypeId,
    UIA_TogglePatternId, UIA_ToggleToggleStatePropertyId, UIA_ValueIsReadOnlyPropertyId,
    UIA_ValuePatternId, UIA_ValueValuePropertyId,
};
use windows::Win32::UI::Input::KeyboardAndMouse::{
    INPUT, INPUT_0, INPUT_KEYBOARD, INPUT_MOUSE, KEYBD_EVENT_FLAGS, KEYBDINPUT,
    KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE, MOUSE_EVENT_FLAGS,
    MOUSEEVENTF_ABSOLUTE, MOUSEEVENTF_HWHEEL, MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP,
    MOUSEEVENTF_MOVE, MOUSEEVENTF_VIRTUALDESK, MOUSEEVENTF_WHEEL, MOUSEINPUT, SendInput,
    VIRTUAL_KEY,
};
use windows::Win32::UI::WindowsAndMessaging::{
    EnumChildWindows, EnumWindows, GA_ROOT, GA_ROOTOWNER, GWL_EXSTYLE, GetAncestor, GetClassNameW,
    GetForegroundWindow, GetSystemMetrics, GetWindowLongW, GetWindowTextW,
    GetWindowThreadProcessId, IsIconic, IsWindow, IsWindowVisible, SM_CXVIRTUALSCREEN,
    SM_CYVIRTUALSCREEN, SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SW_RESTORE, SetForegroundWindow,
    ShowWindow, WS_EX_TOOLWINDOW, WindowFromPoint,
};
use windows::core::{BOOL, BSTR, HSTRING, Interface, PCWSTR, PWSTR, w};

use super::keys::{self, Chord, Typed};
use super::policy::{self, Press, Reach};
use super::tree::{self, Caps, Entry, Props, UiNode};
use super::{Action, Desktop, Direction, Failure, Step, Ticket, WindowInfo, breaks_lines};

/// How long one job of the desktop's thread may take before its caller gives up on it.
const JOB_TIMEOUT: Duration = Duration::from_secs(12);
/// How long a look may walk the tree.
const LOOK_BUDGET: Duration = Duration::from_secs(4);
/// UI Automation's own bounds on one call into an app.
const CALL_TIMEOUT_MS: u32 = 2_000;
/// The most windows a listing gives.
const MAX_WINDOWS: usize = 40;
/// How long a window is given to come to the front, and an app to take a step in.
const SETTLE: Duration = Duration::from_millis(120);
/// The most ancestors looked through for one that scrolls.
const SCROLL_ANCESTORS: usize = 6;
/// The most ancestors of the element with the focus looked through for the combo box or spinner
/// the step named, which gives its focus to a part of it (its edit field, its list).
const FOCUS_HOLDER_ANCESTORS: usize = 3;

type Job = Box<dyn FnOnce(&mut State) + Send>;

/// The desktop's thread, reached through jobs.
pub struct UiaDesktop {
    jobs: Mutex<mpsc::Sender<Job>>,
}

impl UiaDesktop {
    /// Starts the thread with its UI Automation client. None if there is none to be had.
    pub fn start() -> Option<Self> {
        let (jobs, inbox) = mpsc::channel::<Job>();
        let (ready, started) = mpsc::channel();
        std::thread::Builder::new()
            .name("raphael-computer".into())
            .spawn(move || {
                // SAFETY: this thread joins the multithreaded apartment once and leaves it last.
                let joined = unsafe { CoInitializeEx(None, COINIT_MULTITHREADED) }.is_ok();
                let state = State::new();
                let _ = ready.send(state.is_some());
                if let Some(mut state) = state {
                    while let Ok(job) = inbox.recv() {
                        job(&mut state);
                    }
                    drop(state);
                }
                if joined {
                    // SAFETY: balances the CoInitializeEx above, after every object is gone.
                    unsafe { CoUninitialize() };
                }
            })
            .ok()?;
        if started.recv().unwrap_or(false) {
            Some(Self {
                jobs: Mutex::new(jobs),
            })
        } else {
            log::warn!("computer: no UI Automation client: the desktop cannot be operated");
            None
        }
    }

    /// Runs `work` on the desktop's thread and waits for what it returns, for a bounded time.
    fn run<T: Send + 'static>(
        &self,
        work: impl FnOnce(&mut State) -> T + Send + 'static,
    ) -> Option<T> {
        let (done, result) = mpsc::channel();
        let job: Job = Box::new(move |state| {
            let _ = done.send(work(state));
        });
        crate::rpc::lock(&self.jobs).send(job).ok()?;
        let outcome = result.recv_timeout(JOB_TIMEOUT).ok();
        if outcome.is_none() {
            log::warn!("computer: a job of the desktop's thread did not finish in time");
        }
        outcome
    }
}

impl Desktop for UiaDesktop {
    fn windows(&self) -> Vec<WindowInfo> {
        self.run(|state| state.windows()).unwrap_or_default()
    }

    fn window(&self, id: i64) -> Option<WindowInfo> {
        self.run(move |state| state.window(id)).flatten()
    }

    fn look(&self, id: i64, ticket: &Ticket) -> Result<(Vec<Entry>, bool), Failure> {
        let ticket = ticket.clone();
        self.run(move |state| state.look(id, &ticket))
            .unwrap_or(Err(Failure::Failed))
    }

    fn act(&self, step: &Step, ticket: &Ticket) -> Result<&'static str, Failure> {
        let (step, ticket) = (step.clone(), ticket.clone());
        self.run(move |state| state.act(&step, &ticket))
            .unwrap_or(Err(Failure::Failed))
    }

    fn focus(&self, id: i64, ticket: &Ticket) -> Result<(), Failure> {
        let ticket = ticket.clone();
        self.run(move |state| {
            ticket.check()?;
            let hwnd = state.hwnd(id)?;
            bring_to_front(hwnd, &ticket)
        })
        .unwrap_or(Err(Failure::Failed))
    }

    fn forget(&self) {
        let _ = self.run(|state| {
            state.looks.clear();
            state.apps.clear();
        });
    }
}

/// What the desktop's thread holds.
struct State {
    automation: IUIAutomation,
    cache: IUIAutomationCacheRequest,
    view: IUIAutomationCondition,
    /// The elements of the latest look at each window, in the order of their ids.
    looks: HashMap<i64, Vec<IUIAutomationElement>>,
    /// Each process's app: executable, name, elevated.
    apps: HashMap<u32, (String, String, bool)>,
}

impl State {
    fn new() -> Option<Self> {
        // SAFETY: the thread is in an apartment; the objects stay on it.
        unsafe {
            let automation: IUIAutomation =
                CoCreateInstance(&CUIAutomation8, None, CLSCTX_INPROC_SERVER).ok()?;
            if let Ok(bounded) = automation.cast::<IUIAutomation2>() {
                let _ = bounded.SetConnectionTimeout(CALL_TIMEOUT_MS);
                let _ = bounded.SetTransactionTimeout(CALL_TIMEOUT_MS);
            }
            let cache = automation.CreateCacheRequest().ok()?;
            for property in [
                UIA_ControlTypePropertyId,
                UIA_NamePropertyId,
                UIA_IsEnabledPropertyId,
                UIA_HasKeyboardFocusPropertyId,
                UIA_IsPasswordPropertyId,
                UIA_IsOffscreenPropertyId,
                UIA_BoundingRectanglePropertyId,
                UIA_ValueValuePropertyId,
                UIA_ValueIsReadOnlyPropertyId,
                UIA_ToggleToggleStatePropertyId,
                UIA_ExpandCollapseExpandCollapseStatePropertyId,
                UIA_SelectionItemIsSelectedPropertyId,
            ] {
                cache.AddProperty(property).ok()?;
            }
            for pattern in [
                UIA_InvokePatternId,
                UIA_TogglePatternId,
                UIA_SelectionItemPatternId,
                UIA_ExpandCollapsePatternId,
                UIA_ValuePatternId,
                UIA_ScrollPatternId,
            ] {
                cache.AddPattern(pattern).ok()?;
            }
            let view = automation.ControlViewCondition().ok()?;
            Some(Self {
                automation,
                cache,
                view,
                looks: HashMap::new(),
                apps: HashMap::new(),
            })
        }
    }

    fn hwnd(&self, id: i64) -> Result<HWND, Failure> {
        let hwnd = HWND(id as isize as *mut c_void);
        // SAFETY: a plain query; any value may be asked about.
        if unsafe { IsWindow(Some(hwnd)) }.as_bool() {
            Ok(hwnd)
        } else {
            Err(Failure::Gone)
        }
    }

    fn windows(&mut self) -> Vec<WindowInfo> {
        let mut found: Vec<HWND> = Vec::new();
        // SAFETY: the callback only pushes to `found`, which outlives the call.
        unsafe {
            let _ = EnumWindows(Some(collect_window), LPARAM((&raw mut found) as isize));
        }
        let foreground = unsafe { GetForegroundWindow() };
        let mut windows: Vec<WindowInfo> = found
            .into_iter()
            .filter_map(|hwnd| self.describe(hwnd, foreground))
            .take(MAX_WINDOWS)
            .collect();
        windows.sort_by_key(|window| !window.foreground);
        windows
    }

    fn window(&mut self, id: i64) -> Option<WindowInfo> {
        let hwnd = self.hwnd(id).ok()?;
        // SAFETY: a plain query.
        let foreground = unsafe { GetForegroundWindow() };
        self.describe(hwnd, foreground)
    }

    /// A window as the listing gives it, if it is one an app shows.
    fn describe(&mut self, hwnd: HWND, foreground: HWND) -> Option<WindowInfo> {
        // SAFETY: plain queries of a window handle.
        unsafe {
            if !IsWindowVisible(hwnd).as_bool() || cloaked(hwnd) {
                return None;
            }
            if (GetWindowLongW(hwnd, GWL_EXSTYLE) as u32) & WS_EX_TOOLWINDOW.0 != 0 {
                return None;
            }
        }
        let title = policy::clean(&window_text(hwnd), 200);
        if title.is_empty() {
            return None;
        }
        let class = class_name(hwnd);
        let mut pid = 0u32;
        // SAFETY: a plain query.
        unsafe { GetWindowThreadProcessId(hwnd, Some(&raw mut pid)) };
        if class == "ApplicationFrameWindow" {
            pid = hosted_app(hwnd, pid)?;
        }
        if pid == 0 {
            return None;
        }
        let (exe, name, elevated) = self.app_of(pid);
        Some(WindowInfo {
            id: hwnd.0 as isize as i64,
            pid,
            exe,
            name,
            title,
            class,
            foreground: hwnd == foreground,
            elevated,
        })
    }

    fn app_of(&mut self, pid: u32) -> (String, String, bool) {
        self.apps
            .entry(pid)
            .or_insert_with(|| {
                let path = image_path(pid).unwrap_or_default();
                let exe = policy::exe_of(&path);
                let name = file_description(&path)
                    .map(|name| policy::clean(&name, 60))
                    .filter(|name| !name.is_empty())
                    .unwrap_or_else(|| policy::fallback_name(&exe));
                let elevated = crate::paste::describe_process(pid).1;
                (exe, name, elevated)
            })
            .clone()
    }

    fn look(&mut self, id: i64, ticket: &Ticket) -> Result<(Vec<Entry>, bool), Failure> {
        let hwnd = self.hwnd(id)?;
        // SAFETY: COM calls on this thread's objects.
        let root = unsafe {
            self.automation
                .ElementFromHandleBuildCache(hwnd, &self.cache)
        }
        .map_err(|_| Failure::Gone)?;
        let walker = Walker {
            cache: &self.cache,
            view: &self.view,
        };
        let began = Instant::now();
        let shot = tree::build(
            Node {
                element: root,
                walker: &walker,
            },
            Caps::default(),
            || began.elapsed() > LOOK_BUDGET,
        );
        // A run that ended while the look walked the tree keeps nothing: its elements were
        // forgotten, and must stay so.
        ticket.check()?;
        let elements = shot.nodes.into_iter().map(|node| node.element).collect();
        self.looks.insert(id, elements);
        log::debug!(
            "computer: a look of {} elements (cut: {})",
            shot.entries.len(),
            shot.truncated
        );
        Ok((shot.entries, shot.truncated))
    }

    fn element(&self, step: &Step) -> Result<Option<IUIAutomationElement>, Failure> {
        let Some(id) = step.element else {
            return Ok(None);
        };
        let elements = self.looks.get(&step.window).ok_or(Failure::Stale)?;
        let index = usize::try_from(id).map_err(|_| Failure::Stale)?;
        let element = elements
            .get(index.wrapping_sub(1))
            .ok_or(Failure::Stale)?
            .clone();
        // Asked again, now: the look may be old, and the element gone or changed.
        // SAFETY: COM calls on this thread's objects.
        match unsafe { element.CurrentIsPassword() } {
            Ok(password) if password.as_bool() => return Err(Failure::Password),
            Ok(_) => {}
            Err(_) => return Err(Failure::Stale),
        }
        // A click on it or text for it: the element is the step's target. Keys are checked on
        // the element with the focus, which they reach, and which must then be this one
        // (`check_focus`).
        if matches!(step.action, Action::Click | Action::Type(_)) && handoff(&element) {
            return Err(Failure::Handoff);
        }
        Ok(Some(element))
    }

    fn act(&mut self, step: &Step, ticket: &Ticket) -> Result<&'static str, Failure> {
        // A job that waited behind another (a slow look) for a run that ended meanwhile.
        ticket.check()?;
        let hwnd = self.hwnd(step.window)?;
        let element = self.element(step)?;
        bring_to_front(hwnd, ticket)?;
        // SAFETY: COM calls on this thread's objects, and input sent with SendInput.
        unsafe {
            match (&step.action, element) {
                (Action::Click, Some(element)) => click(&element, hwnd, ticket),
                (Action::Focus, Some(element)) => {
                    ticket.check()?;
                    element
                        .SetFocus()
                        .map(|()| "focus")
                        .map_err(|_| Failure::Failed)
                }
                (Action::Scroll(direction), Some(element)) => {
                    self.scroll(&element, *direction, hwnd, ticket)
                }
                (Action::Type(text), element) => {
                    self.type_text(element.as_ref(), text, step.confirmed, hwnd, ticket)
                }
                (Action::Keys(chord), element) => {
                    if let Some(element) = &element {
                        give_focus(element, ticket)?;
                    }
                    self.check_focus(hwnd, element.as_ref(), Press::Keys(*chord), step.confirmed)?;
                    send(ticket, &chord_inputs(*chord)).map(|()| "keyboard")
                }
                _ => Err(Failure::BadRequest),
            }
        }
    }

    /// What keys or typed text would reach, read live, just before they are sent: the window in
    /// front must be the step's, and the element with the focus must be the one the step named
    /// (`target`, if it named one), and may be what [`policy::may_reach`] lets `press` go to. An
    /// element with the focus that cannot be read is no element the keys may go to.
    fn check_focus(
        &self,
        hwnd: HWND,
        target: Option<&IUIAutomationElement>,
        press: Press,
        confirmed: bool,
    ) -> Result<(), Failure> {
        if !in_front(hwnd) {
            return Err(Failure::Failed);
        }
        // SAFETY: COM calls on this thread's objects.
        let reach = unsafe {
            let focused = self
                .automation
                .GetFocusedElement()
                .map_err(|_| Failure::Failed)?;
            let password = focused
                .CurrentIsPassword()
                .map_err(|_| Failure::Failed)?
                .as_bool();
            // A name that cannot be read is no name: the other checks still hold.
            let name = focused
                .CurrentName()
                .map(|name| name.to_string())
                .unwrap_or_default();
            let role = focused.CurrentControlType().map(|c| c.0).unwrap_or(0);
            let text_role = role == UIA_EditControlTypeId.0 || role == UIA_DocumentControlTypeId.0;
            Reach {
                password,
                handoff: policy::is_handoff_name(&name),
                on_target: target.is_none_or(|target| self.holds_focus(target, &focused)),
                edits_text: edits_text(&focused),
                sends: policy::is_confirm_name(&name),
                message_box: text_role && policy::is_message_name(&name),
            }
        };
        policy::may_reach(reach, press, confirmed)
    }

    /// Whether `focused`, the element with the focus, is `target`, or a part of a combo box or a
    /// spinner `target` is (which give their focus to their edit field). Any other element that
    /// holds the focus inside `target` is not it: a pane named while its Send button has the
    /// focus would let Enter press Send. An element that cannot be compared is not it.
    fn holds_focus(&self, target: &IUIAutomationElement, focused: &IUIAutomationElement) -> bool {
        // SAFETY: COM calls on this thread's objects, which this runs on.
        unsafe {
            let same = |a: &IUIAutomationElement, b: &IUIAutomationElement| -> bool {
                self.automation
                    .CompareElements(a, b)
                    .is_ok_and(|same| same.as_bool())
            };
            if same(target, focused) {
                return true;
            }
            let role = target.CurrentControlType().map(|c| c.0).unwrap_or(0);
            if role != UIA_ComboBoxControlTypeId.0 && role != UIA_SpinnerControlTypeId.0 {
                return false;
            }
            let Ok(walker) = self.automation.RawViewWalker() else {
                return false;
            };
            let mut current = walker.GetParentElement(focused).ok();
            for _ in 0..FOCUS_HOLDER_ANCESTORS {
                let Some(here) = current.take() else { break };
                if same(target, &here) {
                    return true;
                }
                current = walker.GetParentElement(&here).ok();
            }
            false
        }
    }

    unsafe fn type_text(
        &self,
        element: Option<&IUIAutomationElement>,
        text: &str,
        confirmed: bool,
        hwnd: HWND,
        ticket: &Ticket,
    ) -> Result<&'static str, Failure> {
        // SAFETY: COM calls on this thread's objects.
        unsafe {
            if let Some(element) = element {
                let role = element.CurrentControlType().map(|c| c.0).unwrap_or(0);
                // An edit field takes the text as its value (a line break in it is no Enter); a
                // document gets it at the caret.
                if (role == UIA_EditControlTypeId.0 || role == UIA_ComboBoxControlTypeId.0)
                    && let Ok(value) =
                        element.GetCurrentPatternAs::<IUIAutomationValuePattern>(UIA_ValuePatternId)
                    && value.CurrentIsReadOnly().is_ok_and(|r| !r.as_bool())
                {
                    ticket.check()?;
                    if value.SetValue(&BSTR::from(text)).is_ok() {
                        return Ok("value");
                    }
                }
            }
            // Typed keys press Enter for a line break, which may send a message or a form: only
            // in a step the owner said yes to (refused here before the focus moves, and by
            // `may_reach` before every batch).
            if breaks_lines(text) && !confirmed {
                return Err(Failure::Unconfirmed);
            }
            if let Some(element) = element {
                give_focus(element, ticket)?;
            }
        }
        let press = Press::Text {
            line_breaks: breaks_lines(text),
        };
        for batch in keys::typing(text) {
            self.check_focus(hwnd, element, press, confirmed)?;
            match batch {
                Typed::Units(units) => inject(ticket, &unit_inputs(&units))?,
                Typed::Enter => {
                    inject(ticket, &enter_inputs())?;
                    std::thread::sleep(SETTLE);
                }
            }
        }
        std::thread::sleep(SETTLE);
        Ok("keyboard")
    }

    unsafe fn scroll(
        &self,
        element: &IUIAutomationElement,
        direction: Direction,
        hwnd: HWND,
        ticket: &Ticket,
    ) -> Result<&'static str, Failure> {
        let (horizontal, vertical): (ScrollAmount, ScrollAmount) = match direction {
            Direction::Up => (ScrollAmount_NoAmount, ScrollAmount_LargeDecrement),
            Direction::Down => (ScrollAmount_NoAmount, ScrollAmount_LargeIncrement),
            Direction::Left => (ScrollAmount_LargeDecrement, ScrollAmount_NoAmount),
            Direction::Right => (ScrollAmount_LargeIncrement, ScrollAmount_NoAmount),
        };
        // SAFETY: COM calls on this thread's objects.
        unsafe {
            if let Ok(walker) = self.automation.RawViewWalker() {
                let mut current = Some(element.clone());
                for _ in 0..=SCROLL_ANCESTORS {
                    let Some(here) = current.take() else { break };
                    if let Ok(pattern) =
                        here.GetCurrentPatternAs::<IUIAutomationScrollPattern>(UIA_ScrollPatternId)
                    {
                        ticket.check()?;
                        if pattern.Scroll(horizontal, vertical).is_ok() {
                            return Ok("scroll");
                        }
                    }
                    current = walker.GetParentElement(&here).ok();
                }
            }
            let point = centre(element)?;
            if !point_is_windows(point, hwnd) {
                return Err(Failure::Failed);
            }
            let notch: i32 = 120 * 3;
            let (flags, amount) = match direction {
                Direction::Up => (MOUSEEVENTF_WHEEL, notch),
                Direction::Down => (MOUSEEVENTF_WHEEL, -notch),
                Direction::Left => (MOUSEEVENTF_HWHEEL, -notch),
                Direction::Right => (MOUSEEVENTF_HWHEEL, notch),
            };
            let mut inputs = vec![mouse_move(point)];
            inputs.push(mouse(flags, amount as u32, 0, 0));
            send(ticket, &inputs).map(|()| "wheel")
        }
    }
}

/// The cached view of an element, for [`tree::build`].
struct Walker<'a> {
    cache: &'a IUIAutomationCacheRequest,
    view: &'a IUIAutomationCondition,
}

struct Node<'a> {
    element: IUIAutomationElement,
    walker: &'a Walker<'a>,
}

impl UiNode for Node<'_> {
    fn props(&self) -> Props {
        let e = &self.element;
        // SAFETY: cached reads of an element of this thread.
        unsafe {
            let flag = |value: windows::core::Result<BOOL>| value.is_ok_and(|v| v.as_bool());
            let password = flag(e.CachedIsPassword());
            let mut actions = Vec::new();
            let mut state = None;
            let mut value = None;
            if e.GetCachedPatternAs::<IUIAutomationInvokePattern>(UIA_InvokePatternId)
                .is_ok()
            {
                actions.push("invoke");
            }
            if let Ok(toggle) =
                e.GetCachedPatternAs::<IUIAutomationTogglePattern>(UIA_TogglePatternId)
            {
                actions.push("toggle");
                state = match toggle.CachedToggleState() {
                    Ok(s) if s == ToggleState_On => Some("on"),
                    Ok(s) if s == ToggleState_Off => Some("off"),
                    Ok(s) if s == ToggleState_Indeterminate => Some("mixed"),
                    _ => None,
                };
            }
            if let Ok(item) = e
                .GetCachedPatternAs::<IUIAutomationSelectionItemPattern>(UIA_SelectionItemPatternId)
            {
                actions.push("select");
                if flag(item.CachedIsSelected()) {
                    state = Some("selected");
                }
            }
            if let Ok(expand) = e.GetCachedPatternAs::<IUIAutomationExpandCollapsePattern>(
                UIA_ExpandCollapsePatternId,
            ) {
                match expand.CachedExpandCollapseState() {
                    Ok(s)
                        if s == ExpandCollapseState_Expanded
                            || s == ExpandCollapseState_PartiallyExpanded =>
                    {
                        actions.push("expand");
                        state = Some("expanded");
                    }
                    Ok(s) if s == ExpandCollapseState_Collapsed => {
                        actions.push("expand");
                        state = Some("collapsed");
                    }
                    _ => {}
                }
            }
            if let Ok(pattern) =
                e.GetCachedPatternAs::<IUIAutomationValuePattern>(UIA_ValuePatternId)
            {
                if !flag(pattern.CachedIsReadOnly()) {
                    actions.push("value");
                }
                // A password field's value is never read.
                if !password {
                    value = pattern.CachedValue().ok().map(|v| v.to_string());
                }
            }
            if e.GetCachedPatternAs::<IUIAutomationScrollPattern>(UIA_ScrollPatternId)
                .is_ok()
            {
                actions.push("scroll");
            }
            let rect = e.CachedBoundingRectangle().ok().and_then(|r| {
                let (w, h) = (r.right - r.left, r.bottom - r.top);
                (w > 0 && h > 0).then_some([r.left, r.top, w, h])
            });
            Props {
                control_type: e.CachedControlType().map(|c| c.0).unwrap_or(50_025),
                name: e.CachedName().map(|n| n.to_string()).unwrap_or_default(),
                value,
                enabled: flag(e.CachedIsEnabled()),
                focused: flag(e.CachedHasKeyboardFocus()),
                password,
                offscreen: flag(e.CachedIsOffscreen()),
                state,
                actions,
                rect,
            }
        }
    }

    fn children(&self) -> Vec<Self> {
        // SAFETY: COM calls on this thread's objects.
        unsafe {
            let Ok(found) = self.element.FindAllBuildCache(
                TreeScope_Children,
                self.walker.view,
                self.walker.cache,
            ) else {
                return Vec::new();
            };
            let count = found.Length().unwrap_or(0).max(0);
            (0..count)
                .filter_map(|index| found.GetElement(index).ok())
                .map(|element| Node {
                    element,
                    walker: self.walker,
                })
                .collect()
        }
    }
}

/// A click: the element's pattern for it if it has one, else the mouse at its centre. The run's
/// ticket is checked before each pattern is called.
unsafe fn click(
    element: &IUIAutomationElement,
    hwnd: HWND,
    ticket: &Ticket,
) -> Result<&'static str, Failure> {
    // A pattern call that ran into UI Automation's time bound was made: the app is busy with it
    // (a button that opens a modal dialog does not return until the dialog closes).
    let made = |result: windows::core::Result<()>| match result {
        Ok(()) => true,
        Err(error) => error.code().0 as u32 == UIA_E_TIMEOUT,
    };
    // SAFETY: COM calls on this thread's objects.
    unsafe {
        if let Ok(invoke) =
            element.GetCurrentPatternAs::<IUIAutomationInvokePattern>(UIA_InvokePatternId)
        {
            ticket.check()?;
            if made(invoke.Invoke()) {
                return Ok("invoke");
            }
        }
        if let Ok(toggle) =
            element.GetCurrentPatternAs::<IUIAutomationTogglePattern>(UIA_TogglePatternId)
        {
            ticket.check()?;
            if made(toggle.Toggle()) {
                return Ok("toggle");
            }
        }
        if let Ok(item) = element
            .GetCurrentPatternAs::<IUIAutomationSelectionItemPattern>(UIA_SelectionItemPatternId)
        {
            ticket.check()?;
            if made(item.Select()) {
                return Ok("select");
            }
        }
        if let Ok(expand) = element
            .GetCurrentPatternAs::<IUIAutomationExpandCollapsePattern>(UIA_ExpandCollapsePatternId)
        {
            ticket.check()?;
            let collapsed = expand
                .CurrentExpandCollapseState()
                .is_ok_and(|s| s == ExpandCollapseState_Collapsed);
            let done = if collapsed {
                expand.Expand()
            } else {
                expand.Collapse()
            };
            if made(done) {
                return Ok(if collapsed { "expand" } else { "collapse" });
            }
        }
        let point = centre(element)?;
        if !point_is_windows(point, hwnd) {
            return Err(Failure::Failed);
        }
        send(
            ticket,
            &[
                mouse_move(point),
                mouse(MOUSEEVENTF_LEFTDOWN, 0, 0, 0),
                mouse(MOUSEEVENTF_LEFTUP, 0, 0, 0),
            ],
        )
        .map(|()| "mouse")
    }
}

/// The centre of an element on the screen.
unsafe fn centre(element: &IUIAutomationElement) -> Result<POINT, Failure> {
    // SAFETY: a COM call on this thread's object.
    let rect: RECT = unsafe { element.CurrentBoundingRectangle() }.map_err(|_| Failure::Stale)?;
    if rect.right <= rect.left || rect.bottom <= rect.top {
        return Err(Failure::Failed);
    }
    Ok(POINT {
        x: rect.left + (rect.right - rect.left) / 2,
        y: rect.top + (rect.bottom - rect.top) / 2,
    })
}

/// Whether a click at `point` lands in the window `hwnd`: no other window (the overlay least of
/// all) is over it there.
fn point_is_windows(point: POINT, hwnd: HWND) -> bool {
    // SAFETY: plain queries.
    unsafe {
        let there = WindowFromPoint(point);
        !there.is_invalid() && GetAncestor(there, GA_ROOT) == GetAncestor(hwnd, GA_ROOT)
    }
}

/// Whether the window in front is `target` itself or one it owns (a dialog of its), from the
/// window in front, its top-level ancestor and the owner at the root of its owners (handles as
/// numbers, 0 for none). Another top-level window of the same process (a second Chrome window)
/// is not the target's, whatever process it is of.
fn fronts(front: isize, front_root: isize, front_root_owner: isize, target: isize) -> bool {
    front != 0 && target != 0 && [front, front_root, front_root_owner].contains(&target)
}

/// Whether the window in front is `hwnd` (or one it owns, a dialog).
fn in_front(hwnd: HWND) -> bool {
    // SAFETY: plain queries.
    unsafe {
        let front = GetForegroundWindow();
        if front.is_invalid() {
            return false;
        }
        let handle = |hwnd: HWND| hwnd.0 as isize;
        fronts(
            handle(front),
            handle(GetAncestor(front, GA_ROOT)),
            handle(GetAncestor(front, GA_ROOTOWNER)),
            handle(GetAncestor(hwnd, GA_ROOT)),
        )
    }
}

/// Brings `hwnd` to the front, restored if it was minimized. Windows lets a process do that only
/// when it made the last input, so a press of a key that no app takes (0xE8, which pasting
/// already sends for the same kind of reason) comes first. Windows may refuse all the same; the
/// step then finds out (`in_front`), and a pattern needs no front anyway. `Stopped`, with nothing
/// done, once the run is over.
fn bring_to_front(hwnd: HWND, ticket: &Ticket) -> Result<(), Failure> {
    /// A virtual key no app answers to.
    const UNASSIGNED: u16 = 0xE8;
    if in_front(hwnd) {
        return Ok(());
    }
    let pressed = inject(
        ticket,
        &[
            key(UNASSIGNED, 0, KEYBD_EVENT_FLAGS::default()),
            key(UNASSIGNED, 0, KEYEVENTF_KEYUP),
        ],
    );
    if pressed == Err(Failure::Stopped) {
        return pressed;
    }
    ticket.check()?;
    // SAFETY: plain window calls.
    unsafe {
        if IsIconic(hwnd).as_bool() {
            let _ = ShowWindow(hwnd, SW_RESTORE);
        }
        let _ = SetForegroundWindow(hwnd);
    }
    std::thread::sleep(SETTLE);
    Ok(())
}

/// Sends `inputs` at once, if the run of `ticket` is still under way.
fn inject(ticket: &Ticket, inputs: &[INPUT]) -> Result<(), Failure> {
    ticket.check()?;
    // SAFETY: `inputs` are initialized inputs of the size given.
    let sent = unsafe { SendInput(inputs, size_of::<INPUT>() as i32) };
    if sent as usize == inputs.len() {
        Ok(())
    } else {
        Err(Failure::Failed)
    }
}

/// Sends `inputs`, if the run of `ticket` is still under way, and gives the app a moment.
fn send(ticket: &Ticket, inputs: &[INPUT]) -> Result<(), Failure> {
    inject(ticket, inputs)?;
    std::thread::sleep(SETTLE);
    Ok(())
}

/// Gives `element` the keyboard focus for keys or typed text, and the app a moment to move it
/// there. `Focus` when the element cannot take it (a text, a pane, a disabled button): nothing is
/// then sent, since the keys would reach whatever had the focus before.
fn give_focus(element: &IUIAutomationElement, ticket: &Ticket) -> Result<(), Failure> {
    ticket.check()?;
    // SAFETY: a COM call on an object of the desktop's thread, which this runs on.
    unsafe { element.SetFocus() }.map_err(|_| Failure::Focus)?;
    std::thread::sleep(SETTLE);
    Ok(())
}

/// Whether the live element is a field whose text Delete and Backspace only edit: an Edit or a
/// Document with a Value pattern that is not read only (the core's `safety.edits_text`, on what
/// the look said). A document that cannot be edited (a mail's reading pane, a web page) is not,
/// and neither is one whose pattern cannot be read.
fn edits_text(element: &IUIAutomationElement) -> bool {
    // SAFETY: COM calls on an object of the desktop's thread, which this runs on.
    unsafe {
        let role = element.CurrentControlType().map(|c| c.0).unwrap_or(0);
        (role == UIA_EditControlTypeId.0 || role == UIA_DocumentControlTypeId.0)
            && element
                .GetCurrentPatternAs::<IUIAutomationValuePattern>(UIA_ValuePatternId)
                .is_ok_and(|value| value.CurrentIsReadOnly().is_ok_and(|r| !r.as_bool()))
    }
}

/// Whether the live element is called like a sign-in, a payment field or a code. A name that
/// cannot be read is no such name: the element's other checks still hold.
fn handoff(element: &IUIAutomationElement) -> bool {
    // SAFETY: a COM call on an object of the desktop's thread, which this runs on.
    unsafe { element.CurrentName() }.is_ok_and(|name| policy::is_handoff_name(&name.to_string()))
}

fn key(vk: u16, scan: u16, flags: KEYBD_EVENT_FLAGS) -> INPUT {
    INPUT {
        r#type: INPUT_KEYBOARD,
        Anonymous: INPUT_0 {
            ki: KEYBDINPUT {
                wVk: VIRTUAL_KEY(vk),
                wScan: scan,
                dwFlags: flags,
                time: 0,
                dwExtraInfo: handy_keys::RAPHAEL_INPUT_MARKER,
            },
        },
    }
}

fn mouse(flags: MOUSE_EVENT_FLAGS, data: u32, dx: i32, dy: i32) -> INPUT {
    INPUT {
        r#type: INPUT_MOUSE,
        Anonymous: INPUT_0 {
            mi: MOUSEINPUT {
                dx,
                dy,
                mouseData: data,
                dwFlags: flags,
                time: 0,
                dwExtraInfo: handy_keys::RAPHAEL_INPUT_MARKER,
            },
        },
    }
}

/// The mouse moved to `point`, in the coordinates of the whole virtual screen.
fn mouse_move(point: POINT) -> INPUT {
    // SAFETY: plain queries.
    let (left, top, width, height) = unsafe {
        (
            GetSystemMetrics(SM_XVIRTUALSCREEN),
            GetSystemMetrics(SM_YVIRTUALSCREEN),
            GetSystemMetrics(SM_CXVIRTUALSCREEN).max(2),
            GetSystemMetrics(SM_CYVIRTUALSCREEN).max(2),
        )
    };
    let scale = |at: i32, from: i32, size: i32| {
        ((i64::from(at - from) * 65_535) / i64::from(size - 1)).clamp(0, 65_535) as i32
    };
    mouse(
        MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
        0,
        scale(point.x, left, width),
        scale(point.y, top, height),
    )
}

/// A chord as key presses: the modifiers down, the key down and up, the modifiers up.
fn chord_inputs(chord: Chord) -> Vec<INPUT> {
    const CONTROL: u16 = 0x11;
    const SHIFT: u16 = 0x10;
    const ALT: u16 = 0x12;
    let held: Vec<u16> = [
        (chord.ctrl, CONTROL),
        (chord.shift, SHIFT),
        (chord.alt, ALT),
    ]
    .into_iter()
    .filter_map(|(on, vk)| on.then_some(vk))
    .collect();
    let (vk, extended) = chord.key.virtual_key();
    let extended = if extended {
        KEYEVENTF_EXTENDEDKEY
    } else {
        KEYBD_EVENT_FLAGS::default()
    };
    let mut inputs: Vec<INPUT> = held
        .iter()
        .map(|&m| key(m, 0, KEYBD_EVENT_FLAGS::default()))
        .collect();
    inputs.push(key(vk, 0, extended));
    inputs.push(key(vk, 0, extended | KEYEVENTF_KEYUP));
    inputs.extend(held.iter().rev().map(|&m| key(m, 0, KEYEVENTF_KEYUP)));
    inputs
}

/// UTF-16 units as Unicode key presses, each pressed and released.
fn unit_inputs(units: &[u16]) -> Vec<INPUT> {
    units
        .iter()
        .flat_map(|&unit| {
            [
                key(0, unit, KEYEVENTF_UNICODE),
                key(0, unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP),
            ]
        })
        .collect()
}

/// Enter, pressed and released: a line break of typed text.
fn enter_inputs() -> [INPUT; 2] {
    [
        key(0x0D, 0, KEYBD_EVENT_FLAGS::default()),
        key(0x0D, 0, KEYEVENTF_KEYUP),
    ]
}

unsafe extern "system" fn collect_window(hwnd: HWND, found: LPARAM) -> BOOL {
    // SAFETY: `found` is the `Vec<HWND>` that `State::windows` passed, alive for the call.
    let found = unsafe { &mut *(found.0 as *mut Vec<HWND>) };
    found.push(hwnd);
    BOOL::from(found.len() < 1024)
}

/// The process of a UWP app's `CoreWindow` in its frame `frame` (whose own process is
/// `frame_pid`), or None for a frame without one.
fn hosted_app(frame: HWND, frame_pid: u32) -> Option<u32> {
    struct Search {
        frame_pid: u32,
        found: u32,
    }
    unsafe extern "system" fn child(hwnd: HWND, search: LPARAM) -> BOOL {
        // SAFETY: `search` is the `Search` below, alive for the call.
        let search = unsafe { &mut *(search.0 as *mut Search) };
        let mut pid = 0u32;
        // SAFETY: a plain query.
        unsafe { GetWindowThreadProcessId(hwnd, Some(&raw mut pid)) };
        if pid != 0 && pid != search.frame_pid {
            search.found = pid;
            return BOOL::from(false);
        }
        BOOL::from(true)
    }
    let mut search = Search {
        frame_pid,
        found: 0,
    };
    // SAFETY: the callback only writes to `search`, which outlives the call.
    unsafe {
        let _ = EnumChildWindows(Some(frame), Some(child), LPARAM((&raw mut search) as isize));
    }
    (search.found != 0).then_some(search.found)
}

/// Whether DWM hides the window (on another virtual desktop, a suspended app).
fn cloaked(hwnd: HWND) -> bool {
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
    .is_ok()
        && cloaked != 0
}

fn window_text(hwnd: HWND) -> String {
    let mut text = [0u16; 512];
    // SAFETY: `text` has room for the length given.
    let len = unsafe { GetWindowTextW(hwnd, &mut text) };
    String::from_utf16_lossy(&text[..usize::try_from(len).unwrap_or(0).min(text.len())])
}

fn class_name(hwnd: HWND) -> String {
    let mut name = [0u16; 256];
    // SAFETY: `name` has room for the length given.
    let len = unsafe { GetClassNameW(hwnd, &mut name) };
    String::from_utf16_lossy(&name[..usize::try_from(len).unwrap_or(0).min(name.len())])
}

/// The full path of a process's executable.
fn image_path(pid: u32) -> Option<String> {
    // SAFETY: `Owned` closes the handle; `path` has room for `len` units.
    unsafe {
        let process = windows::core::Owned::new(
            OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid).ok()?,
        );
        let mut path = vec![0u16; 32_768];
        let mut len = path.len() as u32;
        QueryFullProcessImageNameW(
            *process,
            PROCESS_NAME_WIN32,
            PWSTR(path.as_mut_ptr()),
            &mut len,
        )
        .ok()?;
        Some(String::from_utf16_lossy(&path[..len as usize]))
    }
}

/// The file description of an executable (`Microsoft Excel`), from its version resource.
fn file_description(path: &str) -> Option<String> {
    if path.is_empty() {
        return None;
    }
    let wide: Vec<u16> = path.encode_utf16().chain(std::iter::once(0)).collect();
    let file = PCWSTR(wide.as_ptr());
    // SAFETY: `data` has the size the first call gave; the pointers VerQueryValueW returns point
    // into `data`, which outlives them, and say how long they are.
    unsafe {
        let size = GetFileVersionInfoSizeW(file, None);
        if size == 0 {
            return None;
        }
        let mut data = vec![0u8; size as usize];
        GetFileVersionInfoW(file, None, size, data.as_mut_ptr().cast()).ok()?;
        let mut found: *mut c_void = std::ptr::null_mut();
        let mut len = 0u32;
        let (language, codepage) = if VerQueryValueW(
            data.as_ptr().cast(),
            w!("\\VarFileInfo\\Translation"),
            &mut found,
            &mut len,
        )
        .as_bool()
            && len >= 4
            && !found.is_null()
        {
            let pair = found as *const u16;
            (*pair, *pair.add(1))
        } else {
            (0x0409, 0x04B0)
        };
        let key = HSTRING::from(format!(
            "\\StringFileInfo\\{language:04x}{codepage:04x}\\FileDescription"
        ));
        if !VerQueryValueW(data.as_ptr().cast(), &key, &mut found, &mut len).as_bool()
            || found.is_null()
            || len == 0
        {
            return None;
        }
        let units = std::slice::from_raw_parts(found as *const u16, len as usize);
        let text = String::from_utf16_lossy(units);
        Some(text.trim_end_matches('\0').trim().to_owned()).filter(|t| !t.is_empty())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::computer::keys::parse_chord;

    fn vk(input: &INPUT) -> (u16, bool) {
        // SAFETY: the inputs below are keyboard inputs.
        let ki = unsafe { input.Anonymous.ki };
        (ki.wVk.0, ki.dwFlags.0 & KEYEVENTF_KEYUP.0 != 0)
    }

    #[test]
    fn a_chord_presses_its_modifiers_around_its_key_and_releases_them_all() {
        let inputs = chord_inputs(parse_chord("Ctrl+Shift+Tab").unwrap());
        let keys: Vec<(u16, bool)> = inputs.iter().map(vk).collect();
        assert_eq!(
            keys,
            [
                (0x11, false),
                (0x10, false),
                (0x09, false),
                (0x09, true),
                (0x10, true),
                (0x11, true)
            ]
        );
        for input in &inputs {
            // SAFETY: keyboard inputs.
            assert_eq!(
                unsafe { input.Anonymous.ki.dwExtraInfo },
                handy_keys::RAPHAEL_INPUT_MARKER
            );
        }
    }

    #[test]
    fn text_is_typed_as_unicode_and_a_line_break_is_enter() {
        let units: Vec<u16> = "七".encode_utf16().collect();
        let inputs = unit_inputs(&units);
        assert_eq!(inputs.len(), 2);
        // SAFETY: keyboard inputs.
        let first = unsafe { inputs[0].Anonymous.ki };
        assert_eq!(first.wScan, '七' as u16);
        assert_ne!(first.dwFlags.0 & KEYEVENTF_UNICODE.0, 0);
        assert!(vk(&inputs[1]).1);
        let enter = enter_inputs();
        assert_eq!(
            [vk(&enter[0]), vk(&enter[1])],
            [(0x0D, false), (0x0D, true)]
        );
        // a character beyond the basic plane is two units, each pressed and released
        let units: Vec<u16> = "𠮷".encode_utf16().collect();
        assert_eq!(unit_inputs(&units).len(), 4);
    }

    #[test]
    fn another_window_of_the_same_process_in_front_is_not_the_steps() {
        let (target, other_chrome, dialog) = (0x100, 0x200, 0x300);
        // the step's own window in front, or a dialog it owns
        assert!(fronts(target, target, target, target));
        assert!(fronts(dialog, dialog, target, target));
        // a second window of the same browser in front: a window of its own, owned by none
        assert!(!fronts(other_chrome, other_chrome, other_chrome, target));
        // a dialog of that other window
        assert!(!fronts(dialog, dialog, other_chrome, target));
        // nothing in front, or no target
        assert!(!fronts(0, 0, 0, target));
        assert!(!fronts(0, 0, 0, 0));
    }
}
