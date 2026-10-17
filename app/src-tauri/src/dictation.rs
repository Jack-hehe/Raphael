//! The dictation controller: hotkey → recording → processing → paste.
//!
//! [`DictationController`] turns the dictation key's events (through [`HoldTap`]) into calls to
//! the core (`dictation.start`, `dictation.stop`, `dictation.cancel`), drives the overlay, and
//! hands the finished text to the [`Paster`]. It updates its state at once and runs the core calls
//! in tasks of their own, so Esc is handled while a stop call is still in flight. Each recording
//! has its own session id; a result for a session that is no longer the current one (cancelled,
//! or the core restarted meanwhile) is dropped.
//!
//! Two rules keep it out of trouble with the real overlay and paster:
//!
//! - Overlay calls are made outside the state lock: they can wait for the main thread, and the
//!   tray's handlers run on the main thread.
//! - `hide` never follows a `notice`: a hide cancels the notice and the ones queued after it.
//!
//! [`spawn`] builds the controller from the app state and runs it in a task that listens to the
//! hotkeys, the core's notifications and the tray.

use std::sync::{Arc, Mutex};

use serde_json::{Value, json};
use tokio::task::JoinHandle;

use crate::holdtap::{Action, HoldTap};
use crate::hotkey::{CancelSwitch, HotkeyEvent};
use crate::notices::{self, Notice};
use crate::overlay::{Mode, OverlayApi};
use crate::paste::{CopyReason, PasteOutcome, Paster, Target};
use crate::rpc::{CoreApi, lock};

/// `NO_MICROPHONE` in the core's `protocol.py`.
const NO_MICROPHONE: i64 = 1004;
/// A paste-last press this soon after a recording started cancels that recording: Right Alt+V
/// pressed the dictation key first.
const PASTE_LAST_CANCELS_WITHIN_MS: u64 = 1000;

/// Where the dictation is.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Phase {
    Idle,
    Recording,
    /// The key is released; the core is finishing the text, or it is being pasted.
    Processing,
}

struct State {
    phase: Phase,
    holdtap: HoldTap,
    /// The current recording's session id, while not idle.
    session: Option<String>,
    /// When the current recording started, on the clock of [`DictationController::on_hotkey`].
    started_ms: u64,
    /// The fallback notices already shown in this app session: each is shown once.
    shown: Vec<Notice>,
}

/// The dictation controller. Cheap to clone: the clones share their state.
pub struct DictationController<C: CoreApi, O: OverlayApi, P: Paster, S: CancelSwitch> {
    core: Arc<C>,
    overlay: Arc<O>,
    paster: Arc<P>,
    cancel: Arc<S>,
    state: Arc<Mutex<State>>,
}

impl<C: CoreApi, O: OverlayApi, P: Paster, S: CancelSwitch> Clone
    for DictationController<C, O, P, S>
{
    fn clone(&self) -> Self {
        Self {
            core: self.core.clone(),
            overlay: self.overlay.clone(),
            paster: self.paster.clone(),
            cancel: self.cancel.clone(),
            state: self.state.clone(),
        }
    }
}

impl<C: CoreApi, O: OverlayApi, P: Paster, S: CancelSwitch> DictationController<C, O, P, S> {
    pub fn new(core: Arc<C>, overlay: Arc<O>, paster: Arc<P>, cancel: Arc<S>) -> Self {
        Self {
            core,
            overlay,
            paster,
            cancel,
            state: Arc::new(Mutex::new(State {
                phase: Phase::Idle,
                holdtap: HoldTap::new(),
                session: None,
                started_ms: 0,
                shown: Vec::new(),
            })),
        }
    }

    pub fn phase(&self) -> Phase {
        lock(&self.state).phase
    }

    /// Handles a key event that happened at `now_ms` (any millisecond clock that only goes
    /// forward). Returns the task that makes the core calls, if any. Must be called from within
    /// a Tokio runtime.
    pub fn on_hotkey(&self, ev: HotkeyEvent, now_ms: u64) -> Option<JoinHandle<()>> {
        match ev {
            HotkeyEvent::DictationDown => {
                let action = {
                    let mut state = lock(&self.state);
                    if state.phase == Phase::Processing {
                        log::debug!("dictation key ignored: still processing the last one");
                        return None;
                    }
                    state.holdtap.on_down(now_ms)
                };
                self.act(action, now_ms)
            }
            HotkeyEvent::DictationUp => {
                let action = lock(&self.state).holdtap.on_up(now_ms);
                self.act(action, now_ms)
            }
            HotkeyEvent::Cancel => self.cancel_current(),
            HotkeyEvent::PasteLast => Some(self.paste_last_at(Some(now_ms))),
        }
    }

    /// The microphone's loudness, from 0 to 1. Shown only while recording.
    pub fn on_level(&self, level: f32) {
        if self.phase() == Phase::Recording {
            self.overlay.level(level);
        }
    }

    /// The core was started again (or is gone for good): whatever it was doing is lost. Back to
    /// idle; the overlay is hidden only if a dictation was showing, so a notice stays.
    pub fn on_core_restarted(&self) {
        let was = {
            let mut state = lock(&self.state);
            let was = state.phase;
            state.phase = Phase::Idle;
            state.session = None;
            state.holdtap.reset();
            was
        };
        self.cancel.set(false);
        if was != Phase::Idle {
            log::warn!("the core restarted during a dictation; it is lost");
            self.overlay.hide();
        }
    }

