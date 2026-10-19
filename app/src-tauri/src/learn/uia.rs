//! Paste-scoped UIA reads, terminal regions, and an opt-in ASCII keyboard fallback.
//! The focused element and its edit/document ancestors are the only text sources queried.

use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex, PoisonError};
use std::time::{Duration, Instant};

use handy_keys::{
    EditCancellation, EditEvent, EditObservation, observe_edits, observe_submissions,
};
use windows::Win32::Foundation::{E_INVALIDARG, E_POINTER, HWND, LPARAM, WPARAM};
use windows::Win32::System::Com::{
    CLSCTX_INPROC_SERVER, COINIT_MULTITHREADED, CoCreateInstance, CoInitializeEx, CoUninitialize,
    SAFEARRAY,
};
use windows::Win32::System::Ole::{
    SafeArrayDestroy, SafeArrayGetDim, SafeArrayGetElement, SafeArrayGetElemsize,
    SafeArrayGetLBound, SafeArrayGetUBound, SafeArrayGetVartype,
};
use windows::Win32::System::Variant::VT_I4;
use windows::Win32::UI::Accessibility::{
    CUIAutomation, CUIAutomation8, IUIAutomation, IUIAutomation2, IUIAutomationCacheRequest,
    IUIAutomationElement, IUIAutomationFocusChangedEventHandler,
    IUIAutomationFocusChangedEventHandler_Impl, IUIAutomationTextPattern, IUIAutomationTextRange,
    IUIAutomationValuePattern, TextPatternRangeEndpoint_End, TextPatternRangeEndpoint_Start,
    TextUnit_Character, UIA_DocumentControlTypeId, UIA_EditControlTypeId, UIA_IsPasswordPropertyId,
    UIA_TextControlTypeId, UIA_TextPatternId, UIA_ValuePatternId, UiaRootObjectId,
};
use windows::Win32::UI::WindowsAndMessaging::{
    GUITHREADINFO, GetForegroundWindow, GetGUIThreadInfo, GetWindowThreadProcessId,
    SMTO_ABORTIFHUNG, SendMessageTimeoutW, WM_GETOBJECT,
};
use windows::core::{Interface, Ref, implement};

use super::apps::{AppKind, classify, needs_accessibility_hint};
use super::keystrokes::Keystrokes;
use super::terminal::{TerminalDecision, TerminalTracker};
use super::{Correction, FieldProbe, Snapshot, Tracker, WatchScope, WatchedField};
use crate::paste::Target;

const MAX_CHARS: usize = 20_000;
/// UI Automation waits 2 s to connect and 20 s per call unless told otherwise, and the watch
/// thread would stand still that long on a busy app.
const CALL_TIMEOUT_MS: u32 = 500;
static CODE_HINT_LOGGED: AtomicBool = AtomicBool::new(false);

/// Which UI Automation class a client was made from.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum ClientClass {
    /// `CUIAutomation8`: offers `IUIAutomation2`, whose timeouts can be set.
    Modern,
    /// `CUIAutomation`: the original class. On some systems (Windows 11 build 26300 among them)
    /// it answers E_NOINTERFACE for `IUIAutomation2`, so its calls keep the default timeouts.
    Original,
}

/// Makes the client with the first creator that works. The modern class comes first, because
/// only it can be given timeouts; `original` runs only when `modern` fails.
fn create_in_order<T, E>(
    modern: impl FnOnce() -> Result<T, E>,
    original: impl FnOnce() -> Result<T, E>,
) -> Result<(T, ClientClass), E> {
    match modern() {
        Ok(client) => Ok((client, ClientClass::Modern)),
        Err(_) => original().map(|client| (client, ClientClass::Original)),
    }
}

fn hresult<T>(result: &windows::core::Result<T>) -> String {
    match result {
        Ok(_) => "ok".to_owned(),
        Err(error) => format!("0x{:08X}", error.code().0 as u32),
    }
}

/// A UI Automation client with its call timeouts set where the system allows it. Only metadata
/// is logged: classes, timeouts and HRESULTs.
fn create_client() -> Option<IUIAutomation> {
    // SAFETY: the caller joined a COM apartment, which the client does not outlive.
    let modern = || {
        unsafe { CoCreateInstance::<_, IUIAutomation>(&CUIAutomation8, None, CLSCTX_INPROC_SERVER) }
            .inspect_err(|error| {
                log::info!(
                    "learn: CUIAutomation8 unavailable (hresult: 0x{:08X})",
                    error.code().0 as u32
                );
            })
    };
    let original = || unsafe {
        CoCreateInstance::<_, IUIAutomation>(&CUIAutomation, None, CLSCTX_INPROC_SERVER)
    };
    let (automation, class) = create_in_order(modern, original)
        .inspect_err(|error| {
            log::info!(
                "learn: UI Automation client unavailable (hresult: 0x{:08X})",
                error.code().0 as u32
            );
        })
        .ok()?;
    apply_timeouts(&automation, class);
    Some(automation)
}

/// Bounds how long one call into an unresponsive app may take. A client that cannot be bounded
/// is still used, and says so.
fn apply_timeouts(automation: &IUIAutomation, class: ClientClass) {
    let client = match automation.cast::<IUIAutomation2>() {
        Ok(client) => client,
        Err(error) => {
            log::info!(
                "learn: UI Automation timeouts unavailable (client: {class:?}, hresult: 0x{:08X})",
                error.code().0 as u32
            );
            return;
        }
    };
    // SAFETY: plain property setters on a client owned by this thread.
    let (connection, transaction) = unsafe {
        (
            client.SetConnectionTimeout(CALL_TIMEOUT_MS),
            client.SetTransactionTimeout(CALL_TIMEOUT_MS),
        )
    };
    if connection.is_ok() && transaction.is_ok() {
        log::debug!("learn: UI Automation timeouts set ({CALL_TIMEOUT_MS} ms, client: {class:?})");
    } else {
        log::info!(
            "learn: UI Automation timeouts not set (client: {class:?}, connection: {}, transaction: {})",
            hresult(&connection),
            hresult(&transaction)
        );
    }
}

pub struct UiaProbe;

