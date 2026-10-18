//! Paste-scoped UIA reads, terminal regions, and an opt-in ASCII keyboard fallback.
//! The focused element and its edit/document ancestors are the only text sources queried.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, PoisonError};
use std::time::{Duration, Instant};

use handy_keys::{
    EditCancellation, EditEvent, EditObservation, observe_edits, observe_submissions,
};
use windows::Win32::Foundation::{HWND, LPARAM, WPARAM};
use windows::Win32::System::Com::{
    CLSCTX_INPROC_SERVER, COINIT_MULTITHREADED, CoCreateInstance, CoInitializeEx, CoUninitialize,
};
use windows::Win32::UI::Accessibility::{
    CUIAutomation, IUIAutomation, IUIAutomation2, IUIAutomationCacheRequest, IUIAutomationElement,
    IUIAutomationFocusChangedEventHandler, IUIAutomationFocusChangedEventHandler_Impl,
    IUIAutomationTextPattern, IUIAutomationTextRange, IUIAutomationValuePattern,
    TextPatternRangeEndpoint_End, TextPatternRangeEndpoint_Start, TextUnit_Character,
    UIA_DocumentControlTypeId, UIA_EditControlTypeId, UIA_IsPasswordPropertyId,
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
const CALL_TIMEOUT_MS: u32 = 500;
static CODE_HINT_LOGGED: AtomicBool = AtomicBool::new(false);

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
        // SAFETY: all COM objects stay on this watch thread and drop before its apartment.
        let automation: IUIAutomation = probe_step(
            unsafe { CoCreateInstance(&CUIAutomation, None, CLSCTX_INPROC_SERVER) }.ok(),
            "automation_client",
        )?;
        if let Ok(client) = automation.cast::<IUIAutomation2>() {
            unsafe {
                drop(client.SetConnectionTimeout(CALL_TIMEOUT_MS));
                drop(client.SetTransactionTimeout(CALL_TIMEOUT_MS));
            }
        }
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
        let focus = Arc::new(FocusWatch::default());
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
        if !field.still_focused() {
            return probe_step(None, "focus_changed_after_subscription");
        }
        if kind == AppKind::Terminal {
            // Screen text cannot prove whether a command has already been submitted. Cover the
            // discovery window too, before accepting any initial terminal text.
            if !subscribed || !field.paste_input_is_current() {
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
        if let Some(observation) = &observation {
            self.focus.attach(observation.cancellation());
        }
        self.observation = observation;
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
        if !self.scope.active()
            || self.focus.left.load(Ordering::SeqCst)
            || foreground() != self.hwnd
        {
            return false;
        }
        let focused = unsafe {
            self.automation.GetFocusedElement().is_ok_and(|now| {
                safe_password_status(declared_password(&now))
                    && self
                        .automation
                        .CompareElements(&now, &self.focused)
                        .is_ok_and(|same| same.as_bool())
            })
        };
        focused
            && self.scope.active()
            && !self.focus.left.load(Ordering::SeqCst)
            && foreground() == self.hwnd
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

/// A focus subscription can cancel only the observation attached to this watch.
#[derive(Default)]
struct FocusWatch {
    left: AtomicBool,
    cancellation: Mutex<Option<EditCancellation>>,
}

impl FocusWatch {
    fn attach(&self, cancellation: EditCancellation) {
        let mut slot = self
            .cancellation
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        if self.left.load(Ordering::SeqCst) {
            // A focus event may arrive before observation arming has finished.
            drop(slot);
            cancellation.cancel();
        } else {
            *slot = Some(cancellation);
        }
    }

    fn changed(&self) {
        self.left.store(true, Ordering::SeqCst);
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
        _sender: Ref<IUIAutomationElement>,
    ) -> windows::core::Result<()> {
        self.0.changed();
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