    /// Pastes the latest dictation from the history into the window that has focus now (the
    /// tray's 「貼上上一次聽寫」).
    pub fn paste_last(&self) -> JoinHandle<()> {
        self.paste_last_at(None)
    }

    fn act(&self, action: Action, now_ms: u64) -> Option<JoinHandle<()>> {
        match action {
            Action::StartRecording => self.start(now_ms),
            Action::StopRecording => self.stop(),
            Action::None => None,
        }
    }

    fn start(&self, now_ms: u64) -> Option<JoinHandle<()>> {
        let session = uuid::Uuid::new_v4().to_string();
        {
            let mut state = lock(&self.state);
            if state.phase != Phase::Idle {
                return None;
            }
            state.phase = Phase::Recording;
            state.session = Some(session.clone());
            state.started_ms = now_ms;
        }
        self.overlay.show(Mode::Listening);
        self.cancel.set(true);

        let this = self.clone();
        Some(tokio::spawn(async move {
            let started = this
                .core
                .call("dictation.start", json!({ "session_id": session }))
                .await;
            let Err(err) = started else { return };
            if !this.end_session(&session) {
                return;
            }
            if err.code == NO_MICROPHONE {
                log::warn!("dictation.start: no microphone ({err})");
                this.show(notices::NO_MIC);
            } else {
                log::error!("dictation.start failed: {err}");
                this.overlay.hide();
            }
        }))
    }

    fn stop(&self) -> Option<JoinHandle<()>> {
        let session = {
            let mut state = lock(&self.state);
            let session = state.session.clone();
            if state.phase != Phase::Recording || session.is_none() {
                return None;
            }
            state.phase = Phase::Processing;
            session?
        };
        // The window to paste into is the one that has focus when the key is released.
        let target = self.paster.foreground();
        self.overlay.set_mode(Mode::Thinking);

        let this = self.clone();
        Some(tokio::spawn(async move {
            let target_app = target.as_ref().map(|target| target.process_name.clone());
            let reply = this
                .core
                .call(
                    "dictation.stop",
                    json!({ "session_id": session, "target_app": target_app }),
                )
                .await;
            if !this.is_current(&session) {
                log::info!("dropping the result of a dictation that was cancelled");
                return;
            }
            let reply = match reply {
                Ok(reply) => reply,
                Err(err) => {
                    log::error!("dictation.stop failed: {err}");
                    if this.end_session(&session) {
                        this.overlay.hide();
                    }
                    return;
                }
            };
            let text = reply.get("text").and_then(Value::as_str).unwrap_or("");
            if text.is_empty() {
                log::info!("the dictation has no text; nothing to paste");
                if this.end_session(&session) {
                    this.overlay.hide();
                }
                return;
            }
            let reason = reply
                .get("fallback_reason")
                .and_then(Value::as_str)
                .map(str::to_owned);

            let outcome = this.deliver(text.to_owned(), target).await;
            if !this.end_session(&session) {
                return;
            }
            let fallback = this.first_time(notices::notice_for_fallback(reason.as_deref()));
            this.report(outcome, fallback, true);
        }))
    }

    fn cancel_current(&self) -> Option<JoinHandle<()>> {
        let session = {
            let mut state = lock(&self.state);
            if state.phase == Phase::Idle {
                return None;
            }
            state.phase = Phase::Idle;
            state.holdtap.reset();
            state.session.take()
        };
        self.cancel.set(false);
        self.overlay.hide();
        let session = session?;
        Some(self.spawn_cancel_call(session))
    }

    fn spawn_cancel_call(&self, session: String) -> JoinHandle<()> {
        let core = self.core.clone();
        tokio::spawn(async move { cancel_call(core.as_ref(), &session).await })
    }

    fn paste_last_at(&self, now_ms: Option<u64>) -> JoinHandle<()> {
        // Right Alt+V: the Right Alt went down first and started a recording. Drop it quietly.
        let cancelled = now_ms.and_then(|now_ms| {
            let mut state = lock(&self.state);
            let fresh = state.phase == Phase::Recording
                && now_ms.saturating_sub(state.started_ms) < PASTE_LAST_CANCELS_WITHIN_MS;
            if !fresh {
                return None;
            }
            state.phase = Phase::Idle;
            state.holdtap.reset();
            state.session.take()
        });
        if cancelled.is_some() {
            self.cancel.set(false);
            self.overlay.hide();
        }
        let target = self.paster.foreground();

        let this = self.clone();
        tokio::spawn(async move {
            if let Some(session) = cancelled {
                cancel_call(this.core.as_ref(), &session).await;
            }
            let latest = match this.core.call("history.latest", json!({})).await {
                Ok(latest) => latest,
                Err(err) => {
                    log::error!("history.latest failed: {err}");
                    return;
                }
            };
            let text = latest
                .get("item")
                .and_then(|item| item.get("text"))
                .and_then(Value::as_str)
                .filter(|text| !text.is_empty());
            let Some(text) = text else {
                log::info!("paste last: the history is empty");
                return;
            };
            let outcome = this.deliver(text.to_owned(), target).await;
            this.report(outcome, None, false);
        })
    }