impl FieldProbe for UiaProbe {
    fn focused(&self, target: &Target) -> Option<Box<dyn WatchedField>> {
        self.after_paste(
            target,
            "",
            WatchScope {
                deadline: Instant::now() + Duration::from_secs(60),
                generation: Arc::new(std::sync::atomic::AtomicU64::new(0)),
                started: 0,
                cancellation: super::CANCELLATION.load(Ordering::SeqCst),
                input: None,
            },
        )
    }

    fn after_paste(
        &self,
        target: &Target,
        pasted: &str,
        scope: WatchScope,
    ) -> Option<Box<dyn WatchedField>> {
        if !scope.active() {
            return None;
        }
        let expires = scope.deadline;
        let com = probe_step(Com::join(), "com_apartment")?;
        // All COM objects stay on this watch thread and must be released before its apartment
        // ends, which the guard in `UiaField` does last.
        let automation = probe_step(create_client(), "automation_client")?;
        if foreground() != target.hwnd || !scope.active() {
            return probe_step(None, "target_changed_before_discovery");
        }
        let kind = classify(&target.process_name);
        if matches!(kind, AppKind::Chromium | AppKind::CodeEditor) {
            activate_accessibility(target.hwnd);
            // Chromium builds its accessibility tree asynchronously after the first request.
            std::thread::sleep(
                Duration::from_millis(50).min(expires.saturating_duration_since(Instant::now())),
            );
        }
        let root = probe_step(
            unsafe { automation.ElementFromHandle(HWND(target.hwnd as *mut _)) }.ok(),
            "target_root",
        )?;
        let focused = probe_step(
            unsafe { automation.GetFocusedElement() }.ok(),
            "focused_element",
        )?;
        let password = declared_password(&focused);
        if !safe_password_status(password) {
            log::info!(
                "learn: probe stopped (stage: password_status, role: {:?}, declared: {password:?})",
                unsafe { focused.CurrentControlType() }
                    .ok()
                    .map(|role| role.0)
            );
            return None;
        }
        let keyboard_field = unsafe { focused.CurrentControlType() }
            .ok()
            .is_some_and(|role| keyboard_field_role(role.0));
        let readers = probe_step(
            find_readers(
                &automation,
                &root,
                &focused,
                &scope,
                kind == AppKind::Terminal,
            ),
            "focused_ancestry",
        )?;
        if foreground() != target.hwnd || !scope.active() {
            return probe_step(None, "target_changed_after_discovery");
        }
        // The root is declared before the field, so it would outlive the field (and the COM
        // apartment its guard ends) on every return below.
        drop(root);
        let metadata = focus_element_metadata(&focused, target.pid);
        log::info!(
            "learn: initial focus metadata (role: {:?}, has_keyboard_focus: {:?}, target_process: {:?})",
            metadata.role,
            metadata.has_keyboard_focus,
            metadata.target_process
        );
        let identity = runtime_id(&focused);
        log::info!(
            "learn: focus identity (available: {}, hresult: {:?})",
            identity.is_ok(),
            identity
                .as_ref()
                .err()
                .map(|error| format!("0x{:08X}", error.code().0 as u32))
        );
        let focus = Arc::new(FocusWatch::new(identity.ok(), target.pid, target.hwnd));
        let handler: IUIAutomationFocusChangedEventHandler = FocusChanged(focus.clone()).into();
        // Plain fields can still be polled without events. Terminals require a submission guard.
        let subscribed = unsafe {
            automation.AddFocusChangedEventHandler(None::<&IUIAutomationCacheRequest>, &handler)
        }
        .is_ok();
        let mut field = UiaField {
            automation,
            focused,
            readers,
            hwnd: target.hwnd,
            kind,
            scope,
            pasted: pasted.to_owned(),
            mode: ReadMode::Pending,
            ended: false,
            initial: None,
            keys: Keystrokes::new(pasted),
            observation: None,
            focus,
            handler: subscribed.then_some(handler),
            _com: com,
        };
        if let Err(failure) = field.check_focus() {
            log::info!(
                "learn: probe stopped (stage: focus_changed_after_subscription, reason: {}, final_recheck: {}, declared_password: {:?}, hresult: {:?})",
                failure.reason,
                failure.final_recheck,
                failure.declared_password,
                failure.hresult.map(|code| format!("0x{:08X}", code as u32))
            );
            field.log_failed_focus_snapshot();
            return None;
        }
        if kind == AppKind::Terminal {
            // Screen text cannot prove whether a command has already been submitted. Cover the
            // discovery window too, before accepting any initial terminal text.
            if !subscribed
                || !field.paste_input_is_current()
                || !field.focus.input_observation_allowed()
            {
                return probe_step(None, "terminal_checkpoint_or_subscription");
            }
            field.set_observation(observe_submissions(
                target.hwnd,
                expires.saturating_duration_since(Instant::now()),
            ));
            if field.observation.is_none()
                || !field.paste_input_is_current()
                || !field.still_focused()
            {
                return probe_step(None, "terminal_observer_unavailable_or_stale");
            }
        }
        field.initial = field.select_source();
        if subscribed && !pasted.is_empty() && field.scope.active() {
            let remaining = expires.saturating_duration_since(Instant::now());
            if kind != AppKind::Terminal
                && matches!(field.mode, ReadMode::Pending)
                && keyboard_field
                && field.keys.is_some()
                && field.focus.input_observation_allowed()
                && field
                    .scope
                    .input
                    .as_ref()
                    .is_some_and(|input| input.is_current())
            {
                field.set_observation(observe_edits(target.hwnd, remaining));
                if field.still_focused()
                    && field
                        .scope
                        .input
                        .as_ref()
                        .is_some_and(|input| input.is_current())
                    && field.observation.is_some()
                {
                    field.mode = ReadMode::Keys;
                    field.initial = Some(pasted.to_owned());
                } else {
                    field.observation.take();
                }
            }
        }
        if !field.read_allowed() {
            return probe_step(None, "source_changed_during_discovery");
        }
        log::info!(
            "learn: field probe (sources: {}, keyboard: {}, terminal: {})",
            field.readers.len(),
            field.observation.is_some(),
            kind == AppKind::Terminal
        );
        Some(Box::new(field))
    }
}

