//! The dictation controller: hotkey → recording → processing → paste.
//!
//! [`DictationController`] turns the dictation key's events (through [`HoldTap`]) into calls to
//! the core (`dictation.start`, `dictation.stop`, `dictation.cancel`), drives the overlay, and
//! hands the finished text to the [`Paster`]. It updates its state at once and runs the core calls
//! in tasks of their own, so Esc is handled while a stop call is still in flight. Each recording
//! has its own session id; a result for a session that is no longer the current one (cancelled,
//! or the core restarted meanwhile) is dropped.
//!
//! Rules that keep it out of trouble with the real core, overlay and paster:
//!
//! - Overlay calls are made outside the state lock: they can wait for the main thread, and the
//!   tray's handlers run on the main thread. The cancel switch is only a flag (a channel send),
//!   so it is set under the lock, together with the phase it follows.
//! - `hide` never follows a `notice`: a hide cancels the notice and the ones queued after it.
//! - The core hears of a session's `stop` or `cancel` only after it has answered that session's
//!   `dictation.start`: the calls are made by separate tasks, and a stop that overtook its start
//!   would find no session, leaving the microphone open and the core busy for good.
//!
//! [`spawn`] builds the controller from the app state and runs it in a task that listens to the
//! hotkeys, the core's notifications and the tray.

use std::sync::{Arc, Mutex};

use serde_json::{Value, json};
use tokio::sync::watch;
use tokio::task::JoinHandle;

use crate::holdtap::{Action, HoldTap};
use crate::hotkey::{CancelSwitch, HotkeyEvent};
use crate::notices::{self, Notice};
use crate::overlay::{Mode, OverlayApi};
use crate::paste::{CopyReason, PasteOutcome, Paster, Target};
use crate::rpc::{CORE_EXITED, CoreApi, RpcError, TIMEOUT, lock};

/// `BUSY` in the core's `protocol.py`: a dictation session is already active.
const BUSY: i64 = 1003;
/// `NO_MICROPHONE` in the core's `protocol.py`.
const NO_MICROPHONE: i64 = 1004;
/// `NOT_CONFIGURED` in the core's `protocol.py`: the speech model is not ready.
const NOT_CONFIGURED: i64 = 1001;
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

/// What became of a session's `dictation.start` call.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Started {
    /// The core has not answered yet.
    Pending,
    Yes,
    /// The call failed: the core has no such session, so there is nothing to stop or cancel.
    Failed,
}

/// A recording's session: its id, and whether the core has finished starting it.
#[derive(Clone)]
struct Session {
    id: String,
    started: watch::Receiver<Started>,
}

impl Session {
    /// Waits for the core's answer to this session's `dictation.start`. True if it started; false
    /// if the call failed (or its task is gone without an answer): then a `dictation.stop` or
    /// `dictation.cancel` would find no session.
    async fn start_succeeded(&self) -> bool {
        let mut started = self.started.clone();
        let answer = started
            .wait_for(|started| *started != Started::Pending)
            .await
            .map(|started| *started);
        matches!(answer, Ok(Started::Yes))
    }
}