    /// Pastes on a blocking thread: the paster can take seconds.
    async fn deliver(&self, text: String, target: Option<Target>) -> Option<PasteOutcome> {
        let paster = self.paster.clone();
        match tokio::task::spawn_blocking(move || paster.deliver(&text, target.as_ref())).await {
            Ok(outcome) => Some(outcome),
            Err(err) => {
                log::error!("pasting failed: {err}");
                None
            }
        }
    }

    /// Shows what became of a delivery: the notice for text that was only copied, then the
    /// fallback notice. With neither, a dictation's emblem is hidden (`hide_if_quiet`).
    fn report(&self, outcome: Option<PasteOutcome>, fallback: Option<Notice>, hide_if_quiet: bool) {
        let copied = match outcome {
            Some(PasteOutcome::CopiedOnly(CopyReason::Elevated)) => Some(notices::ELEVATED),
            Some(PasteOutcome::CopiedOnly(CopyReason::FocusChanged | CopyReason::NoTarget)) => {
                Some(notices::FOCUS_CHANGED)
            }
            Some(PasteOutcome::Pasted) | None => None,
        };
        let mut shown = false;
        for notice in copied.into_iter().chain(fallback) {
            self.show(notice);
            shown = true;
        }
        if !shown && hide_if_quiet {
            self.overlay.hide();
        }
    }

    fn show(&self, notice: Notice) {
        self.overlay.notice(notice.zh, notice.ja);
    }

    /// `notice`, unless it was shown before in this app session.
    fn first_time(&self, notice: Option<Notice>) -> Option<Notice> {
        let notice = notice?;
        let mut state = lock(&self.state);
        if state.shown.contains(&notice) {
            return None;
        }
        state.shown.push(notice);
        Some(notice)
    }

    fn is_current(&self, session: &str) -> bool {
        lock(&self.state).session.as_deref() == Some(session)
    }

    /// Ends `session` if it is still the current one: back to idle, cancel keys off. Returns
    /// whether it was current; if not, whoever ended it has taken care of the overlay.
    fn end_session(&self, session: &str) -> bool {
        {
            let mut state = lock(&self.state);
            if state.session.as_deref() != Some(session) {
                return false;
            }
            state.phase = Phase::Idle;
            state.session = None;
            state.holdtap.reset();
        }
        self.cancel.set(false);
        true
    }
}

async fn cancel_call<C: CoreApi>(core: &C, session: &str) {
    if let Err(err) = core
        .call("dictation.cancel", json!({ "session_id": session }))
        .await
    {
        log::warn!("dictation.cancel failed: {err}");
    }
}

#[cfg(test)]
impl<C: CoreApi, O: OverlayApi, P: Paster, S: CancelSwitch> DictationController<C, O, P, S> {
    fn core(&self) -> &C {
        &self.core
    }
    fn overlay(&self) -> &O {
        &self.overlay
    }
    fn paster(&self) -> &P {
        &self.paster
    }
    fn cancel(&self) -> &S {
        &self.cancel
    }
}

// ---------- in the app ----------

#[cfg(windows)]
pub use app::spawn;

#[cfg(windows)]
mod app {
    use std::future::pending;
    use std::sync::Arc;
    use std::time::{Duration, Instant};

    use serde_json::Value;
    use tauri::{AppHandle, Manager};
    use tokio::sync::broadcast::{self, error::RecvError};
    use tokio::sync::mpsc::UnboundedReceiver;
    use tokio::task::JoinHandle;

    use super::DictationController;
    use crate::core_process::{CORE_DOWN, CORE_RESTARTED, CoreProcess};
    use crate::hotkey::{CancelSwitch, HotkeyEvent, HotkeyService};
    use crate::overlay::{Mode, Overlay, OverlayApi};
    use crate::paste::WindowsPaster;
    use crate::rpc::Notification;
    use crate::tray::TrayCommand;

    /// How long 「暫停 1 小時」 pauses the hotkeys.
    const PAUSE: Duration = Duration::from_secs(3600);

    /// The overlay, if it could be created. Without it dictation works, unseen.
    struct MaybeOverlay(Option<Arc<Overlay>>);

    impl OverlayApi for MaybeOverlay {
        fn show(&self, mode: Mode) {
            if let Some(overlay) = &self.0 {
                overlay.show(mode);
            }
        }
        fn set_mode(&self, mode: Mode) {
            if let Some(overlay) = &self.0 {
                overlay.set_mode(mode);
            }
        }
        fn level(&self, value: f32) {
            if let Some(overlay) = &self.0 {
                overlay.level(value);
            }
        }
        fn notice(&self, zh: &str, ja: &str) {
            match &self.0 {
                Some(overlay) => overlay.notice(zh, ja),
                None => log::warn!("notice without an overlay: {zh}"),
            }
        }
        fn hide(&self) {
            if let Some(overlay) = &self.0 {
                overlay.hide();
            }
        }
    }

    /// The hotkey service, if it started. Without it there are no cancel keys to switch.
    struct MaybeHotkeys(Option<Arc<HotkeyService>>);

    impl CancelSwitch for MaybeHotkeys {
        fn set(&self, on: bool) {
            if let Some(hotkeys) = &self.0 {
                hotkeys.set_cancel_enabled(on);
            }
        }
    }

    type Controller = DictationController<CoreProcess, MaybeOverlay, WindowsPaster, MaybeHotkeys>;

