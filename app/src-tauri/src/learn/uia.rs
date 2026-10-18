//! [`UiaProbe`]: the focused text field through Windows UI Automation.
//!
//! Each watch runs on a thread of its own, which joins the multithreaded COM apartment, creates
//! the UI Automation client, and keeps the field's element until the watch ends. A field is read
//! with its Value pattern (single-line inputs, most edit controls) or else its Text pattern
//! (documents, rich edits, browsers' editable areas). UI Automation calls into the target
//! program, so each one is bounded by a timeout: a program that hangs ends the watch instead of
//! holding the thread.

use windows::Win32::System::Com::{
    CLSCTX_INPROC_SERVER, COINIT_MULTITHREADED, CoCreateInstance, CoInitializeEx, CoUninitialize,
};
use windows::Win32::UI::Accessibility::{
    CUIAutomation, IUIAutomation, IUIAutomation2, IUIAutomationElement, IUIAutomationTextPattern,
    IUIAutomationValuePattern, UIA_TextPatternId, UIA_ValuePatternId,
};
use windows::Win32::UI::WindowsAndMessaging::GetForegroundWindow;
use windows::core::Interface;

use super::{FieldProbe, Snapshot, WatchedField};
use crate::paste::Target;

/// The most characters read from a field.
const MAX_CHARS: usize = 20_000;
/// How long one UI Automation call may wait for the target program, in ms.
const CALL_TIMEOUT_MS: u32 = 2_000;

/// The [`FieldProbe`] for Windows.
pub struct UiaProbe;

impl FieldProbe for UiaProbe {
    fn focused(&self, target: &Target) -> Option<Box<dyn WatchedField>> {
        let com = Com::join()?;
        // SAFETY: COM is initialized on this thread; the client is released before `com`.
        let automation: IUIAutomation =
            match unsafe { CoCreateInstance(&CUIAutomation, None, CLSCTX_INPROC_SERVER) } {
                Ok(automation) => automation,
                Err(err) => {
                    log::warn!("learn: no UI Automation: {err}");
                    return None;
                }
            };
        if let Ok(automation) = automation.cast::<IUIAutomation2>() {
            // SAFETY: plain setters. Windows 7 has no IUIAutomation2; the defaults apply there.
            unsafe {
                drop(automation.SetConnectionTimeout(CALL_TIMEOUT_MS));
                drop(automation.SetTransactionTimeout(CALL_TIMEOUT_MS));
            }
        }
        if foreground() != target.hwnd {
            log::info!("learn: the window changed right after the paste");
            return None;
        }
        // SAFETY: plain UI Automation queries on this thread's client.
        let element = unsafe { automation.GetFocusedElement() }.ok()?;
        if unsafe { element.CurrentIsPassword() }.is_ok_and(|password| password.as_bool()) {
            log::info!("learn: not watching a password field");
            return None;
        }
        let value =
            unsafe { element.GetCurrentPatternAs::<IUIAutomationValuePattern>(UIA_ValuePatternId) }
                .ok();
        let text =
            unsafe { element.GetCurrentPatternAs::<IUIAutomationTextPattern>(UIA_TextPatternId) }
                .ok();
        if value.is_none() && text.is_none() {
            log::info!("learn: the focused element has no text to read");
            return None;
        }
        log::info!(
            "learn: watching the field (value pattern: {}, text pattern: {})",
            value.is_some(),
            text.is_some()
        );
        Some(Box::new(UiaField {
            automation,
            element,
            value,
            text,
            hwnd: target.hwnd,
            _com: com,
        }))
    }
}

/// The watched field. The fields drop in order, so the COM objects go before `_com`.
struct UiaField {
    automation: IUIAutomation,
    element: IUIAutomationElement,
    value: Option<IUIAutomationValuePattern>,
    text: Option<IUIAutomationTextPattern>,
    hwnd: isize,
    _com: Com,
}

impl WatchedField for UiaField {
    fn read(&mut self) -> Snapshot {
        if foreground() != self.hwnd || !self.still_focused() {
            return Snapshot::Left;
        }
        match self.text_now() {
            Some(text) => Snapshot::Text(text),
            None => Snapshot::Left,
        }
    }
}

impl UiaField {
    fn still_focused(&self) -> bool {
        // SAFETY: plain UI Automation queries on this thread's client.
        unsafe {
            self.automation
                .GetFocusedElement()
                .and_then(|now| self.automation.CompareElements(&now, &self.element))
                .is_ok_and(|same| same.as_bool())
        }
    }

    /// The field's text: its value, or, if that is empty or missing, its document's text.
    fn text_now(&self) -> Option<String> {
        if let Some(value) = &self.value {
            // SAFETY: as above.
            if let Ok(value) = unsafe { value.CurrentValue() } {
                let value = value.to_string();
                if !value.is_empty() || self.text.is_none() {
                    return Some(value.chars().take(MAX_CHARS).collect());
                }
            }
        }
        let text = self.text.as_ref()?;
        // SAFETY: as above.
        let range = unsafe { text.DocumentRange() }.ok()?;
        let text = unsafe { range.GetText(MAX_CHARS as i32) }.ok()?;
        Some(text.to_string())
    }
}

/// This thread's membership of the multithreaded COM apartment.
struct Com;

impl Com {
    fn join() -> Option<Self> {
        // SAFETY: balanced by `CoUninitialize` in `drop`, on the same thread (the watch thread
        // keeps the guard until it ends).
        let joined = unsafe { CoInitializeEx(None, COINIT_MULTITHREADED) };
        if joined.is_err() {
            log::warn!("learn: COM is not available on this thread: {joined:?}");
            return None;
        }
        Some(Self)
    }
}

impl Drop for Com {
    fn drop(&mut self) {
        // SAFETY: this thread joined in `join`.
        unsafe { CoUninitialize() };
    }
}

/// The window that has focus now, as a number.
fn foreground() -> isize {
    // SAFETY: a plain query.
    unsafe { GetForegroundWindow() }.0 as isize
}