/// Logs only a fixed stage identifier; never element names, values, or dictated text.
fn probe_step<T>(value: Option<T>, stage: &'static str) -> Option<T> {
    if value.is_none() {
        log::info!("learn: probe stopped (stage: {stage})");
    }
    value
}

fn readable_role(role: i32, terminal: bool, focused: bool) -> bool {
    role == UIA_EditControlTypeId.0
        || role == UIA_DocumentControlTypeId.0
        || (terminal && focused && role == UIA_TextControlTypeId.0)
}

fn safe_password_status(password: Option<bool>) -> bool {
    password == Some(false)
}

fn keyboard_field_role(role: i32) -> bool {
    role == UIA_EditControlTypeId.0
}

fn declared_password(element: &IUIAutomationElement) -> Option<bool> {
    // Ignore UIA's default false: an absent property on a renderer/container is not proof that
    // the actual input is non-secret. VT_UNKNOWN/NotSupported cannot convert to a bool.
    let value =
        unsafe { element.GetCurrentPropertyValueEx(UIA_IsPasswordPropertyId, true) }.ok()?;
    bool::try_from(&value).ok()
}

fn document_is_scoped(terminal: bool, focused: bool, child_range: bool) -> bool {
    terminal || focused || child_range
}

/// Verify ancestry to the target root and skip every unknown/password field. Empty readers
/// still allow keyboard fallback for a known focused field that exposes no text.
fn find_readers(
    automation: &IUIAutomation,
    root: &IUIAutomationElement,
    focused: &IUIAutomationElement,
    scope: &WatchScope,
    terminal: bool,
) -> Option<Vec<Reader>> {
    let walker = unsafe { automation.RawViewWalker() }.ok()?;
    let mut element = focused.clone();
    let mut readers = Vec::new();
    for _ in 0..32 {
        if !scope.active()
            || !safe_password_status(
                unsafe { element.CurrentIsPassword() }
                    .ok()
                    .map(|b| b.as_bool()),
            )
        {
            return None;
        }
        let role = unsafe { element.CurrentControlType() }.ok()?.0;
        let same = unsafe { automation.CompareElements(&element, focused) }
            .ok()?
            .as_bool();
        if readable_role(role, terminal, same) {
            let mut value = unsafe {
                element.GetCurrentPatternAs::<IUIAutomationValuePattern>(UIA_ValuePatternId)
            }
            .ok();
            let mut text = unsafe {
                element.GetCurrentPatternAs::<IUIAutomationTextPattern>(UIA_TextPatternId)
            }
            .ok();
            let range = if !same && !terminal {
                text.as_ref()
                    .and_then(|t| unsafe { t.RangeFromChild(focused) }.ok())
            } else {
                None
            };
            // An ancestor web document can include unrelated form fields. Read only its focused
            // descendant's range; never fall back to the whole page if the provider cannot scope it.
            if role == UIA_DocumentControlTypeId.0 && !same && !terminal {
                value = None;
            }
            if !document_is_scoped(
                terminal,
                same || role == UIA_EditControlTypeId.0,
                range.is_some(),
            ) {
                text = None;
            }
            if value.is_some() || text.is_some() {
                readers.push(Reader { value, text, range });
            }
        }
        if unsafe { automation.CompareElements(&element, root) }
            .ok()?
            .as_bool()
        {
            return Some(readers);
        }
        element = unsafe { walker.GetParentElement(&element) }.ok()?;
    }
    None
}

/// Ask the existing Chromium provider to expose accessibility. No global screen-reader state,
/// command-line flags, or editor settings are changed.
fn activate_accessibility(hwnd: isize) {
    unsafe {
        let root = HWND(hwnd as *mut _);
        let thread = GetWindowThreadProcessId(root, None);
        let mut info = GUITHREADINFO {
            cbSize: std::mem::size_of::<GUITHREADINFO>() as u32,
            ..Default::default()
        };
        let child = GetGUIThreadInfo(thread, &mut info)
            .ok()
            .map(|()| info.hwndFocus);
        for window in [Some(root), child].into_iter().flatten() {
            if window.0.is_null() {
                continue;
            }
            for object in [UiaRootObjectId, -4] {
                // -4 is OBJID_CLIENT, for Chromium versions using the MSAA bridge.
                SendMessageTimeoutW(
                    window,
                    WM_GETOBJECT,
                    WPARAM(0),
                    LPARAM(object as isize),
                    SMTO_ABORTIFHUNG,
                    100,
                    None,
                );
            }
        }
    }
}

struct Reader {
    value: Option<IUIAutomationValuePattern>,
    text: Option<IUIAutomationTextPattern>,
    range: Option<IUIAutomationTextRange>,
}

impl Reader {
    fn read(&self, terminal: bool, scope: &WatchScope) -> Option<String> {
        if !scope.active() {
            return None;
        }
        if !terminal
            && let Some(value) = &self.value
            && let Ok(value) = unsafe { value.CurrentValue() }
        {
            let value = value.to_string();
            if !value.is_empty() || self.text.is_none() {
                let mut tail: Vec<_> = value.chars().rev().take(MAX_CHARS).collect();
                tail.reverse();
                return Some(tail.into_iter().collect());
            }
        }
        if !scope.active() {
            return None;
        }
        let text = self.text.as_ref()?;
        if let Some(range) = &self.range {
            // Moving a child range's start backwards can escape into other fields in the
            // containing document. Its original boundaries must remain authoritative.
            if !scope.active() {
                return None;
            }
            return unsafe { range.GetText(MAX_CHARS as i32) }
                .ok()
                .map(|text| text.to_string());
        }
        // Bound DocumentRange at its end so long terminal history cannot hide the command.
        let tail = unsafe { text.DocumentRange() }.ok()?;
        let bounded = unsafe {
            tail.MoveEndpointByRange(
                TextPatternRangeEndpoint_Start,
                &tail,
                TextPatternRangeEndpoint_End,
            )
            .and_then(|()| {
                tail.MoveEndpointByUnit(
                    TextPatternRangeEndpoint_Start,
                    TextUnit_Character,
                    -(MAX_CHARS as i32),
                )
            })
        };
        if bounded.is_ok()
            && scope.active()
            && let Ok(value) = unsafe { tail.GetText(MAX_CHARS as i32) }
        {
            return Some(value.to_string());
        }
        if terminal {
            let ranges = unsafe { text.GetVisibleRanges() }.ok()?;
            let count = unsafe { ranges.Length() }.ok()?.min(64);
            let mut out = String::new();
            for i in 0..count {
                if !scope.active() {
                    return None;
                }
                let range = unsafe { ranges.GetElement(i) }.ok()?;
                let left = MAX_CHARS.saturating_sub(out.chars().count());
                if left == 0 {
                    break;
                }
                if !scope.active() {
                    return None;
                }
                out.push_str(&unsafe { range.GetText(left as i32) }.ok()?.to_string());
            }
            return Some(out);
        }
        None
    }
}