    /// Builds the controller from what is in the app state (`Arc<CoreProcess>`, `Arc<Overlay>`,
    /// `Arc<HotkeyService>`, each of which may be missing) and runs it in a task that listens to
    /// the hotkeys, the core's notifications and the tray. Without a core, hotkeys are logged and
    /// ignored.
    pub fn spawn(
        app: &AppHandle,
        hotkey_events: Option<UnboundedReceiver<HotkeyEvent>>,
        tray_commands: UnboundedReceiver<TrayCommand>,
    ) {
        let core = app
            .try_state::<Arc<CoreProcess>>()
            .map(|core| core.inner().clone());
        let overlay = app
            .try_state::<Arc<Overlay>>()
            .map(|overlay| overlay.inner().clone());
        let hotkeys = app
            .try_state::<Arc<HotkeyService>>()
            .map(|hotkeys| hotkeys.inner().clone());

        let notifications = core.as_ref().map(|core| core.subscribe());
        let controller = match core {
            Some(core) => Some(DictationController::new(
                core,
                Arc::new(MaybeOverlay(overlay)),
                Arc::new(WindowsPaster::new()),
                Arc::new(MaybeHotkeys(hotkeys.clone())),
            )),
            None => {
                log::error!("dictation is off: the Python core is not running");
                None
            }
        };
        tauri::async_runtime::spawn(run(
            controller,
            hotkeys,
            hotkey_events,
            notifications,
            Some(tray_commands),
        ));
    }

    async fn run(
        controller: Option<Controller>,
        hotkeys: Option<Arc<HotkeyService>>,
        mut hotkey_events: Option<UnboundedReceiver<HotkeyEvent>>,
        mut notifications: Option<broadcast::Receiver<Notification>>,
        mut tray_commands: Option<UnboundedReceiver<TrayCommand>>,
    ) {
        let clock = Instant::now();
        let now_ms = || u64::try_from(clock.elapsed().as_millis()).unwrap_or(u64::MAX);
        let mut resume: Option<JoinHandle<()>> = None;

        loop {
            tokio::select! {
                event = next(&mut hotkey_events) => match event {
                    Some(event) => match &controller {
                        // The task runs by itself; nothing waits for it.
                        Some(controller) => drop(controller.on_hotkey(event, now_ms())),
                        None => log::info!("hotkey {event:?} ignored: the core is not running"),
                    },
                    None => hotkey_events = None,
                },
                notification = next_notification(&mut notifications) => {
                    let Some(controller) = &controller else { continue };
                    match notification {
                        Ok(notification) => on_notification(controller, &notification),
                        Err(RecvError::Lagged(missed)) => {
                            // A core.restarted may have been among them.
                            log::warn!("missed {missed} notifications from the core; resetting the dictation");
                            drop(controller.on_hotkey(HotkeyEvent::Cancel, now_ms()));
                            controller.on_core_restarted();
                        }
                        Err(RecvError::Closed) => notifications = None,
                    }
                }
                command = next(&mut tray_commands) => match command {
                    Some(TrayCommand::PasteLast) => match &controller {
                        Some(controller) => drop(controller.paste_last()),
                        None => log::info!("paste last ignored: the core is not running"),
                    },
                    Some(TrayCommand::PauseOneHour) => pause(hotkeys.as_ref(), &mut resume),
                    None => tray_commands = None,
                },
            }
        }
    }

    fn on_notification(controller: &Controller, notification: &Notification) {
        match notification.method.as_str() {
            "audio.level" => {
                if let Some(level) = notification.params.get("level").and_then(Value::as_f64) {
                    controller.on_level(level as f32);
                }
            }
            CORE_RESTARTED | CORE_DOWN => controller.on_core_restarted(),
            _ => {}
        }
    }

    /// Pauses the hotkeys for an hour. Pausing again starts the hour over.
    fn pause(hotkeys: Option<&Arc<HotkeyService>>, resume: &mut Option<JoinHandle<()>>) {
        let Some(hotkeys) = hotkeys else {
            log::warn!("pause ignored: the hotkeys are off");
            return;
        };
        log::info!("hotkeys paused for {PAUSE:?}");
        hotkeys.set_paused(true);
        if let Some(previous) = resume.take() {
            previous.abort();
        }
        let hotkeys = hotkeys.clone();
        *resume = Some(tokio::spawn(async move {
            tokio::time::sleep(PAUSE).await;
            log::info!("hotkeys resumed");
            hotkeys.set_paused(false);
        }));
    }

    /// The next message, or never once the channel is gone.
    async fn next<T>(receiver: &mut Option<UnboundedReceiver<T>>) -> Option<T> {
        match receiver {
            Some(receiver) => receiver.recv().await,
            None => pending().await,
        }
    }

