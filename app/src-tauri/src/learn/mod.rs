//! Learning from the user's corrections (Typeless-style): after a dictation is pasted, the field
//! it went into is watched for a little while. When the user selects a misheard word and types
//! the right one, the word goes into the dictionary by itself, with the misheard text as its
//! misheard form, and Raphael says so (《了。已將「手機」加入字典。》).
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

mod corrections;
#[cfg(windows)]
mod uia;

use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Duration;

use serde_json::{Value, json};
use tokio::sync::oneshot;
use tokio::task::JoinHandle;

pub use corrections::{Correction, MIN_KEPT_SHARE, Tracker};
#[cfg(windows)]
pub use uia::UiaProbe;

use crate::notices;
use crate::overlay::OverlayApi;
use crate::paste::{Shortcut, Target, shortcut_for};
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
}

/// Finds the field to watch. [`UiaProbe`] is the real one; tests use a fake.
pub trait FieldProbe: Send + Sync + 'static {
    /// The field that has the keyboard focus, right after the paste into `target`: `None` unless
    /// it is in `target`'s window, is no password field and its text can be read. Called on the
    /// watch's own thread, which then uses the field and drops it.
    fn focused(&self, target: &Target) -> Option<Box<dyn WatchedField>>;
}

/// What the dictation controller tells about its pastes.
pub trait AfterPaste: Send + Sync + 'static {
    /// A dictation's `text` was pasted into `target`.
    fn pasted(&self, text: &str, target: &Target);
    /// A new dictation is starting: a watch that is still running ends.
    fn dictation_started(&self);
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
    let corrections = tracker.corrections();
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
    timing: Timing,
}

impl<C: CoreApi, O: OverlayApi, P: FieldProbe> Learner<C, O, P> {
    pub fn new(core: Arc<C>, overlay: Arc<O>, probe: Arc<P>) -> Self {
        Self {
            core,
            overlay,
            probe,
            generation: Arc::new(AtomicU64::new(0)),
            timing: TIMING,
        }
    }

    /// Starts the watch after `text` was pasted into `target`; `None` if there is nothing to
    /// watch (an elevated window, a terminal). Must be called from within a Tokio runtime.
    fn start(&self, text: &str, target: &Target) -> Option<JoinHandle<()>> {
        if target.is_elevated {
            log::info!("learn: not watching an elevated window");
            return None;
        }
        // A terminal's text is its whole screen, not a field: its prompt and output would read as
        // the user's edits.
        if shortcut_for(&target.process_name) != Shortcut::CtrlV {
            log::info!("learn: not watching a terminal");
            return None;
        }
        let core = self.core.clone();
        let overlay = self.overlay.clone();
        let probe = self.probe.clone();
        let generation = self.generation.clone();
        let started = generation.load(Ordering::SeqCst);
        let timing = self.timing;
        let (text, target) = (text.to_owned(), target.clone());
        Some(tokio::spawn(async move {
            if !learning_is_on(core.as_ref()).await {
                return;
            }
            let (found, corrections) = oneshot::channel();
            // UI Automation is used from a thread of its own (COM, multithreaded apartment), which
            // the field never leaves.
            let current = generation.clone();
            let thread = std::thread::Builder::new()
                .name("raphael-learn".to_owned())
                .spawn(move || {
                    let Some(mut field) = probe.focused(&target) else {
                        log::info!("learn: the focused field cannot be read; not watching");
                        drop(found.send(Vec::new()));
                        return;
                    };
                    let wait = || {
                        std::thread::sleep(timing.poll);
                        current.load(Ordering::SeqCst) == started
                    };
                    drop(found.send(watch(field.as_mut(), &text, timing, wait)));
                });
            if let Err(err) = thread {
                log::error!("learn: could not start the watch: {err}");
                return;
            }
            let Ok(corrections) = corrections.await else {
                log::error!("learn: the watch ended without an answer");
                return;
            };
            if corrections.is_empty() {
                return;
            }
            let words = learn(core.as_ref(), &corrections).await;
            if words.is_empty() {
                return;
            }
            if generation.load(Ordering::SeqCst) != started {
                log::info!("learn: a new dictation is under way; no notice");
                return;
            }
            let (zh, ja) = notices::learned(&words);
            overlay.notice(&zh, &ja);
        }))
    }
}

impl<C: CoreApi, O: OverlayApi, P: FieldProbe> AfterPaste for Learner<C, O, P> {
    fn pasted(&self, text: &str, target: &Target) {
        drop(self.start(text, target));
    }

    fn dictation_started(&self) {
        self.generation.fetch_add(1, Ordering::SeqCst);
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
    struct Overlay(Mutex<Vec<(String, String)>>);

    impl OverlayApi for Overlay {
        fn show(&self, _: Mode) {}
        fn set_mode(&self, _: Mode) {}
        fn level(&self, _: f32) {}
        fn notice(&self, zh: &str, ja: &str) {
            self.0.lock().unwrap().push((zh.to_owned(), ja.to_owned()));
        }
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
            [(
                "《了。已將「手提電話」加入字典。》".to_owned(),
                "《了。「手提電話」を辞書に追加しました。》".to_owned()
            )]
        );
        assert_eq!(probe.asked.lock().unwrap()[0].process_name, "LINE.exe");
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
    async fn elevated_windows_and_terminals_are_not_watched() {
        let (core, _, probe, learner) = learner(Core::learning(), sent_field());
        let elevated = Target {
            is_elevated: true,
            ..target("notepad.exe")
        };
        assert!(learner.start(PASTED, &elevated).is_none());
        assert!(
            learner
                .start(PASTED, &target("WindowsTerminal.exe"))
                .is_none()
        );
        assert!(probe.asked.lock().unwrap().is_empty());
        assert!(core.calls("settings.get").is_empty());
    }

    #[tokio::test]
    async fn an_unreadable_field_learns_nothing() {
        let (core, overlay, _, learner) = learner(Core::learning(), FakeProbe::unreadable());
        run(&learner, &target("notepad.exe")).await;
        assert!(core.calls("dictionary.learn").is_empty());
        assert!(overlay.0.lock().unwrap().is_empty());
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
            overlay.0.lock().unwrap()[0].0,
            "《了。已將「手機」、「Typeless」加入字典。》"
        );
    }
}
