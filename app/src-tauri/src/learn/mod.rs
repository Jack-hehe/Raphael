//! Learning from the user's corrections (Typeless-style): after a dictation is pasted, the field
//! it went into is watched for a little while. When the user selects a misheard word and types
//! the right one, the word goes into the dictionary by itself, with the misheard text as its
//! misheard form, and Raphael says so (《了。「手機」已加入詞典。》).
//!
//! - [`Learner`] is the controller's [`AfterPaste`] hook. After a paste (not a copy) it asks the
//!   core whether `learn_corrections` is on, then runs a watch on a thread of its own.
//! - The watch ([`watch`]) asks the [`FieldProbe`] for the field that has the keyboard focus (on
//!   Windows: UI Automation, [`UiaProbe`]), finds the pasted text in it, and reads it every 500 ms
//!   until the "send" moment: the field lost most of the pasted text (the message was sent and the
//!   box cleared), the focus moved on, 60 seconds passed, or a new dictation started. The last
//!   text read before that moment is the one compared with the text right after the paste
//!   ([`Tracker`]).
//! - The corrections go to the core (`dictionary.learn`), which skips dismissed words and words it
//!   knows, and answers with the words that changed the dictionary. Those get one notice.
//!
//! Privacy: the text is read only from the field the dictation was pasted into, only for this
//! short while, and only on this computer. It is never logged (only lengths and counts) and never
//! kept: the word pairs that are learned are the only thing that is stored.

mod apps;
mod cjk_words;
mod corrections;
mod keystrokes;
#[cfg(all(test, windows))]
mod native_tests;
mod terminal;
#[cfg(windows)]
mod uia;

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex, PoisonError};
use std::time::{Duration, Instant};

use serde_json::{Value, json};
use tokio::sync::oneshot;
use tokio::task::JoinHandle;

pub use corrections::{Correction, MIN_KEPT_SHARE, Tracker};
#[cfg(windows)]
pub use uia::UiaProbe;

#[cfg(windows)]
use crate::commands::MainEvent;
use crate::notices::{self, LanguageCell};
use crate::overlay::OverlayApi;
use crate::paste::{PasteReceipt, Target};
use crate::rpc::CoreApi;

/// What a read of the watched field gives.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Snapshot {
    /// The field's whole text (at most about 20 000 characters).
    Text(String),
    /// The field is no longer the one with the focus, or it cannot be read any more.
    Left,
}

/// The text field a dictation was pasted into.
pub trait WatchedField {
    fn read(&mut self) -> Snapshot;

    /// Sources that observe individual edits preserve their own finer-grained origin history.
    fn corrections(&self) -> Option<Vec<Correction>> {
        None
    }
}

/// Finds the field to watch. [`UiaProbe`] is the real one; tests use a fake.
pub trait FieldProbe: Send + Sync + 'static {
    /// The field that has the keyboard focus, right after the paste into `target`: `None` unless
    /// it is in `target`'s window, is no password field and its text can be read. Called on the
    /// watch's own thread, which then uses the field and drops it.
    fn focused(&self, target: &Target) -> Option<Box<dyn WatchedField>>;

    /// A paste-scoped probe may use the text to isolate a terminal region or seed a keyboard
    /// reconstruction. The deadline includes discovery and all subsequent reads.
    fn after_paste(
        &self,
        target: &Target,
        _pasted: &str,
        _scope: WatchScope,
    ) -> Option<Box<dyn WatchedField>> {
        self.focused(target)
    }
}

const WATCH_LIMIT: Duration = Duration::from_secs(60);
static CANCELLATION: AtomicU64 = AtomicU64::new(0);

/// Validity travels into discovery too, so a cancelled probe cannot arm a fresh observer.
#[derive(Clone)]
pub struct WatchScope {
    deadline: Instant,
    generation: Arc<AtomicU64>,
    started: u64,
    cancellation: u64,
    #[cfg(windows)]
    input: Option<handy_keys::InputCheckpoint>,
}

impl WatchScope {
    fn active(&self) -> bool {
        self.generation.load(Ordering::SeqCst) == self.started
            && CANCELLATION.load(Ordering::SeqCst) == self.cancellation
            && Instant::now() < self.deadline
    }
}

/// Stops current watches when the setting is switched off. No text is retained by the hook.
pub fn cancel_watches() {
    CANCELLATION.fetch_add(1, Ordering::SeqCst);
    #[cfg(windows)]
    handy_keys::cancel_edit_observations();
}

/// Why learning is limited in an app, for the main window (see [`MainEvent::LearnLimited`]).
#[cfg(windows)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum LimitReason {
    /// A code editor whose accessibility support is off keeps its text out of reach of other
    /// programs: the text box UI Automation sees is empty.
    AccessibilityOff,
}

#[cfg(windows)]
impl LimitReason {
    /// The identifier the main window knows it by. It must not change.
    const fn id(self) -> &'static str {
        match self {
            LimitReason::AccessibilityOff => "accessibility_off",
        }
    }
}