enum ReadMode {
    Pending,
    Field(usize),
    Terminal {
        tracker: Box<TerminalTracker>,
        reader: usize,
    },
    Keys,
}

struct UiaField {
    automation: IUIAutomation,
    focused: IUIAutomationElement,
    readers: Vec<Reader>,
    hwnd: isize,
    kind: AppKind,
    scope: WatchScope,
    pasted: String,
    mode: ReadMode,
    ended: bool,
    initial: Option<String>,
    keys: Option<Keystrokes>,
    observation: Option<EditObservation>,
    focus: Arc<FocusWatch>,
    handler: Option<IUIAutomationFocusChangedEventHandler>,
    _com: Com,
}

/// Fixed metadata only: no element names, runtime identifiers, or field contents.
struct FocusFailure {
    reason: &'static str,
    final_recheck: bool,
    declared_password: Option<bool>,
    hresult: Option<i32>,
}

impl FocusFailure {
    fn new(reason: &'static str) -> Self {
        Self {
            reason,
            final_recheck: false,
            declared_password: None,
            hresult: None,
        }
    }
}

impl WatchedField for UiaField {
    fn read(&mut self) -> Snapshot {
        if !self.read_allowed() {
            return Snapshot::Left;
        }
        if let Some(initial) = self.initial.take() {
            return Snapshot::Text(initial);
        }
        let mut submitted = false;
        let mut invalidated = false;
        if let Some(observation) = &mut self.observation {
            for event in observation.drain() {
                submitted |= event == EditEvent::Enter;
                invalidated |= event == EditEvent::Invalidated;
                if let Some(keys) = &mut self.keys {
                    keys.apply(event);
                }
            }
        }
        if matches!(self.mode, ReadMode::Pending) {
            if submitted || invalidated {
                return Snapshot::Left;
            }
            // Retry patterns after accessibility activation without searching other fields.
            if let Ok(root) =
                unsafe { self.automation.ElementFromHandle(HWND(self.hwnd as *mut _)) }
                && let Some(readers) = find_readers(
                    &self.automation,
                    &root,
                    &self.focused,
                    &self.scope,
                    self.kind == AppKind::Terminal,
                )
            {
                self.readers = readers;
            }
            if let Some(text) = self.select_source() {
                return Snapshot::Text(text);
            }
            return Snapshot::Text(String::new());
        }
        match &mut self.mode {
            ReadMode::Field(index) => {
                let index = *index;
                self.read_source(index)
                    .map(Snapshot::Text)
                    .unwrap_or(Snapshot::Left)
            }
            ReadMode::Terminal { reader, .. } => {
                if terminal_ended(submitted, invalidated) {
                    self.ended = true;
                    self.observation.take();
                    return Snapshot::Left;
                }
                let reader = *reader;
                let Some(text) = self.read_source(reader) else {
                    return Snapshot::Left;
                };
                let ReadMode::Terminal { tracker, .. } = &mut self.mode else {
                    unreachable!("reading a source does not change its mode");
                };
                if tracker.observe(&text) == TerminalDecision::Continue {
                    Snapshot::Text(tracker.region().to_owned())
                } else {
                    self.observation.take();
                    Snapshot::Left
                }
            }
            ReadMode::Keys => {
                let Some(keys) = &self.keys else {
                    return Snapshot::Left;
                };
                let Some(text) = keys.text().map(str::to_owned) else {
                    self.observation.take();
                    return Snapshot::Left;
                };
                if keys.is_complete() {
                    self.ended = true;
                    self.observation.take();
                }
                Snapshot::Text(text)
            }
            ReadMode::Pending => Snapshot::Left,
        }
    }

    fn corrections(&self) -> Option<Vec<Correction>> {
        match &self.mode {
            ReadMode::Terminal { tracker, .. } => Some(tracker.corrections()),
            ReadMode::Keys => Some(
                self.keys
                    .as_ref()
                    .map(Keystrokes::corrections)
                    .unwrap_or_default(),
            ),
            _ => None,
        }
    }
}

impl UiaField {
    fn set_observation(&mut self, observation: Option<EditObservation>) {
        self.observation =
            observation.filter(|observation| self.focus.attach(observation.cancellation()));
    }

    fn paste_input_is_current(&self) -> bool {
        self.scope
            .input
            .as_ref()
            .is_some_and(|input| input.is_current())
    }

    fn read_allowed(&mut self) -> bool {
        if self.ended || !self.still_focused() {
            self.ended = true;
            self.observation.take();
            return false;
        }
        if self.kind == AppKind::Terminal {
            let stopped = self.observation.as_mut().is_none_or(|observation| {
                observation
                    .drain()
                    .into_iter()
                    .any(|event| matches!(event, EditEvent::Enter | EditEvent::Invalidated))
            });
            if stopped {
                if let ReadMode::Terminal { tracker, .. } = &mut self.mode {
                    tracker.submitted();
                }
                self.ended = true;
                self.observation.take();
                return false;
            }
        }
        true
    }

    fn read_source(&mut self, index: usize) -> Option<String> {
        checked_read(self, Self::read_allowed, |field| {
            field.readers[index].read(field.kind == AppKind::Terminal, &field.scope)
        })
    }

