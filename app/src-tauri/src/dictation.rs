//! The dictation controller: hotkey → recording → processing → paste, and hotkey → question →
//! answer.
//!
//! [`DictationController`] turns the dictation key's events (through [`HoldTap`], in the mode the
//! person chose: press to start and again to stop, or hold while talking) into calls to
//! the core (`dictation.start`, `dictation.stop`, `dictation.cancel`), drives the overlay, and
//! hands the finished text to the [`Paster`]. It updates its state at once and runs the core calls
//! in tasks of their own, so Esc is handled while a stop call is still in flight. Each recording
//! has its own session id; a result for a session that is no longer the current one (cancelled,
//! or the core restarted meanwhile) is dropped.
//!
//! There are two kinds of session, and only one at a time:
//!
//! - **Dictation** (the dictation keys): the text is cleaned up and pasted.
//! - **Agent** (the agent keys, `Right Alt+Space`): the recording is a question for Raphael, who
//!   greets it on the overlay while he listens (a dictation shows the emblem alone). The
//!   core starts it with `purpose: "agent"` and transcribes it without cleanup; the text goes to
//!   `agent.ask`, and the answer is shown by the overlay (`say`). An answer that needs a YES/NO
//!   first is shown as a question (`ask`) whose buttons answer through [`on_confirm`]; Esc and
//!   the clock answer NO; then `agent.confirm` gives the answer. An agent session never pastes and
//!   never touches the clipboard of the focused window.
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
//! - Right Alt+Space is two keys in a row: Right Alt alone starts a dictation, as it does for
//!   Right Alt+V, and the Space that follows within a second takes that recording over. The
//!   dictation is cancelled quietly (the emblem it showed stays, and is the agent's, with
//!   Raphael's greeting beside it), the core starts the question only after it has cancelled the
//!   dictation, and the release of Right Alt does nothing. While an agent session is under way the
//!   dictation key starts no dictation; in toggle mode it ends the question that is listening, as
//!   it ends a dictation, and the Space of a Right Alt+Space pressed to stop it does nothing more.
//!
//! [`spawn`] builds the controller from the app state and runs it in a task that listens to the
//! hotkeys, the core's notifications and the tray.

use std::sync::{Arc, Mutex};
use std::time::Duration;

use serde_json::{Value, json};
use tokio::sync::{oneshot, watch};
use tokio::task::JoinHandle;

use crate::agent::{self, Reply};
use crate::device_settings::DictationMode;
use crate::holdtap::{Action, HoldTap};
use crate::hotkey::{CancelSwitch, HotkeyEvent};
use crate::learn::AfterPaste;
use crate::notices::{self, LanguageCell, Notice};
use crate::overlay::{AskPrompt, Mode, OverlayApi, Progress, Stage, Tier};
use crate::paste::{CopyReason, PasteDelivery, PasteOutcome, Paster, Target};
use crate::rpc::{CORE_EXITED, CoreApi, RpcError, TIMEOUT, lock};

/// `BUSY` in the core's `protocol.py`: a dictation session is already active.
const BUSY: i64 = 1003;
/// `NO_MICROPHONE` in the core's `protocol.py`.
const NO_MICROPHONE: i64 = 1004;
/// `NOT_CONFIGURED` in the core's `protocol.py`: the speech model is not ready.
const NOT_CONFIGURED: i64 = 1001;
/// A paste-last or agent key press this soon after a dictation started cancels it: Right Alt+V or
/// Right Alt+Space pressed the dictation key first.
const CHORD_CANCELS_WITHIN_MS: u64 = 1000;

/// The dictation mode that is in force: written by the device settings (at start, and when the
/// person changes it), read by the controller each time a dictation begins. Clones share the
/// value. A change while a dictation is under way does not touch that dictation ([`HoldTap`] keeps
/// the mode it started with).
#[derive(Clone, Debug, Default)]
pub struct ModeCell(Arc<Mutex<DictationMode>>);

impl ModeCell {
    pub fn get(&self) -> DictationMode {
        *lock(&self.0)
    }

    pub fn set(&self, mode: DictationMode) {
        *lock(&self.0) = mode;
    }
}

/// Where the session is.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Phase {
    Idle,
    Recording,
    /// The key is released; the core is finishing the text, or it is being pasted. For a question
    /// to Raphael this lasts until its answer has been shown.
    Processing,
}

/// What a session is for.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Kind {
    /// The text is cleaned up and pasted.
    Dictation,
    /// The text is a question for Raphael; nothing is pasted.
    Agent,
}

/// What a question to Raphael is doing, more closely than its [`Phase`].
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum AgentStage {
    /// The microphone is open (`Phase::Recording`).
    Listening,
    /// The key is released and the core is turning the speech into text.
    Transcribing,
    /// The text is with the agent (`agent.ask`).
    Thinking,
    /// A YES/NO question is on screen, waiting for the answer.
    Asking,
    /// The answer is with the agent (`agent.confirm`).
    Confirming,
    /// The answer is on screen. It is the end of the session for all but the cancel keys, which
    /// dismiss it, and the keys that begin something new, which replace it.
    Speaking,
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

/// A recording's session: its id, what it is for, and whether the core has finished starting it.
#[derive(Clone)]
struct Session {
    id: String,
    kind: Kind,
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

/// The YES/NO question on screen. Whoever answers it (a button of the overlay, Esc, the clock)
/// takes it out of the state and sends the answer: the first to do so wins, and the others find
/// nothing to answer.
struct PendingAsk {
    id: String,
    /// How much the question matters: the YES to a `T3` one waits for the long press.
    tier: Tier,
    /// When the question was put on screen.
    shown: tokio::time::Instant,
    answer: oneshot::Sender<bool>,
}

struct State {
    phase: Phase,
    /// The dictation key.
    holdtap: HoldTap,
    /// The agent key, which follows the dictation mode too.
    agent_holdtap: HoldTap,
    /// The current recording's session, while not idle.
    session: Option<Session>,
    /// What the current session does, if it is a question to Raphael.
    agent: Option<AgentStage>,
    /// The question on screen, while there is one.
    ask: Option<PendingAsk>,
    /// When the current recording started, on the clock of [`DictationController::on_hotkey`].
    started_ms: u64,
    /// When the dictation key last ended a question, on the same clock: the agent key pressed a
    /// moment later is the Space of a Right Alt+Space pressed to end it.
    question_stopped_ms: Option<u64>,
    /// The fallback notices already shown in this app session: each is shown once.
    shown: Vec<Notice>,
    /// [`notices::CORE_DOWN`] was shown since the core last (re)started: it is shown once per
    /// down-state.
    down_noticed: bool,
}

impl State {
    fn kind(&self) -> Option<Kind> {
        self.session.as_ref().map(|session| session.kind)
    }

    /// Back to idle for any reason other than a key: nothing is recording, no key is half pressed,
    /// no question is open (its answer is dropped, which wakes whoever waits for it). Returns the
    /// session that ended.
    fn go_idle(&mut self) -> Option<Session> {
        self.phase = Phase::Idle;
        self.holdtap.reset();
        self.agent_holdtap.reset();
        self.agent = None;
        self.ask = None;
        self.session.take()
    }
}

/// The dictation controller. Cheap to clone: the clones share their state.
pub struct DictationController<C: CoreApi, O: OverlayApi, P: Paster, S: CancelSwitch> {
    core: Arc<C>,
    overlay: Arc<O>,
    paster: Arc<P>,
    cancel: Arc<S>,
    mode: ModeCell,
    /// The language of every notice the controller shows.
    language: LanguageCell,
    state: Arc<Mutex<State>>,
    /// Serializes state transitions with their overlay calls. Only background controller work
    /// takes this lock; UI-thread readers use `state`, which is never held across an overlay call.
    transitions: Arc<Mutex<()>>,
    /// Told of each dictation that was pasted and each that starts: learns from the user's
    /// corrections (`learn`).
    after_paste: Option<Arc<dyn AfterPaste>>,
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
            mode: self.mode.clone(),
            language: self.language.clone(),
            state: self.state.clone(),
            transitions: self.transitions.clone(),
            after_paste: self.after_paste.clone(),
        }
    }
}

impl<C: CoreApi, O: OverlayApi, P: Paster, S: CancelSwitch> DictationController<C, O, P, S> {
    /// A controller that takes the dictation mode from `mode`, which the device settings keep up
    /// to date.
    pub fn new(
        core: Arc<C>,
        overlay: Arc<O>,
        paster: Arc<P>,
        cancel: Arc<S>,
        mode: ModeCell,
    ) -> Self {
        Self {
            core,
            overlay,
            paster,
            cancel,
            mode,
            language: LanguageCell::default(),
            state: Arc::new(Mutex::new(State {
                phase: Phase::Idle,
                holdtap: HoldTap::new(),
                agent_holdtap: HoldTap::new(),
                session: None,
                agent: None,
                ask: None,
                started_ms: 0,
                question_stopped_ms: None,
                shown: Vec::new(),
                down_noticed: false,
            })),
            transitions: Arc::new(Mutex::new(())),
            after_paste: None,
        }
    }

    /// The controller, telling `hook` of its pastes.
    pub fn with_after_paste(mut self, hook: Arc<dyn AfterPaste>) -> Self {
        self.after_paste = Some(hook);
        self
    }

    /// The controller, showing its notices in the language `language` holds, which the device
    /// settings keep up to date.
    pub fn with_language(mut self, language: LanguageCell) -> Self {
        self.language = language;
        self
    }

    pub fn phase(&self) -> Phase {
        lock(&self.state).phase
    }

    /// Whether the overlay is nobody's: nothing is going on, or only an answer is on screen,
    /// which whatever comes next replaces.
    fn is_quiet(&self) -> bool {
        let state = lock(&self.state);
        state.phase == Phase::Idle || state.agent == Some(AgentStage::Speaking)
    }

    /// Handles a key event that happened at `now_ms` (any millisecond clock that only goes
    /// forward). Returns the task that makes the core calls, if any. Must be called from within
    /// a Tokio runtime.
    pub fn on_hotkey(&self, ev: HotkeyEvent, now_ms: u64) -> Option<JoinHandle<()>> {
        let _transition = lock(&self.transitions);
        match ev {
            HotkeyEvent::DictationDown => {
                // An answer that is only being shown does not keep a dictation waiting.
                self.end_speaking();
                // Only a press that begins a dictation looks at the mode; one that is under way
                // keeps the mode it began with.
                let mode = self.mode.get();
                let action = {
                    let mut state = lock(&self.state);
                    if state.phase == Phase::Processing {
                        log::debug!("dictation key ignored: still processing the last one");
                        return None;
                    }
                    if state.kind() == Some(Kind::Agent) {
                        // Right Alt ends a question as it ends a dictation (toggle mode), and
                        // never starts a dictation. The Space of a Right Alt+Space pressed to
                        // stop it does nothing more (see `agent_down`).
                        if state.agent_holdtap.on_stop_key() != Action::StopRecording {
                            log::debug!("dictation key ignored: Raphael is listening");
                            return None;
                        }
                        state.question_stopped_ms = Some(now_ms);
                        drop(state);
                        log::info!("agent: stopping (dictation key)");
                        return self.stop_agent();
                    }
                    state.holdtap.on_down(mode)
                };
                self.act(action, now_ms)
            }
            HotkeyEvent::DictationUp => {
                let action = lock(&self.state).holdtap.on_up();
                self.act(action, now_ms)
            }
            HotkeyEvent::AgentDown => self.agent_down(now_ms),
            HotkeyEvent::AgentUp => {
                let action = lock(&self.state).agent_holdtap.on_up();
                match action {
                    Action::StopRecording => {
                        log::info!("agent: stopping");
                        self.stop_agent()
                    }
                    Action::StartRecording | Action::None => None,
                }
            }
            HotkeyEvent::Cancel => self.cancel_current(),
            HotkeyEvent::PasteLast => {
                if lock(&self.state).agent == Some(AgentStage::Asking) {
                    // Like the other keys: while a question waits for its answer, nothing else
                    // may put a report or a paste in its way.
                    log::debug!("paste-last key ignored: Raphael is asking");
                    return None;
                }
                Some(self.paste_last_at(now_ms))
            }
        }
    }

    /// The microphone's loudness, from 0 to 1. Shown only while recording.
    pub fn on_level(&self, level: f32) {
        let _transition = lock(&self.transitions);
        if self.phase() == Phase::Recording {
            self.overlay.level(level);
        }
    }