/// What has been reported, so that each thing is said once while Raphael runs.
#[cfg(windows)]
#[derive(Default)]
struct Told(Vec<(&'static str, &'static str)>);

#[cfg(windows)]
impl Told {
    /// Whether this is the first report of `reason` for `app`; remembers it.
    fn first(&mut self, app: &'static str, reason: LimitReason) -> bool {
        let key = (app, reason.id());
        if self.0.contains(&key) {
            return false;
        }
        self.0.push(key);
        true
    }
}

#[cfg(windows)]
static TOLD: Mutex<Told> = Mutex::new(Told(Vec::new()));

/// Tells the main window that learning is limited in `app`, the first time this session.
#[cfg(windows)]
fn report_limited(app: &'static str, reason: LimitReason) {
    report_once(&TOLD, app, reason, crate::commands::tell_main_window);
}

#[cfg(windows)]
fn report_once(
    told: &Mutex<Told>,
    app: &'static str,
    reason: LimitReason,
    send: impl FnOnce(MainEvent),
) {
    if !told
        .lock()
        .unwrap_or_else(PoisonError::into_inner)
        .first(app, reason)
    {
        return;
    }
    // Metadata only: which editor, and why.
    log::info!(
        "learn: learning is limited (app: {app}, reason: {})",
        reason.id()
    );
    send(MainEvent::LearnLimited {
        app: app.to_owned(),
        reason: reason.id().to_owned(),
    });
}

/// What the dictation controller tells about its pastes.
pub trait AfterPaste: Send + Sync + 'static {
    /// A dictation's `text` was pasted into `target`.
    fn pasted(&self, text: &str, target: &Target);
    /// The evidence belongs to this exact delivery, even when continuations resume out of order.
    fn pasted_with_receipt(&self, text: &str, target: &Target, _receipt: PasteReceipt) {
        self.pasted(text, target);
    }
    /// A new dictation is starting: a watch that is still running ends.
    fn dictation_started(&self);
    /// A paste-last action is about to replace the current field contents.
    fn paste_started(&self) {}
}

/// How often the field is read, and for how long.
#[derive(Clone, Copy, Debug)]
pub struct Timing {
    pub poll: Duration,
    /// Reads while waiting for the pasted text to show up in the field.
    pub find_polls: u32,
    /// Reads after that, at most: 120 × 500 ms is a minute.
    pub watch_polls: u32,
}

pub const TIMING: Timing = Timing {
    poll: Duration::from_millis(500),
    find_polls: 6,
    watch_polls: 120,
};

/// Why a watch ended.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum End {
    /// The field lost most of the pasted text: the message was sent, or the text deleted.
    Sent,
    /// The focus moved to another field or window, or the field cannot be read.
    Left,
    /// The minute is over.
    Timeout,
    /// A new dictation started.
    Stopped,
}

/// Watches `field` after `pasted` went into it, and returns the corrections the user made. `wait`
/// waits one poll and says whether to go on (false: a new dictation started). Logs lengths and
/// counts only, never the text.
pub fn watch(
    field: &mut dyn WatchedField,
    pasted: &str,
    timing: Timing,
    mut wait: impl FnMut() -> bool,
) -> Vec<Correction> {
    let mut tracker = None;
    for attempt in 0..=timing.find_polls {
        match field.read() {
            Snapshot::Text(text) => {
                tracker = Tracker::new(&text, pasted);
                if tracker.is_some() {
                    break;
                }
            }
            Snapshot::Left => {
                log::info!("learn: the field was left before the dictation showed in it");
                return Vec::new();
            }
        }
        if attempt == timing.find_polls || !wait() {
            break;
        }
    }
    let Some(mut tracker) = tracker else {
        log::info!(
            "learn: the pasted text ({} characters) is not in the field; not watching",
            pasted.chars().count()
        );
        return Vec::new();
    };

    let mut last = String::new();
    let mut end = End::Timeout;
    for _ in 0..timing.watch_polls {
        if !wait() {
            end = End::Stopped;
            break;
        }
        match field.read() {
            Snapshot::Text(text) => {
                if text == last {
                    continue;
                }
                let next = tracker.advanced(&text);
                if next.kept() < MIN_KEPT_SHARE {
                    end = End::Sent;
                    break;
                }
                tracker = next;
                last = text;
            }
            Snapshot::Left => {
                end = End::Left;
                break;
            }
        }
    }
    let corrections = field.corrections().unwrap_or_else(|| tracker.corrections());
    log::info!(
        "learn: the watch ended ({end:?}) with {} correction(s)",
        corrections.len()
    );
    corrections
}

/// The controller's [`AfterPaste`]: watches the field after each paste and learns the
/// corrections.
pub struct Learner<C: CoreApi, O: OverlayApi, P: FieldProbe> {
    core: Arc<C>,
    overlay: Arc<O>,
    probe: Arc<P>,
    /// Goes up when a dictation starts: a watch stops when it changes, and its notice is left out.
    generation: Arc<AtomicU64>,
    /// Serializes accepting a delivery with cancellation, so stale continuations cannot cancel newer ones.
    latest_delivery: Mutex<u64>,
    timing: Timing,
    /// The language of the notice that names the learned words.
    language: LanguageCell,
}