struct State {
    phase: Phase,
    holdtap: HoldTap,
    /// The current recording's session, while not idle.
    session: Option<Session>,
    /// When the current recording started, on the clock of [`DictationController::on_hotkey`].
    started_ms: u64,
    /// The fallback notices already shown in this app session: each is shown once.
    shown: Vec<Notice>,
    /// [`notices::CORE_DOWN`] was shown since the core last (re)started: it is shown once per
    /// down-state.
    down_noticed: bool,
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
                down_noticed: false,
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
            HotkeyEvent::PasteLast => Some(self.paste_last_at(now_ms)),
        }
    }

    /// The microphone's loudness, from 0 to 1. Shown only while recording.
    pub fn on_level(&self, level: f32) {
        if self.phase() == Phase::Recording {
            self.overlay.level(level);
        }
    }

    /// The core was started again: whatever it was doing is lost (see [`Self::reset`]). If it goes
    /// down again, that is a new down-state, worth a new [`notices::CORE_DOWN`].
    pub fn on_core_restarted(&self) {
        lock(&self.state).down_noticed = false;
        self.reset();
    }

    /// The core is gone for good (`core.down`): whatever it was doing is lost (see
    /// [`Self::reset`]). The next dictation's failure says so, once.
    pub fn on_core_down(&self) {
        self.reset();
    }

    /// Back to idle; the overlay is hidden only if a dictation was showing, so a notice stays.
    fn reset(&self) {
        let was = {
            let mut state = lock(&self.state);
            let was = state.phase;
            state.phase = Phase::Idle;
            state.session = None;
            state.holdtap.reset();
            self.cancel.set(false);
            was
        };
        if was != Phase::Idle {
            log::warn!("the core restarted during a dictation; it is lost");
            self.overlay.hide();
        }
    }

    /// The tray's 「貼上上一次聽寫」: copies the latest dictation from the history and says to
    /// press Ctrl+V. It is not pasted: the tray menu has taken the focus (its own window, or the
    /// taskbar), so there is no window to paste into. The clipboard is not put back. During a
    /// dictation the notice is left out, because the overlay is that dictation's.
    pub fn copy_last(&self) -> JoinHandle<()> {
        let this = self.clone();
        tokio::spawn(async move {
            let Some(text) = this.latest_text().await else {
                return;
            };
            let paster = this.paster.clone();
            if let Err(err) = tokio::task::spawn_blocking(move || paster.copy(&text)).await {
                log::error!("copying the last dictation failed: {err}");
                return;
            }
            if this.phase() == Phase::Idle {
                this.show(notices::LAST_COPIED);
            } else {
                log::info!("the last dictation is copied; no notice during a dictation");
            }
        })
    }

    fn act(&self, action: Action, now_ms: u64) -> Option<JoinHandle<()>> {
        match action {
            Action::StartRecording => {
                log::info!("dictation: recording");
                self.start(now_ms)
            }
            Action::StopRecording => {
                log::info!("dictation: stopping");
                self.stop()
            }
            Action::None => None,
        }
    }

    fn start(&self, now_ms: u64) -> Option<JoinHandle<()>> {
        let id = uuid::Uuid::new_v4().to_string();
        let (answered, started) = watch::channel(Started::Pending);
        {
            let mut state = lock(&self.state);
            if state.phase != Phase::Idle {
                return None;
            }
            state.phase = Phase::Recording;
            state.session = Some(Session {
                id: id.clone(),
                started,
            });
            state.started_ms = now_ms;
            self.cancel.set(true);
        }
        self.overlay.show(Mode::Listening);

        let this = self.clone();
        Some(tokio::spawn(async move {
            let reply = this
                .core
                .call("dictation.start", json!({ "session_id": id }))
                .await;
            let Err(err) = reply else {
                answered.send_replace(Started::Yes);
                return;
            };
            match err.code {
                BUSY => log::error!(
                    "dictation.start: the core is busy with another dictation session ({err}); \
                     this dictation is dropped and the stuck session is cancelled"
                ),
                TIMEOUT => log::error!(
                    "dictation.start timed out ({err}); the core may start it late, so it is \
                     cancelled"
                ),
                _ => log::warn!("dictation.start failed: {err}"),
            }
            // BUSY: some other session holds the core. TIMEOUT: the core may yet start this one,
            // late, and nobody would stop it. Either way the recovery cancel names no session
            // (for BUSY our id means nothing to the core): the core cancels whatever is active.
            // The next press then starts normally; this dictation is not retried. It is sent
            // while this session is still current, so no new session can have started and fall
            // victim to it, and not at all if the session was cancelled meanwhile (the next
            // BUSY recovers).
            if matches!(err.code, BUSY | TIMEOUT) && this.is_current(&id) {
                recovery_cancel(this.core.as_ref()).await;
            }
            this.finish(&id, |this| this.show_failure(&err));
            // Only now: a stop or cancel waiting for this answer has nothing left to stop.
            answered.send_replace(Started::Failed);
        }))
    }

    fn stop(&self) -> Option<JoinHandle<()>> {
        let session = {
            let mut state = lock(&self.state);
            if state.phase != Phase::Recording {
                return None;
            }
            let session = state.session.clone()?;
            state.phase = Phase::Processing;
            session
        };
        // The window to paste into is the one that has focus when the key is released.
        let target = self.paster.foreground();
        self.overlay.set_mode(Mode::Thinking);

        let this = self.clone();
        Some(tokio::spawn(async move {
            // Released before the core answered the start? Then wait: a stop that got there first
            // would find no session. If the start failed, its own error path has dealt with the
            // overlay and there is nothing to stop.
            if !session.start_succeeded().await {
                log::info!("dictation.start failed; no dictation.stop to send");
                return;
            }
            // Esc may have come while waiting; its cancel is on its way, and a stop would only
            // have the core transcribe speech nobody wants.
            if !this.is_current(&session.id) {
                log::info!("the dictation was cancelled before it started; no dictation.stop");
                return;
            }
            let id = session.id;
            let target_app = target.as_ref().map(|target| target.process_name.clone());
            let reply = this
                .core
                .call(
                    "dictation.stop",
                    json!({ "session_id": id, "target_app": target_app }),
                )
                .await;
            if !this.is_current(&id) {
                log::info!("dropping the result of a dictation that was cancelled");
                return;
            }
            let reply = match reply {
                Ok(reply) => reply,
                Err(err) => {
                    log::warn!("dictation.stop failed: {err}");
                    this.finish(&id, |this| this.show_failure(&err));
                    return;
                }
            };
            let text = reply.get("text").and_then(Value::as_str).unwrap_or("");
            if text.is_empty() {
                log::info!("the dictation has no text; nothing to paste");
                this.finish(&id, |this| this.overlay.hide());
                return;
            }
            let reason = reply
                .get("fallback_reason")
                .and_then(Value::as_str)
                .map(str::to_owned);

            let outcome = this.deliver(text.to_owned(), target, Some(&id)).await;
            this.finish(&id, |this| {
                let fallback = this.first_time(notices::notice_for_fallback(reason.as_deref()));
                this.report(outcome, fallback, true);
            });
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
            self.cancel.set(false);
            state.session.take()
        };
        self.overlay.hide();
        let session = session?;
        let core = self.core.clone();
        Some(tokio::spawn(async move {
            cancel_after_start(core.as_ref(), &session).await;
        }))
    }

    /// The paste-last hotkey: pastes the latest dictation from the history into the window that
    /// has focus now.
    fn paste_last_at(&self, now_ms: u64) -> JoinHandle<()> {
        // Right Alt+V: the Right Alt went down first and started a recording. Drop it quietly.
        let cancelled = {
            let mut state = lock(&self.state);
            let fresh = state.phase == Phase::Recording
                && now_ms.saturating_sub(state.started_ms) < PASTE_LAST_CANCELS_WITHIN_MS;
            if fresh {
                state.phase = Phase::Idle;
                state.holdtap.reset();
                self.cancel.set(false);
                state.session.take()
            } else {
                None
            }
        };
        if cancelled.is_some() {
            self.overlay.hide();
        }
        let target = self.paster.foreground();

        let this = self.clone();
        tokio::spawn(async move {
            // The cancel waits for the session's start reply; the paste does not wait for the
            // cancel, so a slow start cannot hold up the text the user asked for.
            let cancel = async {
                if let Some(session) = &cancelled {
                    cancel_after_start(this.core.as_ref(), session).await;
                }
            };
            tokio::join!(cancel, this.paste_latest(target));
        })
    }

    async fn paste_latest(&self, target: Option<Target>) {
        let Some(text) = self.latest_text().await else {
            return;
        };
        let outcome = self.deliver(text, target, None).await;
        self.report(outcome, None, false);
    }

    /// The text of the latest dictation in the history, if there is one.
    async fn latest_text(&self) -> Option<String> {
        let latest = match self.core.call("history.latest", json!({})).await {
            Ok(latest) => latest,
            Err(err) => {
                log::error!("history.latest failed: {err}");
                return None;
            }
        };
        let text = latest
            .get("item")
            .and_then(|item| item.get("text"))
            .and_then(Value::as_str)
            .filter(|text| !text.is_empty());
        if text.is_none() {
            log::info!("paste last: the history is empty");
        }
        text.map(str::to_owned)
    }

    /// Pastes on a blocking thread: the paster can take seconds.
    ///
    /// With a `session`, the paste is dropped if that session is no longer current when the
    /// blocking thread gets to it (Esc, or a core restart, came while it waited in the queue).
    /// That is the last point where Esc can stop it: once `Paster::deliver` has begun, the paste
    /// completes (it takes the clipboard and sends the keys), and a cancel arriving then only
    /// hides the emblem.
    async fn deliver(
        &self,
        text: String,
        target: Option<Target>,
        session: Option<&str>,
    ) -> Option<PasteOutcome> {
        let this = self.clone();
        let session = session.map(str::to_owned);
        let delivered = tokio::task::spawn_blocking(move || {
            if let Some(session) = &session
                && !this.is_current(session)
            {
                log::info!("the dictation was cancelled before it was pasted; not pasting");
                return None;
            }
            Some(this.paster.deliver(&text, target.as_ref()))
        })
        .await;
        match delivered {
            Ok(outcome) => outcome,
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

    /// The end of a dictation whose `dictation.start` or `dictation.stop` failed: Raphael's
    /// notice for the errors the user can do something about, a plain hide for the others.
    fn show_failure(&self, err: &RpcError) {
        match self.failure_notice(err) {
            Some(notice) => self.show(notice),
            None => self.overlay.hide(),
        }
    }

    fn failure_notice(&self, err: &RpcError) -> Option<Notice> {
        match err.code {
            NO_MICROPHONE => Some(notices::NO_MIC),
            NOT_CONFIGURED => Some(notices::NOT_READY),
            CORE_EXITED => {
                let mut state = lock(&self.state);
                if state.down_noticed {
                    return None;
                }
                state.down_noticed = true;
                Some(notices::CORE_DOWN)
            }
            _ => None,
        }
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
        lock(&self.state)
            .session
            .as_ref()
            .is_some_and(|current| current.id == session)
    }

    /// Ends `session` if it is still the current one: back to idle, cancel keys off. Returns
    /// whether it was current; if not, whoever ended it has taken care of the overlay.
    fn end_session(&self, session: &str) -> bool {
        let mut state = lock(&self.state);
        if state
            .session
            .as_ref()
            .is_none_or(|current| current.id != session)
        {
            return false;
        }
        state.phase = Phase::Idle;
        state.session = None;
        state.holdtap.reset();
        // Under the lock, with the phase: a dictation started right after this one must not have
        // its cancel keys switched off by us. The switch is a flag (a channel send), so this
        // neither waits nor calls the overlay.
        self.cancel.set(false);
        true
    }

    /// The end of a session that ran its course: ends it, then shows what became of it with
    /// `show` (overlay calls). If the session was cancelled meanwhile, whoever cancelled it has
    /// taken care of the overlay, and `show` is not called.
    ///
    /// `show` is also skipped if another dictation has started since: the overlay is that one's
    /// now, and a hide or a notice for the old one would wipe its emblem. The check is made right
    /// before `show` and not atomically with it, because overlay calls must stay outside the state
    /// lock; the window that is left is a few instructions wide.
    fn finish(&self, session: &str, show: impl FnOnce(&Self)) {
        if !self.end_session(session) {
            return;
        }
        if self.phase() != Phase::Idle {
            log::info!("a new dictation has started; leaving the overlay to it");
            return;
        }
        show(self);
    }
}

/// Sends `dictation.cancel` for `session` once the core has answered its `dictation.start`. If
/// the start failed there is no session to cancel.
async fn cancel_after_start<C: CoreApi>(core: &C, session: &Session) {
    if !session.start_succeeded().await {
        log::info!("dictation.start failed; no dictation.cancel to send");
        return;
    }
    cancel_call(core, &session.id).await;
}

/// `dictation.cancel` with no session id: the core cancels whatever session is active, and does
/// not fail if there is none.
async fn recovery_cancel<C: CoreApi>(core: &C) {
    if let Err(err) = core.call("dictation.cancel", json!({})).await {
        log::warn!("the recovery dictation.cancel failed: {err}");
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
    use crate::hotkey::{CancelSwitch, HotkeyEvent, HotkeyService, PauseReason};
    use crate::notices;
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
        let overlay = Arc::new(MaybeOverlay(overlay));
        let controller = match core {
            Some(core) => Some(DictationController::new(
                core,
                overlay.clone(),
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
            overlay,
            hotkeys,
            hotkey_events,
            notifications,
            Some(tray_commands),
        ));
    }

    async fn run(
        controller: Option<Controller>,
        overlay: Arc<MaybeOverlay>,
        hotkeys: Option<Arc<HotkeyService>>,
        mut hotkey_events: Option<UnboundedReceiver<HotkeyEvent>>,
        mut notifications: Option<broadcast::Receiver<Notification>>,
        mut tray_commands: Option<UnboundedReceiver<TrayCommand>>,
    ) {
        let clock = Instant::now();
        let now_ms = || u64::try_from(clock.elapsed().as_millis()).unwrap_or(u64::MAX);
        let mut resume: Option<JoinHandle<()>> = None;
        // Without a core, the dictation key is still swallowed: the first press says why
        // nothing happens. There is no core to come back, so it is said once.
        let mut told_no_core = false;

        loop {
            tokio::select! {
                event = next(&mut hotkey_events) => match event {
                    Some(event) => match &controller {
                        // The task runs by itself; nothing waits for it.
                        Some(controller) => drop(controller.on_hotkey(event, now_ms())),
                        None => {
                            log::info!("hotkey {event:?} ignored: the core is not running");
                            if event == HotkeyEvent::DictationDown && !told_no_core {
                                told_no_core = true;
                                overlay.notice(notices::CORE_DOWN.zh, notices::CORE_DOWN.ja);
                            }
                        }
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
                        Some(controller) => drop(controller.copy_last()),
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
            CORE_RESTARTED => controller.on_core_restarted(),
            CORE_DOWN => controller.on_core_down(),
            _ => {}
        }
    }

    /// Pauses the hotkeys for an hour ([`PauseReason::Tray`], independent of the key recorder in
    /// the settings). Pausing again starts the hour over.
    fn pause(hotkeys: Option<&Arc<HotkeyService>>, resume: &mut Option<JoinHandle<()>>) {
        let Some(hotkeys) = hotkeys else {
            log::warn!("pause ignored: the hotkeys are off");
            return;
        };
        log::info!("hotkeys paused for {PAUSE:?}");
        hotkeys.set_paused(PauseReason::Tray, true);
        if let Some(previous) = resume.take() {
            previous.abort();
        }
        let hotkeys = hotkeys.clone();
        *resume = Some(tokio::spawn(async move {
            tokio::time::sleep(PAUSE).await;
            log::info!("hotkeys resumed");
            hotkeys.set_paused(PauseReason::Tray, false);
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
    use crate::notices::{
        CORE_DOWN, ELEVATED, FOCUS_CHANGED, LAST_COPIED, NO_MIC, NOT_READY, OFFLINE,
    };
    use crate::overlay::{Mode, OverlayApi};
    use crate::paste::{CopyReason, PasteOutcome, Paster, Target};
    use crate::rpc::{CORE_EXITED, CoreApi, RpcError, TIMEOUT};
    use serde_json::{Value, json};
    use std::collections::HashMap;
    use std::sync::{Arc, Mutex};
    use tokio::sync::Notify;

    // ---------- fakes ----------

    #[derive(Default)]
    struct FakeCore {
        results: Mutex<HashMap<String, Result<Value, RpcError>>>,
        calls: Mutex<Vec<(String, Value)>>,
        /// What reached the core and when a gated reply was let go: the method name when a call
        /// arrives, and `<method>:reply` when its gate opens.
        order: Mutex<Vec<String>>,
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

        fn order(&self) -> Vec<String> {
            self.order.lock().unwrap().clone()
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
            self.order.lock().unwrap().push(method.to_owned());
            if let Some((gated, gate)) = &self.gate
                && gated == method
            {
                gate.notified().await;
                self.order.lock().unwrap().push(format!("{method}:reply"));
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
        copied: Mutex<Vec<String>>,
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
                copied: Mutex::default(),
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

        fn copied(&self) -> Vec<String> {
            self.copied.lock().unwrap().clone()
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

        fn copy(&self, text: &str) {
            self.copied.lock().unwrap().push(text.to_owned());
        }
    }

    #[derive(Default)]
    struct FakeCancel {
        last: Mutex<Option<bool>>,
        /// The controller's state lock, to see whether it is held while the switch is set.
        probe: Mutex<Option<Arc<Mutex<State>>>>,
        /// Every `set`: the value, and whether the controller's state lock was held.
        sets: Mutex<Vec<(bool, bool)>>,
    }

    impl FakeCancel {
        fn last(&self) -> Option<bool> {
            *self.last.lock().unwrap()
        }

        fn sets(&self) -> Vec<(bool, bool)> {
            self.sets.lock().unwrap().clone()
        }
    }

    impl CancelSwitch for FakeCancel {
        fn set(&self, on: bool) {
            let held = self.probe.lock().unwrap().as_ref().is_some_and(|state| {
                matches!(state.try_lock(), Err(std::sync::TryLockError::WouldBlock))
            });
            self.sets.lock().unwrap().push((on, held));
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

    /// Lets every task that can run, run.
    async fn settle() {
        for _ in 0..5 {
            tokio::task::yield_now().await;
        }
    }

    fn position(order: &[String], what: &str) -> usize {
        order
            .iter()
            .position(|entry| entry == what)
            .unwrap_or_else(|| panic!("{what} is not in {order:?}"))
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
            FakeCore::failing("dictation.stop", RpcError::new(-32603, "internal error")),
            FakePaster::pasting("notepad.exe"),
        );
        run(&c, HotkeyEvent::DictationDown, 0).await;
        run(&c, HotkeyEvent::DictationUp, 900).await;
        assert_eq!(
            c.overlay().events(),
            vec!["show:listening", "mode:thinking", "hide"]
        );
        assert_eq!(c.phase(), Phase::Idle);
        assert_eq!(c.cancel().last(), Some(false));
        assert!(c.paster().delivered().is_empty());
    }

    #[tokio::test]
    async fn a_failed_start_returns_to_idle() {
        let c = controller(
            FakeCore::failing("dictation.start", RpcError::new(-32603, "internal error")),
            FakePaster::pasting("notepad.exe"),
        );
        run(&c, HotkeyEvent::DictationDown, 0).await;
        assert_eq!(c.overlay().events(), vec!["show:listening", "hide"]);
        assert_eq!(c.phase(), Phase::Idle);
        assert_eq!(c.cancel().last(), Some(false));
    }

    // ---------- failures get a word from Raphael ----------

    /// The overlay's last call is the notice `expected`: no hide came after it to cancel it.
    fn ends_with_notice(c: &Ctl, expected: &str) {
        let events = c.overlay().events();
        assert_eq!(events.last().unwrap(), &notice(expected), "{events:?}");
    }

    #[tokio::test]
    async fn no_microphone_at_stop_shows_the_notice() {
        let c = controller(
            FakeCore::failing("dictation.stop", RpcError::new(1004, "no microphone")),
            FakePaster::pasting("notepad.exe"),
        );
        run(&c, HotkeyEvent::DictationDown, 0).await;
        run(&c, HotkeyEvent::DictationUp, 900).await;
        ends_with_notice(&c, NO_MIC.zh);
        assert_eq!(c.phase(), Phase::Idle);
        assert_eq!(c.cancel().last(), Some(false));
    }

    #[tokio::test]
    async fn not_configured_at_start_says_the_model_is_not_ready() {
        let c = controller(
            FakeCore::failing("dictation.start", RpcError::new(1001, "not configured")),
            FakePaster::pasting("notepad.exe"),
        );
        run(&c, HotkeyEvent::DictationDown, 0).await;
        assert_eq!(
            c.overlay().events(),
            vec!["show:listening".to_owned(), notice(NOT_READY.zh)]
        );
        assert_eq!(c.phase(), Phase::Idle);
        assert_eq!(c.cancel().last(), Some(false));
    }

    #[tokio::test]
    async fn not_configured_at_stop_says_the_model_is_not_ready() {
        let c = controller(
            FakeCore::failing("dictation.stop", RpcError::new(1001, "not configured")),
            FakePaster::pasting("notepad.exe"),
        );
        run(&c, HotkeyEvent::DictationDown, 0).await;
        run(&c, HotkeyEvent::DictationUp, 900).await;
        ends_with_notice(&c, NOT_READY.zh);
        assert_eq!(c.phase(), Phase::Idle);
    }

    #[tokio::test]
    async fn a_core_that_is_gone_is_reported_once_until_it_restarts() {
        let c = controller(
            FakeCore::failing(
                "dictation.start",
                RpcError::new(CORE_EXITED, "the core is not running"),
            ),
            FakePaster::pasting("notepad.exe"),
        );
        run(&c, HotkeyEvent::DictationDown, 0).await;
        assert_eq!(
            c.overlay().events(),
            vec!["show:listening".to_owned(), notice(CORE_DOWN.zh)]
        );
        assert_eq!(c.phase(), Phase::Idle);
        assert_eq!(c.cancel().last(), Some(false));

        // the same down-state: no second notice, the emblem just goes away
        run(&c, HotkeyEvent::DictationUp, 300).await;
        run(&c, HotkeyEvent::DictationDown, 5000).await;
        let events = c.overlay().events();
        assert_eq!(&events[2..], ["show:listening", "hide"]);
        // a core.down while it is down changes nothing
        c.on_core_down();
        run(&c, HotkeyEvent::DictationUp, 5300).await;
        run(&c, HotkeyEvent::DictationDown, 9000).await;
        assert_eq!(c.overlay().events().last().unwrap(), "hide");

        // back up, then down again: a new down-state gets its notice
        c.on_core_restarted();
        run(&c, HotkeyEvent::DictationUp, 9300).await;
        run(&c, HotkeyEvent::DictationDown, 12000).await;
        ends_with_notice(&c, CORE_DOWN.zh);
        let count = c
            .overlay()
            .events()
            .iter()
            .filter(|e| **e == notice(CORE_DOWN.zh))
            .count();
        assert_eq!(count, 2);
    }

    #[tokio::test]
    async fn a_core_that_exits_during_the_stop_is_reported() {
        let c = controller(
            FakeCore::failing(
                "dictation.stop",
                RpcError::new(CORE_EXITED, "the core exited"),
            ),
            FakePaster::pasting("notepad.exe"),
        );
        run(&c, HotkeyEvent::DictationDown, 0).await;
        run(&c, HotkeyEvent::DictationUp, 900).await;
        ends_with_notice(&c, CORE_DOWN.zh);
        assert_eq!(c.phase(), Phase::Idle);
        assert!(c.paster().delivered().is_empty());
    }

    #[tokio::test]
    async fn a_core_down_notification_keeps_the_notice_once() {
        // core.down comes first; the press after it still gets the notice, once.
        let c = controller(
            FakeCore::failing(
                "dictation.start",
                RpcError::new(CORE_EXITED, "the core is not running"),
            ),
            FakePaster::pasting("notepad.exe"),
        );
        c.on_core_down();
        run(&c, HotkeyEvent::DictationDown, 0).await;
        ends_with_notice(&c, CORE_DOWN.zh);
        run(&c, HotkeyEvent::DictationUp, 300).await;
        run(&c, HotkeyEvent::DictationDown, 5000).await;
        assert_eq!(c.overlay().events().last().unwrap(), "hide");
    }

    #[tokio::test]
    async fn a_start_timeout_gets_one_recovery_cancel_without_a_session_id() {
        // The core may still start the session after the timeout; nobody would ever stop it.
        let c = controller(
            FakeCore::failing("dictation.start", RpcError::new(TIMEOUT, "timed out")),
            FakePaster::pasting("notepad.exe"),
        );
        run(&c, HotkeyEvent::DictationDown, 0).await;
        assert_eq!(c.core().count("dictation.cancel"), 1);
        assert_eq!(c.core().call_params("dictation.cancel"), json!({}));
        assert_eq!(c.overlay().events(), vec!["show:listening", "hide"]);
        assert_eq!(c.phase(), Phase::Idle);
    }

    #[tokio::test]
    async fn a_start_timeout_after_escape_sends_no_recovery_cancel() {
        let (c, gate) = with_gated_start(FakeCore::failing(
            "dictation.start",
            RpcError::new(TIMEOUT, "timed out"),
        ));
        let down = c
            .on_hotkey(HotkeyEvent::DictationDown, 0)
            .expect("the start runs in a task");
        tokio::task::yield_now().await;
        let esc = c
            .on_hotkey(HotkeyEvent::Cancel, 100)
            .expect("the cancel runs in a task");
        settle().await;
        gate.notify_one();
        down.await.unwrap();
        esc.await.unwrap();
        assert_eq!(c.core().count("dictation.cancel"), 0);
        assert_eq!(c.phase(), Phase::Idle);
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
    async fn the_tray_copies_the_latest_item_and_says_so() {
        // The tray menu has taken the focus (its own window, or the taskbar): a paste would land
        // nowhere. The text is left on the clipboard instead, and Raphael says to press Ctrl+V.
        let c = controller(
            FakeCore::with(
                "history.latest",
                json!({"item": {"id": 3, "text": "上一次"}}),
            ),
            FakePaster::pasting("notepad.exe"),
        );
        c.copy_last().await.unwrap();
        assert_eq!(c.paster().copied(), vec!["上一次"]);
        assert!(c.paster().delivered().is_empty(), "nothing is pasted");
        assert_eq!(c.core().count("dictation.cancel"), 0);
        assert_eq!(c.overlay().events(), vec![notice(LAST_COPIED.zh)]);
    }

    #[tokio::test]
    async fn the_tray_with_an_empty_history_does_nothing() {
        let c = controller(
            FakeCore::with("history.latest", json!({"item": null})),
            FakePaster::pasting("notepad.exe"),
        );
        c.copy_last().await.unwrap();
        assert!(c.paster().copied().is_empty());
        assert!(c.paster().delivered().is_empty());
        assert!(c.overlay().events().is_empty());
    }

    #[tokio::test]
    async fn the_tray_during_a_dictation_copies_without_a_notice() {
        // The overlay belongs to the dictation; a notice would wipe its emblem.
        let c = controller(
            FakeCore::with(
                "history.latest",
                json!({"item": {"id": 3, "text": "上一次"}}),
            ),
            FakePaster::pasting("notepad.exe"),
        );
        run(&c, HotkeyEvent::DictationDown, 0).await;
        c.copy_last().await.unwrap();
        assert_eq!(c.paster().copied(), vec!["上一次"]);
        assert_eq!(c.overlay().events(), vec!["show:listening"]);
        assert_eq!(c.phase(), Phase::Recording);
    }

    #[tokio::test]
    async fn paste_last_hotkey_pastes_into_the_focused_window() {
        let c = controller(
            FakeCore::with(
                "history.latest",
                json!({"item": {"id": 3, "text": "上一次"}}),
            ),
            FakePaster::pasting("notepad.exe"),
        );
        run(&c, HotkeyEvent::PasteLast, 5000).await;
        assert_eq!(c.paster().delivered(), vec!["上一次"]);
        assert!(c.paster().copied().is_empty());
        assert!(c.overlay().events().is_empty());
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
        run(&c, HotkeyEvent::PasteLast, 5000).await;
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

    // ---------- stop and cancel wait for the session's start reply ----------

    /// A controller whose `dictation.start` reply is held back until the returned gate is opened.
    fn with_gated_start(core: FakeCore) -> (Ctl, Arc<Notify>) {
        let (core, gate) = core.gated("dictation.start");
        (controller(core, FakePaster::pasting("notepad.exe")), gate)
    }

    #[tokio::test]
    async fn release_before_the_start_reply_sends_the_stop_after_it() {
        let (c, gate) = with_gated_start(FakeCore::with("dictation.stop", stop_result("好", None)));
        let down = c
            .on_hotkey(HotkeyEvent::DictationDown, 0)
            .expect("the start runs in a task");
        tokio::task::yield_now().await;
        // a hold just over the tap threshold: released while the core is still starting
        let up = c
            .on_hotkey(HotkeyEvent::DictationUp, 400)
            .expect("the stop runs in a task");
        settle().await;
        assert_eq!(c.phase(), Phase::Processing);
        assert_eq!(
            c.core().count("dictation.stop"),
            0,
            "the core must not see the stop before the start's reply"
        );

        gate.notify_one();
        down.await.unwrap();
        up.await.unwrap();
        assert_eq!(
            c.core().order(),
            vec!["dictation.start", "dictation.start:reply", "dictation.stop"]
        );
        assert_eq!(c.paster().delivered(), vec!["好"]);
        assert_eq!(
            c.overlay().events(),
            vec!["show:listening", "mode:thinking", "hide"]
        );
        assert_eq!(c.phase(), Phase::Idle);
        assert_eq!(c.cancel().last(), Some(false));
    }

    #[tokio::test]
    async fn escape_before_the_start_reply_sends_the_cancel_after_it() {
        let (c, gate) = with_gated_start(FakeCore::default());
        let down = c
            .on_hotkey(HotkeyEvent::DictationDown, 0)
            .expect("the start runs in a task");
        tokio::task::yield_now().await;
        let esc = c
            .on_hotkey(HotkeyEvent::Cancel, 150)
            .expect("the cancel runs in a task");
        settle().await;
        // the user sees the cancel at once; the core hears of it once it has started
        assert_eq!(c.phase(), Phase::Idle);
        assert_eq!(c.overlay().events(), vec!["show:listening", "hide"]);
        assert_eq!(
            c.core().count("dictation.cancel"),
            0,
            "the core must not see the cancel before the start's reply"
        );

        gate.notify_one();
        down.await.unwrap();
        esc.await.unwrap();
        assert_eq!(
            c.core().order(),
            vec![
                "dictation.start",
                "dictation.start:reply",
                "dictation.cancel"
            ]
        );
        assert_eq!(
            c.core().call_params("dictation.cancel")["session_id"],
            c.core().call_params("dictation.start")["session_id"]
        );
        assert_eq!(c.overlay().events(), vec!["show:listening", "hide"]);
    }

    #[tokio::test]
    async fn escape_after_release_before_the_start_reply_sends_only_the_cancel() {
        let (c, gate) = with_gated_start(FakeCore::with(
            "dictation.stop",
            stop_result("太晚了", None),
        ));
        let down = c
            .on_hotkey(HotkeyEvent::DictationDown, 0)
            .expect("the start runs in a task");
        tokio::task::yield_now().await;
        let up = c
            .on_hotkey(HotkeyEvent::DictationUp, 400)
            .expect("the stop runs in a task");
        let esc = c
            .on_hotkey(HotkeyEvent::Cancel, 450)
            .expect("the cancel runs in a task");
        settle().await;
        assert_eq!(c.core().count("dictation.stop"), 0);
        assert_eq!(c.core().count("dictation.cancel"), 0);

        gate.notify_one();
        down.await.unwrap();
        up.await.unwrap();
        esc.await.unwrap();
        // the stop was overtaken by the cancel: it is not sent at all, so nothing is transcribed
        assert_eq!(
            c.core().order(),
            vec![
                "dictation.start",
                "dictation.start:reply",
                "dictation.cancel"
            ]
        );
        assert!(c.paster().delivered().is_empty());
        assert_eq!(
            c.overlay().events(),
            vec!["show:listening", "mode:thinking", "hide"]
        );
        assert_eq!(c.phase(), Phase::Idle);
    }

    #[tokio::test]
    async fn a_failed_start_means_no_stop_is_sent() {
        let (c, gate) = with_gated_start(FakeCore::failing(
            "dictation.start",
            RpcError::new(-32603, "internal error"),
        ));
        let down = c
            .on_hotkey(HotkeyEvent::DictationDown, 0)
            .expect("the start runs in a task");
        tokio::task::yield_now().await;
        let up = c
            .on_hotkey(HotkeyEvent::DictationUp, 400)
            .expect("the stop runs in a task");
        settle().await;
        gate.notify_one();
        down.await.unwrap();
        up.await.unwrap();
        assert_eq!(c.core().count("dictation.stop"), 0);
        assert_eq!(c.core().count("dictation.cancel"), 0);
        assert!(c.paster().delivered().is_empty());
        // the start's own error path took care of the overlay
        assert_eq!(
            c.overlay().events(),
            vec!["show:listening", "mode:thinking", "hide"]
        );
        assert_eq!(c.phase(), Phase::Idle);
        assert_eq!(c.cancel().last(), Some(false));
    }

    #[tokio::test]
    async fn a_failed_start_means_no_cancel_is_sent() {
        let (c, gate) = with_gated_start(FakeCore::failing(
            "dictation.start",
            RpcError::new(1004, "no microphone"),
        ));
        let down = c
            .on_hotkey(HotkeyEvent::DictationDown, 0)
            .expect("the start runs in a task");
        tokio::task::yield_now().await;
        let esc = c
            .on_hotkey(HotkeyEvent::Cancel, 100)
            .expect("the cancel runs in a task");
        settle().await;
        gate.notify_one();
        down.await.unwrap();
        esc.await.unwrap();
        assert_eq!(c.core().count("dictation.cancel"), 0);
        // Esc already hid the emblem; the late failure must not put a notice over it
        assert_eq!(c.overlay().events(), vec!["show:listening", "hide"]);
        assert_eq!(c.phase(), Phase::Idle);
    }

    #[tokio::test]
    async fn paste_last_cancel_waits_for_the_start_reply() {
        let (c, gate) = with_gated_start(FakeCore::with(
            "history.latest",
            json!({"item": {"id": 3, "text": "上一次"}}),
        ));
        let down = c
            .on_hotkey(HotkeyEvent::DictationDown, 0)
            .expect("the start runs in a task");
        tokio::task::yield_now().await;
        let paste = c
            .on_hotkey(HotkeyEvent::PasteLast, 200)
            .expect("the paste runs in a task");
        settle().await;
        assert_eq!(c.phase(), Phase::Idle);
        assert_eq!(c.overlay().events(), vec!["show:listening", "hide"]);
        assert_eq!(
            c.core().count("dictation.cancel"),
            0,
            "the core must not see the cancel before the start's reply"
        );

        gate.notify_one();
        down.await.unwrap();
        paste.await.unwrap();
        let order = c.core().order();
        assert!(
            position(&order, "dictation.start:reply") < position(&order, "dictation.cancel"),
            "{order:?}"
        );
        assert_eq!(c.paster().delivered(), vec!["上一次"]);
    }

    #[tokio::test]
    async fn a_busy_core_gets_one_recovery_cancel_without_a_session_id() {
        let c = controller(
            FakeCore::failing("dictation.start", RpcError::new(1003, "busy")),
            FakePaster::pasting("notepad.exe"),
        );
        run(&c, HotkeyEvent::DictationDown, 0).await;
        assert_eq!(c.overlay().events(), vec!["show:listening", "hide"]);
        assert_eq!(c.phase(), Phase::Idle);
        assert_eq!(c.cancel().last(), Some(false));
        // The busy session is somebody else's, so the id we made up means nothing to the core:
        // the recovery cancel names no session and cancels whatever is active.
        assert_eq!(c.core().count("dictation.cancel"), 1);
        assert_eq!(c.core().call_params("dictation.cancel"), json!({}));
        // no automatic retry of the start
        assert_eq!(c.core().count("dictation.start"), 1);
        // the user's next press starts normally
        run(&c, HotkeyEvent::DictationUp, 900).await;
        run(&c, HotkeyEvent::DictationDown, 5000).await;
        assert_eq!(c.core().count("dictation.start"), 2);
        // (the fake core stays busy, so that press gets its own recovery cancel)
        assert_eq!(c.core().count("dictation.cancel"), 2);
    }

    #[tokio::test]
    async fn a_busy_reply_after_escape_sends_no_recovery_cancel() {
        // Esc has ended the session, and the user may have started the next one already: a
        // cancel without a session id could kill that one. The stuck session is cleared by the
        // recovery of the next BUSY instead.
        let (c, gate) = with_gated_start(FakeCore::failing(
            "dictation.start",
            RpcError::new(1003, "busy"),
        ));
        let down = c
            .on_hotkey(HotkeyEvent::DictationDown, 0)
            .expect("the start runs in a task");
        tokio::task::yield_now().await;
        let esc = c
            .on_hotkey(HotkeyEvent::Cancel, 100)
            .expect("the cancel runs in a task");
        settle().await;
        gate.notify_one();
        down.await.unwrap();
        esc.await.unwrap();
        assert_eq!(c.core().count("dictation.cancel"), 0);
        assert_eq!(c.overlay().events(), vec!["show:listening", "hide"]);
        assert_eq!(c.phase(), Phase::Idle);
    }

    // ---------- the end of a session ----------

    #[tokio::test]
    async fn the_cancel_switch_is_set_under_the_state_lock() {
        let c = controller(
            FakeCore::with(
                "history.latest",
                json!({"item": {"id": 3, "text": "上一次"}}),
            ),
            FakePaster::pasting("notepad.exe"),
        );
        *c.cancel().probe.lock().unwrap() = Some(c.state.clone());
        // a dictation to the end
        run(&c, HotkeyEvent::DictationDown, 0).await;
        run(&c, HotkeyEvent::DictationUp, 900).await;
        // Esc
        run(&c, HotkeyEvent::DictationDown, 5000).await;
        run(&c, HotkeyEvent::Cancel, 5100).await;
        // the core restarting
        run(&c, HotkeyEvent::DictationDown, 9000).await;
        c.on_core_restarted();
        // Right Alt+V
        run(&c, HotkeyEvent::DictationDown, 12000).await;
        run(&c, HotkeyEvent::PasteLast, 12200).await;

        // A session that starts right after another ended must not have its keys switched off by
        // the old one: so the switch moves together with the phase, under the lock.
        let sets = c.cancel().sets();
        assert_eq!(
            sets.iter().map(|(on, _)| *on).collect::<Vec<_>>(),
            vec![true, false, true, false, true, false, true, false]
        );
        assert!(
            sets.iter().all(|(_, held)| *held),
            "(value, lock held) per call: {sets:?}"
        );
    }

    #[test]
    fn escape_while_the_paste_is_queued_stops_the_paste() {
        // One thread for blocking work, kept busy: the paste has to wait in the queue, which is
        // where Esc can still catch it.
        let runtime = tokio::runtime::Builder::new_current_thread()
            .max_blocking_threads(1)
            .enable_all()
            .build()
            .unwrap();
        runtime.block_on(async {
            let c = controller(
                FakeCore::with("dictation.stop", stop_result("太晚了", None)),
                FakePaster::pasting("notepad.exe"),
            );
            run(&c, HotkeyEvent::DictationDown, 0).await;
            let (release, held) = std::sync::mpsc::channel::<()>();
            let busy = tokio::task::spawn_blocking(move || {
                let _ = held.recv();
            });
            let up = c
                .on_hotkey(HotkeyEvent::DictationUp, 900)
                .expect("the stop runs in a task");
            settle().await;
            assert_eq!(c.phase(), Phase::Processing);
            assert!(
                c.paster().delivered().is_empty(),
                "the paste is still queued"
            );

            run(&c, HotkeyEvent::Cancel, 1000).await;
            release.send(()).unwrap();
            busy.await.unwrap();
            up.await.unwrap();
            assert!(
                c.paster().delivered().is_empty(),
                "Esc came before the paste began"
            );
            assert_eq!(
                c.overlay().events(),
                vec!["show:listening", "mode:thinking", "hide"]
            );
            assert_eq!(c.phase(), Phase::Idle);
        });
    }

    #[tokio::test]
    async fn core_restart_while_processing_drops_the_late_result() {
        let (core, gate) =
            FakeCore::with("dictation.stop", stop_result("太晚了", None)).gated("dictation.stop");
        let c = controller(core, FakePaster::pasting("notepad.exe"));
        run(&c, HotkeyEvent::DictationDown, 0).await;
        let up = c
            .on_hotkey(HotkeyEvent::DictationUp, 900)
            .expect("the stop runs in a task");
        settle().await;
        assert_eq!(c.phase(), Phase::Processing);

        c.on_core_restarted();
        assert_eq!(c.phase(), Phase::Idle);
        assert_eq!(c.cancel().last(), Some(false));
        let events_after_restart = c.overlay().events();
        assert_eq!(
            events_after_restart,
            vec!["show:listening", "mode:thinking", "hide"]
        );

        // the reply of the old core's session turns up anyway
        gate.notify_one();
        up.await.unwrap();
        assert!(c.paster().delivered().is_empty());
        assert_eq!(c.overlay().events(), events_after_restart);
        assert_eq!(c.phase(), Phase::Idle);
        // the dead core is not sent a cancel
        assert_eq!(c.core().count("dictation.cancel"), 0);
    }

    // ---------- Right Alt+V: the one-second boundary ----------

    #[tokio::test]
    async fn paste_last_just_under_a_second_after_the_start_cancels_the_recording() {
        let c = controller(
            FakeCore::with(
                "history.latest",
                json!({"item": {"id": 3, "text": "上一次"}}),
            ),
            FakePaster::pasting("notepad.exe"),
        );
        run(&c, HotkeyEvent::DictationDown, 0).await;
        run(&c, HotkeyEvent::PasteLast, 999).await;
        assert_eq!(c.core().count("dictation.cancel"), 1);
        assert_eq!(c.phase(), Phase::Idle);
        assert_eq!(c.paster().delivered(), vec!["上一次"]);
    }

    #[tokio::test]
    async fn paste_last_a_full_second_after_the_start_keeps_the_recording() {
        let c = controller(
            FakeCore::with(
                "history.latest",
                json!({"item": {"id": 3, "text": "上一次"}}),
            ),
            FakePaster::pasting("notepad.exe"),
        );
        run(&c, HotkeyEvent::DictationDown, 0).await;
        run(&c, HotkeyEvent::PasteLast, 1000).await;
        assert_eq!(c.core().count("dictation.cancel"), 0);
        assert_eq!(c.phase(), Phase::Recording);
        assert_eq!(c.paster().delivered(), vec!["上一次"]);
    }
}