    /// The core's `dictation.progress`: shown on the emblem if it is about the current session and
    /// the key has been released. Anything else (another session's, a malformed one) is dropped.
    pub fn on_progress(&self, params: &Value) {
        let Some((session, progress)) = parse_progress(params) else {
            log::debug!("dictation.progress ignored: {params}");
            return;
        };
        let _transition = lock(&self.transitions);
        // A question to Raphael is transcribed without the stages that a dictation goes through.
        let current = {
            let state = lock(&self.state);
            state.phase == Phase::Processing
                && state
                    .session
                    .as_ref()
                    .is_some_and(|current| current.id == session && current.kind == Kind::Dictation)
        };
        if current {
            self.overlay.progress(progress);
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

    /// Back to idle; the overlay is hidden only if a dictation was showing, so a notice stays. An
    /// answer that was only being shown stays as well: the core did not take it with it.
    fn reset(&self) {
        let _transition = lock(&self.transitions);
        let (was, stage) = {
            let mut state = lock(&self.state);
            let before = (state.phase, state.agent);
            state.go_idle();
            self.cancel.set(false);
            before
        };
        if was != Phase::Idle {
            log::warn!("the core restarted during a session; it is lost");
            if stage != Some(AgentStage::Speaking) {
                self.overlay.hide();
            }
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
            if this.is_quiet() {
                this.show(notices::LAST_COPIED);
            } else {
                log::info!("the last dictation is copied; no notice during a dictation");
            }
        })
    }

    fn act(&self, action: Action, now_ms: u64) -> Option<JoinHandle<()>> {
        match action {
            Action::StartRecording => {
                log::info!("dictation: recording ({} mode)", self.mode.get().as_str());
                self.start(Kind::Dictation, now_ms)
            }
            Action::StopRecording => {
                log::info!("dictation: stopping");
                self.stop()
            }
            Action::None => None,
        }
    }

    /// Starts a session of `kind`. A question to Raphael may take over the dictation that Right
    /// Alt, the first half of Right Alt+Space, has just started (see [`Self::agent_down`]): that
    /// one is dropped, the emblem it showed is kept and greets the question, and the core hears of
    /// the question only after it has cancelled the dictation.
    fn start(&self, kind: Kind, now_ms: u64) -> Option<JoinHandle<()>> {
        let id = uuid::Uuid::new_v4().to_string();
        let (answered, started) = watch::channel(Started::Pending);
        let replaced = {
            let mut state = lock(&self.state);
            let replaced = match state.phase {
                Phase::Idle => None,
                Phase::Recording
                    if kind == Kind::Agent && state.kind() == Some(Kind::Dictation) =>
                {
                    state.holdtap.reset();
                    state.session.take()
                }
                Phase::Recording | Phase::Processing => return None,
            };
            state.phase = Phase::Recording;
            state.session = Some(Session {
                id: id.clone(),
                kind,
                started,
            });
            state.agent = (kind == Kind::Agent).then_some(AgentStage::Listening);
            state.started_ms = now_ms;
            self.cancel.set(true);
            replaced
        };
        if let Some(hook) = &self.after_paste {
            hook.dictation_started();
        }
        match kind {
            // Raphael greets a question, on the emblem of the dictation it took over too. A
            // dictation shows the emblem alone, so the two are never mistaken for each other.
            Kind::Agent => self
                .overlay
                .greet(notices::GREETING.text(&self.language.get())),
            Kind::Dictation => self.overlay.show(Mode::Listening),
        }

        let this = self.clone();
        Some(tokio::spawn(async move {
            // The core runs one session at a time: the dictation this one took over from has to
            // be gone first, or the core would answer BUSY.
            if let Some(old) = &replaced {
                cancel_after_start(this.core.as_ref(), old).await;
                // Esc may have come meanwhile: nobody wants the question any more, so the core
                // is not asked to open the microphone for it.
                if !this.is_current(&id) {
                    log::info!("the question was cancelled before it started; no dictation.start");
                    answered.send_replace(Started::Failed);
                    return;
                }
            }
            let params = match kind {
                Kind::Dictation => json!({ "session_id": id }),
                Kind::Agent => json!({ "session_id": id, "purpose": "agent" }),
            };
            let reply = this.core.call("dictation.start", params).await;
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
            if state.phase != Phase::Recording || state.kind() != Some(Kind::Dictation) {
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
                // Nothing to paste. If the microphone also delivered no sound, say so, every
                // time: the person would otherwise think the app is broken.
                let silent = reply
                    .get("silent")
                    .and_then(Value::as_bool)
                    .unwrap_or(false);
                if silent {
                    log::info!("the dictation has no text and the microphone was silent");
                    this.finish(&id, |this| this.show(notices::NO_SOUND));
                } else {
                    log::info!("the dictation has no text; nothing to paste");
                    this.finish(&id, |this| this.overlay.hide());
                }
                return;
            }
            // Text is ready, but the page must wait for confirmed delivery before showing 100%.
            {
                let _transition = lock(&this.transitions);
                if !this.is_current(&id) {
                    return;
                }
                this.overlay.progress(Progress::DONE);
            }
            let reason = reply
                .get("fallback_reason")
                .and_then(Value::as_str)
                .map(str::to_owned);

            let delivery = this
                .deliver(text.to_owned(), target.clone(), Some(&id))
                .await;
            this.finish(&id, |this| {
                let fallback = this.first_time(notices::notice_for_fallback(reason.as_deref()));
                this.report(delivery.as_ref(), fallback, true);
                this.learn_after_paste(text, target.as_ref(), delivery);
            });
        }))
    }

    /// Esc. It ends the session, wherever it is: a dictation or a question to Raphael that is
    /// listening, transcribing, thinking, confirming or being shown. Only a YES/NO question on
    /// screen is different: Esc answers NO, and the turn goes on with the denial.
    fn cancel_current(&self) -> Option<JoinHandle<()>> {
        let (session, stage) = {
            let mut state = lock(&self.state);
            if state.phase == Phase::Idle {
                return None;
            }
            let stage = state.agent;
            if stage == Some(AgentStage::Asking) {
                // The turn waits for this answer, and sends `agent.confirm` and shows the reply.
                let pending = state.ask.take();
                drop(state);
                log::info!("agent: Esc denies the question");
                if let Some(pending) = pending {
                    let _ = pending.answer.send(false);
                }
                return None;
            }
            let session = state.go_idle();
            self.cancel.set(false);
            (session, stage)
        };
        log::info!(
            "{}: cancelled",
            if stage.is_some() {
                "agent"
            } else {
                "dictation"
            }
        );
        self.overlay.hide();
        let session = session?;
        let core = self.core.clone();
        Some(tokio::spawn(async move {
            match stage {
                // The text is with the agent: it hears that nobody wants the answer.
                Some(AgentStage::Thinking) => agent_cancel(core.as_ref(), &session.id).await,
                // Nothing is left to cancel: the agent has all it was given, or has said it all.
                Some(AgentStage::Confirming | AgentStage::Speaking) => {}
                _ => cancel_after_start(core.as_ref(), &session).await,
            }
        }))
    }

    /// The paste-last hotkey: pastes the latest dictation from the history into the window that
    /// has focus now.
    fn paste_last_at(&self, now_ms: u64) -> JoinHandle<()> {
        if let Some(hook) = &self.after_paste {
            hook.paste_started();
        }
        // Right Alt+V: the Right Alt went down first and started a dictation. Drop it quietly.
        // (A question to Raphael that is listening is not the Right Alt's doing, and stays.)
        let cancelled = {
            let mut state = lock(&self.state);
            let fresh = state.phase == Phase::Recording
                && state.kind() == Some(Kind::Dictation)
                && now_ms.saturating_sub(state.started_ms) < CHORD_CANCELS_WITHIN_MS;
            if fresh {
                self.cancel.set(false);
                state.go_idle()
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
        let delivery = self.deliver(text.clone(), target.clone(), None).await;
        let _transition = lock(&self.transitions);
        self.report(delivery.as_ref(), None, false);
        if self.is_quiet() {
            self.learn_after_paste(&text, target.as_ref(), delivery);
        }
    }

    fn learn_after_paste(
        &self,
        text: &str,
        target: Option<&Target>,
        delivery: Option<PasteDelivery>,
    ) {
        if let Some(PasteDelivery {
            outcome: PasteOutcome::Pasted,
            receipt: Some(receipt),
        }) = delivery
            && let (Some(hook), Some(target)) = (&self.after_paste, target)
        {
            hook.pasted_with_receipt(text, target, receipt);
        }
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
    ) -> Option<PasteDelivery> {
        let this = self.clone();
        let session = session.map(str::to_owned);
        let delivered = tokio::task::spawn_blocking(move || {
            if let Some(session) = &session
                && !this.is_current(session)
            {
                log::info!("the dictation was cancelled before it was pasted; not pasting");
                return None;
            }
            Some(this.paster.deliver_with_receipt(&text, target.as_ref()))
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
    fn report(
        &self,
        delivery: Option<&PasteDelivery>,
        fallback: Option<Notice>,
        hide_if_quiet: bool,
    ) {
        let outcome = delivery.map(|item| item.outcome);
        let completed = hide_if_quiet
            && delivery
                .is_some_and(|item| item.outcome == PasteOutcome::Pasted && item.receipt.is_some());
        if completed {
            self.overlay.complete();
        }
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
        if !shown && hide_if_quiet && !completed {
            self.overlay.hide();
        }
    }

    /// Shows `notice` in the interface language, and only in that one.
    fn show(&self, notice: Notice) {
        self.overlay.notice(notice.text(&self.language.get()));
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
        state.go_idle();
        // Under the lock, with the phase: a dictation started right after this one must not have
        // its cancel keys switched off by us. The switch is a flag (a channel send), so this
        // neither waits nor calls the overlay.
        self.cancel.set(false);
        true
    }

    // ---------- questions to Raphael ----------

    /// The agent key went down. It follows the dictation mode: a press starts a question and, in
    /// toggle mode, the next one stops it (so does Right Alt alone, see [`Self::on_hotkey`]); in
    /// hold mode its release stops it.
    ///
    /// Right Alt+Space reports Right Alt first, and that started a dictation. If it did so a
    /// moment ago, the Space is the second half of the chord and the question takes the recording
    /// over ([`Self::start`]); a dictation that has been running longer is a real one, and the
    /// key does nothing. Neither does it while something is being processed, nor a moment after
    /// Right Alt ended a question: that is the Space of a Right Alt+Space pressed to end it, even
    /// if the question is over by then (it heard nothing, say).
    fn agent_down(&self, now_ms: u64) -> Option<JoinHandle<()>> {
        {
            let state = lock(&self.state);
            if state.phase != Phase::Recording
                && state
                    .question_stopped_ms
                    .is_some_and(|at| now_ms.saturating_sub(at) < CHORD_CANCELS_WITHIN_MS)
            {
                log::debug!("agent key ignored: the rest of the keys that ended the question");
                return None;
            }
        }
        // An answer that is only being shown does not keep a new question waiting.
        self.end_speaking();
        let mode = self.mode.get();
        let action = {
            let mut state = lock(&self.state);
            if state.phase == Phase::Processing {
                log::debug!("agent key ignored: still processing the last one");
                return None;
            }
            if state.phase == Phase::Recording
                && state.kind() == Some(Kind::Dictation)
                && now_ms.saturating_sub(state.started_ms) >= CHORD_CANCELS_WITHIN_MS
            {
                log::debug!("agent key ignored: a dictation is under way");
                return None;
            }
            state.agent_holdtap.on_down(mode)
        };
        match action {
            Action::StartRecording => {
                log::info!("agent: listening ({} mode)", mode.as_str());
                self.start(Kind::Agent, now_ms)
            }
            Action::StopRecording => {
                log::info!("agent: stopping");
                self.stop_agent()
            }
            Action::None => None,
        }
    }

    fn stop_agent(&self) -> Option<JoinHandle<()>> {
        let session = {
            let mut state = lock(&self.state);
            if state.phase != Phase::Recording || state.kind() != Some(Kind::Agent) {
                return None;
            }
            let session = state.session.clone()?;
            state.phase = Phase::Processing;
            state.agent = Some(AgentStage::Transcribing);
            session
        };
        // The app the person was in when the key was released is the one the question is about.
        let target = self.paster.foreground();
        // No percentage: nothing reports how far an answer has come.
        self.overlay.think_quietly();

        let this = self.clone();
        Some(tokio::spawn(async move {
            this.agent_turn(session, target).await;
        }))
    }

    /// The rest of a question after the key was released: the core transcribes it, the agent
    /// answers it, the overlay shows the answer. Never logs the words of the question or the
    /// answer.
    async fn agent_turn(&self, session: Session, target: Option<Target>) {
        // Released before the core answered the start? Then wait, like a dictation does.
        if !session.start_succeeded().await {
            log::info!("dictation.start failed; no dictation.stop to send");
            return;
        }
        if !self.is_current(&session.id) {
            log::info!("the question was cancelled before it started; no dictation.stop");
            return;
        }
        let id = session.id;
        let target_app = target.map(|target| target.process_name);
        let transcript = self
            .core
            .call(
                "dictation.stop",
                json!({ "session_id": id, "target_app": target_app }),
            )
            .await;
        if !self.is_current(&id) {
            log::info!("dropping the transcript of a question that was cancelled");
            return;
        }
        let transcript = match transcript {
            Ok(transcript) => transcript,
            Err(err) => {
                log::warn!("dictation.stop failed: {err}");
                self.finish(&id, |this| this.show_failure(&err));
                return;
            }
        };
        let question = transcript
            .get("text")
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim();
        if question.is_empty() {
            // Nothing was said. Like a dictation, say so if the microphone gave no sound.
            let silent = transcript
                .get("silent")
                .and_then(Value::as_bool)
                .unwrap_or(false);
            if silent {
                log::info!("the question has no text and the microphone was silent");
                self.finish(&id, |this| this.show(notices::NO_SOUND));
            } else {
                log::info!("the question has no text; nothing to ask");
                self.finish(&id, |this| this.overlay.hide());
            }
            return;
        }
        if !self.set_stage(&id, AgentStage::Thinking) {
            return;
        }
        let answer = self
            .core
            .call(
                "agent.ask",
                json!({
                    "session_id": id,
                    "text": question,
                    "ui_language": self.language.get(),
                    "target_app": target_app,
                }),
            )
            .await;
        let Some((prompt, answer)) = self.show_reply(&id, answer, true) else {
            return;
        };
        // A question is on screen. Only YES goes through; everything else is a NO.
        let Some(approved) = self.await_answer(&id, &prompt, answer).await else {
            return;
        };
        let answer = self
            .core
            .call(
                "agent.confirm",
                json!({ "id": prompt.id, "approved": approved }),
            )
            .await;
        // The answer to an answer is never another question.
        let _ = self.show_reply(&id, answer, false);
    }

    /// Shows what the agent replied (to the question, or, with `may_ask` false, to its answer):
    /// the answer, a failure, or a YES/NO question, which is returned together with the receiver
    /// of its answer. `None` once there is nothing more for the turn to do: it was shown, it
    /// failed, or the session ended meanwhile.
    fn show_reply(
        &self,
        id: &str,
        reply: Result<Value, RpcError>,
        may_ask: bool,
    ) -> Option<(AskPrompt, oneshot::Receiver<bool>)> {
        // All of it, the check that the session still exists included, is one transition: Esc
        // and the core's restarts cannot come in between.
        let _transition = lock(&self.transitions);
        if !self.is_current(id) {
            log::info!("dropping the reply to a question that was cancelled");
            // A question nobody will ever see is denied, not left open until it expires.
            if let Ok(value) = &reply
                && let Some(confirm) = value
                    .get("confirm")
                    .and_then(|confirm| confirm.get("id"))
                    .and_then(Value::as_str)
            {
                self.deny_in_background(confirm.to_owned());
            }
            return None;
        }
        let reply = match reply {
            Ok(reply) => reply,
            Err(err) => {
                // Only the code: the core's message may quote what was asked.
                log::warn!("the agent failed (code {})", err.code);
                if err.code == TIMEOUT {
                    // The agent may still be at it, and may yet ask a question nobody will see.
                    self.cancel_in_background(id.to_owned());
                }
                self.end_session(id);
                // A core that is gone says so, once; every other failure is the agent being
                // unable to answer, and its details are not for the person to read.
                let gone = (err.code == CORE_EXITED)
                    .then(|| self.failure_notice(&err))
                    .flatten();
                self.show(gone.unwrap_or(notices::ASK_FAILED));
                return None;
            }
        };
        let parsed = if may_ask {
            agent::parse_reply(&reply)
        } else {
            agent::parse_answer(&reply)
        };
        match parsed {
            Ok(Reply::Say { prefix, text }) => {
                self.speak(id, &prefix, &text);
                None
            }
            Ok(Reply::Ask(prompt)) => {
                let (answer, wait) = oneshot::channel();
                {
                    let mut state = lock(&self.state);
                    state.agent = Some(AgentStage::Asking);
                    state.ask = Some(PendingAsk {
                        id: prompt.id.clone(),
                        tier: prompt.tier,
                        shown: tokio::time::Instant::now(),
                        answer,
                    });
                }
                self.overlay.ask(&prompt);
                Some((prompt, wait))
            }
            Err(bad) => {
                log::warn!("the agent's reply cannot be shown");
                if let Some(confirm) = bad.confirm_id {
                    self.deny_in_background(confirm);
                }
                self.end_session(id);
                self.show(notices::ASK_FAILED);
                None
            }
        }
    }

    /// Waits for the answer to the question on screen: a button of the overlay ([`Self::on_confirm`]),
    /// Esc, or the time the question was given. Returns whether it was approved, or `None` if the
    /// session ended meanwhile.
    async fn await_answer(
        &self,
        id: &str,
        prompt: &AskPrompt,
        wait: oneshot::Receiver<bool>,
    ) -> Option<bool> {
        // A click must not take the focus from the app the person is typing into. The overlay
        // cannot be activated, in theory; if it was in practice, the log says so.
        let before = self.paster.foreground();
        let approved = tokio::select! {
            answer = wait => answer.unwrap_or(false),
            () = tokio::time::sleep(Duration::from_millis(prompt.timeout_ms)) => {
                log::info!("agent: the question timed out");
                false
            }
        };
        if stole_focus(
            before.as_ref(),
            self.paster.foreground().as_ref(),
            std::process::id(),
        ) {
            // The click must leave the person where they were: the focus goes back.
            let refocused = before
                .as_ref()
                .is_some_and(|target| self.paster.refocus(target));
            log::warn!(
                "agent: a window of Raphael's own got the focus while a question was open \
                 (the overlay may have been activated); given back: {refocused}"
            );
        }
        // The answer is in. A session that ended meanwhile has no more to do: its cancel took
        // care of the overlay. Otherwise the question stays on the screen, and the window keeps
        // the mouse, until the reply (or whatever else comes) replaces it.
        let _transition = lock(&self.transitions);
        let mut state = lock(&self.state);
        if state
            .session
            .as_ref()
            .is_none_or(|current| current.id != id)
        {
            return None;
        }
        state.ask = None;
        state.agent = Some(AgentStage::Confirming);
        Some(approved)
    }

    /// The overlay's answer to the question on screen: its button was clicked (`overlay_confirm`).
    /// An answer to anything but the question that is open now (a second click, a late one, one for
    /// a question that is gone) is dropped.
    pub fn on_confirm(&self, id: &str, approved: bool) {
        let pending = {
            let mut state = lock(&self.state);
            let Some(ask) = state.ask.as_ref().filter(|ask| ask.id == id) else {
                drop(state);
                log::debug!("agent: an answer for no open question is dropped");
                return;
            };
            // The page arms the YES to a question that cannot be undone with a long press. A YES
            // that comes sooner is not from a person holding the button: the question stays
            // open for the real one.
            let armed = ask.shown.elapsed() >= Duration::from_millis(agent::T3_ARMS_AFTER_MS);
            if approved && ask.tier == Tier::T3 && !armed {
                drop(state);
                log::warn!("agent: a YES to a question that cannot be undone came too early");
                return;
            }
            state.ask.take()
        };
        if let Some(pending) = pending {
            log::info!("agent: the question was answered");
            let _ = pending.answer.send(approved);
        }
    }

    /// Shows Raphael's answer and keeps it on screen for as long as it takes to read. The session
    /// lasts that long too: Esc dismisses the answer, and the keys that begin something new replace
    /// it. Called with the transitions held.
    fn speak(&self, id: &str, prefix: &str, text: &str) {
        {
            let mut state = lock(&self.state);
            state.agent = Some(AgentStage::Speaking);
        }
        self.overlay.say(prefix, text);
        let hold = Duration::from_millis(agent::say_hold_ms(text));
        let (this, id) = (self.clone(), id.to_owned());
        tokio::spawn(async move {
            tokio::time::sleep(hold).await;
            this.finish_speaking(&id);
        });
    }

    /// The answer has been on screen long enough: the session is over, and Esc is Esc again.
    fn finish_speaking(&self, id: &str) {
        let _transition = lock(&self.transitions);
        let mut state = lock(&self.state);
        if state.agent == Some(AgentStage::Speaking)
            && state
                .session
                .as_ref()
                .is_some_and(|current| current.id == id)
        {
            state.go_idle();
            self.cancel.set(false);
        }
    }

    /// An answer that is only being shown ends when something new begins. No overlay call: what
    /// begins next takes the stage.
    fn end_speaking(&self) {
        let mut state = lock(&self.state);
        if state.agent == Some(AgentStage::Speaking) {
            state.go_idle();
            self.cancel.set(false);
        }
    }

    /// Moves the question on to `stage`, unless the session ended. False if it did.
    fn set_stage(&self, id: &str, stage: AgentStage) -> bool {
        let _transition = lock(&self.transitions);
        let mut state = lock(&self.state);
        if state
            .session
            .as_ref()
            .is_none_or(|current| current.id != id)
        {
            return false;
        }
        state.agent = Some(stage);
        true
    }

    /// The confirmation `confirm` will never be seen, so it is denied now instead of expiring.
    fn deny_in_background(&self, confirm: String) {
        let core = self.core.clone();
        tokio::spawn(async move {
            let denied = json!({ "id": confirm, "approved": false });
            if let Err(err) = core.call("agent.confirm", denied).await {
                log::warn!("denying an unseen confirmation failed (code {})", err.code);
            }
        });
    }

    /// The agent is told that nobody waits for the answer to the question of `session` any more.
    fn cancel_in_background(&self, session: String) {
        let core = self.core.clone();
        tokio::spawn(async move { agent_cancel(core.as_ref(), &session).await });
    }

    /// The end of a session that ran its course: ends it, then shows what became of it with
    /// `show` (overlay calls). If the session was cancelled meanwhile, whoever cancelled it has
    /// taken care of the overlay, and `show` is not called.
    ///
    /// A new recording must not publish its show between ending this session and publishing its
    /// hide or notice. Serialize that whole transition with the hotkeys, while keeping window
    /// calls outside the state lock so UI-thread readers can still run.
    fn finish(&self, session: &str, show: impl FnOnce(&Self)) {
        let _transition = lock(&self.transitions);
        if !self.end_session(session) {
            return;
        }
        show(self);
    }
}

/// The session id and what to show, from a `dictation.progress` notification's params
/// (`{session_id, stage, done, total, audio_seconds}`, stage "transcribing" or "cleaning"). Counts
/// past `u32::MAX` are clamped; a missing or unusable `audio_seconds` is 0.
fn parse_progress(params: &Value) -> Option<(&str, Progress)> {
    let session = params.get("session_id")?.as_str()?;
    let stage = match params.get("stage")?.as_str()? {
        "transcribing" => Stage::Transcribing,
        "cleaning" => Stage::Cleaning,
        "done" => Stage::Done,
        _ => return None,
    };
    let count = |key: &str| {
        params
            .get(key)?
            .as_u64()
            .map(|n| u32::try_from(n).unwrap_or(u32::MAX))
    };
    let audio_seconds = params
        .get("audio_seconds")
        .and_then(Value::as_f64)
        .filter(|seconds| seconds.is_finite() && *seconds >= 0.0)
        .unwrap_or(0.0) as f32;
    Some((
        session,
        Progress {
            stage,
            done: count("done")?,
            total: count("total")?,
            audio_seconds,
        },
    ))
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

/// Whether a click on the overlay took the focus: a window of this very process is in front now,
/// and was not before. (The person may move to Raphael's own window meanwhile, which the log
/// then reports too; it is a hint for the person who checks that the overlay never activates.)
fn stole_focus(before: Option<&Target>, after: Option<&Target>, own_pid: u32) -> bool {
    after.is_some_and(|after| after.pid == own_pid)
        && !before.is_some_and(|before| before.pid == own_pid)
}

/// `agent.cancel` for `session`: the agent drops the question and any confirmation it has open.
async fn agent_cancel<C: CoreApi>(core: &C, session: &str) {
    if let Err(err) = core
        .call("agent.cancel", json!({ "session_id": session }))
        .await
    {
        log::warn!("agent.cancel failed (code {})", err.code);
    }
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
    fn mode(&self) -> ModeCell {
        self.mode.clone()
    }
    fn language(&self) -> LanguageCell {
        self.language.clone()
    }
    fn agent_stage(&self) -> Option<AgentStage> {
        lock(&self.state).agent
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

    use super::{DictationController, ModeCell};
    use crate::core_process::{CORE_DOWN, CORE_RESTARTED, CoreProcess};
    use crate::hotkey::{CancelSwitch, HotkeyEvent, HotkeyService, PauseReason};
    use crate::learn::{Learner, UiaProbe};
    use crate::notices::{self, LanguageCell};
    use crate::overlay::{
        AskPrompt, ConfirmSender, Confirmation, Mode, Overlay, OverlayApi, Progress,
    };
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
        fn greet(&self, line: &str) {
            if let Some(overlay) = &self.0 {
                overlay.greet(line);
            }
        }
        fn set_mode(&self, mode: Mode) {
            if let Some(overlay) = &self.0 {
                overlay.set_mode(mode);
            }
        }
        fn think_quietly(&self) {
            if let Some(overlay) = &self.0 {
                overlay.think_quietly();
            }
        }
        fn level(&self, value: f32) {
            if let Some(overlay) = &self.0 {
                overlay.level(value);
            }
        }
        fn notice(&self, text: &str) {
            match &self.0 {
                Some(overlay) => overlay.notice(text),
                None => log::warn!("notice without an overlay: {text}"),
            }
        }
        fn learned_notice(&self, text: &str) {
            match &self.0 {
                Some(overlay) => overlay.learned_notice(text),
                None => log::warn!("learned notice without an overlay"),
            }
        }
        fn say(&self, prefix: &str, text: &str) {
            match &self.0 {
                Some(overlay) => overlay.say(prefix, text),
                None => log::warn!("answer without an overlay"),
            }
        }
        fn ask(&self, prompt: &AskPrompt) {
            match &self.0 {
                Some(overlay) => overlay.ask(prompt),
                None => log::warn!("question without an overlay"),
            }
        }
        fn hide(&self) {
            if let Some(overlay) = &self.0 {
                overlay.hide();
            }
        }
        fn complete(&self) {
            if let Some(overlay) = &self.0 {
                overlay.complete();
            }
        }
        fn progress(&self, progress: Progress) {
            if let Some(overlay) = &self.0 {
                overlay.progress(progress);
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

    /// What the controller's task listens to. A source that is missing or gone is `None`.
    struct Inputs {
        hotkey_events: Option<UnboundedReceiver<HotkeyEvent>>,
        notifications: Option<broadcast::Receiver<Notification>>,
        tray_commands: Option<UnboundedReceiver<TrayCommand>>,
        /// The answers to the overlay's questions (`overlay_confirm`).
        confirmations: Option<UnboundedReceiver<Confirmation>>,
    }

    /// Builds the controller from what is in the app state (`Arc<CoreProcess>`, `Arc<Overlay>`,
    /// `Arc<HotkeyService>`, each of which may be missing) and runs it in a task that listens to
    /// the hotkeys, the core's notifications, the tray and the overlay's buttons. Without a core,
    /// hotkeys are logged and ignored.
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

        // The device settings (`commands::start`, which runs after this) put the saved dictation
        // mode in this cell and change it when the person does, found through the app state.
        let mode = ModeCell::default();
        app.manage(mode.clone());
        // The same for the interface language, which Raphael's notices follow.
        let language = LanguageCell::default();
        app.manage(language.clone());
        // The overlay page answers its questions with `overlay_confirm`, which sends here.
        let (confirmations, confirmation_rx) = tokio::sync::mpsc::unbounded_channel();
        app.manage(ConfirmSender(confirmations));

        let notifications = core.as_ref().map(|core| core.subscribe());
        let overlay = Arc::new(MaybeOverlay(overlay));
        let controller = match core {
            Some(core) => {
                // Learns from the user's corrections after a paste (`learn`).
                let learner = Learner::new(core.clone(), overlay.clone(), Arc::new(UiaProbe))
                    .with_language(language.clone());
                Some(
                    DictationController::new(
                        core,
                        overlay.clone(),
                        Arc::new(WindowsPaster::new()),
                        Arc::new(MaybeHotkeys(hotkeys.clone())),
                        mode,
                    )
                    .with_language(language.clone())
                    .with_after_paste(Arc::new(learner)),
                )
            }
            None => {
                log::error!("dictation is off: the Python core is not running");
                None
            }
        };
        let inputs = Inputs {
            hotkey_events,
            notifications,
            tray_commands: Some(tray_commands),
            confirmations: Some(confirmation_rx),
        };
        tauri::async_runtime::spawn(run(controller, overlay, language, hotkeys, inputs));
    }

    async fn run(
        controller: Option<Controller>,
        overlay: Arc<MaybeOverlay>,
        language: LanguageCell,
        hotkeys: Option<Arc<HotkeyService>>,
        inputs: Inputs,
    ) {
        let Inputs {
            mut hotkey_events,
            mut notifications,
            mut tray_commands,
            mut confirmations,
        } = inputs;
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
                            if matches!(
                                event,
                                HotkeyEvent::DictationDown | HotkeyEvent::AgentDown
                            ) && !told_no_core
                            {
                                told_no_core = true;
                                overlay.notice(notices::CORE_DOWN.text(&language.get()));
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
                answer = next(&mut confirmations) => match answer {
                    Some(Confirmation { id, approved }) => match &controller {
                        Some(controller) => controller.on_confirm(&id, approved),
                        None => log::info!("an answer from the overlay is dropped: the core is not running"),
                    },
                    None => confirmations = None,
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
            "dictation.progress" => controller.on_progress(&notification.params),
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
    use crate::device_settings::DeviceSettings;
    use crate::hotkey::{CancelSwitch, HotkeyEvent};
    use crate::notices::{
        ASK_FAILED, BUDGET, CORE_DOWN, ELEVATED, FOCUS_CHANGED, GREETING, LAST_COPIED, NO_MIC,
        NO_SOUND, NOT_READY, OFFLINE,
    };
    use crate::overlay::{Mode, OverlayApi, OverlayEvent, Progress, takes_clicks};
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

    /// Records what the overlay is told. `events` leaves out the progress, which most tests are
    /// not about; `timeline` has everything, in order.
    #[derive(Default)]
    struct FakeOverlay {
        completions: Mutex<u32>,
        /// How many times the emblem thought without a progress caption.
        quiet_thoughts: Mutex<u32>,
        /// Raphael's greetings, oldest first; `events` has a `greet` for each.
        greetings: Mutex<Vec<String>>,
        events: Mutex<Vec<String>>,
        timeline: Mutex<Vec<String>>,
        /// Whether the real overlay window would take the mouse now: the rule of
        /// `Overlay::publish`, applied to every call.
        mouse: Mutex<bool>,
        paused_hide: Mutex<
            Option<(
                std::sync::mpsc::SyncSender<()>,
                std::sync::mpsc::Receiver<()>,
            )>,
        >,
    }

    impl FakeOverlay {
        fn events(&self) -> Vec<String> {
            self.events.lock().unwrap().clone()
        }

        fn timeline(&self) -> Vec<String> {
            self.timeline.lock().unwrap().clone()
        }

        fn progress(&self) -> Vec<String> {
            self.timeline()
                .into_iter()
                .filter(|event| event.starts_with("progress:"))
                .collect()
        }

        fn push(&self, event: String) {
            self.timeline.lock().unwrap().push(event.clone());
            self.events.lock().unwrap().push(event);
        }

        /// Whether the window would take the mouse now.
        fn mouse(&self) -> bool {
            *self.mouse.lock().unwrap()
        }

        /// What the real overlay does with the mouse for `event`.
        fn track(&self, event: OverlayEvent) {
            if let Some(on) = takes_clicks(&event) {
                *self.mouse.lock().unwrap() = on;
            }
        }
    }

    fn mode_name(mode: Mode) -> String {
        format!("{mode:?}").to_lowercase()
    }

    impl OverlayApi for FakeOverlay {
        fn show(&self, mode: Mode) {
            self.track(OverlayEvent::Show { mode, line: None });
            self.push(format!("show:{}", mode_name(mode)));
        }
        fn greet(&self, line: &str) {
            self.track(OverlayEvent::Show {
                mode: Mode::Listening,
                line: Some(line.to_owned()),
            });
            self.greetings.lock().unwrap().push(line.to_owned());
            self.push("greet".to_owned());
        }
        fn set_mode(&self, mode: Mode) {
            self.track(OverlayEvent::Mode { mode, quiet: false });
            self.push(format!("mode:{}", mode_name(mode)));
        }
        fn think_quietly(&self) {
            self.track(OverlayEvent::Mode {
                mode: Mode::Thinking,
                quiet: true,
            });
            // The same stage for the controller's tests; the page tells the two apart.
            *self.quiet_thoughts.lock().unwrap() += 1;
            self.push("mode:thinking".to_owned());
        }
        fn level(&self, value: f32) {
            self.track(OverlayEvent::Level { value });
            self.push(format!("level:{value}"));
        }
        fn notice(&self, text: &str) {
            self.track(OverlayEvent::Notice {
                text: text.to_owned(),
            });
            self.push(format!("notice:{text}"));
        }
        fn say(&self, prefix: &str, text: &str) {
            self.track(OverlayEvent::Say {
                prefix: prefix.to_owned(),
                text: text.to_owned(),
            });
            self.push(format!("say:{prefix}|{text}"));
        }
        fn ask(&self, prompt: &AskPrompt) {
            self.track(OverlayEvent::Ask(prompt.clone()));
            self.push(format!(
                "ask:{}|{:?}|{}|{}|{}|{}|{}",
                prompt.id,
                prompt.tier,
                prompt.prefix,
                prompt.text,
                prompt.yes,
                prompt.no,
                prompt.timeout_ms
            ));
        }
        fn hide(&self) {
            self.track(OverlayEvent::Hide);
            if let Some((entered, resume)) = self.paused_hide.lock().unwrap().take() {
                entered.send(()).unwrap();
                resume.recv().unwrap();
            }
            self.push("hide".to_owned());
        }
        fn complete(&self) {
            *self.completions.lock().unwrap() += 1;
            self.hide();
        }
        fn progress(&self, progress: Progress) {
            self.track(OverlayEvent::Progress(progress));
            let stage = format!("{:?}", progress.stage).to_lowercase();
            self.timeline.lock().unwrap().push(format!(
                "progress:{stage}:{}/{}:{}",
                progress.done, progress.total, progress.audio_seconds
            ));
        }
    }

    struct FakePaster {
        foreground: Option<Target>,
        outcome: PasteOutcome,
        delivered: Mutex<Vec<String>>,
        copied: Mutex<Vec<String>>,
        gate: Option<(Arc<Notify>, Mutex<std::sync::mpsc::Receiver<()>>)>,
        /// While set, the window in front is one of Raphael's own: the overlay took the focus.
        stolen: Mutex<bool>,
        /// The windows the focus was given back to.
        refocused: Mutex<Vec<Target>>,
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
                gate: None,
                stolen: Mutex::default(),
                refocused: Mutex::default(),
            }
        }

        fn gated(mut self) -> (Self, Arc<Notify>, std::sync::mpsc::Sender<()>) {
            let entered = Arc::new(Notify::new());
            let (release, waiting) = std::sync::mpsc::channel();
            self.gate = Some((entered.clone(), Mutex::new(waiting)));
            (self, entered, release)
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
            if *self.stolen.lock().unwrap() {
                return Some(Target {
                    hwnd: 99,
                    pid: std::process::id(),
                    process_name: "app.exe".to_owned(),
                    is_elevated: false,
                });
            }
            self.foreground.clone()
        }

        fn refocus(&self, target: &Target) -> bool {
            self.refocused.lock().unwrap().push(target.clone());
            *self.stolen.lock().unwrap() = false;
            true
        }

        fn deliver(&self, text: &str, at_stop: Option<&Target>) -> PasteOutcome {
            assert_eq!(
                at_stop,
                self.foreground.as_ref(),
                "delivered to the target taken at stop"
            );
            self.delivered.lock().unwrap().push(text.to_owned());
            if let Some((entered, waiting)) = &self.gate {
                entered.notify_one();
                waiting.lock().unwrap().recv().unwrap();
            }
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

    fn controller_in(mode: DictationMode, core: FakeCore, paster: FakePaster) -> Ctl {
        let cell = ModeCell::default();
        cell.set(mode);
        DictationController::new(
            Arc::new(core),
            Arc::new(FakeOverlay::default()),
            Arc::new(paster),
            Arc::new(FakeCancel::default()),
            cell,
        )
    }

    /// A controller in hold mode: the shortest key sequence for a whole dictation is down, up.
    /// Most tests are about what happens after the key (the core, the overlay, pasting) and use
    /// it; the ones about the modes themselves say which mode they use.
    fn controller(core: FakeCore, paster: FakePaster) -> Ctl {
        controller_in(DictationMode::Hold, core, paster)
    }

    const BOTH_MODES: [DictationMode; 2] = [DictationMode::Toggle, DictationMode::Hold];

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

    // ---------- toggle mode: press to start, press again to stop ----------

    fn toggle_controller(core: FakeCore, paster: FakePaster) -> Ctl {
        controller_in(DictationMode::Toggle, core, paster)
    }

    #[test]
    fn the_mode_cell_starts_in_toggle_mode_like_the_settings() {
        assert_eq!(ModeCell::default().get(), DictationMode::Toggle);
        assert_eq!(
            ModeCell::default().get(),
            DeviceSettings::default().dictation_mode
        );
    }

    #[tokio::test]
    async fn toggle_a_press_starts_its_release_does_nothing_the_next_press_stops() {
        let c = toggle_controller(
            FakeCore::with("dictation.stop", stop_result("好", None)),
            FakePaster::pasting("notepad.exe"),
        );
        run(&c, HotkeyEvent::DictationDown, 0).await;
        assert_eq!(c.phase(), Phase::Recording);
        assert_eq!(c.cancel().last(), Some(true));
        run(&c, HotkeyEvent::DictationUp, 120).await;
        assert_eq!(c.phase(), Phase::Recording, "the release does not stop it");
        assert_eq!(c.core().count("dictation.stop"), 0);
        run(&c, HotkeyEvent::DictationDown, 2000).await;
        assert_eq!(c.paster().delivered(), vec!["好"]);
        assert_eq!(c.phase(), Phase::Idle);
        run(&c, HotkeyEvent::DictationUp, 2100).await;
        assert_eq!(
            c.phase(),
            Phase::Idle,
            "the release of the stopping press does nothing"
        );
        assert_eq!(c.core().count("dictation.start"), 1);
        assert_eq!(c.core().count("dictation.stop"), 1);
        assert_eq!(
            c.overlay().events(),
            vec!["show:listening", "mode:thinking", "hide"]
        );
        assert_eq!(c.cancel().last(), Some(false));
    }

    #[tokio::test]
    async fn toggle_holding_the_key_long_changes_nothing() {
        let c = toggle_controller(
            FakeCore::with("dictation.stop", stop_result("好", None)),
            FakePaster::pasting("notepad.exe"),
        );
        run(&c, HotkeyEvent::DictationDown, 0).await;
        // ten seconds with the key down, then let go: still just a start
        run(&c, HotkeyEvent::DictationUp, 10_000).await;
        assert_eq!(c.phase(), Phase::Recording);
        assert_eq!(c.core().count("dictation.stop"), 0);
        // a stopping press held long is a stop too, and its release changes nothing
        run(&c, HotkeyEvent::DictationDown, 30_000).await;
        assert_eq!(c.paster().delivered(), vec!["好"]);
        run(&c, HotkeyEvent::DictationUp, 40_000).await;
        assert_eq!(c.phase(), Phase::Idle);
        assert_eq!(c.core().count("dictation.start"), 1);
    }

    #[tokio::test]
    async fn toggle_each_dictation_starts_afresh() {
        let c = toggle_controller(
            FakeCore::with("dictation.stop", stop_result("好", None)),
            FakePaster::pasting("notepad.exe"),
        );
        for start in [0, 10_000] {
            run(&c, HotkeyEvent::DictationDown, start).await;
            run(&c, HotkeyEvent::DictationUp, start + 100).await;
            run(&c, HotkeyEvent::DictationDown, start + 3000).await;
            run(&c, HotkeyEvent::DictationUp, start + 3100).await;
        }
        assert_eq!(c.core().count("dictation.start"), 2);
        assert_eq!(c.core().count("dictation.stop"), 2);
        assert_eq!(c.paster().delivered(), vec!["好", "好"]);
    }

    #[tokio::test]
    async fn toggle_escape_after_the_release_cancels() {
        let c = toggle_controller(FakeCore::default(), FakePaster::pasting("notepad.exe"));
        run(&c, HotkeyEvent::DictationDown, 0).await;
        run(&c, HotkeyEvent::DictationUp, 100).await;
        run(&c, HotkeyEvent::Cancel, 2000).await;
        assert_eq!(c.core().count("dictation.cancel"), 1);
        assert_eq!(c.core().count("dictation.stop"), 0);
        assert_eq!(c.phase(), Phase::Idle);
        assert_eq!(c.cancel().last(), Some(false));
        assert_eq!(c.overlay().events().last().unwrap(), "hide");
        // the next press starts a new recording; it is not taken for the stop of the old one
        run(&c, HotkeyEvent::DictationDown, 4000).await;
        assert_eq!(c.core().count("dictation.start"), 2);
        assert_eq!(c.phase(), Phase::Recording);
    }

    #[tokio::test]
    async fn toggle_escape_with_the_key_still_down_cancels_and_its_release_is_ignored() {
        let c = toggle_controller(FakeCore::default(), FakePaster::pasting("notepad.exe"));
        run(&c, HotkeyEvent::DictationDown, 0).await;
        run(&c, HotkeyEvent::Cancel, 500).await;
        assert_eq!(c.core().count("dictation.cancel"), 1);
        assert_eq!(c.phase(), Phase::Idle);
        run(&c, HotkeyEvent::DictationUp, 700).await;
        assert_eq!(c.phase(), Phase::Idle);
        assert_eq!(c.core().count("dictation.stop"), 0);
        run(&c, HotkeyEvent::DictationDown, 3000).await;
        assert_eq!(c.core().count("dictation.start"), 2);
        assert_eq!(c.phase(), Phase::Recording);
    }

    #[tokio::test]
    async fn toggle_escape_while_processing_drops_the_late_result() {
        let (core, gate) =
            FakeCore::with("dictation.stop", stop_result("太晚了", None)).gated("dictation.stop");
        let c = toggle_controller(core, FakePaster::pasting("notepad.exe"));
        run(&c, HotkeyEvent::DictationDown, 0).await;
        run(&c, HotkeyEvent::DictationUp, 100).await;
        let stop = c
            .on_hotkey(HotkeyEvent::DictationDown, 3000)
            .expect("the stop runs in a task");
        tokio::task::yield_now().await;
        assert_eq!(c.phase(), Phase::Processing);
        run(&c, HotkeyEvent::Cancel, 3200).await;
        assert_eq!(c.core().count("dictation.cancel"), 1);
        assert_eq!(c.phase(), Phase::Idle);
        gate.notify_one();
        stop.await.unwrap();
        assert!(c.paster().delivered().is_empty());
        assert_eq!(c.phase(), Phase::Idle);
    }

    #[tokio::test]
    async fn toggle_a_press_while_processing_is_ignored() {
        let (core, gate) =
            FakeCore::with("dictation.stop", stop_result("好", None)).gated("dictation.stop");
        let c = toggle_controller(core, FakePaster::pasting("notepad.exe"));
        run(&c, HotkeyEvent::DictationDown, 0).await;
        run(&c, HotkeyEvent::DictationUp, 100).await;
        let stop = c
            .on_hotkey(HotkeyEvent::DictationDown, 3000)
            .expect("the stop runs in a task");
        tokio::task::yield_now().await;
        run(&c, HotkeyEvent::DictationUp, 3100).await;
        // impatient: two more presses while the core is still working
        run(&c, HotkeyEvent::DictationDown, 3500).await;
        run(&c, HotkeyEvent::DictationUp, 3600).await;
        run(&c, HotkeyEvent::DictationDown, 3800).await;
        run(&c, HotkeyEvent::DictationUp, 3900).await;
        assert_eq!(c.core().count("dictation.start"), 1);
        assert_eq!(c.core().count("dictation.cancel"), 0);
        assert_eq!(c.phase(), Phase::Processing);
        gate.notify_one();
        stop.await.unwrap();
        assert_eq!(c.paster().delivered(), vec!["好"]);
        assert_eq!(c.phase(), Phase::Idle);
        // and afterwards the key is a fresh start
        run(&c, HotkeyEvent::DictationDown, 9000).await;
        assert_eq!(c.core().count("dictation.start"), 2);
    }

    #[tokio::test]
    async fn toggle_a_stray_release_does_nothing() {
        let c = toggle_controller(FakeCore::default(), FakePaster::pasting("notepad.exe"));
        run(&c, HotkeyEvent::DictationUp, 0).await;
        assert_eq!(c.phase(), Phase::Idle);
        assert_eq!(c.core().count("dictation.start"), 0);
        assert!(c.overlay().events().is_empty());
        run(&c, HotkeyEvent::DictationDown, 500).await;
        assert_eq!(c.phase(), Phase::Recording, "the key still works");
    }

    #[tokio::test]
    async fn toggle_a_second_down_without_a_release_is_auto_repeat() {
        let c = toggle_controller(FakeCore::default(), FakePaster::pasting("notepad.exe"));
        run(&c, HotkeyEvent::DictationDown, 0).await;
        run(&c, HotkeyEvent::DictationDown, 100).await;
        run(&c, HotkeyEvent::DictationDown, 200).await;
        assert_eq!(c.core().count("dictation.start"), 1);
        assert_eq!(c.core().count("dictation.stop"), 0);
        assert_eq!(c.phase(), Phase::Recording);
    }

    #[tokio::test]
    async fn toggle_the_stop_is_sent_even_before_the_start_was_answered() {
        // press, release, press again quickly: the stop waits for the start's reply
        let (c, gate) = with_gated_start_in(
            DictationMode::Toggle,
            FakeCore::with("dictation.stop", stop_result("好", None)),
        );
        let down = c
            .on_hotkey(HotkeyEvent::DictationDown, 0)
            .expect("the start runs in a task");
        tokio::task::yield_now().await;
        run(&c, HotkeyEvent::DictationUp, 80).await;
        let stop = c
            .on_hotkey(HotkeyEvent::DictationDown, 300)
            .expect("the stop runs in a task");
        settle().await;
        assert_eq!(c.phase(), Phase::Processing);
        assert_eq!(c.core().count("dictation.stop"), 0);
        gate.notify_one();
        down.await.unwrap();
        stop.await.unwrap();
        assert_eq!(
            c.core().order(),
            vec!["dictation.start", "dictation.start:reply", "dictation.stop"]
        );
        assert_eq!(c.paster().delivered(), vec!["好"]);
    }

    #[tokio::test]
    async fn toggle_a_failed_start_leaves_the_next_press_a_start() {
        let c = toggle_controller(
            FakeCore::failing("dictation.start", RpcError::new(1004, "no microphone")),
            FakePaster::pasting("notepad.exe"),
        );
        run(&c, HotkeyEvent::DictationDown, 0).await;
        assert_eq!(c.phase(), Phase::Idle);
        run(&c, HotkeyEvent::DictationUp, 100).await;
        run(&c, HotkeyEvent::DictationDown, 5000).await;
        assert_eq!(c.core().count("dictation.start"), 2, "not a stop");
        assert_eq!(c.core().count("dictation.stop"), 0);
    }

    // ---------- hold mode: hold to talk, release to stop ----------

    #[tokio::test]
    async fn hold_a_quick_press_stops_too_however_short() {
        // The old auto-detection took a press under 350 ms for a tap and left the recording on.
        let c = controller(
            FakeCore::with("dictation.stop", stop_result("好", None)),
            FakePaster::pasting("notepad.exe"),
        );
        run(&c, HotkeyEvent::DictationDown, 0).await;
        run(&c, HotkeyEvent::DictationUp, 60).await;
        assert_eq!(c.core().count("dictation.stop"), 1);
        assert_eq!(c.paster().delivered(), vec!["好"]);
        assert_eq!(c.phase(), Phase::Idle);
    }

    #[tokio::test]
    async fn hold_a_long_press_stops_on_release() {
        let c = controller(
            FakeCore::with("dictation.stop", stop_result("好", None)),
            FakePaster::pasting("notepad.exe"),
        );
        run(&c, HotkeyEvent::DictationDown, 0).await;
        run(&c, HotkeyEvent::DictationDown, 500).await; // auto-repeat
        assert_eq!(c.phase(), Phase::Recording);
        assert_eq!(
            c.core().count("dictation.stop"),
            0,
            "nothing stops it while the key is down"
        );
        run(&c, HotkeyEvent::DictationUp, 60_000).await;
        assert_eq!(c.core().count("dictation.start"), 1);
        assert_eq!(c.core().count("dictation.stop"), 1);
        assert_eq!(c.paster().delivered(), vec!["好"]);
    }

    #[tokio::test]
    async fn hold_a_stray_release_does_nothing() {
        let c = controller(FakeCore::default(), FakePaster::pasting("notepad.exe"));
        run(&c, HotkeyEvent::DictationUp, 0).await;
        assert_eq!(c.phase(), Phase::Idle);
        assert_eq!(c.core().count("dictation.stop"), 0);
        assert!(c.overlay().events().is_empty());
    }

    #[tokio::test]
    async fn hold_the_release_after_a_stop_does_not_stop_again() {
        let c = controller(
            FakeCore::with("dictation.stop", stop_result("好", None)),
            FakePaster::pasting("notepad.exe"),
        );
        run(&c, HotkeyEvent::DictationDown, 0).await;
        run(&c, HotkeyEvent::DictationUp, 900).await;
        run(&c, HotkeyEvent::DictationUp, 1000).await;
        assert_eq!(c.core().count("dictation.stop"), 1);
    }

    // ---------- the mode can change; it takes effect from the next dictation ----------

    #[tokio::test]
    async fn changing_the_mode_during_a_toggle_dictation_takes_effect_from_the_next_one() {
        let c = toggle_controller(
            FakeCore::with("dictation.stop", stop_result("好", None)),
            FakePaster::pasting("notepad.exe"),
        );
        run(&c, HotkeyEvent::DictationDown, 0).await;
        c.mode().set(DictationMode::Hold);
        // this dictation is still a toggle one: the release does not stop it
        run(&c, HotkeyEvent::DictationUp, 200).await;
        assert_eq!(c.phase(), Phase::Recording);
        assert_eq!(c.core().count("dictation.stop"), 0);
        run(&c, HotkeyEvent::DictationDown, 3000).await;
        assert_eq!(c.core().count("dictation.stop"), 1);
        run(&c, HotkeyEvent::DictationUp, 3100).await;
        assert_eq!(c.phase(), Phase::Idle);

        // the next one is a hold dictation
        run(&c, HotkeyEvent::DictationDown, 6000).await;
        run(&c, HotkeyEvent::DictationUp, 6100).await;
        assert_eq!(c.core().count("dictation.start"), 2);
        assert_eq!(c.core().count("dictation.stop"), 2);
        assert_eq!(c.phase(), Phase::Idle);
    }

    #[tokio::test]
    async fn changing_the_mode_during_a_hold_dictation_takes_effect_from_the_next_one() {
        let c = controller(
            FakeCore::with("dictation.stop", stop_result("好", None)),
            FakePaster::pasting("notepad.exe"),
        );
        run(&c, HotkeyEvent::DictationDown, 0).await;
        c.mode().set(DictationMode::Toggle);
        // this dictation is still a hold one: the release stops it
        run(&c, HotkeyEvent::DictationUp, 900).await;
        assert_eq!(c.core().count("dictation.stop"), 1);
        assert_eq!(c.phase(), Phase::Idle);

        // the next one is a toggle dictation
        run(&c, HotkeyEvent::DictationDown, 5000).await;
        run(&c, HotkeyEvent::DictationUp, 5100).await;
        assert_eq!(c.phase(), Phase::Recording);
        assert_eq!(c.core().count("dictation.stop"), 1);
        run(&c, HotkeyEvent::DictationDown, 8000).await;
        assert_eq!(c.core().count("dictation.stop"), 2);
    }

    #[test]
    fn the_mode_cell_is_shared_between_clones() {
        let cell = ModeCell::default();
        let other = cell.clone();
        other.set(DictationMode::Hold);
        assert_eq!(cell.get(), DictationMode::Hold);
        cell.set(DictationMode::Toggle);
        assert_eq!(other.get(), DictationMode::Toggle);
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

    // ---------- a microphone that picked up no sound ----------

    fn empty_result(silent: bool) -> Value {
        json!({"id": null, "text": "", "fallback_reason": "empty", "silent": silent})
    }

    #[tokio::test]
    async fn empty_and_silent_result_shows_the_no_sound_notice() {
        let c = controller(
            FakeCore::with("dictation.stop", empty_result(true)),
            FakePaster::pasting("notepad.exe"),
        );
        run(&c, HotkeyEvent::DictationDown, 0).await;
        run(&c, HotkeyEvent::DictationUp, 900).await;
        assert!(c.paster().delivered().is_empty());
        assert_eq!(
            c.overlay().events(),
            vec![
                "show:listening".to_owned(),
                "mode:thinking".to_owned(),
                notice(NO_SOUND.zh)
            ],
            "no hide after the notice: it would cancel it"
        );
        assert_eq!(c.phase(), Phase::Idle);
        assert_eq!(c.cancel().last(), Some(false));
    }

    #[tokio::test]
    async fn empty_and_not_silent_result_just_hides() {
        let c = controller(
            FakeCore::with("dictation.stop", empty_result(false)),
            FakePaster::pasting("notepad.exe"),
        );
        run(&c, HotkeyEvent::DictationDown, 0).await;
        run(&c, HotkeyEvent::DictationUp, 900).await;
        assert!(c.paster().delivered().is_empty());
        assert_eq!(
            c.overlay().events(),
            vec!["show:listening", "mode:thinking", "hide"]
        );
        assert_eq!(c.phase(), Phase::Idle);
        assert_eq!(c.cancel().last(), Some(false));
    }

    #[tokio::test]
    async fn a_silent_flag_that_is_not_a_boolean_is_not_silent() {
        for silent in [json!(null), json!("true"), json!(1)] {
            let c = controller(
                FakeCore::with(
                    "dictation.stop",
                    json!({"id": null, "text": "", "fallback_reason": "empty", "silent": silent}),
                ),
                FakePaster::pasting("notepad.exe"),
            );
            run(&c, HotkeyEvent::DictationDown, 0).await;
            run(&c, HotkeyEvent::DictationUp, 900).await;
            assert_eq!(c.overlay().events().last().unwrap(), "hide", "{silent}");
        }
    }

    #[tokio::test]
    async fn the_no_sound_notice_is_shown_every_time() {
        let c = controller(
            FakeCore::with("dictation.stop", empty_result(true)),
            FakePaster::pasting("notepad.exe"),
        );
        for start in [0, 10_000] {
            run(&c, HotkeyEvent::DictationDown, start).await;
            run(&c, HotkeyEvent::DictationUp, start + 900).await;
        }
        let shown = c
            .overlay()
            .events()
            .into_iter()
            .filter(|event| *event == notice(NO_SOUND.zh))
            .count();
        assert_eq!(shown, 2);
        assert_eq!(c.phase(), Phase::Idle);
    }

    #[tokio::test]
    async fn the_no_sound_notice_works_in_toggle_mode_too() {
        let c = toggle_controller(
            FakeCore::with("dictation.stop", empty_result(true)),
            FakePaster::pasting("notepad.exe"),
        );
        run(&c, HotkeyEvent::DictationDown, 0).await;
        run(&c, HotkeyEvent::DictationUp, 100).await;
        run(&c, HotkeyEvent::DictationDown, 3000).await;
        ends_with_notice(&c, NO_SOUND.zh);
        assert_eq!(c.phase(), Phase::Idle);
    }

    #[tokio::test]
    async fn escape_while_processing_drops_a_late_silent_result() {
        let (core, gate) =
            FakeCore::with("dictation.stop", empty_result(true)).gated("dictation.stop");
        let c = controller(core, FakePaster::pasting("notepad.exe"));
        run(&c, HotkeyEvent::DictationDown, 0).await;
        let up = c
            .on_hotkey(HotkeyEvent::DictationUp, 900)
            .expect("the stop runs in a task");
        tokio::task::yield_now().await;
        run(&c, HotkeyEvent::Cancel, 1200).await;
        assert_eq!(c.overlay().events().last().unwrap(), "hide");
        let events_after_cancel = c.overlay().events();

        gate.notify_one();
        up.await.unwrap();
        assert_eq!(c.overlay().events(), events_after_cancel, "no late notice");
        assert_eq!(c.phase(), Phase::Idle);
    }

    #[tokio::test]
    async fn a_late_silent_result_leaves_a_new_dictation_its_overlay() {
        let (core, gate) =
            FakeCore::with("dictation.stop", empty_result(true)).gated("dictation.stop");
        let c = controller(core, FakePaster::pasting("notepad.exe"));
        run(&c, HotkeyEvent::DictationDown, 0).await;
        let up = c
            .on_hotkey(HotkeyEvent::DictationUp, 900)
            .expect("the stop runs in a task");
        tokio::task::yield_now().await;
        // Esc, then a new press: the new recording is the current session, the old stop is stale
        run(&c, HotkeyEvent::Cancel, 1000).await;
        run(&c, HotkeyEvent::DictationDown, 1500).await;
        assert_eq!(c.phase(), Phase::Recording);

        gate.notify_one();
        up.await.unwrap();
        assert!(
            !c.overlay().events().contains(&notice(NO_SOUND.zh)),
            "the old dictation's notice must not wipe the new one's emblem"
        );
        assert_eq!(c.phase(), Phase::Recording);
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

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn a_finishing_hide_cannot_overtake_the_next_recordings_show() {
        let c = toggle_controller(FakeCore::default(), FakePaster::pasting("notepad.exe"));
        run(&c, HotkeyEvent::DictationDown, 0).await;
        run(&c, HotkeyEvent::DictationUp, 100).await;
        let id = c.core().call_params("dictation.start")["session_id"]
            .as_str()
            .unwrap()
            .to_owned();
        let (entered, waiting) = std::sync::mpsc::sync_channel(0);
        let (resume, paused) = std::sync::mpsc::sync_channel(0);
        *c.overlay().paused_hide.lock().unwrap() = Some((entered, paused));
        let ending = c.clone();
        let finish = std::thread::spawn(move || {
            ending.finish(&id, |this| this.overlay.hide());
        });
        waiting.recv().unwrap();

        // Start precisely after finish's idle check, before its hide is published. A real
        // window call may wait here for the UI thread; a new hotkey arrives on another worker.
        let next = c.clone();
        let runtime = tokio::runtime::Handle::current();
        let (attempted, attempt) = std::sync::mpsc::sync_channel(0);
        let (started, start_done) = std::sync::mpsc::sync_channel(1);
        let start = std::thread::spawn(move || {
            let _runtime = runtime.enter();
            attempted.send(()).unwrap();
            let task = next.on_hotkey(HotkeyEvent::DictationDown, 3000);
            started.send(()).unwrap();
            task
        });
        attempt.recv().unwrap();
        // The fixed controller queues this start behind the unfinished overlay transition.
        // The old one completes it immediately, exposing show -> show -> hide.
        let _ = start_done.recv_timeout(std::time::Duration::from_millis(100));
        resume.send(()).unwrap();
        finish.join().unwrap();
        if let Some(task) = start.join().unwrap() {
            task.await.unwrap();
        }
        assert_eq!(c.phase(), Phase::Recording);
        assert_eq!(
            c.overlay().events(),
            vec!["show:listening", "hide", "show:listening"]
        );
    }

    #[tokio::test]
    async fn toggle_cancel_between_recordings_resets_holdtap_and_shows_each_session() {
        let c = toggle_controller(FakeCore::default(), FakePaster::pasting("notepad.exe"));
        run(&c, HotkeyEvent::DictationDown, 0).await;
        run(&c, HotkeyEvent::DictationUp, 100).await;
        run(&c, HotkeyEvent::Cancel, 2000).await;
        run(&c, HotkeyEvent::DictationDown, 3000).await;
        run(&c, HotkeyEvent::DictationUp, 3100).await;
        assert_eq!(c.phase(), Phase::Recording);
        assert_eq!(c.core().count("dictation.start"), 2);
        assert_eq!(c.core().count("dictation.stop"), 0);
        assert_eq!(c.core().count("dictation.cancel"), 1);
        assert_eq!(
            c.overlay().events(),
            vec!["show:listening", "hide", "show:listening"]
        );
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
    async fn a_notice_is_shown_in_the_interface_language_and_in_that_one_only() {
        for (language, expected) in [
            ("zh-TW", NO_MIC.zh),
            ("en", NO_MIC.en),
            ("ja", NO_MIC.ja),
            // a language nobody offers is the default one, never a pair
            ("fr", NO_MIC.zh),
        ] {
            let c = controller(
                FakeCore::failing("dictation.start", RpcError::new(1004, "no microphone")),
                FakePaster::pasting("notepad.exe"),
            );
            c.language().set(language);
            run(&c, HotkeyEvent::DictationDown, 0).await;
            assert_eq!(
                c.overlay().events(),
                vec!["show:listening".to_owned(), notice(expected)],
                "{language}"
            );
        }
    }

    #[tokio::test]
    async fn a_language_change_reaches_the_next_notice() {
        let c = controller(
            FakeCore::failing("dictation.start", RpcError::new(1004, "no microphone")),
            FakePaster::pasting("notepad.exe"),
        );
        run(&c, HotkeyEvent::DictationDown, 0).await;
        ends_with_notice(&c, NO_MIC.zh);
        run(&c, HotkeyEvent::DictationUp, 300).await;
        c.language().set("en");
        run(&c, HotkeyEvent::DictationDown, 5000).await;
        ends_with_notice(&c, NO_MIC.en);
    }

    #[tokio::test]
    async fn the_fallback_notices_follow_the_language_too() {
        let c = controller(
            FakeCore::with("dictation.stop", stop_result("原文", Some("budget"))),
            FakePaster::pasting("notepad.exe"),
        );
        c.language().set("ja");
        run(&c, HotkeyEvent::DictationDown, 0).await;
        run(&c, HotkeyEvent::DictationUp, 900).await;
        ends_with_notice(&c, BUDGET.ja);
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
        // Both deliveries complete; the first then queues the once-per-session notice.
        assert_eq!(
            c.overlay().events(),
            vec![
                "show:listening",
                "mode:thinking",
                "hide",
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
        // Right Alt+V: the Right Alt went down first (a start in either mode), then V
        for mode in BOTH_MODES {
            let c = controller_in(
                mode,
                FakeCore::with(
                    "history.latest",
                    json!({"item": {"id": 3, "text": "上一次"}}),
                ),
                FakePaster::pasting("notepad.exe"),
            );
            run(&c, HotkeyEvent::DictationDown, 0).await;
            assert_eq!(c.phase(), Phase::Recording, "{mode:?}");
            run(&c, HotkeyEvent::PasteLast, 200).await;
            assert_eq!(c.core().count("dictation.cancel"), 1, "{mode:?}");
            assert_eq!(c.paster().delivered(), vec!["上一次"], "{mode:?}");
            assert_eq!(c.phase(), Phase::Idle, "{mode:?}");
            assert_eq!(c.cancel().last(), Some(false), "{mode:?}");
            assert_eq!(
                c.overlay().events(),
                vec!["show:listening", "hide"],
                "{mode:?}: dropped quietly"
            );
            // the release of the Right Alt does nothing
            run(&c, HotkeyEvent::DictationUp, 300).await;
            assert_eq!(c.core().count("dictation.stop"), 0, "{mode:?}");
            assert_eq!(c.phase(), Phase::Idle, "{mode:?}");
        }
    }

    #[tokio::test]
    async fn paste_last_leaves_the_next_press_a_start_in_both_modes() {
        // In toggle mode a detector that still thought it was listening would take the next
        // press for a stop of the recording that Right Alt+V dropped.
        for mode in BOTH_MODES {
            let c = controller_in(
                mode,
                FakeCore::with(
                    "history.latest",
                    json!({"item": {"id": 3, "text": "上一次"}}),
                ),
                FakePaster::pasting("notepad.exe"),
            );
            run(&c, HotkeyEvent::DictationDown, 0).await;
            run(&c, HotkeyEvent::PasteLast, 200).await;
            run(&c, HotkeyEvent::DictationUp, 300).await;
            run(&c, HotkeyEvent::DictationDown, 5000).await;
            assert_eq!(c.core().count("dictation.start"), 2, "{mode:?}");
            assert_eq!(c.core().count("dictation.stop"), 0, "{mode:?}");
            assert_eq!(c.phase(), Phase::Recording, "{mode:?}");
        }
    }

    #[tokio::test]
    async fn paste_last_after_the_dictation_key_was_let_go_in_toggle_mode_still_cancels() {
        // Toggle mode, a quick tap on Right Alt then a V within the second (the key is up and
        // the recording runs): the same fresh recording, dropped the same way.
        let c = toggle_controller(
            FakeCore::with(
                "history.latest",
                json!({"item": {"id": 3, "text": "上一次"}}),
            ),
            FakePaster::pasting("notepad.exe"),
        );
        run(&c, HotkeyEvent::DictationDown, 0).await;
        run(&c, HotkeyEvent::DictationUp, 90).await;
        run(&c, HotkeyEvent::PasteLast, 400).await;
        assert_eq!(c.core().count("dictation.cancel"), 1);
        assert_eq!(c.phase(), Phase::Idle);
        run(&c, HotkeyEvent::DictationDown, 3000).await;
        assert_eq!(c.core().count("dictation.start"), 2, "a start, not a stop");
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
        for mode in BOTH_MODES {
            let c = controller_in(
                mode,
                FakeCore::with(
                    "history.latest",
                    json!({"item": {"id": 3, "text": "上一次"}}),
                ),
                FakePaster::pasting("notepad.exe"),
            );
            run(&c, HotkeyEvent::DictationDown, 0).await;
            run(&c, HotkeyEvent::PasteLast, 1500).await;
            assert_eq!(c.core().count("dictation.cancel"), 0, "{mode:?}");
            assert_eq!(c.phase(), Phase::Recording, "{mode:?}");
        }
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
        with_gated_start_in(DictationMode::Hold, core)
    }

    fn with_gated_start_in(mode: DictationMode, core: FakeCore) -> (Ctl, Arc<Notify>) {
        let (core, gate) = core.gated("dictation.start");
        (
            controller_in(mode, core, FakePaster::pasting("notepad.exe")),
            gate,
        )
    }

    #[tokio::test]
    async fn release_before_the_start_reply_sends_the_stop_after_it() {
        let (c, gate) = with_gated_start(FakeCore::with("dictation.stop", stop_result("好", None)));
        let down = c
            .on_hotkey(HotkeyEvent::DictationDown, 0)
            .expect("the start runs in a task");
        tokio::task::yield_now().await;
        // released while the core is still starting
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
        for mode in BOTH_MODES {
            let c = controller_in(
                mode,
                FakeCore::with(
                    "history.latest",
                    json!({"item": {"id": 3, "text": "上一次"}}),
                ),
                FakePaster::pasting("notepad.exe"),
            );
            run(&c, HotkeyEvent::DictationDown, 0).await;
            run(&c, HotkeyEvent::PasteLast, 999).await;
            assert_eq!(c.core().count("dictation.cancel"), 1, "{mode:?}");
            assert_eq!(c.phase(), Phase::Idle, "{mode:?}");
            assert_eq!(c.paster().delivered(), vec!["上一次"], "{mode:?}");
        }
    }

    #[tokio::test]
    async fn paste_last_a_full_second_after_the_start_keeps_the_recording() {
        for mode in BOTH_MODES {
            let c = controller_in(
                mode,
                FakeCore::with(
                    "history.latest",
                    json!({"item": {"id": 3, "text": "上一次"}}),
                ),
                FakePaster::pasting("notepad.exe"),
            );
            run(&c, HotkeyEvent::DictationDown, 0).await;
            run(&c, HotkeyEvent::PasteLast, 1000).await;
            assert_eq!(c.core().count("dictation.cancel"), 0, "{mode:?}");
            assert_eq!(c.phase(), Phase::Recording, "{mode:?}");
            assert_eq!(c.paster().delivered(), vec!["上一次"], "{mode:?}");
        }
    }

    // ---------- progress after the stop ----------

    #[test]
    fn only_confirmed_successful_delivery_reaches_one_hundred_percent() {
        let c = controller(
            FakeCore::with("dictation.stop", stop_result("hello", None)),
            FakePaster::pasting("notepad.exe"),
        );
        let confirmed = PasteDelivery {
            outcome: PasteOutcome::Pasted,
            receipt: Some(crate::paste::PasteReceipt::new()),
        };
        let failed = PasteDelivery {
            outcome: PasteOutcome::Pasted,
            receipt: None,
        };
        let copied = PasteDelivery {
            outcome: PasteOutcome::CopiedOnly(CopyReason::Elevated),
            receipt: None,
        };
        c.report(Some(&confirmed), None, true);
        assert_eq!(*c.overlay().completions.lock().unwrap(), 1);
        c.report(Some(&failed), None, true);
        c.report(Some(&copied), None, true);
        c.report(None, None, true);
        assert_eq!(*c.overlay().completions.lock().unwrap(), 1);
        c.report(Some(&confirmed), Some(NO_SOUND), true);
        assert_eq!(*c.overlay().completions.lock().unwrap(), 2);
    }

    fn session_of(c: &Ctl) -> String {
        c.core().call_params("dictation.start")["session_id"]
            .as_str()
            .unwrap()
            .to_owned()
    }

    fn progress_params(session: &str, stage: &str, done: u64, total: u64) -> Value {
        json!({
            "session_id": session, "stage": stage, "done": done, "total": total,
            "audio_seconds": 4.5,
        })
    }

    #[tokio::test]
    async fn progress_for_the_current_session_reaches_the_overlay_while_processing() {
        let (core, gate) =
            FakeCore::with("dictation.stop", stop_result("好", None)).gated("dictation.stop");
        let c = controller(core, FakePaster::pasting("notepad.exe"));
        run(&c, HotkeyEvent::DictationDown, 0).await;
        let id = session_of(&c);
        let stop = c
            .on_hotkey(HotkeyEvent::DictationUp, 900)
            .expect("the stop runs in a task");
        settle().await;
        c.on_progress(&progress_params(&id, "transcribing", 1, 3));
        c.on_progress(&progress_params(&id, "cleaning", 3, 3));
        assert_eq!(
            c.overlay().progress(),
            vec!["progress:transcribing:1/3:4.5", "progress:cleaning:3/3:4.5",]
        );
        gate.notify_one();
        stop.await.unwrap();
        assert_eq!(
            c.overlay().timeline(),
            vec![
                "show:listening",
                "mode:thinking",
                "progress:transcribing:1/3:4.5",
                "progress:cleaning:3/3:4.5",
                "progress:done:0/0:0",
                "hide",
            ]
        );
    }

    #[tokio::test]
    async fn progress_for_another_session_is_ignored() {
        let (core, gate) =
            FakeCore::with("dictation.stop", stop_result("好", None)).gated("dictation.stop");
        let c = controller(core, FakePaster::pasting("notepad.exe"));
        run(&c, HotkeyEvent::DictationDown, 0).await;
        let stop = c.on_hotkey(HotkeyEvent::DictationUp, 900).unwrap();
        settle().await;
        c.on_progress(&progress_params("an-old-session", "transcribing", 1, 3));
        c.on_progress(&json!({"stage": "cleaning", "done": 1, "total": 1}));
        assert!(c.overlay().progress().is_empty());
        gate.notify_one();
        stop.await.unwrap();
    }

    #[tokio::test]
    async fn progress_while_recording_or_idle_is_ignored() {
        let c = controller(
            FakeCore::with("dictation.stop", stop_result("好", None)),
            FakePaster::pasting("notepad.exe"),
        );
        c.on_progress(&progress_params("nobody", "transcribing", 0, 1));
        run(&c, HotkeyEvent::DictationDown, 0).await;
        let id = session_of(&c);
        c.on_progress(&progress_params(&id, "transcribing", 0, 1));
        assert!(c.overlay().progress().is_empty());
        run(&c, HotkeyEvent::DictationUp, 900).await;
        // the dictation is over: a late message for it changes nothing
        c.on_progress(&progress_params(&id, "cleaning", 1, 1));
        assert_eq!(c.overlay().progress(), vec!["progress:done:0/0:0"]);
    }

    #[tokio::test]
    async fn malformed_progress_is_ignored() {
        let (core, gate) =
            FakeCore::with("dictation.stop", stop_result("好", None)).gated("dictation.stop");
        let c = controller(core, FakePaster::pasting("notepad.exe"));
        run(&c, HotkeyEvent::DictationDown, 0).await;
        let id = session_of(&c);
        let stop = c.on_hotkey(HotkeyEvent::DictationUp, 900).unwrap();
        settle().await;
        for params in [
            json!({"session_id": id, "stage": "dreaming", "done": 1, "total": 2}),
            json!({"session_id": id, "stage": "transcribing", "done": -1, "total": 2}),
            json!({"session_id": id, "stage": "transcribing", "done": "1", "total": 2}),
            json!({"session_id": id, "stage": "transcribing"}),
            json!("nonsense"),
        ] {
            c.on_progress(&params);
        }
        assert!(c.overlay().progress().is_empty());
        // counts past u32 are clamped, a missing or bad audio length is 0
        c.on_progress(&json!({
            "session_id": id, "stage": "transcribing", "done": 1, "total": u64::MAX,
            "audio_seconds": "long",
        }));
        assert_eq!(
            c.overlay().progress(),
            vec![format!("progress:transcribing:1/{}:0", u32::MAX)]
        );
        gate.notify_one();
        stop.await.unwrap();
    }

    #[tokio::test]
    async fn a_silent_reply_reports_no_sound_without_claiming_completion() {
        let c = controller(
            FakeCore::with("dictation.stop", empty_result(true)),
            FakePaster::pasting("notepad.exe"),
        );
        run(&c, HotkeyEvent::DictationDown, 0).await;
        run(&c, HotkeyEvent::DictationUp, 900).await;
        let timeline = c.overlay().timeline();
        assert_eq!(timeline.last(), Some(&notice(NO_SOUND.zh)));
        assert!(c.overlay().progress().is_empty());
        assert_eq!(*c.overlay().completions.lock().unwrap(), 0);
    }

    #[tokio::test]
    async fn the_reply_fills_the_ring_before_the_paste_notice() {
        let c = controller(
            FakeCore::with("dictation.stop", stop_result("好", None)),
            FakePaster::copying("notepad.exe", CopyReason::Elevated),
        );
        run(&c, HotkeyEvent::DictationDown, 0).await;
        run(&c, HotkeyEvent::DictationUp, 900).await;
        let timeline = c.overlay().timeline();
        assert_eq!(
            timeline[timeline.len() - 2..],
            ["progress:done:0/0:0".to_owned(), notice(ELEVATED.zh)]
        );
    }

    #[tokio::test]
    async fn a_failed_stop_does_not_fill_the_ring() {
        let c = controller(
            FakeCore::failing("dictation.stop", RpcError::new(NO_MICROPHONE, "gone")),
            FakePaster::pasting("notepad.exe"),
        );
        run(&c, HotkeyEvent::DictationDown, 0).await;
        run(&c, HotkeyEvent::DictationUp, 900).await;
        assert!(c.overlay().progress().is_empty());
    }

    #[tokio::test]
    async fn a_cancelled_dictation_does_not_fill_the_ring() {
        let (core, gate) =
            FakeCore::with("dictation.stop", stop_result("好", None)).gated("dictation.stop");
        let c = controller(core, FakePaster::pasting("notepad.exe"));
        run(&c, HotkeyEvent::DictationDown, 0).await;
        let stop = c.on_hotkey(HotkeyEvent::DictationUp, 900).unwrap();
        settle().await;
        run(&c, HotkeyEvent::Cancel, 1000).await;
        gate.notify_one();
        stop.await.unwrap();
        assert!(c.overlay().progress().is_empty());
    }

    // ---------- learning from the user's corrections ----------

    /// Records what the controller tells its after-paste hook.
    #[derive(Default)]
    struct FakeHook(Mutex<Vec<String>>);

    impl crate::learn::AfterPaste for FakeHook {
        fn pasted(&self, text: &str, target: &Target) {
            self.0
                .lock()
                .unwrap()
                .push(format!("pasted:{text}:{}", target.process_name));
        }
        fn dictation_started(&self) {
            self.0.lock().unwrap().push("started".to_owned());
        }
    }

    #[tokio::test]
    async fn the_hook_hears_of_each_start_and_each_paste() {
        let hook = Arc::new(FakeHook::default());
        let c = controller(
            FakeCore::with("dictation.stop", stop_result("好", None)),
            FakePaster::pasting("notepad.exe"),
        )
        .with_after_paste(hook.clone());
        run(&c, HotkeyEvent::DictationDown, 0).await;
        run(&c, HotkeyEvent::DictationUp, 900).await;
        assert_eq!(
            *hook.0.lock().unwrap(),
            ["started", "pasted:好:notepad.exe"]
        );
    }

    #[tokio::test]
    async fn text_left_on_the_clipboard_is_not_watched() {
        let hook = Arc::new(FakeHook::default());
        let c = controller(
            FakeCore::with("dictation.stop", stop_result("好", None)),
            FakePaster::copying("notepad.exe", CopyReason::FocusChanged),
        )
        .with_after_paste(hook.clone());
        run(&c, HotkeyEvent::DictationDown, 0).await;
        run(&c, HotkeyEvent::DictationUp, 900).await;
        assert_eq!(*hook.0.lock().unwrap(), ["started"]);
    }

    #[tokio::test]
    async fn paste_last_watches_only_a_successful_paste_without_starting_a_dictation() {
        for copied in [false, true] {
            let hook = Arc::new(FakeHook::default());
            let paster = if copied {
                FakePaster::copying("notepad.exe", CopyReason::FocusChanged)
            } else {
                FakePaster::pasting("notepad.exe")
            };
            let c = controller(
                FakeCore::with("history.latest", json!({"item": {"text": "hello"}})),
                paster,
            )
            .with_after_paste(hook.clone());
            run(&c, HotkeyEvent::PasteLast, 0).await;
            let expected = if copied {
                vec![]
            } else {
                vec!["pasted:hello:notepad.exe"]
            };
            assert_eq!(*hook.0.lock().unwrap(), expected);
        }
    }

    #[tokio::test]
    async fn a_cancelled_paste_completion_cannot_restart_learning_during_a_new_recording() {
        let hook = Arc::new(FakeHook::default());
        let (paster, entered, release) = FakePaster::pasting("notepad.exe").gated();
        let c = controller(
            FakeCore::with("dictation.stop", stop_result("old dictation", None)),
            paster,
        )
        .with_after_paste(hook.clone());
        run(&c, HotkeyEvent::DictationDown, 0).await;
        let finishing = c.on_hotkey(HotkeyEvent::DictationUp, 900).unwrap();
        entered.notified().await;
        run(&c, HotkeyEvent::Cancel, 1000).await;
        run(&c, HotkeyEvent::DictationDown, 2000).await;
        assert_eq!(c.phase(), Phase::Recording);
        release.send(()).unwrap();
        finishing.await.unwrap();
        // The already-started OS paste can complete, but its old learning hook cannot restart.
        assert_eq!(c.paster().delivered(), ["old dictation"]);
        assert_eq!(*hook.0.lock().unwrap(), ["started", "started"]);
        assert_eq!(c.phase(), Phase::Recording);
    }

    #[tokio::test]
    async fn a_paste_last_completion_cannot_restart_learning_during_a_new_recording() {
        let hook = Arc::new(FakeHook::default());
        let (paster, entered, release) = FakePaster::pasting("notepad.exe").gated();
        let c = controller(
            FakeCore::with(
                "history.latest",
                json!({"item": {"text": "last dictation"}}),
            ),
            paster,
        )
        .with_after_paste(hook.clone());
        let finishing = c.on_hotkey(HotkeyEvent::PasteLast, 0).unwrap();
        entered.notified().await;
        run(&c, HotkeyEvent::DictationDown, 2000).await;
        assert_eq!(c.phase(), Phase::Recording);
        release.send(()).unwrap();
        finishing.await.unwrap();
        assert_eq!(c.paster().delivered(), ["last dictation"]);
        assert_eq!(*hook.0.lock().unwrap(), ["started"]);
        assert_eq!(c.phase(), Phase::Recording);
    }

    #[tokio::test]
    async fn overlapping_identical_pastes_keep_their_own_receipts() {
        #[derive(Default)]
        struct ReceiptHook(Mutex<Vec<(u64, std::time::Instant)>>);
        impl AfterPaste for ReceiptHook {
            fn pasted(&self, _: &str, _: &Target) {
                panic!("the controller must retain the delivery receipt");
            }
            fn pasted_with_receipt(
                &self,
                _: &str,
                _: &Target,
                receipt: crate::paste::PasteReceipt,
            ) {
                self.0
                    .lock()
                    .unwrap()
                    .push((receipt.sequence, receipt.started));
            }
            fn dictation_started(&self) {}
        }
        let hook = Arc::new(ReceiptHook::default());
        let c = controller(FakeCore::default(), FakePaster::pasting("notepad.exe"))
            .with_after_paste(hook.clone());
        let target = c.paster.foreground();
        let first = c
            .deliver("same dictated text".to_owned(), target.clone(), None)
            .await
            .unwrap();
        let first_receipt = first.receipt.as_ref().unwrap();
        let first_stamp = (first_receipt.sequence, first_receipt.started);
        let second = c
            .deliver("same dictated text".to_owned(), target.clone(), None)
            .await
            .unwrap();
        let second_receipt = second.receipt.as_ref().unwrap();
        let second_stamp = (second_receipt.sequence, second_receipt.started);
        assert!(first_stamp.0 < second_stamp.0);
        // A delayed first continuation must not consume or impersonate the second receipt.
        c.learn_after_paste("same dictated text", target.as_ref(), Some(second));
        c.learn_after_paste("same dictated text", target.as_ref(), Some(first));
        assert_eq!(*hook.0.lock().unwrap(), [second_stamp, first_stamp]);
    }

    #[test]
    fn a_failed_send_without_a_receipt_never_starts_learning() {
        let hook = Arc::new(FakeHook::default());
        let c = controller(FakeCore::default(), FakePaster::pasting("notepad.exe"))
            .with_after_paste(hook.clone());
        c.learn_after_paste(
            "same dictated text",
            c.paster.foreground().as_ref(),
            Some(PasteDelivery {
                outcome: PasteOutcome::Pasted,
                receipt: None,
            }),
        );
        assert!(hook.0.lock().unwrap().is_empty());
    }

    #[tokio::test]
    async fn a_correction_after_the_paste_is_learned_once_and_reported() {
        use crate::learn::Learner;
        use crate::learn::fake::{FakeProbe, text};

        let pasted = "我昨天買了一支新的首機，拍照很漂亮，電池也很耐用，整體來說我很滿意";
        let core = FakeCore::with("dictation.stop", stop_result(pasted, None));
        core.script(
            "settings.get",
            Ok(json!({"settings": {"learn_corrections": true}})),
        );
        core.script("dictionary.learn", Ok(json!({"learned": ["手提電話"]})));
        let core = Arc::new(core);
        let overlay = Arc::new(FakeOverlay::default());
        // The field: the pasted text, the user's fix, then the box cleared by sending.
        let fixed = pasted.replacen("首機", "手提電話", 1);
        let probe = Arc::new(FakeProbe::reading(vec![
            text(pasted),
            text(&fixed),
            text(""),
            text("never read"),
        ]));
        let learner = Learner::quick(core.clone(), overlay.clone(), probe.clone());
        let cell = ModeCell::default();
        cell.set(DictationMode::Hold);
        let c = DictationController::new(
            core,
            overlay,
            Arc::new(FakePaster::pasting("LINE.exe")),
            Arc::new(FakeCancel::default()),
            cell,
        )
        .with_after_paste(Arc::new(learner));

        run(&c, HotkeyEvent::DictationDown, 0).await;
        run(&c, HotkeyEvent::DictationUp, 900).await;
        let learned = notice("《了。「手提電話」已加入詞典。》");
        for _ in 0..400 {
            if c.overlay().events().contains(&learned) {
                break;
            }
            tokio::time::sleep(std::time::Duration::from_millis(5)).await;
        }
        assert_eq!(
            c.overlay().events(),
            vec![
                "show:listening".to_owned(),
                "mode:thinking".to_owned(),
                "hide".to_owned(),
                learned
            ]
        );
        assert_eq!(c.core().count("dictionary.learn"), 1);
        assert_eq!(
            c.core().call_params("dictionary.learn"),
            json!({"corrections": [{"word": "手提電話", "misheard": "首機"}]})
        );
        assert_eq!(
            probe.reads(),
            3,
            "the field is not read after it was cleared"
        );
        assert_eq!(probe.asked.lock().unwrap()[0].process_name, "LINE.exe");
    }

    // ====================================================================================
    // Questions to Raphael: the agent keys
    // ====================================================================================

    use HotkeyEvent::{AgentDown, AgentUp, Cancel, DictationDown, DictationUp};

    impl FakeCore {
        /// The params of every call of `method`, oldest first.
        fn all_params(&self, method: &str) -> Vec<Value> {
            self.calls
                .lock()
                .unwrap()
                .iter()
                .filter(|(m, _)| m == method)
                .map(|(_, params)| params.clone())
                .collect()
        }
    }

    /// What `dictation.stop` answers for a question: the transcript, nothing else.
    fn transcript(text: &str) -> Value {
        json!({"purpose": "agent", "text": text, "stt": text, "audio_seconds": 2.5})
    }

    /// The agent's answer.
    fn answer(prefix: &str, text: &str) -> Value {
        json!({"status": "done", "turn_id": 1, "prefix": prefix, "text": text})
    }

    /// The agent wants a YES/NO first.
    fn question(id: &str, timeout_ms: u64) -> Value {
        question_of_tier(id, "T2", timeout_ms)
    }

    fn question_of_tier(id: &str, tier: &str, timeout_ms: u64) -> Value {
        json!({
            "status": "confirm",
            "turn_id": 2,
            "confirm": {
                "id": id, "tier": tier, "prefix": "問", "text": "要複製到剪貼簿嗎？",
                "yes": "是", "no": "否", "timeout_ms": timeout_ms,
            },
        })
    }

    /// The overlay call that shows [`question`].
    fn asked(id: &str, timeout_ms: u64) -> String {
        format!("ask:{id}|T2|問|要複製到剪貼簿嗎？|是|否|{timeout_ms}")
    }

    /// A core whose speech is "幫我複製這句話" and whose agent replies `reply`.
    fn agent_core(reply: Value) -> FakeCore {
        let core = FakeCore::with("dictation.stop", transcript("幫我複製這句話"));
        core.script("agent.ask", Ok(reply));
        core
    }

    /// A controller in hold mode whose agent replies `reply`.
    fn agent(reply: Value) -> Ctl {
        controller(agent_core(reply), FakePaster::pasting("chrome.exe"))
    }

    /// A whole question in hold mode, up to the answer being shown.
    async fn ask_aloud(c: &Ctl) {
        run(c, AgentDown, 0).await;
        run(c, AgentUp, 900).await;
    }

    /// A question in hold mode, with the key released: its turn runs in the task returned.
    async fn start_turn(c: &Ctl) -> JoinHandle<()> {
        run(c, AgentDown, 0).await;
        c.on_hotkey(AgentUp, 900).expect("the turn runs in a task")
    }

    /// Lets the tasks run until the question is in `stage`.
    async fn until_stage(c: &Ctl, stage: AgentStage) {
        for _ in 0..200 {
            if c.agent_stage() == Some(stage) {
                return;
            }
            tokio::task::yield_now().await;
        }
        panic!(
            "the question never reached {stage:?}: {:?}",
            c.agent_stage()
        );
    }

    /// The agent asks for confirmation `c-1`, which is on screen; `agent.confirm` answers 了.
    async fn question_on_screen(timeout_ms: u64) -> (Ctl, JoinHandle<()>) {
        let core = agent_core(question("c-1", timeout_ms));
        core.script("agent.confirm", Ok(answer("了", "已複製到剪貼簿。")));
        let c = controller(core, FakePaster::pasting("chrome.exe"));
        let turn = start_turn(&c).await;
        until_stage(&c, AgentStage::Asking).await;
        (c, turn)
    }

    /// Like [`question_on_screen`], but `agent.confirm` waits for the gate that is returned.
    async fn gated_question_on_screen(timeout_ms: u64) -> (Ctl, JoinHandle<()>, Arc<Notify>) {
        let core = agent_core(question("c-1", timeout_ms));
        core.script("agent.confirm", Ok(answer("了", "已複製到剪貼簿。")));
        let (core, gate) = core.gated("agent.confirm");
        let c = controller(core, FakePaster::pasting("chrome.exe"));
        let turn = start_turn(&c).await;
        until_stage(&c, AgentStage::Asking).await;
        (c, turn, gate)
    }

    // ---------- the whole way ----------

    #[tokio::test(start_paused = true)]
    async fn a_question_in_hold_mode_is_asked_and_the_answer_is_shown_and_then_over() {
        let c = agent(answer("答", "台北現在二十四度。"));
        run(&c, AgentDown, 0).await;
        assert_eq!(c.phase(), Phase::Recording);
        assert_eq!(c.agent_stage(), Some(AgentStage::Listening));
        assert_eq!(c.cancel().last(), Some(true));
        let id = session_of(&c);
        assert_eq!(
            c.core().call_params("dictation.start"),
            json!({"session_id": id, "purpose": "agent"})
        );

        run(&c, AgentUp, 900).await;
        assert_eq!(
            c.core().call_params("dictation.stop"),
            json!({"session_id": id, "target_app": "chrome.exe"})
        );
        assert_eq!(
            c.core().call_params("agent.ask"),
            json!({
                "session_id": id, "text": "幫我複製這句話", "ui_language": "zh-TW",
                "target_app": "chrome.exe",
            })
        );
        assert_eq!(
            c.overlay().events(),
            vec!["greet", "mode:thinking", "say:答|台北現在二十四度。"]
        );
        // It is nobody's to paste, and the history is not asked either.
        assert!(c.paster().delivered().is_empty());
        assert!(c.paster().copied().is_empty());
        assert_eq!(c.core().count("history.latest"), 0);

        // The answer stays on screen for as long as it takes to read, and Esc can still dismiss
        // it, so the cancel keys stay.
        assert_eq!(c.phase(), Phase::Processing);
        assert_eq!(c.agent_stage(), Some(AgentStage::Speaking));
        assert_eq!(c.cancel().last(), Some(true));
        let hold = Duration::from_millis(agent::say_hold_ms("台北現在二十四度。"));
        tokio::time::advance(hold - Duration::from_millis(1)).await;
        settle().await;
        assert_eq!(c.agent_stage(), Some(AgentStage::Speaking));
        tokio::time::advance(Duration::from_millis(1)).await;
        settle().await;
        assert_eq!(c.phase(), Phase::Idle);
        assert_eq!(c.agent_stage(), None);
        assert_eq!(c.cancel().last(), Some(false));
        assert_eq!(
            c.overlay().events().len(),
            3,
            "the answer vanishes by itself"
        );
    }

    #[tokio::test]
    async fn a_question_in_toggle_mode_starts_with_a_press_and_stops_with_the_next_one() {
        let c = controller_in(
            DictationMode::Toggle,
            agent_core(answer("答", "好")),
            FakePaster::pasting("chrome.exe"),
        );
        run(&c, AgentDown, 0).await;
        assert_eq!(c.phase(), Phase::Recording);
        run(&c, AgentUp, 120).await;
        assert_eq!(c.phase(), Phase::Recording, "the release does not stop it");
        assert_eq!(c.core().count("dictation.stop"), 0);
        run(&c, AgentDown, 3000).await;
        assert_eq!(c.core().count("dictation.stop"), 1);
        run(&c, AgentUp, 3100).await;
        assert_eq!(
            c.overlay().events(),
            vec!["greet", "mode:thinking", "say:答|好"],
            "the release of the stopping press does nothing"
        );
        assert_eq!(c.core().count("dictation.start"), 1);
        assert_eq!(c.core().count("agent.ask"), 1);
    }

    #[tokio::test]
    async fn the_agent_key_in_hold_mode_ignores_auto_repeat_and_a_stray_release() {
        let c = agent(answer("答", "好"));
        run(&c, AgentUp, 0).await;
        assert_eq!(c.phase(), Phase::Idle);
        run(&c, AgentDown, 100).await;
        run(&c, AgentDown, 150).await;
        run(&c, AgentDown, 200).await;
        assert_eq!(c.core().count("dictation.start"), 1);
        assert_eq!(c.core().count("dictation.stop"), 0);
        run(&c, AgentUp, 5000).await;
        assert_eq!(c.core().count("dictation.stop"), 1);
        run(&c, AgentUp, 5100).await;
        assert_eq!(c.core().count("dictation.stop"), 1, "one stop per press");
    }

    #[tokio::test]
    async fn the_mode_of_a_question_is_the_one_it_began_with() {
        let c = agent(answer("答", "好"));
        run(&c, AgentDown, 0).await;
        c.mode().set(DictationMode::Toggle);
        // still a hold question: the release stops it
        run(&c, AgentUp, 900).await;
        assert_eq!(c.core().count("dictation.stop"), 1);
        // the next one is a toggle question
        run(&c, AgentDown, 9000).await;
        run(&c, AgentUp, 9100).await;
        assert_eq!(c.core().count("dictation.start"), 2);
        assert_eq!(c.core().count("dictation.stop"), 1);
        assert_eq!(c.phase(), Phase::Recording);
    }

    #[tokio::test]
    async fn a_question_is_thought_about_without_a_percentage_and_a_dictation_with_one() {
        let c = agent(answer("答", "好"));
        ask_aloud(&c).await;
        assert_eq!(*c.overlay().quiet_thoughts.lock().unwrap(), 1);
        assert!(c.overlay().events().contains(&"mode:thinking".to_owned()));

        let c = controller(FakeCore::default(), FakePaster::pasting("notepad.exe"));
        run(&c, DictationDown, 0).await;
        run(&c, DictationUp, 900).await;
        settle().await;
        assert_eq!(*c.overlay().quiet_thoughts.lock().unwrap(), 0);
        assert!(c.overlay().events().contains(&"mode:thinking".to_owned()));
    }

    #[tokio::test]
    async fn the_question_goes_to_the_agent_in_the_interface_language() {
        for language in ["zh-TW", "en", "ja"] {
            let c = agent(answer("答", "好"));
            c.language().set(language);
            ask_aloud(&c).await;
            assert_eq!(
                c.core().call_params("agent.ask")["ui_language"],
                language,
                "{language}"
            );
        }
    }

    #[tokio::test(start_paused = true)]
    async fn a_click_that_took_the_focus_gives_it_back_to_the_app_in_front() {
        let (c, turn) = question_on_screen(30_000).await;
        // the overlay was activated by the click on its button
        *c.paster().stolen.lock().unwrap() = true;
        c.on_confirm("c-1", true);
        turn.await.unwrap();
        assert_eq!(
            *c.paster().refocused.lock().unwrap(),
            [FakePaster::target("chrome.exe")]
        );
    }

    #[tokio::test(start_paused = true)]
    async fn a_click_that_left_the_focus_alone_leaves_it_alone() {
        let (c, turn) = question_on_screen(30_000).await;
        c.on_confirm("c-1", false);
        turn.await.unwrap();
        assert!(c.paster().refocused.lock().unwrap().is_empty());
    }

    #[tokio::test]
    async fn the_question_is_about_the_app_in_front_when_the_key_is_released() {
        let c = controller(
            agent_core(answer("答", "好")),
            FakePaster {
                foreground: None,
                ..FakePaster::pasting("chrome.exe")
            },
        );
        ask_aloud(&c).await;
        assert_eq!(c.core().call_params("agent.ask")["target_app"], Value::Null);
        assert_eq!(
            c.core().call_params("dictation.stop")["target_app"],
            Value::Null
        );
    }

    #[tokio::test]
    async fn the_hook_hears_that_the_microphone_opens_but_never_of_a_paste() {
        let hook = Arc::new(FakeHook::default());
        let c = controller(
            agent_core(answer("答", "好")),
            FakePaster::pasting("chrome.exe"),
        )
        .with_after_paste(hook.clone());
        ask_aloud(&c).await;
        assert_eq!(*hook.0.lock().unwrap(), ["started"]);
    }

    #[tokio::test]
    async fn the_answer_may_be_a_refusal() {
        let c = agent(answer("否", "沒有設定金鑰。"));
        ask_aloud(&c).await;
        assert_eq!(
            c.overlay().events().last().unwrap(),
            "say:否|沒有設定金鑰。"
        );
    }

    // ---------- Right Alt+Space ----------

    #[tokio::test]
    async fn right_alt_and_space_asks_and_does_not_dictate_in_both_modes() {
        for mode in BOTH_MODES {
            let c = controller_in(
                mode,
                agent_core(answer("答", "好")),
                FakePaster::pasting("chrome.exe"),
            );
            // Right Alt goes down first, and starts a dictation...
            run(&c, DictationDown, 0).await;
            assert_eq!(c.agent_stage(), None, "{mode:?}");
            // ...which the Space that follows takes over.
            run(&c, AgentDown, 150).await;
            assert_eq!(c.phase(), Phase::Recording, "{mode:?}");
            assert_eq!(c.agent_stage(), Some(AgentStage::Listening), "{mode:?}");
            let starts = c.core().all_params("dictation.start");
            assert_eq!(starts.len(), 2, "{mode:?}");
            assert!(starts[0].get("purpose").is_none(), "{mode:?}");
            assert_eq!(starts[1]["purpose"], "agent", "{mode:?}");
            // The core runs one session at a time: the dictation is gone before the question.
            assert_eq!(
                c.core().order(),
                vec!["dictation.start", "dictation.cancel", "dictation.start"],
                "{mode:?}"
            );
            assert_eq!(
                c.core().all_params("dictation.cancel"),
                vec![json!({"session_id": starts[0]["session_id"]})],
                "{mode:?}"
            );
            // One emblem: the dictation's, which is the question's now and greets it. Nothing
            // flickers.
            assert_eq!(
                c.overlay().events(),
                vec!["show:listening", "greet"],
                "{mode:?}"
            );
            assert_eq!(
                *c.overlay().greetings.lock().unwrap(),
                vec![GREETING.zh],
                "{mode:?}"
            );
            assert!(!c.overlay().mouse(), "{mode:?}: a greeting takes no clicks");
            assert_eq!(c.cancel().last(), Some(true), "{mode:?}");

            if mode == DictationMode::Hold {
                run(&c, AgentUp, 900).await;
                run(&c, DictationUp, 950).await;
            } else {
                // both keys are let go, the question keeps listening, then the chord again: its
                // Right Alt stops the question, and its Space finds nothing more to stop
                run(&c, AgentUp, 200).await;
                run(&c, DictationUp, 220).await;
                assert_eq!(c.phase(), Phase::Recording, "{mode:?}");
                run(&c, DictationDown, 3000).await;
                assert_eq!(c.core().count("dictation.stop"), 1, "{mode:?}");
                run(&c, AgentDown, 3100).await;
                run(&c, AgentUp, 3150).await;
                run(&c, DictationUp, 3200).await;
            }
            let id = starts[1]["session_id"].clone();
            assert_eq!(c.core().count("dictation.stop"), 1, "{mode:?}");
            assert_eq!(
                c.core().call_params("dictation.stop")["session_id"],
                id,
                "{mode:?}"
            );
            assert_eq!(
                c.overlay().events(),
                vec!["show:listening", "greet", "mode:thinking", "say:答|好"],
                "{mode:?}"
            );
            assert_eq!(c.core().count("dictation.start"), 2, "{mode:?}");
            assert!(c.paster().delivered().is_empty(), "{mode:?}");
        }
    }

    #[tokio::test]
    async fn right_alt_alone_ends_a_question_in_toggle_mode_as_it_ends_a_dictation() {
        // asked with Right Alt+Space, or with an agent key that has no Right Alt in it
        for chord in [true, false] {
            let c = controller_in(
                DictationMode::Toggle,
                agent_core(answer("答", "好")),
                FakePaster::pasting("chrome.exe"),
            );
            if chord {
                run(&c, DictationDown, 0).await;
            }
            run(&c, AgentDown, 150).await;
            run(&c, AgentUp, 200).await;
            run(&c, DictationUp, 220).await;
            assert_eq!(c.agent_stage(), Some(AgentStage::Listening), "{chord}");
            run(&c, DictationDown, 3000).await;
            run(&c, DictationUp, 3100).await;
            assert_eq!(c.core().count("dictation.stop"), 1, "{chord}");
            assert_eq!(c.core().count("agent.ask"), 1, "{chord}");
            assert_eq!(c.overlay().events().last().unwrap(), "say:答|好", "{chord}");
            // no dictation came of the key, and nothing was pasted
            let starts = c.core().all_params("dictation.start");
            assert_eq!(starts.len(), if chord { 2 } else { 1 }, "{chord}");
            assert_eq!(starts.last().unwrap()["purpose"], "agent", "{chord}");
            assert!(c.paster().delivered().is_empty(), "{chord}");
        }
    }

    #[tokio::test]
    async fn right_alt_leaves_a_question_alone_while_its_own_key_decides() {
        // toggle: the press that started the question is still down
        let c = controller_in(
            DictationMode::Toggle,
            agent_core(answer("答", "好")),
            FakePaster::pasting("chrome.exe"),
        );
        run(&c, AgentDown, 0).await;
        run(&c, DictationDown, 100).await;
        run(&c, DictationUp, 150).await;
        assert_eq!(c.agent_stage(), Some(AgentStage::Listening));
        assert_eq!(c.core().count("dictation.stop"), 0);
        run(&c, AgentUp, 200).await;
        run(&c, DictationDown, 3000).await;
        assert_eq!(
            c.core().count("dictation.stop"),
            1,
            "now it ends the question"
        );

        // hold: the release of the agent key ends the question, not Right Alt
        let c = agent(answer("答", "好"));
        run(&c, AgentDown, 0).await;
        run(&c, DictationDown, 100).await;
        run(&c, DictationUp, 150).await;
        assert_eq!(c.agent_stage(), Some(AgentStage::Listening));
        assert_eq!(c.core().count("dictation.stop"), 0);
        run(&c, AgentUp, 900).await;
        assert_eq!(c.core().count("dictation.stop"), 1);
        assert_eq!(c.core().count("dictation.start"), 1, "no dictation either");
    }

    /// A core that hears nothing: a question is over as soon as it is stopped.
    fn silent_core() -> FakeCore {
        FakeCore::with(
            "dictation.stop",
            json!({"purpose": "agent", "text": "", "silent": true}),
        )
    }

    #[tokio::test]
    async fn the_space_of_the_right_alt_and_space_that_ended_a_question_asks_nothing_new() {
        let c = controller_in(
            DictationMode::Toggle,
            silent_core(),
            FakePaster::pasting("chrome.exe"),
        );
        run(&c, DictationDown, 0).await;
        run(&c, AgentDown, 150).await;
        run(&c, AgentUp, 200).await;
        run(&c, DictationUp, 220).await;
        // the chord again, to end it: the question is over before its Space comes
        run(&c, DictationDown, 3000).await;
        assert_eq!(c.phase(), Phase::Idle);
        run(&c, AgentDown, 3100).await;
        run(&c, AgentUp, 3150).await;
        run(&c, DictationUp, 3200).await;
        assert_eq!(c.phase(), Phase::Idle);
        assert_eq!(c.core().count("dictation.start"), 2, "no new question");
        ends_with_notice(&c, NO_SOUND.zh);

        // the chord pressed afresh is a new question, however soon
        run(&c, DictationDown, 3500).await;
        run(&c, AgentDown, 3600).await;
        assert_eq!(c.agent_stage(), Some(AgentStage::Listening));
        assert_eq!(c.overlay().greetings.lock().unwrap().len(), 2);
    }

    #[tokio::test]
    async fn a_second_after_right_alt_ended_a_question_the_agent_key_asks_again() {
        let c = controller_in(
            DictationMode::Toggle,
            silent_core(),
            FakePaster::pasting("chrome.exe"),
        );
        run(&c, AgentDown, 0).await;
        run(&c, AgentUp, 100).await;
        run(&c, DictationDown, 3000).await;
        run(&c, DictationUp, 3100).await;
        run(&c, AgentDown, 3999).await;
        run(&c, AgentUp, 3999).await;
        assert_eq!(c.phase(), Phase::Idle, "still the keys that ended it");
        run(&c, AgentDown, 4000).await;
        assert_eq!(c.agent_stage(), Some(AgentStage::Listening));
    }

    #[tokio::test]
    async fn a_question_is_greeted_in_the_interface_language_and_a_dictation_is_not() {
        for (language, line) in [
            ("zh-TW", GREETING.zh),
            ("en", GREETING.en),
            ("ja", GREETING.ja),
        ] {
            let c = agent(answer("答", "好"));
            c.language().set(language);
            ask_aloud(&c).await;
            assert_eq!(
                *c.overlay().greetings.lock().unwrap(),
                vec![line],
                "{language}"
            );
            assert_eq!(c.overlay().events()[0], "greet", "{language}");
        }

        let c = controller(FakeCore::default(), FakePaster::pasting("notepad.exe"));
        run(&c, DictationDown, 0).await;
        run(&c, DictationUp, 900).await;
        settle().await;
        assert!(c.overlay().greetings.lock().unwrap().is_empty());
        assert_eq!(c.overlay().events()[0], "show:listening");
    }

    #[tokio::test]
    async fn the_keys_of_the_chord_may_be_let_go_in_either_order() {
        for release in [[AgentUp, DictationUp], [DictationUp, AgentUp]] {
            let c = agent(answer("答", "好"));
            run(&c, DictationDown, 0).await;
            run(&c, AgentDown, 150).await;
            for event in release {
                run(&c, event, 900).await;
            }
            assert_eq!(
                c.overlay().events(),
                vec!["show:listening", "greet", "mode:thinking", "say:答|好"],
                "{release:?}"
            );
            assert_eq!(c.core().count("dictation.stop"), 1, "{release:?}");
        }
    }

    #[tokio::test]
    async fn the_next_dictation_after_the_chord_is_a_clean_start_in_both_modes() {
        for mode in BOTH_MODES {
            let c = controller_in(
                mode,
                agent_core(answer("答", "好")),
                FakePaster::pasting("chrome.exe"),
            );
            run(&c, DictationDown, 0).await;
            run(&c, AgentDown, 150).await;
            if mode == DictationMode::Toggle {
                run(&c, AgentUp, 200).await;
                run(&c, DictationUp, 220).await;
                run(&c, AgentDown, 2000).await;
                run(&c, AgentUp, 2050).await;
                run(&c, DictationUp, 2060).await;
            } else {
                run(&c, AgentUp, 900).await;
                run(&c, DictationUp, 950).await;
            }
            // the answer is on screen; Right Alt alone dictates
            run(&c, DictationDown, 6000).await;
            assert_eq!(c.phase(), Phase::Recording, "{mode:?}");
            assert_eq!(c.agent_stage(), None, "{mode:?}");
            let starts = c.core().all_params("dictation.start");
            assert_eq!(starts.len(), 3, "{mode:?}");
            assert!(starts[2].get("purpose").is_none(), "{mode:?}");
            run(&c, DictationUp, 6100).await;
            if mode == DictationMode::Toggle {
                run(&c, DictationDown, 9000).await;
                run(&c, DictationUp, 9100).await;
            }
            assert_eq!(c.phase(), Phase::Idle, "{mode:?}");
            assert_eq!(c.core().count("dictation.stop"), 2, "{mode:?}");
        }
    }

    #[tokio::test]
    async fn a_dictation_that_has_run_for_a_second_is_not_taken_over() {
        for mode in BOTH_MODES {
            let c = controller_in(
                mode,
                FakeCore::default(),
                FakePaster::pasting("notepad.exe"),
            );
            run(&c, DictationDown, 0).await;
            run(&c, AgentDown, 1000).await;
            assert_eq!(c.phase(), Phase::Recording, "{mode:?}");
            assert_eq!(c.agent_stage(), None, "{mode:?}");
            assert_eq!(c.core().count("dictation.start"), 1, "{mode:?}");
            assert_eq!(c.core().count("dictation.cancel"), 0, "{mode:?}");
            run(&c, AgentUp, 1100).await;
            assert_eq!(c.phase(), Phase::Recording, "{mode:?}");
            assert_eq!(c.overlay().events(), vec!["show:listening"], "{mode:?}");
        }
    }

    #[tokio::test]
    async fn a_press_just_under_a_second_after_the_dictation_started_takes_it_over() {
        for mode in BOTH_MODES {
            let c = controller_in(
                mode,
                FakeCore::default(),
                FakePaster::pasting("notepad.exe"),
            );
            run(&c, DictationDown, 0).await;
            run(&c, AgentDown, 999).await;
            assert_eq!(c.agent_stage(), Some(AgentStage::Listening), "{mode:?}");
            assert_eq!(c.core().count("dictation.cancel"), 1, "{mode:?}");
        }
    }

    #[tokio::test]
    async fn the_agent_key_does_nothing_while_a_dictation_is_processed() {
        let (core, gate) =
            FakeCore::with("dictation.stop", stop_result("好", None)).gated("dictation.stop");
        let c = controller(core, FakePaster::pasting("notepad.exe"));
        run(&c, DictationDown, 0).await;
        let stop = c
            .on_hotkey(DictationUp, 900)
            .expect("the stop runs in a task");
        settle().await;
        assert_eq!(c.phase(), Phase::Processing);
        run(&c, AgentDown, 1000).await;
        run(&c, AgentUp, 1100).await;
        assert_eq!(c.core().count("dictation.start"), 1);
        assert_eq!(c.agent_stage(), None);
        gate.notify_one();
        stop.await.unwrap();
        assert_eq!(c.paster().delivered(), vec!["好"]);
        // and the key is a fresh start afterwards
        run(&c, AgentDown, 5000).await;
        assert_eq!(c.agent_stage(), Some(AgentStage::Listening));
    }

    #[tokio::test]
    async fn the_chord_before_the_dictation_was_started_waits_for_the_core() {
        let (core, gate) = agent_core(answer("答", "好")).gated("dictation.start");
        let c = controller(core, FakePaster::pasting("chrome.exe"));
        let first = c
            .on_hotkey(DictationDown, 0)
            .expect("the start runs in a task");
        tokio::task::yield_now().await;
        let second = c
            .on_hotkey(AgentDown, 100)
            .expect("the question starts in a task");
        settle().await;
        assert_eq!(c.agent_stage(), Some(AgentStage::Listening));
        assert_eq!(
            c.core().count("dictation.cancel"),
            0,
            "the core must not see the cancel before the start's reply"
        );
        // the dictation is started, then cancelled, and only then the question is started
        gate.notify_one();
        first.await.unwrap();
        settle().await;
        assert_eq!(
            c.core().order(),
            vec![
                "dictation.start",
                "dictation.start:reply",
                "dictation.cancel",
                "dictation.start"
            ]
        );
        gate.notify_one();
        second.await.unwrap();
        assert_eq!(c.overlay().events(), vec!["show:listening", "greet"]);
        // released before the core answered the question's start: the stop waits for it too
        run(&c, AgentUp, 400).await;
        assert_eq!(c.core().count("dictation.stop"), 1);
        assert_eq!(c.overlay().events().last().unwrap(), "say:答|好");
    }

    #[tokio::test]
    async fn the_failure_of_the_dictation_that_the_chord_dropped_is_not_told() {
        // The core refuses to start (no microphone). The dictation that Right Alt started is
        // dropped by then, so its failure is nobody's; the question's own is told, once.
        let (core, gate) =
            FakeCore::failing("dictation.start", RpcError::new(1004, "no microphone"))
                .gated("dictation.start");
        let c = controller(core, FakePaster::pasting("chrome.exe"));
        let first = c
            .on_hotkey(DictationDown, 0)
            .expect("the start runs in a task");
        tokio::task::yield_now().await;
        let second = c
            .on_hotkey(AgentDown, 100)
            .expect("the question starts in a task");
        settle().await;
        gate.notify_one();
        first.await.unwrap();
        assert_eq!(
            c.overlay().events(),
            vec!["show:listening", "greet"],
            "the dropped dictation says nothing"
        );
        settle().await;
        gate.notify_one();
        second.await.unwrap();
        assert_eq!(
            c.overlay().events(),
            vec![
                "show:listening".to_owned(),
                "greet".to_owned(),
                notice(NO_MIC.zh)
            ]
        );
        assert_eq!(
            c.core().count("dictation.cancel"),
            0,
            "nothing was started, so there is nothing to cancel"
        );
        assert_eq!(c.phase(), Phase::Idle);
        assert_eq!(c.cancel().last(), Some(false));
    }

    #[tokio::test]
    async fn escape_after_the_chord_cancels_the_question() {
        for mode in BOTH_MODES {
            let c = controller_in(
                mode,
                FakeCore::default(),
                FakePaster::pasting("notepad.exe"),
            );
            run(&c, DictationDown, 0).await;
            run(&c, AgentDown, 100).await;
            run(&c, Cancel, 600).await;
            assert_eq!(c.phase(), Phase::Idle, "{mode:?}");
            assert_eq!(c.cancel().last(), Some(false), "{mode:?}");
            assert_eq!(c.core().count("dictation.cancel"), 2, "{mode:?}");
            let starts = c.core().all_params("dictation.start");
            assert_eq!(
                c.core().all_params("dictation.cancel")[1],
                json!({"session_id": starts[1]["session_id"]}),
                "{mode:?}: the question is the second cancel"
            );
            assert_eq!(
                c.overlay().events(),
                vec!["show:listening", "greet", "hide"],
                "{mode:?}"
            );
            // the keys let go afterwards do nothing, and the next press starts afresh
            run(&c, AgentUp, 700).await;
            run(&c, DictationUp, 710).await;
            assert_eq!(c.core().count("dictation.stop"), 0, "{mode:?}");
            run(&c, AgentDown, 5000).await;
            assert_eq!(c.agent_stage(), Some(AgentStage::Listening), "{mode:?}");
        }
    }

    #[tokio::test]
    async fn paste_last_leaves_a_question_that_is_listening_alone() {
        let core = FakeCore::with(
            "history.latest",
            json!({"item": {"id": 3, "text": "上一次"}}),
        );
        let c = controller(core, FakePaster::pasting("notepad.exe"));
        run(&c, AgentDown, 0).await;
        run(&c, HotkeyEvent::PasteLast, 200).await;
        assert_eq!(c.phase(), Phase::Recording);
        assert_eq!(c.agent_stage(), Some(AgentStage::Listening));
        assert_eq!(c.core().count("dictation.cancel"), 0);
        assert_eq!(c.paster().delivered(), vec!["上一次"]);
    }

    // ---------- nothing to ask, and things that go wrong ----------

    #[tokio::test]
    async fn a_question_nobody_spoke_is_hidden_and_the_agent_is_not_asked() {
        for text in ["", "  \n "] {
            let c = controller(
                FakeCore::with("dictation.stop", transcript(text)),
                FakePaster::pasting("chrome.exe"),
            );
            ask_aloud(&c).await;
            assert_eq!(
                c.overlay().events(),
                vec!["greet", "mode:thinking", "hide"],
                "{text:?}"
            );
            assert_eq!(c.core().count("agent.ask"), 0, "{text:?}");
            assert_eq!(c.phase(), Phase::Idle, "{text:?}");
            assert_eq!(c.cancel().last(), Some(false), "{text:?}");
        }
    }

    #[tokio::test]
    async fn a_silent_microphone_is_told_like_it_is_for_a_dictation() {
        let c = controller(
            FakeCore::with(
                "dictation.stop",
                json!({"purpose": "agent", "text": "", "silent": true}),
            ),
            FakePaster::pasting("chrome.exe"),
        );
        ask_aloud(&c).await;
        ends_with_notice(&c, NO_SOUND.zh);
        assert_eq!(c.core().count("agent.ask"), 0);
        assert_eq!(c.phase(), Phase::Idle);
    }

    #[tokio::test]
    async fn a_failed_start_of_a_question_is_told_like_a_dictation_start() {
        let c = controller(
            FakeCore::failing("dictation.start", RpcError::new(1004, "no microphone")),
            FakePaster::pasting("chrome.exe"),
        );
        run(&c, AgentDown, 0).await;
        assert_eq!(
            c.overlay().events(),
            vec!["greet".to_owned(), notice(NO_MIC.zh)]
        );
        assert_eq!(c.core().call_params("dictation.start")["purpose"], "agent");
        assert_eq!(c.phase(), Phase::Idle);
        assert_eq!(c.cancel().last(), Some(false));
        // released before the failure was known: there is nothing to stop
        run(&c, AgentUp, 100).await;
        assert_eq!(c.core().count("dictation.stop"), 0);
    }

    #[tokio::test]
    async fn a_busy_core_gets_the_recovery_cancel_for_a_question_too() {
        let c = controller(
            FakeCore::failing("dictation.start", RpcError::new(1003, "busy")),
            FakePaster::pasting("chrome.exe"),
        );
        run(&c, AgentDown, 0).await;
        assert_eq!(c.core().call_params("dictation.cancel"), json!({}));
        assert_eq!(c.overlay().events(), vec!["greet", "hide"]);
        assert_eq!(c.phase(), Phase::Idle);
    }

    #[tokio::test]
    async fn a_failed_stop_of_a_question_is_told_like_a_dictation_stop() {
        let c = controller(
            FakeCore::failing("dictation.stop", RpcError::new(1004, "no microphone")),
            FakePaster::pasting("chrome.exe"),
        );
        ask_aloud(&c).await;
        ends_with_notice(&c, NO_MIC.zh);
        assert_eq!(c.core().count("agent.ask"), 0);

        let c = controller(
            FakeCore::failing("dictation.stop", RpcError::new(-32603, "internal error")),
            FakePaster::pasting("chrome.exe"),
        );
        ask_aloud(&c).await;
        assert_eq!(c.overlay().events().last().unwrap(), "hide");
    }

    #[tokio::test]
    async fn an_agent_that_fails_is_told_as_unable_to_answer_and_never_as_an_error() {
        for err in [
            RpcError::new(-32603, "internal error: secret details"),
            RpcError::new(1001, "not configured"),
        ] {
            let core = agent_core(answer("答", "好"));
            core.script("agent.ask", Err(err.clone()));
            let c = controller(core, FakePaster::pasting("chrome.exe"));
            ask_aloud(&c).await;
            ends_with_notice(&c, ASK_FAILED.zh);
            assert!(
                !c.overlay().events().iter().any(|e| e.contains("secret")),
                "the shell never shows raw errors"
            );
            assert_eq!(c.phase(), Phase::Idle, "{err}");
            assert_eq!(c.cancel().last(), Some(false), "{err}");
        }
    }

    #[tokio::test]
    async fn an_agent_that_does_not_answer_in_time_is_told_to_stop() {
        let core = agent_core(answer("答", "好"));
        core.script("agent.ask", Err(RpcError::new(TIMEOUT, "timed out")));
        let c = controller(core, FakePaster::pasting("chrome.exe"));
        ask_aloud(&c).await;
        settle().await;
        ends_with_notice(&c, ASK_FAILED.zh);
        assert_eq!(
            c.core().call_params("agent.cancel"),
            json!({"session_id": session_of(&c)})
        );
    }

    #[tokio::test]
    async fn a_core_that_exits_during_a_question_is_reported_once() {
        for (second, expected) in [(false, CORE_DOWN.zh), (true, ASK_FAILED.zh)] {
            let core = agent_core(answer("答", "好"));
            core.script(
                "agent.ask",
                Err(RpcError::new(CORE_EXITED, "the core exited")),
            );
            let c = controller(core, FakePaster::pasting("chrome.exe"));
            if second {
                // the down-state was reported already
                lock(&c.state).down_noticed = true;
            }
            ask_aloud(&c).await;
            ends_with_notice(&c, expected);
        }
    }

    #[tokio::test]
    async fn a_reply_that_cannot_be_shown_is_told_as_unable_to_answer() {
        for reply in [
            json!({}),
            json!({"status": "done"}),
            json!({"status": "done", "prefix": "", "text": "x"}),
            json!({"status": "weird"}),
            json!({"status": "confirm"}),
        ] {
            let c = agent(reply.clone());
            ask_aloud(&c).await;
            ends_with_notice(&c, ASK_FAILED.zh);
            assert_eq!(c.core().count("agent.confirm"), 0, "{reply}");
            assert_eq!(c.phase(), Phase::Idle, "{reply}");
        }
    }

    #[tokio::test]
    async fn a_question_that_cannot_be_shown_is_denied_instead_of_left_to_expire() {
        let mut broken = question("c-5", 30_000);
        broken["confirm"].as_object_mut().unwrap().remove("yes");
        let c = agent(broken);
        ask_aloud(&c).await;
        settle().await;
        ends_with_notice(&c, ASK_FAILED.zh);
        assert_eq!(
            c.core().all_params("agent.confirm"),
            vec![json!({"id": "c-5", "approved": false})]
        );
    }

    /// The text of the log call that starts at `rest` (just after `log::`), up to its closing
    /// parenthesis.
    fn log_call(rest: &str) -> &str {
        let (mut depth, mut in_string, mut escaped) = (0i32, false, false);
        for (at, c) in rest.char_indices() {
            if in_string {
                match (escaped, c) {
                    (true, _) => escaped = false,
                    (false, '\\') => escaped = true,
                    (false, '"') => in_string = false,
                    _ => {}
                }
                continue;
            }
            match c {
                '"' => in_string = true,
                '(' => depth += 1,
                ')' if depth > 0 => {
                    depth -= 1;
                    if depth == 0 {
                        return &rest[..=at];
                    }
                }
                _ => {}
            }
        }
        rest
    }

    #[test]
    fn the_log_never_gets_the_words_of_a_question_or_an_answer() {
        // The log is global, so the source is read: no log call of the controller carries one of
        // the variables that hold the words.
        let source = include_str!("dictation.rs").replace("\r\n", "\n");
        let production = source.split("#[cfg(test)]").next().unwrap();
        let mut calls = 0;
        for rest in production.split("log::").skip(1) {
            let call = log_call(rest);
            calls += 1;
            for words in [
                "question",
                "text",
                "prefix",
                "transcript",
                "reply",
                "answer",
                "prompt",
                "value",
            ] {
                assert!(
                    !call.contains(&format!("{{{words}")) && !call.contains(&format!(", {words}")),
                    "log::{call}"
                );
            }
        }
        assert!(calls > 20, "the scan found only {calls} log calls");
    }

    // ---------- YES or NO ----------

    #[tokio::test]
    async fn the_question_is_on_screen_and_a_click_on_yes_confirms() {
        let (c, turn) = question_on_screen(30_000).await;
        assert_eq!(
            c.overlay().events(),
            vec![
                "greet".to_owned(),
                "mode:thinking".to_owned(),
                asked("c-1", 30_000)
            ]
        );
        assert_eq!(c.agent_stage(), Some(AgentStage::Asking));
        assert_eq!(c.phase(), Phase::Processing);
        assert_eq!(
            c.cancel().last(),
            Some(true),
            "Esc must work while it is asked"
        );
        assert_eq!(c.core().count("agent.confirm"), 0);

        c.on_confirm("c-1", true);
        turn.await.unwrap();
        assert_eq!(
            c.core().call_params("agent.confirm"),
            json!({"id": "c-1", "approved": true})
        );
        // the question stays until the reply replaces it
        assert_eq!(c.overlay().events()[3..], ["say:了|已複製到剪貼簿。"]);
        assert_eq!(c.agent_stage(), Some(AgentStage::Speaking));
        assert!(c.paster().delivered().is_empty() && c.paster().copied().is_empty());
    }

    #[tokio::test]
    async fn a_click_on_no_denies() {
        let (c, turn) = question_on_screen(30_000).await;
        c.on_confirm("c-1", false);
        turn.await.unwrap();
        assert_eq!(
            c.core().call_params("agent.confirm"),
            json!({"id": "c-1", "approved": false})
        );
        assert_eq!(
            c.overlay().events().last().unwrap(),
            "say:了|已複製到剪貼簿。"
        );
    }

    #[tokio::test]
    async fn escape_denies_the_question_and_the_reply_is_shown() {
        let (c, turn) = question_on_screen(30_000).await;
        assert!(
            c.on_hotkey(Cancel, 5000).is_none(),
            "Esc is the answer here, not a cancel to wait for"
        );
        turn.await.unwrap();
        assert_eq!(
            c.core().call_params("agent.confirm"),
            json!({"id": "c-1", "approved": false})
        );
        assert_eq!(
            c.overlay().events()[3..],
            ["say:了|已複製到剪貼簿。"],
            "no hide: the session goes on to the reply"
        );
        assert_eq!(c.core().count("agent.cancel"), 0);
        assert_eq!(c.core().count("dictation.cancel"), 0);
    }

    #[tokio::test(start_paused = true)]
    async fn a_question_nobody_answers_is_denied_when_its_time_is_up() {
        let (c, turn) = question_on_screen(5_000).await;
        assert_eq!(c.overlay().events().last().unwrap(), &asked("c-1", 5_000));
        tokio::time::advance(Duration::from_millis(4_999)).await;
        settle().await;
        assert_eq!(c.agent_stage(), Some(AgentStage::Asking));
        assert_eq!(c.core().count("agent.confirm"), 0);
        tokio::time::advance(Duration::from_millis(1)).await;
        turn.await.unwrap();
        assert_eq!(
            c.core().call_params("agent.confirm"),
            json!({"id": "c-1", "approved": false})
        );
        assert_eq!(c.overlay().events()[3..], ["say:了|已複製到剪貼簿。"]);
        // a click that comes after the time is up is too late
        c.on_confirm("c-1", true);
        settle().await;
        assert_eq!(c.core().count("agent.confirm"), 1);
    }

    #[tokio::test]
    async fn only_the_first_answer_counts() {
        let (c, turn) = question_on_screen(30_000).await;
        c.on_confirm("c-1", false);
        c.on_confirm("c-1", true);
        assert!(c.on_hotkey(Cancel, 5000).is_none());
        turn.await.unwrap();
        assert_eq!(
            c.core().all_params("agent.confirm"),
            vec![json!({"id": "c-1", "approved": false})]
        );
    }

    #[tokio::test]
    async fn an_answer_to_another_question_is_dropped() {
        let (c, turn) = question_on_screen(30_000).await;
        c.on_confirm("c-9", true);
        c.on_confirm("", true);
        settle().await;
        assert_eq!(c.agent_stage(), Some(AgentStage::Asking));
        assert_eq!(c.core().count("agent.confirm"), 0);
        c.on_confirm("c-1", true);
        turn.await.unwrap();
        assert_eq!(
            c.core().call_params("agent.confirm"),
            json!({"id": "c-1", "approved": true})
        );
    }

    #[tokio::test]
    async fn an_answer_with_no_question_open_is_dropped() {
        let c = agent(answer("答", "好"));
        c.on_confirm("c-1", true);
        ask_aloud(&c).await;
        c.on_confirm("c-1", true);
        settle().await;
        assert_eq!(c.core().count("agent.confirm"), 0);
        assert_eq!(c.overlay().events().len(), 3);
    }

    #[tokio::test]
    async fn the_time_of_a_question_is_the_cores_within_reason() {
        for (named, shown) in [
            (json!(5_000), 5_000),
            (json!(0), 30_000),
            (json!(null), 30_000),
            (json!(10_000_000), 120_000),
        ] {
            let mut reply = question("c-1", 1);
            reply["confirm"]["timeout_ms"] = named.clone();
            let core = agent_core(reply);
            let c = controller(core, FakePaster::pasting("chrome.exe"));
            let turn = start_turn(&c).await;
            until_stage(&c, AgentStage::Asking).await;
            assert_eq!(
                c.overlay().events().last().unwrap(),
                &asked("c-1", shown),
                "{named}"
            );
            c.on_confirm("c-1", false);
            turn.await.unwrap();
        }
    }

    #[tokio::test]
    async fn the_keys_do_nothing_while_a_question_is_asked() {
        let core = agent_core(question("c-1", 30_000));
        core.script("agent.confirm", Ok(answer("了", "已複製到剪貼簿。")));
        core.script(
            "history.latest",
            Ok(json!({"item": {"id": 3, "text": "上一次"}})),
        );
        let c = controller(core, FakePaster::pasting("chrome.exe"));
        let turn = start_turn(&c).await;
        until_stage(&c, AgentStage::Asking).await;
        run(&c, AgentDown, 6000).await;
        run(&c, AgentUp, 6100).await;
        run(&c, DictationDown, 6200).await;
        run(&c, DictationUp, 6300).await;
        run(&c, HotkeyEvent::PasteLast, 6400).await;
        assert_eq!(c.agent_stage(), Some(AgentStage::Asking));
        assert_eq!(c.core().count("dictation.start"), 1);
        assert_eq!(c.core().count("history.latest"), 0, "paste-last waits too");
        assert!(c.paster().delivered().is_empty());
        c.on_confirm("c-1", true);
        turn.await.unwrap();
        assert_eq!(c.core().count("agent.confirm"), 1);
    }

    #[tokio::test]
    async fn an_answer_that_is_a_new_question_is_denied_and_not_shown() {
        let core = agent_core(question("c-1", 30_000));
        core.script("agent.confirm", Ok(question("c-2", 30_000)));
        let c = controller(core, FakePaster::pasting("chrome.exe"));
        let turn = start_turn(&c).await;
        until_stage(&c, AgentStage::Asking).await;
        c.on_confirm("c-1", true);
        turn.await.unwrap();
        settle().await;
        assert_eq!(
            c.core().all_params("agent.confirm"),
            vec![
                json!({"id": "c-1", "approved": true}),
                json!({"id": "c-2", "approved": false})
            ]
        );
        ends_with_notice(&c, ASK_FAILED.zh);
        assert!(!c.overlay().events().contains(&asked("c-2", 30_000)));
    }

    #[tokio::test]
    async fn a_confirmation_that_fails_is_told_as_unable_to_answer() {
        let core = agent_core(question("c-1", 30_000));
        core.script(
            "agent.confirm",
            Err(RpcError::new(-32603, "internal error")),
        );
        let c = controller(core, FakePaster::pasting("chrome.exe"));
        let turn = start_turn(&c).await;
        until_stage(&c, AgentStage::Asking).await;
        c.on_confirm("c-1", true);
        turn.await.unwrap();
        ends_with_notice(&c, ASK_FAILED.zh);
        assert_eq!(c.phase(), Phase::Idle);
        assert_eq!(c.cancel().last(), Some(false));
    }

    #[tokio::test]
    async fn the_core_restarting_during_a_question_drops_it() {
        let (c, turn) = question_on_screen(30_000).await;
        c.on_core_restarted();
        assert_eq!(c.overlay().events().last().unwrap(), "hide");
        assert_eq!(c.phase(), Phase::Idle);
        assert_eq!(c.cancel().last(), Some(false));
        c.on_confirm("c-1", true);
        turn.await.unwrap();
        assert_eq!(c.core().count("agent.confirm"), 0, "the old core is gone");
        assert_eq!(c.overlay().events().last().unwrap(), "hide");
    }

    // ---------- Esc ----------

    #[tokio::test]
    async fn escape_while_a_question_is_listening_cancels_it_in_the_core() {
        for mode in BOTH_MODES {
            let c = controller_in(
                mode,
                FakeCore::default(),
                FakePaster::pasting("notepad.exe"),
            );
            run(&c, AgentDown, 0).await;
            run(&c, Cancel, 500).await;
            assert_eq!(
                c.core().call_params("dictation.cancel"),
                json!({"session_id": session_of(&c)}),
                "{mode:?}"
            );
            assert_eq!(c.overlay().events(), vec!["greet", "hide"], "{mode:?}");
            assert_eq!(c.phase(), Phase::Idle, "{mode:?}");
            assert_eq!(c.cancel().last(), Some(false), "{mode:?}");
            // the release after Esc does nothing and the next press starts afresh
            run(&c, AgentUp, 600).await;
            assert_eq!(c.core().count("dictation.stop"), 0, "{mode:?}");
            run(&c, AgentDown, 4000).await;
            assert_eq!(c.core().count("dictation.start"), 2, "{mode:?}");
            assert_eq!(c.phase(), Phase::Recording, "{mode:?}");
        }
    }

    #[tokio::test]
    async fn escape_while_the_speech_is_transcribed_cancels_it_in_the_core() {
        let (core, gate) = agent_core(answer("答", "太晚了")).gated("dictation.stop");
        let c = controller(core, FakePaster::pasting("chrome.exe"));
        let turn = start_turn(&c).await;
        settle().await;
        assert_eq!(c.agent_stage(), Some(AgentStage::Transcribing));
        run(&c, Cancel, 1200).await;
        assert_eq!(
            c.core().call_params("dictation.cancel"),
            json!({"session_id": session_of(&c)})
        );
        assert_eq!(c.overlay().events().last().unwrap(), "hide");
        assert_eq!(c.phase(), Phase::Idle);
        let after_cancel = c.overlay().events();
        gate.notify_one();
        turn.await.unwrap();
        assert_eq!(
            c.core().count("agent.ask"),
            0,
            "a late transcript asks nothing"
        );
        assert_eq!(c.overlay().events(), after_cancel);
    }

    #[tokio::test]
    async fn escape_while_the_agent_thinks_cancels_it_and_drops_the_late_answer() {
        let (core, gate) = agent_core(answer("答", "太晚了")).gated("agent.ask");
        let c = controller(core, FakePaster::pasting("chrome.exe"));
        let turn = start_turn(&c).await;
        until_stage(&c, AgentStage::Thinking).await;
        run(&c, Cancel, 1200).await;
        assert_eq!(
            c.core().call_params("agent.cancel"),
            json!({"session_id": session_of(&c)})
        );
        assert_eq!(
            c.core().count("dictation.cancel"),
            0,
            "the speech is long over"
        );
        assert_eq!(c.overlay().events().last().unwrap(), "hide");
        assert_eq!(c.phase(), Phase::Idle);
        assert_eq!(c.cancel().last(), Some(false));
        let after_cancel = c.overlay().events();
        gate.notify_one();
        turn.await.unwrap();
        assert_eq!(c.overlay().events(), after_cancel, "no late answer");
    }

    #[tokio::test]
    async fn a_question_that_arrives_after_escape_is_denied_unseen() {
        let (core, gate) = agent_core(question("c-1", 30_000)).gated("agent.ask");
        let c = controller(core, FakePaster::pasting("chrome.exe"));
        let turn = start_turn(&c).await;
        until_stage(&c, AgentStage::Thinking).await;
        run(&c, Cancel, 1200).await;
        gate.notify_one();
        turn.await.unwrap();
        settle().await;
        assert_eq!(
            c.core().all_params("agent.confirm"),
            vec![json!({"id": "c-1", "approved": false})]
        );
        assert!(!c.overlay().events().contains(&asked("c-1", 30_000)));
    }

    #[tokio::test]
    async fn escape_while_the_answer_to_a_confirmation_is_awaited_drops_it() {
        let core = agent_core(question("c-1", 30_000));
        core.script("agent.confirm", Ok(answer("了", "太晚了")));
        let (core, gate) = core.gated("agent.confirm");
        let c = controller(core, FakePaster::pasting("chrome.exe"));
        let turn = start_turn(&c).await;
        until_stage(&c, AgentStage::Asking).await;
        c.on_confirm("c-1", true);
        until_stage(&c, AgentStage::Confirming).await;
        run(&c, Cancel, 6000).await;
        assert_eq!(c.overlay().events().last().unwrap(), "hide");
        assert_eq!(c.phase(), Phase::Idle);
        assert_eq!(c.core().count("agent.cancel"), 0);
        assert_eq!(c.core().count("dictation.cancel"), 0);
        let after_cancel = c.overlay().events();
        gate.notify_one();
        turn.await.unwrap();
        assert_eq!(c.overlay().events(), after_cancel, "no late reply");
    }

    #[tokio::test(start_paused = true)]
    async fn escape_dismisses_an_answer_that_is_on_screen() {
        let c = agent(answer("答", "好"));
        ask_aloud(&c).await;
        assert_eq!(c.agent_stage(), Some(AgentStage::Speaking));
        run(&c, Cancel, 1500).await;
        assert_eq!(c.overlay().events().last().unwrap(), "hide");
        assert_eq!(c.phase(), Phase::Idle);
        assert_eq!(c.cancel().last(), Some(false));
        assert_eq!(
            c.core().count("agent.cancel"),
            0,
            "nothing is left to cancel"
        );
        assert_eq!(c.core().count("dictation.cancel"), 0);
        // the clock of the answer that is gone does nothing later
        let events = c.overlay().events();
        tokio::time::advance(Duration::from_secs(30)).await;
        settle().await;
        assert_eq!(c.overlay().events(), events);
        assert_eq!(c.cancel().last(), Some(false));
    }

    #[tokio::test]
    async fn escape_with_nothing_on_screen_still_does_nothing() {
        let c = agent(answer("答", "好"));
        run(&c, Cancel, 0).await;
        assert!(c.overlay().events().is_empty());
        assert_eq!(c.core().count("agent.cancel"), 0);
    }

    // ---------- something new replaces an answer ----------

    #[tokio::test(start_paused = true)]
    async fn a_new_question_replaces_an_answer_that_is_on_screen() {
        let c = agent(answer("答", "好"));
        ask_aloud(&c).await;
        assert_eq!(c.agent_stage(), Some(AgentStage::Speaking));
        run(&c, AgentDown, 5_000).await;
        assert_eq!(c.phase(), Phase::Recording);
        assert_eq!(c.agent_stage(), Some(AgentStage::Listening));
        assert_eq!(c.cancel().last(), Some(true));
        assert_eq!(
            c.overlay().events()[3..],
            ["greet"],
            "the new emblem replaces the answer; nothing is hidden in between"
        );
        // the clock of the old answer does not end the new question
        tokio::time::advance(Duration::from_secs(30)).await;
        settle().await;
        assert_eq!(c.phase(), Phase::Recording);
        assert_eq!(c.cancel().last(), Some(true));
    }

    #[tokio::test(start_paused = true)]
    async fn a_dictation_replaces_an_answer_that_is_on_screen() {
        let c = agent(answer("答", "好"));
        ask_aloud(&c).await;
        run(&c, DictationDown, 5_000).await;
        assert_eq!(c.phase(), Phase::Recording);
        assert_eq!(c.agent_stage(), None);
        assert_eq!(c.cancel().last(), Some(true));
        let starts = c.core().all_params("dictation.start");
        assert!(
            starts[1].get("purpose").is_none(),
            "a dictation, not a question"
        );
        // the clock of the old answer does not end the dictation
        tokio::time::advance(Duration::from_secs(30)).await;
        settle().await;
        assert_eq!(c.phase(), Phase::Recording);
        // and the dictation is pasted as always
        run(&c, DictationUp, 5_050).await;
        assert_eq!(c.paster().delivered(), vec!["幫我複製這句話"]);
    }

    #[tokio::test(start_paused = true)]
    async fn the_core_restarting_leaves_an_answer_on_screen_alone() {
        let c = agent(answer("答", "好"));
        ask_aloud(&c).await;
        let events = c.overlay().events();
        c.on_core_restarted();
        assert_eq!(
            c.overlay().events(),
            events,
            "the answer is not the core's to take back"
        );
        assert_eq!(c.phase(), Phase::Idle);
        assert_eq!(c.cancel().last(), Some(false));
    }

    // ---------- what else must hold ----------

    #[tokio::test]
    async fn the_progress_of_a_dictation_is_not_shown_for_a_question() {
        let (core, gate) = agent_core(answer("答", "好")).gated("dictation.stop");
        let c = controller(core, FakePaster::pasting("chrome.exe"));
        let turn = start_turn(&c).await;
        settle().await;
        let id = session_of(&c);
        c.on_progress(&progress_params(&id, "transcribing", 1, 3));
        assert!(c.overlay().progress().is_empty());
        gate.notify_one();
        turn.await.unwrap();
        assert!(c.overlay().progress().is_empty());
    }

    #[tokio::test]
    async fn the_loudness_reaches_the_overlay_while_a_question_is_listening() {
        let c = agent(answer("答", "好"));
        c.on_level(0.5);
        run(&c, AgentDown, 0).await;
        c.on_level(0.25);
        run(&c, AgentUp, 900).await;
        c.on_level(0.75);
        assert_eq!(
            c.overlay().events(),
            vec!["greet", "level:0.25", "mode:thinking", "say:答|好"]
        );
    }

    #[tokio::test]
    async fn the_cancel_switch_follows_a_question_under_the_state_lock() {
        let core = agent_core(question("c-1", 30_000));
        core.script("agent.confirm", Ok(answer("了", "已複製到剪貼簿。")));
        let c = controller(core, FakePaster::pasting("chrome.exe"));
        *c.cancel().probe.lock().unwrap() = Some(c.state.clone());
        let turn = start_turn(&c).await;
        until_stage(&c, AgentStage::Asking).await;
        c.on_confirm("c-1", true);
        turn.await.unwrap();
        // an answer that is dismissed, a dictation, a question that takes over from Right Alt,
        // and Esc once more
        run(&c, Cancel, 5000).await;
        run(&c, DictationDown, 9000).await;
        run(&c, AgentDown, 9100).await;
        run(&c, Cancel, 9500).await;
        let sets = c.cancel().sets();
        assert_eq!(
            sets.iter().map(|(on, _)| *on).collect::<Vec<_>>(),
            vec![true, false, true, true, false]
        );
        assert!(
            sets.iter().all(|(_, held)| *held),
            "(value, lock held) per call: {sets:?}"
        );
    }

    #[tokio::test]
    async fn a_question_with_a_confirmation_makes_these_overlay_calls_in_order() {
        // Every overlay call of a whole question with a confirmation, in order.
        let (c, turn) = question_on_screen(30_000).await;
        c.on_confirm("c-1", true);
        turn.await.unwrap();
        assert_eq!(
            c.overlay().timeline(),
            vec![
                "greet".to_owned(),
                "mode:thinking".to_owned(),
                asked("c-1", 30_000),
                "say:了|已複製到剪貼簿。".to_owned(),
            ]
        );
    }

    // ---------- the mouse stays with a question until its reply ----------

    #[tokio::test]
    async fn a_click_leaves_the_mouse_with_the_question_until_the_reply_is_shown() {
        let (c, turn, gate) = gated_question_on_screen(30_000).await;
        assert!(c.overlay().mouse(), "the buttons must be clickable");
        c.on_confirm("c-1", true);
        until_stage(&c, AgentStage::Confirming).await;
        assert!(
            c.overlay().mouse(),
            "a second click on a button that is still drawn must not land in the app below"
        );
        gate.notify_one();
        turn.await.unwrap();
        assert!(!c.overlay().mouse(), "the reply gives the corner back");
    }

    #[tokio::test]
    async fn escape_leaves_the_mouse_with_the_question_until_the_reply_is_shown() {
        let (c, turn, gate) = gated_question_on_screen(30_000).await;
        assert!(c.on_hotkey(Cancel, 5000).is_none());
        until_stage(&c, AgentStage::Confirming).await;
        assert!(c.overlay().mouse());
        gate.notify_one();
        turn.await.unwrap();
        assert!(!c.overlay().mouse());
    }

    #[tokio::test(start_paused = true)]
    async fn the_clock_leaves_the_mouse_with_the_question_until_the_reply_is_shown() {
        let (c, turn, gate) = gated_question_on_screen(5_000).await;
        tokio::time::advance(Duration::from_millis(5_000)).await;
        until_stage(&c, AgentStage::Confirming).await;
        assert!(c.overlay().mouse());
        gate.notify_one();
        turn.await.unwrap();
        assert!(!c.overlay().mouse());
    }

    #[tokio::test]
    async fn whatever_ends_a_question_gives_the_mouse_back() {
        // Esc once more while the answer is awaited: the session is over and the overlay hidden.
        let (c, turn, gate) = gated_question_on_screen(30_000).await;
        c.on_confirm("c-1", true);
        until_stage(&c, AgentStage::Confirming).await;
        run(&c, Cancel, 6000).await;
        assert!(!c.overlay().mouse());
        gate.notify_one();
        turn.await.unwrap();
        assert!(!c.overlay().mouse());

        // the core restarting
        let (c, turn) = question_on_screen(30_000).await;
        assert!(c.overlay().mouse());
        c.on_core_restarted();
        turn.await.unwrap();
        assert!(!c.overlay().mouse());

        // a reply that cannot be shown is told as a notice, which is a scene of its own
        let core = agent_core(question("c-1", 30_000));
        core.script(
            "agent.confirm",
            Err(RpcError::new(-32603, "internal error")),
        );
        let c = controller(core, FakePaster::pasting("chrome.exe"));
        let turn = start_turn(&c).await;
        until_stage(&c, AgentStage::Asking).await;
        assert!(c.overlay().mouse());
        c.on_confirm("c-1", false);
        turn.await.unwrap();
        ends_with_notice(&c, ASK_FAILED.zh);
        assert!(!c.overlay().mouse());
    }

    #[tokio::test]
    async fn paste_last_does_nothing_while_a_question_is_asked() {
        // A paste that was only copied would show a notice, which ends the question's scene.
        let core = agent_core(question("c-1", 30_000));
        core.script("agent.confirm", Ok(answer("了", "已複製到剪貼簿。")));
        core.script(
            "history.latest",
            Ok(json!({"item": {"id": 3, "text": "上一次"}})),
        );
        let c = controller(
            core,
            FakePaster::copying("powershell.exe", CopyReason::Elevated),
        );
        let turn = start_turn(&c).await;
        until_stage(&c, AgentStage::Asking).await;
        assert!(c.on_hotkey(HotkeyEvent::PasteLast, 6000).is_none());
        settle().await;
        assert!(c.paster().delivered().is_empty());
        assert!(
            !c.overlay()
                .events()
                .iter()
                .any(|e| e.starts_with("notice:")),
            "{:?}",
            c.overlay().events()
        );
        assert!(c.overlay().mouse());
        c.on_confirm("c-1", true);
        turn.await.unwrap();
        assert_eq!(
            c.overlay().events().last().unwrap(),
            "say:了|已複製到剪貼簿。"
        );
    }

    // ---------- a YES to a question that cannot be undone has to be armed ----------

    #[tokio::test(start_paused = true)]
    async fn a_yes_to_a_t3_question_waits_for_the_long_press() {
        let core = agent_core(question_of_tier("c-1", "T3", 30_000));
        core.script("agent.confirm", Ok(answer("了", "已刪除。")));
        let c = controller(core, FakePaster::pasting("chrome.exe"));
        let turn = start_turn(&c).await;
        until_stage(&c, AgentStage::Asking).await;
        // a YES at once is not a person holding the button: dropped, the question stays open
        c.on_confirm("c-1", true);
        settle().await;
        assert_eq!(c.agent_stage(), Some(AgentStage::Asking));
        assert_eq!(c.core().count("agent.confirm"), 0);
        tokio::time::advance(Duration::from_millis(agent::T3_ARMS_AFTER_MS - 1)).await;
        c.on_confirm("c-1", true);
        settle().await;
        assert_eq!(c.agent_stage(), Some(AgentStage::Asking));
        assert_eq!(c.core().count("agent.confirm"), 0);
        // armed
        tokio::time::advance(Duration::from_millis(1)).await;
        c.on_confirm("c-1", true);
        turn.await.unwrap();
        assert_eq!(
            c.core().call_params("agent.confirm"),
            json!({"id": "c-1", "approved": true})
        );
    }

    #[tokio::test(start_paused = true)]
    async fn a_no_is_never_held_back_and_neither_is_a_yes_to_a_t2_question() {
        // a NO to a T3 question, at once
        let core = agent_core(question_of_tier("c-1", "T3", 30_000));
        core.script("agent.confirm", Ok(answer("了", "好")));
        let c = controller(core, FakePaster::pasting("chrome.exe"));
        let turn = start_turn(&c).await;
        until_stage(&c, AgentStage::Asking).await;
        c.on_confirm("c-1", false);
        turn.await.unwrap();
        assert_eq!(
            c.core().call_params("agent.confirm"),
            json!({"id": "c-1", "approved": false})
        );
        // a YES to a T2 question, at once
        let (c, turn) = question_on_screen(30_000).await;
        c.on_confirm("c-1", true);
        turn.await.unwrap();
        assert_eq!(
            c.core().call_params("agent.confirm"),
            json!({"id": "c-1", "approved": true})
        );
    }

    #[tokio::test(start_paused = true)]
    async fn escape_and_the_clock_deny_a_t3_question_whenever_they_come() {
        for escape in [true, false] {
            let core = agent_core(question_of_tier("c-1", "T3", 5_000));
            core.script("agent.confirm", Ok(answer("了", "好")));
            let c = controller(core, FakePaster::pasting("chrome.exe"));
            let turn = start_turn(&c).await;
            until_stage(&c, AgentStage::Asking).await;
            if escape {
                assert!(c.on_hotkey(Cancel, 5000).is_none());
            } else {
                tokio::time::advance(Duration::from_millis(5_000)).await;
            }
            turn.await.unwrap();
            assert_eq!(
                c.core().call_params("agent.confirm"),
                json!({"id": "c-1", "approved": false}),
                "escape: {escape}"
            );
        }
    }

    // ---------- the overlay is nobody's while only an answer is on screen ----------

    #[tokio::test]
    async fn the_tray_copy_during_an_answer_on_screen_says_so() {
        let core = agent_core(answer("答", "好"));
        core.script(
            "history.latest",
            Ok(json!({"item": {"id": 3, "text": "上一次"}})),
        );
        let c = controller(core, FakePaster::pasting("chrome.exe"));
        ask_aloud(&c).await;
        assert_eq!(c.agent_stage(), Some(AgentStage::Speaking));
        c.copy_last().await.unwrap();
        assert_eq!(c.paster().copied(), vec!["上一次"]);
        ends_with_notice(&c, LAST_COPIED.zh);
    }

    #[tokio::test]
    async fn the_tray_copy_during_a_question_leaves_the_overlay_alone() {
        let core = agent_core(answer("答", "好"));
        core.script(
            "history.latest",
            Ok(json!({"item": {"id": 3, "text": "上一次"}})),
        );
        let c = controller(core, FakePaster::pasting("chrome.exe"));
        run(&c, AgentDown, 0).await;
        c.copy_last().await.unwrap();
        assert_eq!(c.paster().copied(), vec!["上一次"]);
        assert_eq!(c.overlay().events(), vec!["greet"]);
    }

    #[tokio::test]
    async fn a_paste_last_during_an_answer_on_screen_is_watched_for_corrections() {
        let hook = Arc::new(FakeHook::default());
        let core = agent_core(answer("答", "好"));
        core.script("history.latest", Ok(json!({"item": {"text": "hello"}})));
        let c = controller(core, FakePaster::pasting("notepad.exe")).with_after_paste(hook.clone());
        ask_aloud(&c).await;
        run(&c, HotkeyEvent::PasteLast, 5000).await;
        assert_eq!(
            *hook.0.lock().unwrap(),
            ["started", "pasted:hello:notepad.exe"]
        );
    }

    // ---------- a question that was cancelled before it started ----------

    #[tokio::test]
    async fn escape_before_the_question_was_started_opens_no_microphone_for_it() {
        let (core, gate) = FakeCore::default().gated("dictation.start");
        let c = controller(core, FakePaster::pasting("chrome.exe"));
        let first = c
            .on_hotkey(DictationDown, 0)
            .expect("the start runs in a task");
        tokio::task::yield_now().await;
        let second = c
            .on_hotkey(AgentDown, 100)
            .expect("the question starts in a task");
        settle().await;
        // Esc while the core has not even answered the dictation's start
        let esc = c.on_hotkey(Cancel, 200).expect("the cancel runs in a task");
        gate.notify_one();
        first.await.unwrap();
        second.await.unwrap();
        esc.await.unwrap();
        assert_eq!(
            c.core().count("dictation.start"),
            1,
            "the question never reached the core"
        );
        assert_eq!(
            c.core().count("dictation.cancel"),
            1,
            "only the dictation was cancelled"
        );
        assert_eq!(
            c.overlay().events(),
            vec!["show:listening", "greet", "hide"]
        );
        assert_eq!(c.phase(), Phase::Idle);
        assert_eq!(c.cancel().last(), Some(false));
    }

    // ---------- the log tells when a click took the focus ----------

    #[test]
    fn a_click_took_the_focus_when_a_window_of_ours_is_in_front_and_was_not() {
        let target = |pid| Target {
            hwnd: 1,
            pid,
            process_name: "x.exe".to_owned(),
            is_elevated: false,
        };
        let (other, ours) = (target(7), target(99));
        assert!(stole_focus(Some(&other), Some(&ours), 99));
        assert!(stole_focus(None, Some(&ours), 99));
        assert!(
            !stole_focus(Some(&ours), Some(&ours), 99),
            "it was ours before: nothing was taken"
        );
        assert!(!stole_focus(Some(&other), Some(&other), 99));
        assert!(!stole_focus(Some(&other), None, 99));
        assert!(!stole_focus(None, None, 99));
    }
}