impl<C: CoreApi, O: OverlayApi, P: FieldProbe> Learner<C, O, P> {
    pub fn new(core: Arc<C>, overlay: Arc<O>, probe: Arc<P>) -> Self {
        Self {
            core,
            overlay,
            probe,
            generation: Arc::new(AtomicU64::new(0)),
            latest_delivery: Mutex::new(0),
            timing: TIMING,
            language: LanguageCell::default(),
        }
    }

    /// The learner, naming the learned words in the language `language` holds, which the device
    /// settings keep up to date.
    pub fn with_language(mut self, language: LanguageCell) -> Self {
        self.language = language;
        self
    }

    /// Starts the watch after `text` was pasted into `target`; `None` if there is nothing to
    /// watch (an elevated window). Must be called from within a Tokio runtime.
    fn start(&self, text: &str, target: &Target) -> Option<JoinHandle<()>> {
        self.start_with_receipt(text, target, PasteReceipt::new())
    }

    fn start_with_receipt(
        &self,
        text: &str,
        target: &Target,
        receipt: PasteReceipt,
    ) -> Option<JoinHandle<()>> {
        if target.is_elevated {
            log::info!("learn: not watching an elevated window");
            return None;
        }
        let mut latest_delivery = self
            .latest_delivery
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        if receipt.sequence <= *latest_delivery {
            return None;
        }
        *latest_delivery = receipt.sequence;
        #[cfg(windows)]
        handy_keys::cancel_edit_observations();
        let deadline = receipt.started + WATCH_LIMIT;
        let core = self.core.clone();
        let overlay = self.overlay.clone();
        let probe = self.probe.clone();
        let generation = self.generation.clone();
        // Even a paste-last action replaces an older watch.
        let started = generation.fetch_add(1, Ordering::SeqCst) + 1;
        let cancellation = CANCELLATION.load(Ordering::SeqCst);
        let scope = WatchScope {
            deadline,
            generation: generation.clone(),
            started,
            cancellation,
            #[cfg(windows)]
            input: receipt.input,
        };
        drop(latest_delivery);
        let timing = self.timing;
        let language = self.language.clone();
        let (text, target) = (text.to_owned(), target.clone());
        Some(tokio::spawn(async move {
            if !learning_is_on(core.as_ref()).await {
                return;
            }
            let (found, corrections) = oneshot::channel();
            // UI Automation is used from a thread of its own (COM, multithreaded apartment), which
            // the field never leaves.
            let thread = std::thread::Builder::new()
                .name("raphael-learn".to_owned())
                .spawn(move || {
                    let active = || scope.active();
                    if !active() {
                        drop(found.send(Vec::new()));
                        return;
                    }
                    let Some(mut field) = probe.after_paste(&target, &text, scope.clone()) else {
                        log::info!("learn: the focused field cannot be read; not watching");
                        drop(found.send(Vec::new()));
                        return;
                    };
                    let wait = || {
                        std::thread::sleep(
                            timing
                                .poll
                                .min(deadline.saturating_duration_since(Instant::now())),
                        );
                        active()
                    };
                    let result = if active() {
                        watch(field.as_mut(), &text, timing, wait)
                    } else {
                        Vec::new()
                    };
                    // Drop the UIA field and its observer before making any RPC or notice.
                    drop(field);
                    drop(found.send(result));
                });
            if let Err(err) = thread {
                log::error!("learn: could not start the watch: {err}");
                return;
            }
            let Ok(corrections) = corrections.await else {
                log::error!("learn: the watch ended without an answer");
                return;
            };
            let words =
                learn_if_current(core.as_ref(), &corrections, &CANCELLATION, cancellation).await;
            if words.is_empty() {
                return;
            }
            if generation.load(Ordering::SeqCst) != started {
                log::info!("learn: a new dictation is under way; no notice");
                return;
            }
            overlay.learned_notice(&notices::learned(&words, &language.get()));
        }))
    }
}

impl<C: CoreApi, O: OverlayApi, P: FieldProbe> AfterPaste for Learner<C, O, P> {
    fn pasted(&self, text: &str, target: &Target) {
        drop(self.start(text, target));
    }

    fn pasted_with_receipt(&self, text: &str, target: &Target, receipt: PasteReceipt) {
        drop(self.start_with_receipt(text, target, receipt));
    }

    fn dictation_started(&self) {
        let _delivery = self
            .latest_delivery
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        self.generation.fetch_add(1, Ordering::SeqCst);
        #[cfg(windows)]
        handy_keys::cancel_edit_observations();
    }

    fn paste_started(&self) {
        self.dictation_started();
    }
}