    fn select_source(&mut self) -> Option<String> {
        for index in 0..self.readers.len() {
            let Some(text) = self.read_source(index) else {
                continue;
            };
            if self.kind == AppKind::Terminal {
                if let Some(tracker) = TerminalTracker::new(&text, &self.pasted) {
                    let region = tracker.region().to_owned();
                    self.mode = ReadMode::Terminal {
                        tracker: Box::new(tracker),
                        reader: index,
                    };
                    return Some(region);
                }
            } else if Tracker::new(&text, &self.pasted).is_some() {
                self.mode = ReadMode::Field(index);
                self.observation.take();
                self.keys.take();
                return Some(text);
            }
        }
        if needs_accessibility_hint(self.kind) && !CODE_HINT_LOGGED.swap(true, Ordering::Relaxed) {
            log::info!(
                "learn: editor text unavailable; editor.accessibilitySupport=on may expose it; settings unchanged"
            );
        }
        None
    }

    fn still_focused(&self) -> bool {
        self.check_focus().is_ok()
    }

    /// Only the initial failed probe reads this metadata; it never changes cancellation state.
    fn log_failed_focus_snapshot(&self) {
        let now = match unsafe { self.automation.GetFocusedElement() } {
            Ok(now) => now,
            Err(error) => {
                log::info!(
                    "learn: failed focus snapshot (focused_element: false, hresult: 0x{:08X})",
                    error.code().0 as u32
                );
                return;
            }
        };
        let metadata = focus_element_metadata(&now, self.focus.target_pid);
        let same_element = unsafe { self.automation.CompareElements(&now, &self.focused) }
            .map(|same| same.as_bool())
            .map_err(|error| error.code().0);
        let same_runtime_id = runtime_id(&now)
            .map(|current| {
                self.focus
                    .runtime_id
                    .as_ref()
                    .filter(|original| !original.is_empty())
                    .map(|original| original == &current)
            })
            .map_err(|error| error.code().0);
        log::info!(
            "learn: failed focus snapshot (focused_element: true, role: {:?}, has_keyboard_focus: {:?}, target_process: {:?}, same_element: {same_element:?}, same_runtime_id: {same_runtime_id:?})",
            metadata.role,
            metadata.has_keyboard_focus,
            metadata.target_process
        );
    }

    /// Preserve the original short-circuit order before and after the provider calls.
    fn focus_guard(&self, final_recheck: bool) -> Result<(), FocusFailure> {
        let reason = if !self.scope.active() {
            "scope_cancelled"
        } else if self.focus.left.load(Ordering::SeqCst) {
            "focus_event"
        } else if foreground() != self.hwnd {
            "foreground_changed"
        } else {
            return Ok(());
        };
        Err(FocusFailure {
            final_recheck,
            ..FocusFailure::new(reason)
        })
    }

    /// Polling stays silent; only the initial probe reports this failure metadata.
    fn check_focus(&self) -> Result<(), FocusFailure> {
        self.focus_guard(false)?;
        let now = unsafe { self.automation.GetFocusedElement() }.map_err(|error| FocusFailure {
            hresult: Some(error.code().0),
            ..FocusFailure::new("focused_element_error")
        })?;
        let password = declared_password(&now);
        if !safe_password_status(password) {
            return Err(FocusFailure {
                declared_password: password,
                ..FocusFailure::new("password_unknown_or_true")
            });
        }
        let same =
            unsafe { self.automation.CompareElements(&now, &self.focused) }.map_err(|error| {
                FocusFailure {
                    declared_password: password,
                    hresult: Some(error.code().0),
                    ..FocusFailure::new("identity_mismatch_or_error")
                }
            })?;
        if !same.as_bool() {
            return Err(FocusFailure {
                declared_password: password,
                ..FocusFailure::new("identity_mismatch_or_error")
            });
        }
        drop(now);
        self.focus_guard(true).map_err(|mut failure| {
            failure.declared_password = password;
            failure
        })
    }
}

impl Drop for UiaField {
    fn drop(&mut self) {
        self.observation.take();
        if let Some(handler) = self.handler.take() {
            unsafe {
                drop(self.automation.RemoveFocusChangedEventHandler(&handler));
            }
        }
    }
}

/// Read numeric and Boolean properties only, including provider HRESULTs on failure.
struct FocusElementMetadata {
    role: Result<i32, i32>,
    has_keyboard_focus: Result<bool, i32>,
    target_process: Result<bool, i32>,
}

fn focus_element_metadata(element: &IUIAutomationElement, target_pid: u32) -> FocusElementMetadata {
    // SAFETY: the reference stays on its current apartment/callback thread.
    unsafe {
        FocusElementMetadata {
            role: element
                .CurrentControlType()
                .map(|role| role.0)
                .map_err(|error| error.code().0),
            has_keyboard_focus: element
                .CurrentHasKeyboardFocus()
                .map(|focused| focused.as_bool())
                .map_err(|error| error.code().0),
            target_process: element
                .CurrentProcessId()
                .map(|pid| pid as u32 == target_pid)
                .map_err(|error| error.code().0),
        }
    }
}

/// UIA returns caller-owned arrays; copy only integer values before crossing callback threads.
fn runtime_id(element: &IUIAutomationElement) -> windows::core::Result<Vec<i32>> {
    // SAFETY: this COM reference is used only on its current apartment/callback thread.
    let array = unsafe { element.GetRuntimeId() }?;
    // SAFETY: GetRuntimeId transfers ownership of its returned SAFEARRAY to the caller.
    unsafe { copy_owned_runtime_id(array) }
}

struct RuntimeIdArray(*mut SAFEARRAY);

impl Drop for RuntimeIdArray {
    fn drop(&mut self) {
        // SAFETY: the array is owned here and SafeArrayGetElement leaves no locks outstanding.
        unsafe {
            drop(SafeArrayDestroy(self.0));
        }
    }
}