    async fn next_notification(
        receiver: &mut Option<broadcast::Receiver<Notification>>,
    ) -> Result<Notification, RecvError> {
        match receiver {
            Some(receiver) => receiver.recv().await,
            None => pending().await,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::hotkey::{CancelSwitch, HotkeyEvent};
    use crate::notices::{ELEVATED, FOCUS_CHANGED, NO_MIC, OFFLINE};
    use crate::overlay::{Mode, OverlayApi};
    use crate::paste::{CopyReason, PasteOutcome, Paster, Target};
    use crate::rpc::{CORE_EXITED, CoreApi, RpcError};
    use serde_json::{Value, json};
    use std::collections::HashMap;
    use std::sync::{Arc, Mutex};
    use tokio::sync::Notify;

    // ---------- fakes ----------

    #[derive(Default)]
    struct FakeCore {
        results: Mutex<HashMap<String, Result<Value, RpcError>>>,
        calls: Mutex<Vec<(String, Value)>>,
        /// Replies to this method wait until the gate is opened.
        gate: Option<(String, Arc<Notify>)>,
    }

    impl FakeCore {
        fn with(method: &str, result: Value) -> Self {
            let core = Self::default();
            core.script(method, Ok(result));
            core
        }

        fn failing(method: &str, err: RpcError) -> Self {
            let core = Self::default();
            core.script(method, Err(err));
            core
        }

        fn gated(mut self, method: &str) -> (Self, Arc<Notify>) {
            let gate = Arc::new(Notify::new());
            self.gate = Some((method.to_owned(), gate.clone()));
            (self, gate)
        }

        fn script(&self, method: &str, result: Result<Value, RpcError>) {
            self.results
                .lock()
                .unwrap()
                .insert(method.to_owned(), result);
        }

        fn count(&self, method: &str) -> usize {
            self.calls
                .lock()
                .unwrap()
                .iter()
                .filter(|(m, _)| m == method)
                .count()
        }

        fn call_params(&self, method: &str) -> Value {
            self.calls
                .lock()
                .unwrap()
                .iter()
                .rev()
                .find(|(m, _)| m == method)
                .map(|(_, params)| params.clone())
                .unwrap_or_else(|| panic!("{method} was never called"))
        }
    }

    impl CoreApi for FakeCore {
        async fn call(&self, method: &str, params: Value) -> Result<Value, RpcError> {
            self.calls.lock().unwrap().push((method.to_owned(), params));
            if let Some((gated, gate)) = &self.gate
                && gated == method
            {
                gate.notified().await;
            }
            let scripted = self.results.lock().unwrap().get(method).cloned();
            scripted.unwrap_or_else(|| Ok(json!({})))
        }
    }

    #[derive(Default)]
    struct FakeOverlay {
        events: Mutex<Vec<String>>,
    }

    impl FakeOverlay {
        fn events(&self) -> Vec<String> {
            self.events.lock().unwrap().clone()
        }

        fn push(&self, event: String) {
            self.events.lock().unwrap().push(event);
        }
    }

    fn mode_name(mode: Mode) -> String {
        format!("{mode:?}").to_lowercase()
    }

    impl OverlayApi for FakeOverlay {
        fn show(&self, mode: Mode) {
            self.push(format!("show:{}", mode_name(mode)));
        }
        fn set_mode(&self, mode: Mode) {
            self.push(format!("mode:{}", mode_name(mode)));
        }
        fn level(&self, value: f32) {
            self.push(format!("level:{value}"));
        }
        fn notice(&self, zh: &str, _ja: &str) {
            self.push(format!("notice:{zh}"));
        }
        fn hide(&self) {
            self.push("hide".to_owned());
        }
    }

    struct FakePaster {
        foreground: Option<Target>,
        outcome: PasteOutcome,
        delivered: Mutex<Vec<String>>,
    }

    impl FakePaster {
        fn target(process_name: &str) -> Target {
            Target {
                hwnd: 42,
                pid: 7,
                process_name: process_name.to_owned(),
                is_elevated: false,
            }
        }

        fn pasting(process_name: &str) -> Self {
            Self {
                foreground: Some(Self::target(process_name)),
                outcome: PasteOutcome::Pasted,
                delivered: Mutex::default(),
            }
        }

        fn copying(process_name: &str, reason: CopyReason) -> Self {
            Self {
                outcome: PasteOutcome::CopiedOnly(reason),
                ..Self::pasting(process_name)
            }
        }

        fn delivered(&self) -> Vec<String> {
            self.delivered.lock().unwrap().clone()
        }
    }

    impl Paster for FakePaster {
        fn foreground(&self) -> Option<Target> {
            self.foreground.clone()
        }

        fn deliver(&self, text: &str, at_stop: Option<&Target>) -> PasteOutcome {
            assert_eq!(
                at_stop,
                self.foreground.as_ref(),
                "delivered to the target taken at stop"
            );
            self.delivered.lock().unwrap().push(text.to_owned());
            self.outcome
        }
    }

    #[derive(Default)]
    struct FakeCancel {
        last: Mutex<Option<bool>>,
    }

    impl FakeCancel {
        fn last(&self) -> Option<bool> {
            *self.last.lock().unwrap()
        }
    }

    impl CancelSwitch for FakeCancel {
        fn set(&self, on: bool) {
            *self.last.lock().unwrap() = Some(on);
        }
    }

    type Ctl = DictationController<FakeCore, FakeOverlay, FakePaster, FakeCancel>;

    fn controller(core: FakeCore, paster: FakePaster) -> Ctl {
        DictationController::new(
            Arc::new(core),
            Arc::new(FakeOverlay::default()),
            Arc::new(paster),
            Arc::new(FakeCancel::default()),
        )
    }

    fn stop_result(text: &str, fallback_reason: Option<&str>) -> Value {
        json!({"id": 1, "text": text, "fallback_reason": fallback_reason})
    }

    async fn run(c: &Ctl, ev: HotkeyEvent, t: u64) {
        if let Some(h) = c.on_hotkey(ev, t) {
            h.await.unwrap();
        }
    }

    fn notice(zh: &str) -> String {
        format!("notice:{zh}")
    }

    // ---------- tests ----------

    #[tokio::test]
    async fn hold_then_release_pastes_cleaned_text() {
        let c = controller(
            FakeCore::with(
                "dictation.stop",
                json!({"text": "明天下午三點開會。", "fallback_reason": null}),
            ),
            FakePaster::pasting("Code.exe"),
        );
        run(&c, HotkeyEvent::DictationDown, 0).await;
        assert_eq!(c.phase(), Phase::Recording);
        assert_eq!(c.cancel().last(), Some(true));
        run(&c, HotkeyEvent::DictationUp, 900).await;
        assert_eq!(c.paster().delivered(), vec!["明天下午三點開會。"]);
        assert_eq!(
            c.overlay().events(),
            vec!["show:listening", "mode:thinking", "hide"]
        );
        assert_eq!(c.phase(), Phase::Idle);
        assert_eq!(c.cancel().last(), Some(false));
        assert_eq!(
            c.core().call_params("dictation.stop")["target_app"],
            "Code.exe"
        );
        // the stop is for the session that was started
        let session = c.core().call_params("dictation.start")["session_id"].clone();
        assert!(session.as_str().is_some_and(|s| !s.is_empty()));
        assert_eq!(
            c.core().call_params("dictation.stop")["session_id"],
            session
        );
    }

    #[tokio::test]
    async fn tap_starts_and_second_press_stops() {
        let c = controller(
            FakeCore::with("dictation.stop", stop_result("好", None)),
            FakePaster::pasting("notepad.exe"),
        );
        run(&c, HotkeyEvent::DictationDown, 0).await;
        run(&c, HotkeyEvent::DictationUp, 120).await;
        assert_eq!(c.phase(), Phase::Recording);
        assert_eq!(c.core().count("dictation.stop"), 0);
        run(&c, HotkeyEvent::DictationDown, 2000).await;
        assert_eq!(c.paster().delivered(), vec!["好"]);
        assert_eq!(c.phase(), Phase::Idle);
        run(&c, HotkeyEvent::DictationUp, 2100).await;
        assert_eq!(c.phase(), Phase::Idle);
        assert_eq!(c.core().count("dictation.start"), 1);
        assert_eq!(c.core().count("dictation.stop"), 1);
    }

    #[tokio::test]
    async fn each_recording_gets_a_new_session_id() {
        let c = controller(
            FakeCore::with("dictation.stop", stop_result("好", None)),
            FakePaster::pasting("notepad.exe"),
        );
        run(&c, HotkeyEvent::DictationDown, 0).await;
        let first = c.core().call_params("dictation.start")["session_id"].clone();
        run(&c, HotkeyEvent::DictationUp, 900).await;
        run(&c, HotkeyEvent::DictationDown, 5000).await;
        let second = c.core().call_params("dictation.start")["session_id"].clone();
        assert_ne!(first, second);
    }

    #[tokio::test]
    async fn empty_result_hides_without_pasting() {
        let c = controller(
            FakeCore::with(
                "dictation.stop",
                json!({"id": null, "text": "", "fallback_reason": "empty"}),
            ),
            FakePaster::pasting("notepad.exe"),
        );
        run(&c, HotkeyEvent::DictationDown, 0).await;
        run(&c, HotkeyEvent::DictationUp, 900).await;
        assert!(c.paster().delivered().is_empty());
        assert_eq!(c.overlay().events().last().unwrap(), "hide");
        assert_eq!(c.phase(), Phase::Idle);
    }

    #[tokio::test]
    async fn elevated_target_shows_notice() {
        let c = controller(
            FakeCore::with("dictation.stop", stop_result("文字", None)),
            FakePaster::copying("powershell.exe", CopyReason::Elevated),
        );
        run(&c, HotkeyEvent::DictationDown, 0).await;
        run(&c, HotkeyEvent::DictationUp, 900).await;
        assert_eq!(c.paster().delivered(), vec!["文字"]);
        assert_eq!(c.overlay().events().last().unwrap(), &notice(ELEVATED.zh));
        assert!(
            !c.overlay().events().contains(&"hide".to_owned()),
            "a hide would cancel the notice"
        );
        assert_eq!(c.phase(), Phase::Idle);
    }

    #[tokio::test]
    async fn focus_changed_shows_notice() {
        let c = controller(
            FakeCore::with("dictation.stop", stop_result("文字", None)),
            FakePaster::copying("notepad.exe", CopyReason::FocusChanged),
        );
        run(&c, HotkeyEvent::DictationDown, 0).await;
        run(&c, HotkeyEvent::DictationUp, 900).await;
        assert_eq!(
            c.overlay().events().last().unwrap(),
            &notice(FOCUS_CHANGED.zh)
        );
    }

    #[tokio::test]
    async fn no_microphone_returns_to_idle() {
        let c = controller(
            FakeCore::failing("dictation.start", RpcError::new(1004, "no microphone")),
            FakePaster::pasting("notepad.exe"),
        );
        run(&c, HotkeyEvent::DictationDown, 0).await;
        assert_eq!(c.overlay().events().last().unwrap(), &notice(NO_MIC.zh));
        assert_eq!(c.phase(), Phase::Idle);
        assert_eq!(c.cancel().last(), Some(false));
        // the release of that press does nothing
        run(&c, HotkeyEvent::DictationUp, 900).await;
        assert_eq!(c.core().count("dictation.stop"), 0);
        assert_eq!(c.overlay().events().last().unwrap(), &notice(NO_MIC.zh));
    }

    #[tokio::test]
    async fn escape_during_processing_drops_late_result() {
        let (core, gate) =
            FakeCore::with("dictation.stop", stop_result("太晚了", None)).gated("dictation.stop");
        let c = controller(core, FakePaster::pasting("notepad.exe"));
        run(&c, HotkeyEvent::DictationDown, 0).await;
        let up = c
            .on_hotkey(HotkeyEvent::DictationUp, 900)
            .expect("the stop runs in a task");
        tokio::task::yield_now().await;
        assert_eq!(c.phase(), Phase::Processing);
        run(&c, HotkeyEvent::Cancel, 1200).await;
        assert_eq!(c.core().count("dictation.cancel"), 1);
        assert_eq!(
            c.core().call_params("dictation.cancel")["session_id"],
            c.core().call_params("dictation.start")["session_id"]
        );
        assert_eq!(c.overlay().events().last().unwrap(), "hide");
        assert_eq!(c.phase(), Phase::Idle);
        assert_eq!(c.cancel().last(), Some(false));
        let events_after_cancel = c.overlay().events();

        gate.notify_one();
        up.await.unwrap();
        assert!(c.paster().delivered().is_empty());
        assert_eq!(c.overlay().events(), events_after_cancel);
        assert_eq!(c.phase(), Phase::Idle);
    }

    #[tokio::test]
    async fn escape_while_recording_cancels() {
        let c = controller(FakeCore::default(), FakePaster::pasting("notepad.exe"));
        run(&c, HotkeyEvent::DictationDown, 0).await;
        run(&c, HotkeyEvent::Cancel, 2000).await;
        assert_eq!(c.core().count("dictation.cancel"), 1);
        assert_eq!(c.phase(), Phase::Idle);
        // hold mode: the release after Esc does nothing
        run(&c, HotkeyEvent::DictationUp, 2500).await;
        assert_eq!(c.core().count("dictation.stop"), 0);
        // and the next press starts afresh
        run(&c, HotkeyEvent::DictationDown, 4000).await;
        assert_eq!(c.core().count("dictation.start"), 2);
        assert_eq!(c.phase(), Phase::Recording);
    }

    #[tokio::test]
    async fn escape_while_idle_does_nothing() {
        let c = controller(FakeCore::default(), FakePaster::pasting("notepad.exe"));
        run(&c, HotkeyEvent::Cancel, 0).await;
        assert_eq!(c.core().count("dictation.cancel"), 0);
        assert!(c.overlay().events().is_empty());
    }

    #[tokio::test]
    async fn press_during_processing_is_ignored() {
        let (core, gate) =
            FakeCore::with("dictation.stop", stop_result("好", None)).gated("dictation.stop");
        let c = controller(core, FakePaster::pasting("notepad.exe"));
        run(&c, HotkeyEvent::DictationDown, 0).await;
        let up = c
            .on_hotkey(HotkeyEvent::DictationUp, 900)
            .expect("the stop runs in a task");
        tokio::task::yield_now().await;
        run(&c, HotkeyEvent::DictationDown, 1000).await;
        run(&c, HotkeyEvent::DictationUp, 1500).await;
        assert_eq!(c.core().count("dictation.start"), 1);
        assert_eq!(c.phase(), Phase::Processing);
        gate.notify_one();
        up.await.unwrap();
        assert_eq!(c.paster().delivered(), vec!["好"]);
        assert_eq!(c.phase(), Phase::Idle);
    }

    #[tokio::test]
    async fn core_failure_still_returns_to_idle() {
        let c = controller(
            FakeCore::failing(
                "dictation.stop",
                RpcError::new(CORE_EXITED, "the core exited"),
            ),
            FakePaster::pasting("notepad.exe"),
        );
        run(&c, HotkeyEvent::DictationDown, 0).await;
        run(&c, HotkeyEvent::DictationUp, 900).await;
        assert_eq!(c.overlay().events().last().unwrap(), "hide");
        assert_eq!(c.phase(), Phase::Idle);
        assert_eq!(c.cancel().last(), Some(false));
        assert!(c.paster().delivered().is_empty());
    }

    #[tokio::test]
    async fn a_failed_start_returns_to_idle() {
        let c = controller(
            FakeCore::failing(
                "dictation.start",
                RpcError::new(CORE_EXITED, "the core exited"),
            ),
            FakePaster::pasting("notepad.exe"),
        );
        run(&c, HotkeyEvent::DictationDown, 0).await;
        assert_eq!(c.overlay().events().last().unwrap(), "hide");
        assert_eq!(c.phase(), Phase::Idle);
        assert_eq!(c.cancel().last(), Some(false));
    }

    #[tokio::test]
    async fn offline_notice_shows_once_per_session() {
        let c = controller(
            FakeCore::with("dictation.stop", stop_result("原文", Some("error:network"))),
            FakePaster::pasting("notepad.exe"),
        );
        for start in [0, 5000] {
            run(&c, HotkeyEvent::DictationDown, start).await;
            run(&c, HotkeyEvent::DictationUp, start + 900).await;
        }
        assert_eq!(c.paster().delivered(), vec!["原文", "原文"]);
        let offline = c
            .overlay()
            .events()
            .iter()
            .filter(|e| **e == notice(OFFLINE.zh))
            .count();
        assert_eq!(offline, 1);
        // the first dictation ends with the notice (no hide after it), the second with a hide
        assert_eq!(
            c.overlay().events(),
            vec![
                "show:listening",
                "mode:thinking",
                &notice(OFFLINE.zh),
                "show:listening",
                "mode:thinking",
                "hide"
            ]
        );
    }

    #[tokio::test]
    async fn a_plain_timeout_gets_no_notice() {
        let c = controller(
            FakeCore::with("dictation.stop", stop_result("原文", Some("timeout"))),
            FakePaster::pasting("notepad.exe"),
        );
        run(&c, HotkeyEvent::DictationDown, 0).await;
        run(&c, HotkeyEvent::DictationUp, 900).await;
        assert_eq!(c.overlay().events().last().unwrap(), "hide");
    }

    #[tokio::test]
    async fn paste_last_hotkey_cancels_a_fresh_recording() {
        let c = controller(
            FakeCore::with(
                "history.latest",
                json!({"item": {"id": 3, "text": "上一次"}}),
            ),
            FakePaster::pasting("notepad.exe"),
        );
        run(&c, HotkeyEvent::DictationDown, 0).await;
        assert_eq!(c.phase(), Phase::Recording);
        run(&c, HotkeyEvent::PasteLast, 200).await;
        assert_eq!(c.core().count("dictation.cancel"), 1);
        assert_eq!(c.paster().delivered(), vec!["上一次"]);
        assert_eq!(c.phase(), Phase::Idle);
        run(&c, HotkeyEvent::DictationUp, 300).await;
        assert_eq!(c.core().count("dictation.stop"), 0);
        assert_eq!(c.phase(), Phase::Idle);
    }

    #[tokio::test]
    async fn paste_last_from_the_tray_pastes_the_latest_item() {
        let c = controller(
            FakeCore::with(
                "history.latest",
                json!({"item": {"id": 3, "text": "上一次"}}),
            ),
            FakePaster::pasting("notepad.exe"),
        );
        c.paste_last().await.unwrap();
        assert_eq!(c.paster().delivered(), vec!["上一次"]);
        assert_eq!(c.core().count("dictation.cancel"), 0);
        assert!(c.overlay().events().is_empty());
    }

    #[tokio::test]
    async fn paste_last_with_an_empty_history_does_nothing() {
        let c = controller(
            FakeCore::with("history.latest", json!({"item": null})),
            FakePaster::pasting("notepad.exe"),
        );
        c.paste_last().await.unwrap();
        assert!(c.paster().delivered().is_empty());
    }

    #[tokio::test]
    async fn paste_last_that_is_only_copied_shows_the_notice() {
        let c = controller(
            FakeCore::with(
                "history.latest",
                json!({"item": {"id": 3, "text": "上一次"}}),
            ),
            FakePaster::copying("powershell.exe", CopyReason::Elevated),
        );
        c.paste_last().await.unwrap();
        assert_eq!(c.overlay().events(), vec![notice(ELEVATED.zh)]);
    }

    #[tokio::test]
    async fn paste_last_during_a_longer_recording_keeps_it() {
        let c = controller(
            FakeCore::with(
                "history.latest",
                json!({"item": {"id": 3, "text": "上一次"}}),
            ),
            FakePaster::pasting("notepad.exe"),
        );
        run(&c, HotkeyEvent::DictationDown, 0).await;
        run(&c, HotkeyEvent::PasteLast, 1500).await;
        assert_eq!(c.core().count("dictation.cancel"), 0);
        assert_eq!(c.phase(), Phase::Recording);
    }

    #[tokio::test]
    async fn core_restart_resets_to_idle() {
        let c = controller(FakeCore::default(), FakePaster::pasting("notepad.exe"));
        run(&c, HotkeyEvent::DictationDown, 0).await;
        assert_eq!(c.phase(), Phase::Recording);
        c.on_core_restarted();
        assert_eq!(c.overlay().events().last().unwrap(), "hide");
        assert_eq!(c.phase(), Phase::Idle);
        assert_eq!(c.cancel().last(), Some(false));
        run(&c, HotkeyEvent::DictationDown, 5000).await;
        assert_eq!(c.core().count("dictation.start"), 2);
        assert_eq!(c.phase(), Phase::Recording);
    }

    #[tokio::test]
    async fn core_restart_while_idle_leaves_a_notice_alone() {
        let c = controller(
            FakeCore::failing("dictation.start", RpcError::new(1004, "no microphone")),
            FakePaster::pasting("notepad.exe"),
        );
        run(&c, HotkeyEvent::DictationDown, 0).await;
        c.on_core_restarted();
        assert_eq!(c.overlay().events().last().unwrap(), &notice(NO_MIC.zh));
    }

    #[tokio::test]
    async fn level_reaches_the_overlay_only_while_recording() {
        let (core, gate) =
            FakeCore::with("dictation.stop", stop_result("好", None)).gated("dictation.stop");
        let c = controller(core, FakePaster::pasting("notepad.exe"));
        c.on_level(0.5);
        run(&c, HotkeyEvent::DictationDown, 0).await;
        c.on_level(0.25);
        let up = c
            .on_hotkey(HotkeyEvent::DictationUp, 900)
            .expect("the stop runs in a task");
        c.on_level(0.75);
        gate.notify_one();
        up.await.unwrap();
        assert_eq!(
            c.overlay().events(),
            vec!["show:listening", "level:0.25", "mode:thinking", "hide"]
        );
    }
}