/// Whether the core's `learn_corrections` setting is on. Off if it cannot be read (or the core
/// predates it).
async fn learning_is_on<C: CoreApi>(core: &C) -> bool {
    match core.call("settings.get", json!({})).await {
        Ok(reply) => reply
            .pointer("/settings/learn_corrections")
            .and_then(Value::as_bool)
            .unwrap_or(false),
        Err(err) => {
            log::warn!("learn: settings.get failed: {err}");
            false
        }
    }
}

/// Revalidates cancellation after the settings RPC, which can outlive an off/on toggle.
async fn learn_if_current<C: CoreApi>(
    core: &C,
    corrections: &[Correction],
    cancellation: &AtomicU64,
    started: u64,
) -> Vec<String> {
    if corrections.is_empty()
        || cancellation.load(Ordering::SeqCst) != started
        || !learning_is_on(core).await
        || cancellation.load(Ordering::SeqCst) != started
    {
        return Vec::new();
    }
    learn(core, corrections).await
}

/// Hands the corrections to the core; the words it learned.
async fn learn<C: CoreApi>(core: &C, corrections: &[Correction]) -> Vec<String> {
    let params = json!({
        "corrections": corrections
            .iter()
            .map(|c| json!({ "word": c.word, "misheard": c.misheard }))
            .collect::<Vec<_>>(),
    });
    match core.call("dictionary.learn", params).await {
        Ok(reply) => reply
            .get("learned")
            .and_then(Value::as_array)
            .map(|words| {
                words
                    .iter()
                    .filter_map(Value::as_str)
                    .map(str::to_owned)
                    .collect()
            })
            .unwrap_or_default(),
        Err(err) => {
            log::warn!("learn: dictionary.learn failed: {err}");
            Vec::new()
        }
    }
}

#[cfg(test)]
impl<C: CoreApi, O: OverlayApi, P: FieldProbe> Learner<C, O, P> {
    /// No waiting between reads, and a short watch.
    pub fn quick(core: Arc<C>, overlay: Arc<O>, probe: Arc<P>) -> Self {
        Self {
            timing: Timing {
                poll: Duration::ZERO,
                find_polls: 2,
                watch_polls: 20,
            },
            ..Self::new(core, overlay, probe)
        }
    }

    pub fn generation(&self) -> Arc<AtomicU64> {
        self.generation.clone()
    }
}

#[cfg(all(test, windows))]
mod limited_tests {
    use std::cell::RefCell;

    use super::*;

    fn limited(app: &str) -> MainEvent {
        MainEvent::LearnLimited {
            app: app.to_owned(),
            reason: "accessibility_off".to_owned(),
        }
    }

    #[test]
    fn the_identifiers_the_main_window_knows_do_not_change() {
        assert_eq!(LimitReason::AccessibilityOff.id(), "accessibility_off");
    }

    #[test]
    fn each_limit_is_reported_once_per_session() {
        let told = Mutex::new(Told::default());
        let sent = RefCell::new(Vec::new());
        for _ in 0..3 {
            report_once(&told, "vscode", LimitReason::AccessibilityOff, |event| {
                sent.borrow_mut().push(event);
            });
        }
        report_once(&told, "cursor", LimitReason::AccessibilityOff, |event| {
            sent.borrow_mut().push(event);
        });
        report_once(&told, "vscode", LimitReason::AccessibilityOff, |event| {
            sent.borrow_mut().push(event);
        });
        assert_eq!(*sent.borrow(), [limited("vscode"), limited("cursor")]);
    }
}

#[cfg(test)]
pub mod fake {
    //! A [`FieldProbe`] whose field gives scripted reads.