/// Takes ownership of a valid, nullable SAFEARRAY returned by UIA; never interprets ID values.
///
/// # Safety
/// A non-null pointer must identify a live, exclusively owned SAFEARRAY.
unsafe fn copy_owned_runtime_id(array: *mut SAFEARRAY) -> windows::core::Result<Vec<i32>> {
    if array.is_null() {
        return Err(windows::core::Error::from_hresult(E_POINTER));
    }
    let _owned = RuntimeIdArray(array);
    // Microsoft requires clients to destroy received arrays and recommends GetElement:
    // https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-workingwithsafearrays
    // SAFETY: validate the shape, VARTYPE and element width before copying into i32 storage.
    unsafe {
        if SafeArrayGetDim(array) != 1
            || SafeArrayGetVartype(array)? != VT_I4
            || SafeArrayGetElemsize(array) != std::mem::size_of::<i32>() as u32
        {
            return Err(windows::core::Error::from_hresult(E_INVALIDARG));
        }
        let lower = SafeArrayGetLBound(array, 1)?;
        let upper = SafeArrayGetUBound(array, 1)?;
        let count = i64::from(upper) - i64::from(lower) + 1;
        // Bound metadata allocation; an empty or unexpectedly large identity remains unknown.
        if !(1..=128).contains(&count) {
            return Err(windows::core::Error::from_hresult(E_INVALIDARG));
        }
        let mut identity = Vec::with_capacity(count as usize);
        for index in lower..=upper {
            let mut value = 0_i32;
            SafeArrayGetElement(array, &index, (&mut value as *mut i32).cast())?;
            identity.push(value);
        }
        Ok(identity)
    }
}

/// A focus subscription can cancel only the observation attached to this watch.
#[derive(Default)]
struct FocusWatch {
    runtime_id: Option<Vec<i32>>,
    target_pid: u32,
    hwnd: isize,
    left: AtomicBool,
    input_blocked: AtomicBool,
    cancellation: Mutex<Option<EditCancellation>>,
    event_reports: AtomicUsize,
}

impl FocusWatch {
    fn new(runtime_id: Option<Vec<i32>>, target_pid: u32, hwnd: isize) -> Self {
        Self {
            runtime_id,
            target_pid,
            hwnd,
            ..Self::default()
        }
    }

    fn changed_from(
        &self,
        sender_runtime_id: Option<&[i32]>,
        has_keyboard_focus: Option<bool>,
        foreground_matches: bool,
    ) -> Option<bool> {
        let same = self
            .runtime_id
            .as_deref()
            .filter(|identity| !identity.is_empty())
            .zip(sender_runtime_id.filter(|identity| !identity.is_empty()))
            .map(|(original, sender)| original == sender);
        if !foreground_matches || same.is_none() || has_keyboard_focus.is_none() {
            self.changed();
        } else if has_keyboard_focus == Some(false) {
            // A stale sender must not poison field reads, but cannot vouch for an input gap.
            self.block_input_observation();
        } else if same != Some(true) {
            self.changed();
        }
        same
    }

    fn input_observation_allowed(&self) -> bool {
        !self.left.load(Ordering::SeqCst) && !self.input_blocked.load(Ordering::SeqCst)
    }

    fn attach(&self, cancellation: EditCancellation) -> bool {
        let mut slot = self
            .cancellation
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        if !self.input_observation_allowed() {
            // A focus event may arrive before observation arming has finished.
            drop(slot);
            cancellation.cancel();
            false
        } else {
            *slot = Some(cancellation);
            true
        }
    }

    fn changed(&self) {
        self.left.store(true, Ordering::SeqCst);
        self.block_input_observation();
    }

    fn block_input_observation(&self) {
        self.input_blocked.store(true, Ordering::SeqCst);
        let cancellation = self
            .cancellation
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .take();
        if let Some(cancellation) = cancellation {
            cancellation.cancel();
        }
    }
}

#[implement(IUIAutomationFocusChangedEventHandler)]
struct FocusChanged(Arc<FocusWatch>);

impl IUIAutomationFocusChangedEventHandler_Impl for FocusChanged_Impl {
    fn HandleFocusChangedEvent(
        &self,
        sender: Ref<IUIAutomationElement>,
    ) -> windows::core::Result<()> {
        let identity = sender.as_ref().map(runtime_id);
        let has_keyboard_focus = sender.as_ref().map(|element| {
            unsafe { element.CurrentHasKeyboardFocus() }
                .map(|focused| focused.as_bool())
                .map_err(|error| error.code().0)
        });
        let foreground_matches = foreground() == self.0.hwnd;
        let same = self.0.changed_from(
            identity.as_ref().and_then(|result| result.as_deref().ok()),
            has_keyboard_focus.and_then(Result::ok),
            foreground_matches,
        );
        // A small per-watch cap keeps repeated provider notifications from spamming the log.
        if self.0.event_reports.fetch_add(1, Ordering::Relaxed) < 3 {
            let metadata = sender
                .as_ref()
                .map(|element| focus_element_metadata(element, self.0.target_pid));
            log::info!(
                "learn: focus event (sender_present: {}, same_runtime_id: {same:?}, hresult: {:?}, role: {:?}, has_keyboard_focus: {:?}, target_process: {:?}, foreground_matches: {foreground_matches}, input_blocked: {}, field_left: {})",
                identity.is_some(),
                identity
                    .as_ref()
                    .and_then(|result| result.as_ref().err())
                    .map(|error| format!("0x{:08X}", error.code().0 as u32)),
                metadata.as_ref().map(|metadata| metadata.role),
                has_keyboard_focus,
                metadata.as_ref().map(|metadata| metadata.target_process),
                self.0.input_blocked.load(Ordering::SeqCst),
                self.0.left.load(Ordering::SeqCst)
            );
        }
        Ok(())
    }
}

struct Com;
impl Com {
    fn join() -> Option<Self> {
        unsafe { CoInitializeEx(None, COINIT_MULTITHREADED) }
            .ok()
            .ok()?;
        Some(Self)
    }
}
impl Drop for Com {
    fn drop(&mut self) {
        unsafe { CoUninitialize() };
    }
}
fn foreground() -> isize {
    unsafe { GetForegroundWindow() }.0 as isize
}

fn terminal_ended(submitted: bool, invalidated: bool) -> bool {
    submitted || invalidated
}