    use std::collections::VecDeque;
    use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering};
    use std::sync::{Arc, Mutex};

    use super::{FieldProbe, Snapshot, WatchedField};
    use crate::paste::Target;

    /// One scripted read.
    #[derive(Clone, Debug)]
    pub enum Step {
        Read(Snapshot),
        /// A new dictation starts just before this read, which then gives the text.
        NewDictationThen(String),
    }

    pub fn text(text: &str) -> Step {
        Step::Read(Snapshot::Text(text.to_owned()))
    }

    #[derive(Default)]
    pub struct FakeProbe {
        /// `None`: no readable field has the focus.
        steps: Option<Vec<Step>>,
        /// How many reads were made.
        pub reads: Arc<AtomicUsize>,
        /// The targets it was asked about.
        pub asked: Mutex<Vec<Target>>,
        /// The learner's generation, for [`Step::NewDictationThen`].
        pub generation: Mutex<Option<Arc<AtomicU64>>>,
    }

    impl FakeProbe {
        /// A field that reads as `steps`, then as [`Snapshot::Left`].
        pub fn reading(steps: Vec<Step>) -> Self {
            Self {
                steps: Some(steps),
                ..Self::default()
            }
        }

        /// No readable field (a password field, or one without text patterns).
        pub fn unreadable() -> Self {
            Self::default()
        }

        pub fn reads(&self) -> usize {
            self.reads.load(Ordering::SeqCst)
        }
    }

    struct FakeField {
        steps: VecDeque<Step>,
        reads: Arc<AtomicUsize>,
        generation: Option<Arc<AtomicU64>>,
    }

    impl WatchedField for FakeField {
        fn read(&mut self) -> Snapshot {
            self.reads.fetch_add(1, Ordering::SeqCst);
            match self.steps.pop_front() {
                Some(Step::Read(snapshot)) => snapshot,
                Some(Step::NewDictationThen(text)) => {
                    if let Some(generation) = &self.generation {
                        generation.fetch_add(1, Ordering::SeqCst);
                    }
                    Snapshot::Text(text)
                }
                None => Snapshot::Left,
            }
        }
    }

    impl FieldProbe for FakeProbe {
        fn focused(&self, target: &Target) -> Option<Box<dyn WatchedField>> {
            self.asked.lock().unwrap().push(target.clone());
            let steps = self.steps.clone()?;
            Some(Box::new(FakeField {
                steps: steps.into(),
                reads: self.reads.clone(),
                generation: self.generation.lock().unwrap().clone(),
            }))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::fake::{FakeProbe, Step, text};
    use super::*;
    use crate::overlay::{Mode, Progress};
    use crate::rpc::RpcError;
    use std::sync::Mutex;

    const PASTED: &str = "我昨天買了一支新的首機，拍照很漂亮，電池也很耐用，整體來說我很滿意";

    fn scope() -> WatchScope {
        WatchScope {
            deadline: Instant::now() + WATCH_LIMIT,
            generation: Arc::new(AtomicU64::new(7)),
            started: 7,
            cancellation: CANCELLATION.load(Ordering::SeqCst),
            #[cfg(windows)]
            input: None,
        }
    }

    #[test]
    fn discovery_and_reads_share_generation_cancellation_and_the_paste_deadline() {
        let mut scope = scope();
        assert!(scope.active());
        scope.generation.fetch_add(1, Ordering::SeqCst);
        assert!(!scope.active());
        scope.generation.store(7, Ordering::SeqCst);
        scope.deadline = Instant::now() - Duration::from_secs(1);
        assert!(!scope.active());
        scope.deadline = Instant::now() + WATCH_LIMIT;
        scope.cancellation = scope.cancellation.wrapping_add(1);
        assert!(!scope.active());
    }

    fn fixed() -> String {
        PASTED.replacen("首機", "手提電話", 1)
    }

    // ---------- the watch ----------

    struct Script(Vec<Snapshot>, usize);

    impl WatchedField for Script {
        fn read(&mut self) -> Snapshot {
            self.1 += 1;
            if self.0.is_empty() {
                Snapshot::Left
            } else {
                self.0.remove(0)
            }
        }
    }

    fn t(text: &str) -> Snapshot {
        Snapshot::Text(text.to_owned())
    }

    const QUICK: Timing = Timing {
        poll: Duration::ZERO,
        find_polls: 2,
        watch_polls: 10,
    };

    fn misheard_and_word(corrections: &[Correction]) -> Vec<(&str, &str)> {
        corrections
            .iter()
            .map(|c| (c.misheard.as_str(), c.word.as_str()))
            .collect()
    }

    #[test]
    fn the_text_before_the_field_was_cleared_is_the_final_one() {
        let fixed = fixed();
        let mut field = Script(vec![t(PASTED), t(&fixed), t(""), t("下一則訊息")], 0);
        let corrections = watch(&mut field, PASTED, QUICK, || true);
        assert_eq!(misheard_and_word(&corrections), [("首機", "手提電話")]);
        assert_eq!(field.1, 3, "no read after the send");
    }

    #[test]
    fn the_watch_waits_for_the_pasted_text_to_show_up() {
        let fixed = fixed();
        let mut field = Script(vec![t(""), t(PASTED), t(&fixed)], 0);
        let corrections = watch(&mut field, PASTED, QUICK, || true);
        assert_eq!(misheard_and_word(&corrections), [("首機", "手提電話")]);
    }

    #[test]
    fn a_field_that_never_shows_the_text_is_given_up() {
        let mut field = Script(vec![t(""), t(""), t(""), t(PASTED), t(&fixed())], 0);
        assert!(watch(&mut field, PASTED, QUICK, || true).is_empty());
        assert_eq!(field.1, 3, "find_polls + 1 reads");
    }

    #[test]
    fn leaving_the_field_ends_the_watch_with_what_was_read() {
        let fixed = fixed();
        let mut field = Script(vec![t(PASTED), t(&fixed), Snapshot::Left, t(PASTED)], 0);
        let corrections = watch(&mut field, PASTED, QUICK, || true);
        assert_eq!(misheard_and_word(&corrections), [("首機", "手提電話")]);
        assert_eq!(field.1, 3);
    }

    #[test]
    fn the_watch_ends_after_its_time() {
        let fixed = fixed();
        let mut reads = vec![t(PASTED), t(&fixed)];
        reads.extend(std::iter::repeat_n(t(&fixed), 20));
        let mut field = Script(reads, 0);
        let corrections = watch(&mut field, PASTED, QUICK, || true);
        assert_eq!(corrections.len(), 1);
        assert_eq!(field.1, 1 + QUICK.watch_polls as usize);
    }

    #[test]
    fn a_new_dictation_stops_the_watch() {
        let fixed = fixed();
        let mut field = Script(vec![t(PASTED), t(&fixed), t(PASTED)], 0);
        let mut waits = 0;
        let corrections = watch(&mut field, PASTED, QUICK, || {
            waits += 1;
            waits < 2
        });
        assert_eq!(misheard_and_word(&corrections), [("首機", "手提電話")]);
        assert_eq!(field.1, 2);
    }

    #[test]
    fn a_keyboard_source_keeps_its_per_key_history_through_submission() {
        use super::keystrokes::Keystrokes;
        use handy_keys::EditEvent;

        const ENGLISH: &str = "Please ask Allice about the release plan today.";
        struct KeyboardField {
            keys: Keystrokes,
            step: usize,
        }
        impl WatchedField for KeyboardField {
            fn read(&mut self) -> Snapshot {
                self.step += 1;
                match self.step {
                    1 => Snapshot::Text(ENGLISH.to_owned()),
                    2 => {
                        for _ in 0..30 {
                            self.keys.apply(EditEvent::Left { shift: false });
                        }
                        for _ in 0..6 {
                            self.keys.apply(EditEvent::Backspace);
                        }
                        for byte in b"Alice" {
                            self.keys.apply(EditEvent::Ascii(*byte));
                        }
                        self.keys.apply(EditEvent::Enter);
                        Snapshot::Text(self.keys.text().unwrap().to_owned())
                    }
                    _ => Snapshot::Left,
                }
            }
            fn corrections(&self) -> Option<Vec<Correction>> {
                Some(self.keys.corrections())
            }
        }
        let mut field = KeyboardField {
            keys: Keystrokes::new(ENGLISH).unwrap(),
            step: 0,
        };
        let corrections = watch(&mut field, ENGLISH, QUICK, || true);
        assert_eq!(misheard_and_word(&corrections), [("Allice", "Alice")]);
        assert_eq!(field.step, 3);
    }

    // ---------- the learner ----------

    #[derive(Default)]
    struct Core {
        replies: Mutex<Vec<(String, Value)>>,
        calls: Mutex<Vec<(String, Value)>>,
    }

    impl Core {
        fn on(self, method: &str, reply: Value) -> Self {
            self.replies
                .lock()
                .unwrap()
                .push((method.to_owned(), reply));
            self
        }

        fn learning() -> Self {
            Self::default().on(
                "settings.get",
                json!({"settings": {"learn_corrections": true}}),
            )
        }

        fn calls(&self, method: &str) -> Vec<Value> {
            let calls = self.calls.lock().unwrap();
            calls
                .iter()
                .filter(|(m, _)| m == method)
                .map(|(_, p)| p.clone())
                .collect()
        }
    }

    impl CoreApi for Core {
        async fn call(&self, method: &str, params: Value) -> Result<Value, RpcError> {
            self.calls.lock().unwrap().push((method.to_owned(), params));
            let replies = self.replies.lock().unwrap();
            let reply = replies
                .iter()
                .find(|(m, _)| m == method)
                .map(|(_, r)| r.clone());
            Ok(reply.unwrap_or_else(|| json!({})))
        }
    }

    #[derive(Default)]
    struct Overlay(Mutex<Vec<String>>, AtomicU64);

    impl OverlayApi for Overlay {
        fn show(&self, _: Mode) {}
        fn greet(&self, _: &str) {}
        fn set_mode(&self, _: Mode) {}
        fn think_quietly(&self) {}
        fn level(&self, _: f32) {}
        fn notice(&self, text: &str) {
            self.0.lock().unwrap().push(text.to_owned());
        }
        fn learned_notice(&self, text: &str) {
            self.1.fetch_add(1, Ordering::SeqCst);
            self.notice(text);
        }
        fn say(&self, _: &str, _: &str) {}
        fn ask(&self, _: &crate::overlay::AskPrompt) {}
        fn hide(&self) {}
        fn progress(&self, _: Progress) {}
    }

    fn target(process_name: &str) -> Target {
        Target {
            hwnd: 42,
            pid: 7,
            process_name: process_name.to_owned(),
            is_elevated: false,
        }
    }

    type Fixture = (
        Arc<Core>,
        Arc<Overlay>,
        Arc<FakeProbe>,
        Learner<Core, Overlay, FakeProbe>,
    );

    fn learner(core: Core, probe: FakeProbe) -> Fixture {
        let (core, overlay, probe) = (
            Arc::new(core),
            Arc::new(Overlay::default()),
            Arc::new(probe),
        );
        let learner = Learner::quick(core.clone(), overlay.clone(), probe.clone());
        *probe.generation.lock().unwrap() = Some(learner.generation());
        (core, overlay, probe, learner)
    }

    fn sent_field() -> FakeProbe {
        FakeProbe::reading(vec![text(PASTED), text(&fixed()), text("")])
    }

    async fn run(learner: &Learner<Core, Overlay, FakeProbe>, target: &Target) {
        if let Some(task) = learner.start(PASTED, target) {
            task.await.unwrap();
        }
    }

    #[tokio::test]
    async fn a_correction_is_learned_and_reported_once() {
        let core = Core::learning().on("dictionary.learn", json!({"learned": ["手提電話"]}));
        let (core, overlay, probe, learner) = learner(core, sent_field());
        run(&learner, &target("LINE.exe")).await;
        assert_eq!(
            core.calls("dictionary.learn"),
            [json!({"corrections": [{"word": "手提電話", "misheard": "首機"}]})]
        );
        assert_eq!(
            *overlay.0.lock().unwrap(),
            ["《了。「手提電話」已加入詞典。》".to_owned()],
            "one line, in the default language"
        );
        assert_eq!(probe.asked.lock().unwrap()[0].process_name, "LINE.exe");
    }

    #[tokio::test]
    async fn the_learned_words_are_named_in_the_interface_language() {
        for (language, expected) in [
            ("ja", "《了。「手提電話」を辞書に追加しました。》"),
            ("en", "《Understood. “手提電話” added to the dictionary.》"),
        ] {
            let core = Core::learning().on("dictionary.learn", json!({"learned": ["手提電話"]}));
            let (_, overlay, _, learner) = learner(core, sent_field());
            let cell = LanguageCell::default();
            cell.set(language);
            let learner = learner.with_language(cell);
            run(&learner, &target("LINE.exe")).await;
            assert_eq!(
                *overlay.0.lock().unwrap(),
                [expected.to_owned()],
                "{language}"
            );
        }
    }

    #[tokio::test]
    async fn confirmed_words_use_the_dedicated_learned_notice_route() {
        let core = Core::learning().on("dictionary.learn", json!({"learned": ["Typeless"]}));
        let (_, overlay, _, learner) = learner(core, sent_field());
        run(&learner, &target("notepad.exe")).await;
        assert_eq!(overlay.1.load(Ordering::SeqCst), 1);
        assert_eq!(overlay.0.lock().unwrap().len(), 1);
    }

    #[tokio::test]
    async fn disabling_then_reenabling_during_the_final_settings_reply_drops_old_corrections() {
        struct DelayedSettings {
            entered: tokio::sync::Notify,
            reply: tokio::sync::Notify,
            writes: Mutex<Vec<Value>>,
        }
        impl CoreApi for DelayedSettings {
            async fn call(&self, method: &str, params: Value) -> Result<Value, RpcError> {
                if method == "settings.get" {
                    self.entered.notify_one();
                    self.reply.notified().await;
                    // The setting was turned off and back on while this RPC was in flight.
                    Ok(json!({"settings": {"learn_corrections": true}}))
                } else {
                    assert_eq!(method, "dictionary.learn");
                    self.writes.lock().unwrap().push(params);
                    Ok(json!({"learned": ["Typeless"]}))
                }
            }
        }
        let core = Arc::new(DelayedSettings {
            entered: tokio::sync::Notify::new(),
            reply: tokio::sync::Notify::new(),
            writes: Mutex::new(Vec::new()),
        });
        let cancellation = Arc::new(AtomicU64::new(7));
        let pending = {
            let core = core.clone();
            let cancellation = cancellation.clone();
            tokio::spawn(async move {
                learn_if_current(
                    core.as_ref(),
                    &[Correction {
                        word: "Typeless".to_owned(),
                        misheard: "Tyeless".to_owned(),
                    }],
                    cancellation.as_ref(),
                    7,
                )
                .await
            })
        };
        core.entered.notified().await;
        cancellation.fetch_add(1, Ordering::SeqCst);
        core.reply.notify_one();
        assert!(pending.await.unwrap().is_empty());
        assert!(core.writes.lock().unwrap().is_empty());
    }

    #[tokio::test]
    async fn an_older_identical_paste_cannot_cancel_the_newer_watch() {
        let older = PasteReceipt::new();
        let newer = PasteReceipt::new();
        assert!(older.sequence < newer.sequence);
        let (core, _, probe, learner) = learner(Core::learning(), sent_field());
        let target = target("notepad.exe");
        // Deliveries occurred in order, but the second async continuation resumes first.
        let newest = learner.start_with_receipt(PASTED, &target, newer).unwrap();
        let generation = learner.generation.load(Ordering::SeqCst);
        assert!(learner.start_with_receipt(PASTED, &target, older).is_none());
        assert_eq!(learner.generation.load(Ordering::SeqCst), generation);
        newest.await.unwrap();
        assert_eq!(probe.asked.lock().unwrap().len(), 1);
        assert_eq!(core.calls("dictionary.learn").len(), 1);
    }

    #[tokio::test]
    async fn nothing_is_read_when_the_setting_is_off() {
        let core = Core::default().on(
            "settings.get",
            json!({"settings": {"learn_corrections": false}}),
        );
        let (core, overlay, probe, learner) = learner(core, sent_field());
        run(&learner, &target("notepad.exe")).await;
        assert!(probe.asked.lock().unwrap().is_empty());
        assert!(core.calls("dictionary.learn").is_empty());
        assert!(overlay.0.lock().unwrap().is_empty());
    }

    #[tokio::test]
    async fn a_core_without_the_setting_counts_as_off() {
        let (_, _, probe, learner) = learner(Core::default(), sent_field());
        run(&learner, &target("notepad.exe")).await;
        assert!(probe.asked.lock().unwrap().is_empty());
    }

    #[tokio::test]
    async fn elevated_windows_are_not_watched() {
        let (core, _, probe, learner) = learner(Core::learning(), sent_field());
        let elevated = Target {
            is_elevated: true,
            ..target("notepad.exe")
        };
        assert!(learner.start(PASTED, &elevated).is_none());
        assert!(probe.asked.lock().unwrap().is_empty());
        assert!(core.calls("settings.get").is_empty());
    }

    #[tokio::test]
    async fn terminals_are_probed_after_a_paste() {
        let (core, _, probe, learner) = learner(Core::learning(), sent_field());
        run(&learner, &target("WindowsTerminal.exe")).await;
        assert_eq!(probe.asked.lock().unwrap().len(), 1);
        assert_eq!(core.calls("dictionary.learn").len(), 1);
    }

    #[tokio::test]
    async fn an_unreadable_field_learns_nothing() {
        let (core, overlay, _, learner) = learner(Core::learning(), FakeProbe::unreadable());
        run(&learner, &target("notepad.exe")).await;
        assert!(core.calls("dictionary.learn").is_empty());
        assert!(overlay.0.lock().unwrap().is_empty());
    }

    #[tokio::test]
    async fn cancellation_during_discovery_prevents_the_first_text_read() {
        struct CancellingProbe(Arc<std::sync::atomic::AtomicUsize>);
        struct UnusedField(Arc<std::sync::atomic::AtomicUsize>);
        impl WatchedField for UnusedField {
            fn read(&mut self) -> Snapshot {
                self.0.fetch_add(1, Ordering::SeqCst);
                t(PASTED)
            }
        }
        impl FieldProbe for CancellingProbe {
            fn focused(&self, _: &Target) -> Option<Box<dyn WatchedField>> {
                unreachable!()
            }
            fn after_paste(
                &self,
                _: &Target,
                _: &str,
                scope: WatchScope,
            ) -> Option<Box<dyn WatchedField>> {
                scope.generation.fetch_add(1, Ordering::SeqCst);
                assert!(!scope.active());
                Some(Box::new(UnusedField(self.0.clone())))
            }
        }
        let reads = Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let core = Arc::new(Core::learning());
        let learner = Learner::quick(
            core.clone(),
            Arc::new(Overlay::default()),
            Arc::new(CancellingProbe(reads.clone())),
        );
        learner
            .start(PASTED, &target("Code.exe"))
            .unwrap()
            .await
            .unwrap();
        assert_eq!(reads.load(Ordering::SeqCst), 0);
        assert!(core.calls("dictionary.learn").is_empty());
    }

    #[tokio::test]
    async fn no_corrections_no_call() {
        let probe = FakeProbe::reading(vec![text(PASTED), text(PASTED), text("")]);
        let (core, _, _, learner) = learner(Core::learning(), probe);
        run(&learner, &target("notepad.exe")).await;
        assert!(core.calls("dictionary.learn").is_empty());
    }

    #[tokio::test]
    async fn nothing_new_in_the_dictionary_no_notice() {
        let core = Core::learning().on("dictionary.learn", json!({"learned": []}));
        let (core, overlay, _, learner) = learner(core, sent_field());
        run(&learner, &target("notepad.exe")).await;
        assert_eq!(core.calls("dictionary.learn").len(), 1);
        assert!(overlay.0.lock().unwrap().is_empty());
    }

    #[tokio::test]
    async fn a_new_dictation_ends_the_watch_and_keeps_the_notice_out_of_its_way() {
        let core = Core::learning().on("dictionary.learn", json!({"learned": ["手提電話"]}));
        let probe = FakeProbe::reading(vec![
            text(PASTED),
            text(&fixed()),
            Step::NewDictationThen(fixed()),
            text(PASTED),
        ]);
        let (core, overlay, probe, learner) = learner(core, probe);
        run(&learner, &target("notepad.exe")).await;
        assert_eq!(probe.reads(), 3, "no read after the new dictation");
        assert_eq!(
            core.calls("dictionary.learn").len(),
            1,
            "what was corrected is learned"
        );
        assert!(overlay.0.lock().unwrap().is_empty());
    }

    #[tokio::test]
    async fn several_words_get_one_notice() {
        let core =
            Core::learning().on("dictionary.learn", json!({"learned": ["手機", "Typeless"]}));
        let (_, overlay, _, learner) = learner(core, sent_field());
        run(&learner, &target("notepad.exe")).await;
        assert_eq!(
            overlay.0.lock().unwrap()[0],
            "《了。「手機」、「Typeless」已加入詞典。》"
        );
    }
}