/// A provider call may outlive the focused field, the watch, or a terminal submission.
fn checked_read<S, T>(
    state: &mut S,
    mut current: impl FnMut(&mut S) -> bool,
    read: impl FnOnce(&mut S) -> Option<T>,
) -> Option<T> {
    if !current(state) {
        return None;
    }
    let text = read(state)?;
    current(state).then_some(text)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn repeated_focus_events_for_the_same_runtime_id_do_not_cancel() {
        let original = vec![42, 7, 19];
        let focus = FocusWatch::new(Some(original.clone()), 0, 11);
        for _ in 0..3 {
            focus.changed_from(Some(&original), Some(true), true);
            assert!(!focus.left.load(Ordering::SeqCst));
        }
    }

    #[test]
    fn different_or_unknown_focus_runtime_ids_cancel_immediately() {
        for (original, sender) in [
            (Some(vec![42, 7, 19]), Some(vec![42, 7, 20])),
            (Some(vec![42, 7, 19]), None),
            (None, Some(vec![42, 7, 19])),
            (None, None),
            (Some(vec![42, 7, 19]), Some(Vec::new())),
            (Some(Vec::new()), Some(Vec::new())),
        ] {
            let focus = FocusWatch::new(original, 0, 11);
            focus.changed_from(sender.as_deref(), Some(true), true);
            assert!(focus.left.load(Ordering::SeqCst));
        }
    }

    #[test]
    fn a_matching_focus_event_never_revives_a_cancelled_watch() {
        let original = vec![42, 7, 19];
        let focus = FocusWatch::new(Some(original.clone()), 0, 11);
        focus.changed_from(Some(&[42, 7, 20]), Some(true), true);
        focus.changed_from(Some(&original), Some(true), true);
        assert!(focus.left.load(Ordering::SeqCst));
    }

    #[test]
    fn unfocused_background_event_preserves_field_and_blocks_input_observation() {
        let original = vec![42, 7, 19];
        let focus = FocusWatch::new(Some(original.clone()), 0, 11);
        focus.changed_from(Some(&[42, 7, 20]), Some(false), true);
        assert!(!focus.left.load(Ordering::SeqCst));
        assert!(focus.input_blocked.load(Ordering::SeqCst));
        assert!(!focus.input_observation_allowed());
        // A later duplicate cannot re-arm keyboard capture for the same watch.
        focus.changed_from(Some(&original), Some(true), true);
        assert!(!focus.left.load(Ordering::SeqCst));
        assert!(!focus.input_observation_allowed());
    }

    #[test]
    fn focus_events_fail_closed_when_foreground_or_sender_focus_is_unknown() {
        let original = vec![42, 7, 19];
        for (sender, has_keyboard_focus, foreground_matches) in [
            (Some(original.clone()), Some(true), false),
            (Some(vec![42, 7, 20]), Some(false), false),
            (Some(original.clone()), None, true),
            (Some(vec![42, 7, 20]), None, true),
            (None, Some(false), true),
        ] {
            let focus = FocusWatch::new(Some(original.clone()), 0, 11);
            focus.changed_from(sender.as_deref(), has_keyboard_focus, foreground_matches);
            assert!(focus.left.load(Ordering::SeqCst));
        }
    }

    #[test]
    fn runtime_id_arrays_copy_integer_values_with_checked_native_bounds() {
        use windows::Win32::System::Ole::{SafeArrayCreateVector, SafeArrayPutElement};

        let values = [42_i32, 7, 19];
        for lower in [-3, 0, 5] {
            // SAFETY: these test-owned arrays use the same integer element type as UIA IDs.
            unsafe {
                let array = SafeArrayCreateVector(VT_I4, lower, values.len() as u32);
                assert!(!array.is_null());
                let owned = RuntimeIdArray(array);
                for (offset, value) in values.iter().enumerate() {
                    let index = lower + offset as i32;
                    SafeArrayPutElement(array, &index, (value as *const i32).cast()).unwrap();
                }
                // Transfer exactly one ownership obligation to the production copier.
                std::mem::forget(owned);
                let copied = copy_owned_runtime_id(array).unwrap();
                assert!(copied == values);
            }
        }
    }

    #[test]
    fn runtime_id_arrays_reject_null_empty_wrong_type_and_wrong_dimensions() {
        use windows::Win32::System::Com::SAFEARRAYBOUND;
        use windows::Win32::System::Ole::{SafeArrayCreate, SafeArrayCreateVector};
        use windows::Win32::System::Variant::VT_UI4;

        // SAFETY: every non-null array is newly allocated and consumed exactly once.
        unsafe {
            assert!(copy_owned_runtime_id(std::ptr::null_mut()).is_err());
            for (kind, count) in [(VT_I4, 0), (VT_UI4, 3), (VT_I4, 129)] {
                let array = SafeArrayCreateVector(kind, 0, count);
                assert!(!array.is_null());
                assert!(copy_owned_runtime_id(array).is_err());
            }
            let bounds = [
                SAFEARRAYBOUND {
                    cElements: 2,
                    lLbound: 0,
                },
                SAFEARRAYBOUND {
                    cElements: 2,
                    lLbound: 0,
                },
            ];
            let array = SafeArrayCreate(VT_I4, 2, bounds.as_ptr());
            assert!(!array.is_null());
            assert!(copy_owned_runtime_id(array).is_err());
        }
    }

    #[test]
    #[ignore = "requires an explicitly prepared non-sensitive foreground field"]
    fn native_read_only_foreground_probe() {
        use crate::paste::{Paster, WindowsPaster};

        let pasted = match std::env::var("RAPHAEL_NATIVE_PROBE_TEXT") {
            Ok(text) => text,
            Err(std::env::VarError::NotPresent) => {
                eprintln!("native learn probe: result=skipped reason=fixture_missing");
                return;
            }
            Err(std::env::VarError::NotUnicode(_)) => {
                panic!("native learn probe: result=failed reason=fixture_not_unicode");
            }
        };
        assert!(
            !pasted.trim().is_empty(),
            "native learn probe: result=failed reason=fixture_empty"
        );

        struct MetadataLogger;
        impl log::Log for MetadataLogger {
            fn enabled(&self, metadata: &log::Metadata<'_>) -> bool {
                metadata.target().ends_with("::learn::uia")
            }

            fn log(&self, record: &log::Record<'_>) {
                if !self.enabled(record.metadata()) {
                    return;
                }
                let message = record.args().to_string();
                if message.starts_with("learn: probe stopped (stage:")
                    || message.starts_with("learn: field probe (sources:")
                    || message.starts_with("learn: focus identity (")
                    || message.starts_with("learn: focus event (")
                    || message.starts_with("learn: initial focus metadata (")
                    || message.starts_with("learn: failed focus snapshot (")
                {
                    eprintln!("{message}");
                }
            }

            fn flush(&self) {}
        }
        // This opt-in test runs alone; only fixed probe metadata reaches the terminal.
        static LOGGER: MetadataLogger = MetadataLogger;
        assert!(
            log::set_logger(&LOGGER).is_ok(),
            "native learn probe: result=failed reason=logger_already_installed; run this test alone"
        );
        log::set_max_level(log::LevelFilter::Info);

        let target = WindowsPaster::new().foreground().unwrap_or_else(|| {
            panic!("native learn probe: result=failed reason=foreground_unavailable")
        });
        eprintln!(
            "native learn probe: phase=discovery target_kind={:?} elevated={} fixture_chars={}",
            classify(&target.process_name),
            target.is_elevated,
            pasted.chars().count()
        );
        let scope = WatchScope {
            deadline: Instant::now() + Duration::from_secs(60),
            generation: Arc::new(std::sync::atomic::AtomicU64::new(0)),
            started: 0,
            cancellation: super::super::CANCELLATION.load(Ordering::SeqCst),
            input: None,
        };
        let mut field = UiaProbe
            .after_paste(&target, &pasted, scope)
            .unwrap_or_else(|| {
                panic!(
                    "native learn probe: result=failed phase=discovery; see fixed probe metadata above"
                )
            });
        match field.read() {
            Snapshot::Text(text) => {
                let found = Tracker::new(&text, &pasted).is_some();
                eprintln!(
                    "native learn probe: phase=initial snapshot=text read_chars={} fixture_found={found}",
                    text.chars().count()
                );
                assert!(
                    found,
                    "native learn probe: result=failed phase=initial reason=fixture_not_found"
                );
            }
            Snapshot::Left => {
                panic!("native learn probe: result=failed phase=initial reason=field_left");
            }
        }
        drop(field);
        eprintln!("native learn probe: result=passed");
    }

    #[test]
    fn the_modern_client_class_is_tried_first_and_the_original_only_when_it_fails() {
        use std::cell::Cell;

        let originals = Cell::new(0);
        let made = create_in_order(
            || Ok::<_, i32>("modern"),
            || {
                originals.set(originals.get() + 1);
                Ok("original")
            },
        );
        assert_eq!(made, Ok(("modern", ClientClass::Modern)));
        assert_eq!(originals.get(), 0);

        let made = create_in_order(
            || Err(-2147467262),
            || {
                originals.set(originals.get() + 1);
                Ok("original")
            },
        );
        assert_eq!(made, Ok(("original", ClientClass::Original)));
        assert_eq!(originals.get(), 1);
    }

    #[test]
    fn when_no_client_class_can_be_made_the_original_error_is_reported() {
        let made: Result<(&str, ClientClass), i32> = create_in_order(|| Err(1), || Err(2));
        assert_eq!(made, Err(2));
    }

    #[test]
    fn only_explicitly_non_password_fields_are_safe() {
        assert!(safe_password_status(Some(false)));
        assert!(!safe_password_status(Some(true)));
        assert!(!safe_password_status(None));
    }

    #[test]
    fn only_edit_and_document_ancestors_supply_text() {
        assert!(readable_role(50004, false, false));
        assert!(readable_role(50030, false, false));
        assert!(!readable_role(50032, false, false)); // Window
        assert!(!readable_role(50033, false, false)); // Pane
        assert!(!readable_role(50020, false, false)); // Text label
    }

    #[test]
    fn the_focused_terminal_text_provider_is_eligible_without_opening_other_labels() {
        // Windows Terminal exposes its active TextPattern on Text, not Edit or Document.
        assert!(readable_role(UIA_TextControlTypeId.0, true, true));
        assert!(!readable_role(UIA_TextControlTypeId.0, true, false));
        assert!(!readable_role(UIA_TextControlTypeId.0, false, true));
        assert!(!readable_role(UIA_TextControlTypeId.0, false, false));
    }

    #[test]
    fn containing_web_documents_require_a_focused_child_range() {
        assert!(!document_is_scoped(false, false, false));
        assert!(document_is_scoped(false, false, true));
        assert!(document_is_scoped(false, true, false));
        assert!(document_is_scoped(true, false, false));
    }

    #[test]
    fn keyboard_fallback_requires_an_edit_field_not_a_renderer_container() {
        assert!(keyboard_field_role(50004));
        assert!(!keyboard_field_role(50030));
        assert!(!keyboard_field_role(50032));
        assert!(!keyboard_field_role(50033));
    }

    #[test]
    fn unknown_or_submitted_terminal_input_never_reads_a_later_screen() {
        assert!(!terminal_ended(false, false));
        assert!(terminal_ended(true, false));
        assert!(terminal_ended(false, true));
        assert!(terminal_ended(true, true));
    }

    #[test]
    fn a_provider_reply_is_discarded_if_the_watch_ends_during_the_call() {
        #[derive(Clone, Copy)]
        enum DuringRead {
            Submit,
            UnknownInput,
            FocusChange,
            Cancel,
        }
        struct Provider {
            focused: bool,
            active: bool,
            pending: Vec<EditEvent>,
        }
        for event in [
            DuringRead::Submit,
            DuringRead::UnknownInput,
            DuringRead::FocusChange,
            DuringRead::Cancel,
        ] {
            let mut provider = Provider {
                focused: true,
                active: true,
                pending: Vec::new(),
            };
            let text = checked_read(
                &mut provider,
                |provider| provider.focused && provider.active && provider.pending.is_empty(),
                |provider| {
                    // A delayed UIA reply can contain the next command or another field's text.
                    match event {
                        DuringRead::Submit => provider.pending.push(EditEvent::Enter),
                        DuringRead::UnknownInput => provider.pending.push(EditEvent::Invalidated),
                        DuringRead::FocusChange => provider.focused = false,
                        DuringRead::Cancel => provider.active = false,
                    }
                    Some("text returned after the watch ended")
                },
            );
            assert_eq!(text, None);
        }
    }

    #[test]
    fn an_inactive_watch_never_calls_the_provider() {
        let mut calls = 0;
        let text = checked_read(
            &mut calls,
            |_| false,
            |calls| {
                *calls += 1;
                Some("unrelated text")
            },
        );
        assert_eq!(text, None);
        assert_eq!(calls, 0);
    }
}
