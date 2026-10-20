//! Pausing the music and video that play while Raphael listens.
//!
//! When a recording starts, whatever plays on the computer is paused, and when the recording ends
//! exactly what Raphael paused is played again, so the person can focus on speaking. It is the
//! same for a dictation and a question. The system side is the Windows media controls (the ones
//! behind the volume flyout and the keyboard's media keys): a player that publishes to them can
//! be told to pause and to play, as two different verbs, so nothing is ever toggled and a player
//! that was not playing is never started. A player that does not publish to them (some games and
//! players) is not paused.
//!
//! - [`MediaHold`] is what the dictation controller keeps: it knows whether Raphael is holding
//!   media paused for the recording that is under way, and pairs each pause with one resume.
//! - [`MediaPauser`] is what the controller asks of the system: two calls that return at once.
//! - [`Worker`] carries them out on a thread of its own, one request after the other, so the
//!   hotkey thread and the recording never wait for a player that is slow to answer, and a resume
//!   can never get ahead of the pause it follows. What it asks of the system is [`Transport`];
//!   the Windows one is in `windows.rs`.
//!
//! The log has counts and error codes only, never the name of a player or of what it plays.

use std::fmt;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{self, Receiver, RecvTimeoutError, Sender};
use std::thread::{self, JoinHandle};
use std::time::Duration;

#[cfg(windows)]
mod windows;

#[cfg(windows)]
pub use self::windows::WindowsMedia;

/// How long a pause waits for the recording to be cancelled before it acts. Right Alt+V (paste the
/// last dictation) starts a dictation with its first key and drops it with its second a moment
/// later; pausing and resuming the music for that would only make it stutter. The recording does
/// not wait for this, and a sentence takes longer to begin than a chord to be pressed.
pub const SETTLE: Duration = Duration::from_millis(250);

/// Whether a recording pauses the media that plays: the device setting 「說話時暫停播放中的音樂和
/// 影片」. The device settings write it (at start, and when the person changes it) and the
/// controller reads it each time a recording begins; one that is under way is not touched. Clones
/// share the value.
#[derive(Clone, Debug)]
pub struct PauseMediaCell(Arc<AtomicBool>);

impl Default for PauseMediaCell {
    fn default() -> Self {
        Self(Arc::new(AtomicBool::new(true)))
    }
}

impl PauseMediaCell {
    pub fn get(&self) -> bool {
        self.0.load(Ordering::Relaxed)
    }

    pub fn set(&self, on: bool) {
        self.0.store(on, Ordering::Relaxed);
    }
}

/// What the dictation controller asks of the system's media. Both calls return at once: the work
/// is done elsewhere, in order.
pub trait MediaPauser: Send + Sync + 'static {
    /// A recording begins: pauses everything that plays now and remembers what it paused.
    fn pause(&self);
    /// The recording is over: plays what the last `pause` paused, if it is still paused.
    fn resume(&self);
}

/// The controller's side of it: whether Raphael is holding media paused for the recording that is
/// under way. It lives in the controller's state, and both of its calls are only a channel send,
/// like the cancel switch's, so the state lock may be held while they are made.
#[derive(Default)]
pub struct MediaHold {
    media: Option<(Arc<dyn MediaPauser>, PauseMediaCell)>,
    held: bool,
}

impl MediaHold {
    /// A hold that pauses with `media` while `enabled` is set.
    pub fn new(media: Arc<dyn MediaPauser>, enabled: PauseMediaCell) -> Self {
        Self {
            media: Some((media, enabled)),
            held: false,
        }
    }

    /// A recording begins. Pauses the media, unless the setting is off or this recording already
    /// holds it: a question that takes over a dictation is the same recording.
    pub fn begin(&mut self) {
        if self.held {
            return;
        }
        let Some((media, enabled)) = &self.media else {
            return;
        };
        if enabled.get() {
            media.pause();
            self.held = true;
        }
    }

    /// The recording is over, however it ended. Resumes what `begin` paused; does nothing if it
    /// paused nothing, or if this already has. The setting is not looked at again: what Raphael
    /// paused is his to put back, whatever the setting says by now.
    pub fn end(&mut self) {
        if !std::mem::take(&mut self.held) {
            return;
        }
        if let Some((media, _)) = &self.media {
            media.resume();
        }
    }
}

/// Why a call to the system's media failed.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Failure {
    /// The player did not answer in time. What it was asked may still happen.
    TimedOut,
    /// The system refused or failed, with its error code.
    Os(i32),
}

impl fmt::Display for Failure {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Failure::TimedOut => f.write_str("timed out"),
            Failure::Os(code) => write!(f, "error {code:#010x}"),
        }
    }
}

/// What came of a request to a player: it did it (`true`), it declined (`false`), or the call
/// failed.
pub type Outcome = Result<bool, Failure>;

/// Whether a player plays, as far as pausing is concerned. A player that has stopped (a song that
/// ended, a video closed by the person) is neither: it is not paused, and it is not started again.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Playback {
    Playing,
    Paused,
    Other,
}

/// One player in the system's list, as it was at one moment.
pub struct Listed<S> {
    /// Which application the player belongs to. Only compared, never logged.
    pub app: String,
    pub playback: Playback,
    pub session: S,
}

/// What the system's media controls offer, as far as this module goes.
pub trait Transport {
    /// A player, which can be asked to pause and to play.
    type Session;

    /// Every player the system lists now.
    fn list(&mut self) -> Result<Vec<Listed<Self::Session>>, Failure>;
    /// Asks each of `sessions` to pause, all together, and says how each answered, in order.
    fn pause(&mut self, sessions: Vec<Self::Session>) -> Vec<Outcome>;
    /// Asks each of `sessions` to play, all together, and says how each answered, in order.
    fn play(&mut self, sessions: Vec<Self::Session>) -> Vec<Outcome>;
}

/// What is paused for the recording that is under way, and the two ways of changing that.
struct Held<T: Transport> {
    transport: T,
    /// The applications whose players were paused. A player is looked for again by its
    /// application when it is time to resume: one that is gone from the list has nothing to
    /// resume. (Two players of one application are told apart by nothing more than their order.)
    paused: Vec<String>,
}

impl<T: Transport> Held<T> {
    fn new(transport: T) -> Self {
        Self {
            transport,
            paused: Vec::new(),
        }
    }

    /// Pauses every player that plays and remembers it. A player that declined or failed is not
    /// remembered. One that did not answer in time is, because it may have paused after all, and
    /// a resume plays only what is paused by then: better a look too many than a player left
    /// paused for good.
    fn pause(&mut self) {
        let listed = match self.transport.list() {
            Ok(listed) => listed,
            Err(failure) => {
                log::warn!("media: could not list the players ({failure}); nothing is paused");
                return;
            }
        };
        let (apps, sessions): (Vec<String>, Vec<T::Session>) = listed
            .into_iter()
            .filter(|player| player.playback == Playback::Playing)
            .map(|player| (player.app, player.session))
            .unzip();
        if sessions.is_empty() {
            log::info!("media: nothing plays, so nothing is paused");
            return;
        }
        let playing = sessions.len();
        let outcomes = self.transport.pause(sessions);
        let tally = Tally::of(&outcomes);
        for (app, outcome) in apps.into_iter().zip(outcomes) {
            if matches!(outcome, Ok(true) | Err(Failure::TimedOut)) {
                self.paused.push(app);
            }
        }
        log::info!("media: {playing} playing; {tally}");
    }

    /// Plays what `pause` paused, if it is still paused: not what the person played or stopped
    /// meanwhile, and not what is gone. Forgets everything either way.
    fn resume(&mut self) {
        let mut remembered = std::mem::take(&mut self.paused);
        if remembered.is_empty() {
            return;
        }
        let listed = match self.transport.list() {
            Ok(listed) => listed,
            Err(failure) => {
                log::warn!("media: could not list the players ({failure}); nothing is resumed");
                return;
            }
        };
        let total = remembered.len();
        let mut sessions = Vec::new();
        for player in listed {
            let Some(at) = remembered.iter().position(|app| *app == player.app) else {
                continue;
            };
            remembered.swap_remove(at);
            if player.playback == Playback::Paused {
                sessions.push(player.session);
            }
        }
        let gone = remembered.len();
        let paused = sessions.len();
        let moved_on = total - gone - paused;
        let tally = Tally::of(&self.transport.play(sessions));
        log::info!("media: {total} to resume; {gone} gone, {moved_on} no longer paused; {tally}");
    }
}

/// How the requests of one round came out, for the log.
struct Tally {
    done: usize,
    declined: usize,
    timed_out: usize,
    errors: Vec<i32>,
}

impl Tally {
    fn of(outcomes: &[Outcome]) -> Self {
        let mut tally = Self {
            done: 0,
            declined: 0,
            timed_out: 0,
            errors: Vec::new(),
        };
        for outcome in outcomes {
            match outcome {
                Ok(true) => tally.done += 1,
                Ok(false) => tally.declined += 1,
                Err(Failure::TimedOut) => tally.timed_out += 1,
                Err(Failure::Os(code)) => tally.errors.push(*code),
            }
        }
        tally
    }
}

impl fmt::Display for Tally {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "{} done, {} declined, {} timed out, {} failed",
            self.done,
            self.declined,
            self.timed_out,
            self.errors.len()
        )?;
        for code in &self.errors {
            write!(f, " ({code:#010x})")?;
        }
        Ok(())
    }
}

#[derive(Clone, Copy)]
enum Command {
    Pause,
    Resume,
}

/// Handles the requests in the order they came, until the sender is gone. A pause waits `settle`
/// first, and a resume that comes in the meantime cancels it: the recording was dropped before
/// anything was paused, so there is nothing to put back. A resume that comes during a pause that
/// has begun waits for it to end, and then undoes it.
fn run<T: Transport>(held: &mut Held<T>, settle: Duration, inbox: &Receiver<Command>) {
    let mut pausing = false;
    loop {
        let next = if pausing {
            inbox.recv_timeout(settle)
        } else {
            inbox.recv().map_err(|_| RecvTimeoutError::Disconnected)
        };
        match next {
            Ok(Command::Pause) => pausing = true,
            Ok(Command::Resume) => {
                if std::mem::take(&mut pausing) {
                    log::debug!("media: the recording ended before the pause began");
                }
                held.resume();
            }
            Err(RecvTimeoutError::Timeout) => {
                pausing = false;
                held.pause();
            }
            Err(RecvTimeoutError::Disconnected) => return,
        }
    }
}

/// A thread that carries out pauses and resumes, one after the other. Dropping it ends the thread
/// once the requests already sent have been handled.
pub struct Worker {
    commands: Option<Sender<Command>>,
    thread: Option<JoinHandle<()>>,
}

impl Worker {
    /// Starts the thread. `make` builds the [`Transport`] on it, because the system's objects must
    /// live and die on the thread that uses them; if it returns `None` the worker does nothing.
    pub fn start<T, F>(make: F, settle: Duration) -> Self
    where
        T: Transport,
        F: FnOnce() -> Option<T> + Send + 'static,
    {
        let (commands, inbox) = mpsc::channel();
        let thread = thread::Builder::new()
            .name("raphael-media".to_owned())
            .spawn(move || {
                let Some(transport) = make() else {
                    log::warn!("media: the system's media controls are not available");
                    return;
                };
                run(&mut Held::new(transport), settle, &inbox);
            });
        match thread {
            Ok(thread) => Self {
                commands: Some(commands),
                thread: Some(thread),
            },
            Err(err) => {
                log::error!("media: could not start its thread: {err}");
                Self {
                    commands: None,
                    thread: None,
                }
            }
        }
    }

    fn send(&self, command: Command) {
        if let Some(commands) = &self.commands
            && commands.send(command).is_err()
        {
            log::debug!("media: the request is dropped: the thread has ended");
        }
    }
}

impl MediaPauser for Worker {
    fn pause(&self) {
        self.send(Command::Pause);
    }

    fn resume(&self) {
        self.send(Command::Resume);
    }
}

impl Drop for Worker {
    fn drop(&mut self) {
        self.commands = None;
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    /// The players of a pretend system, and what was asked of them.
    #[derive(Default)]
    struct System {
        /// Application and state of each player, in the order the system lists them.
        players: Vec<(String, Playback)>,
        /// Every request, as "pause app" or "play app".
        calls: Vec<String>,
        /// The applications whose players answer with this instead of simply doing what they
        /// are asked. A slow player (`TimedOut`) does it all the same, late.
        answers: Vec<(String, Outcome)>,
        listing_fails: bool,
    }

    /// Says that a pause has begun, then waits to be let go.
    type Stuck = (Sender<()>, Receiver<()>);

    /// A [`Transport`] over a [`System`] that the test can change meanwhile, as a person does.
    #[derive(Clone, Default)]
    struct Fake {
        system: Arc<Mutex<System>>,
        /// One message per round of requests, for a test that waits for the worker's thread.
        pings: Arc<Mutex<Option<Sender<()>>>>,
        /// A pause waits here until the test lets it go: a player that is slow to answer.
        stuck: Arc<Mutex<Option<Stuck>>>,
    }

    impl Fake {
        fn with(players: &[(&str, Playback)]) -> Self {
            let fake = Self::default();
            fake.system.lock().unwrap().players = players
                .iter()
                .map(|(app, playback)| ((*app).to_owned(), *playback))
                .collect();
            fake
        }

        /// The person pressed play, pause or stop in `app`.
        fn set(&self, app: &str, playback: Playback) {
            let mut system = self.system.lock().unwrap();
            for player in system.players.iter_mut().filter(|player| player.0 == app) {
                player.1 = playback;
            }
        }

        /// The person closed `app`.
        fn close(&self, app: &str) {
            self.system
                .lock()
                .unwrap()
                .players
                .retain(|player| player.0 != app);
        }

        /// The person opened `app`, and it plays.
        fn open(&self, app: &str) {
            self.system
                .lock()
                .unwrap()
                .players
                .push((app.to_owned(), Playing));
        }

        fn answer(&self, app: &str, outcome: Outcome) {
            self.system
                .lock()
                .unwrap()
                .answers
                .push((app.to_owned(), outcome));
        }

        fn calls(&self) -> Vec<String> {
            self.system.lock().unwrap().calls.clone()
        }

        fn playback(&self, app: &str) -> Playback {
            let system = self.system.lock().unwrap();
            system
                .players
                .iter()
                .find(|player| player.0 == app)
                .unwrap()
                .1
        }

        /// Reports every round of requests on the returned receiver.
        fn pings(&self) -> Receiver<()> {
            let (ping, pings) = mpsc::channel();
            *self.pings.lock().unwrap() = Some(ping);
            pings
        }

        /// Makes the next pause wait until the returned sender lets it go. The receiver says that
        /// the pause has begun.
        fn stick(&self) -> (Receiver<()>, Sender<()>) {
            let (entered, began) = mpsc::channel();
            let (release, released) = mpsc::channel();
            *self.stuck.lock().unwrap() = Some((entered, released));
            (began, release)
        }

        /// Asks each player in `apps` to move to `to`, as the real ones do unless they answer
        /// otherwise.
        fn request(&mut self, verb: &str, to: Playback, apps: Vec<String>) -> Vec<Outcome> {
            if verb == "pause"
                && let Some((entered, released)) = self.stuck.lock().unwrap().take()
            {
                entered.send(()).unwrap();
                released.recv_timeout(Duration::from_secs(10)).unwrap();
            }
            let mut system = self.system.lock().unwrap();
            let mut outcomes = Vec::new();
            for app in apps {
                system.calls.push(format!("{verb} {app}"));
                let scripted = system
                    .answers
                    .iter()
                    .find(|(name, _)| *name == app)
                    .map(|(_, outcome)| *outcome);
                let outcome = scripted.unwrap_or(Ok(true));
                if matches!(outcome, Ok(true) | Err(Failure::TimedOut)) {
                    for player in system.players.iter_mut().filter(|player| player.0 == app) {
                        player.1 = to;
                    }
                }
                outcomes.push(outcome);
            }
            drop(system);
            if let Some(pings) = &*self.pings.lock().unwrap() {
                let _ = pings.send(());
            }
            outcomes
        }
    }

    impl Transport for Fake {
        type Session = String;

        fn list(&mut self) -> Result<Vec<Listed<String>>, Failure> {
            let system = self.system.lock().unwrap();
            if system.listing_fails {
                return Err(Failure::Os(-2_147_023_170));
            }
            Ok(system
                .players
                .iter()
                .map(|(app, playback)| Listed {
                    app: app.clone(),
                    playback: *playback,
                    session: app.clone(),
                })
                .collect())
        }

        fn pause(&mut self, sessions: Vec<String>) -> Vec<Outcome> {
            self.request("pause", Paused, sessions)
        }

        fn play(&mut self, sessions: Vec<String>) -> Vec<Outcome> {
            self.request("play", Playing, sessions)
        }
    }

    use Playback::{Other, Paused, Playing};

    fn held(fake: &Fake) -> Held<Fake> {
        Held::new(fake.clone())
    }

    // ---------- what is paused, and what is resumed ----------

    #[test]
    fn only_what_plays_is_paused_and_it_is_played_again() {
        let fake = Fake::with(&[("music", Playing), ("video", Playing)]);
        let mut held = held(&fake);
        held.pause();
        assert_eq!(fake.calls(), ["pause music", "pause video"]);
        held.resume();
        assert_eq!(
            fake.calls(),
            ["pause music", "pause video", "play music", "play video"]
        );
        assert_eq!(fake.playback("music"), Playing);
        assert_eq!(fake.playback("video"), Playing);
    }

    #[test]
    fn what_was_not_playing_is_never_touched() {
        // One the person had paused, one that ended, and one that plays.
        let fake = Fake::with(&[("paused", Paused), ("ended", Other), ("music", Playing)]);
        let mut held = held(&fake);
        held.pause();
        held.resume();
        assert_eq!(fake.calls(), ["pause music", "play music"]);
        assert_eq!(fake.playback("paused"), Paused);
        assert_eq!(fake.playback("ended"), Other);
    }

    #[test]
    fn a_resume_plays_only_what_the_pause_paused() {
        let fake = Fake::with(&[("music", Playing), ("podcast", Paused)]);
        let mut held = held(&fake);
        held.pause();
        // The person starts another player while talking, and pauses it again: it is not
        // Raphael's to play.
        fake.open("video");
        fake.set("video", Paused);
        held.resume();
        assert_eq!(fake.calls(), ["pause music", "play music"]);
        assert_eq!(fake.playback("podcast"), Paused);
        assert_eq!(fake.playback("video"), Paused);
    }

    #[test]
    fn with_nothing_playing_nothing_is_asked_and_nothing_is_resumed() {
        let fake = Fake::with(&[("paused", Paused)]);
        let mut held = held(&fake);
        held.pause();
        held.resume();
        assert!(fake.calls().is_empty());
    }

    #[test]
    fn what_the_person_played_themselves_meanwhile_is_left_alone() {
        let fake = Fake::with(&[("music", Playing)]);
        let mut held = held(&fake);
        held.pause();
        fake.set("music", Playing);
        held.resume();
        assert_eq!(fake.calls(), ["pause music"], "it plays already");
    }

    #[test]
    fn what_the_person_stopped_meanwhile_is_not_started() {
        let fake = Fake::with(&[("music", Playing)]);
        let mut held = held(&fake);
        held.pause();
        fake.set("music", Other);
        held.resume();
        assert_eq!(fake.calls(), ["pause music"]);
        assert_eq!(fake.playback("music"), Other);
    }

    #[test]
    fn a_player_that_was_closed_meanwhile_is_forgotten() {
        let fake = Fake::with(&[("music", Playing), ("video", Playing)]);
        let mut held = held(&fake);
        held.pause();
        fake.close("music");
        held.resume();
        assert_eq!(fake.calls(), ["pause music", "pause video", "play video"]);
    }

    #[test]
    fn a_resume_forgets_what_it_resumed() {
        let fake = Fake::with(&[("music", Playing)]);
        let mut held = held(&fake);
        held.pause();
        held.resume();
        // The person pauses it again by hand; a second resume must not undo that.
        fake.set("music", Paused);
        held.resume();
        assert_eq!(fake.calls(), ["pause music", "play music"]);
    }

    #[test]
    fn each_recording_pauses_what_plays_at_its_own_start() {
        let fake = Fake::with(&[("music", Playing)]);
        let mut held = held(&fake);
        held.pause();
        held.resume();
        fake.open("video");
        held.pause();
        held.resume();
        assert_eq!(
            fake.calls(),
            [
                "pause music",
                "play music",
                "pause music",
                "pause video",
                "play music",
                "play video"
            ]
        );
    }

    #[test]
    fn two_pauses_before_a_resume_are_resumed_together() {
        let fake = Fake::with(&[("music", Playing)]);
        let mut held = held(&fake);
        held.pause();
        fake.open("video");
        held.pause();
        held.resume();
        assert_eq!(
            fake.calls(),
            ["pause music", "pause video", "play music", "play video"]
        );
    }

    // ---------- when the system does not cooperate ----------

    #[test]
    fn a_player_that_declined_or_failed_to_pause_is_not_resumed() {
        let fake = Fake::with(&[("a", Playing), ("b", Playing), ("c", Playing)]);
        fake.answer("a", Ok(false));
        fake.answer("b", Err(Failure::Os(-2_147_467_259)));
        let mut held = held(&fake);
        held.pause();
        held.resume();
        // a and b play on; only c was paused, so only c is played.
        assert_eq!(fake.calls(), ["pause a", "pause b", "pause c", "play c"]);
    }

    #[test]
    fn a_player_that_was_slow_to_pause_is_resumed_if_it_paused_in_the_end() {
        let fake = Fake::with(&[("music", Playing)]);
        fake.answer("music", Err(Failure::TimedOut));
        let mut held = held(&fake);
        held.pause();
        assert_eq!(fake.playback("music"), Paused);
        fake.system.lock().unwrap().answers.clear();
        held.resume();
        assert_eq!(fake.calls(), ["pause music", "play music"]);
        assert_eq!(fake.playback("music"), Playing);
    }

    #[test]
    fn a_slow_player_that_never_paused_is_not_played() {
        let fake = Fake::with(&[("music", Playing)]);
        let mut held = held(&fake);
        held.paused.push("music".to_owned());
        // It was slow, and it is still playing: a play would only be a second command.
        held.resume();
        assert!(fake.calls().is_empty());
    }

    #[test]
    fn a_list_that_cannot_be_read_pauses_nothing() {
        let fake = Fake::with(&[("music", Playing)]);
        fake.system.lock().unwrap().listing_fails = true;
        let mut held = held(&fake);
        held.pause();
        fake.system.lock().unwrap().listing_fails = false;
        held.resume();
        assert!(fake.calls().is_empty());
    }

    #[test]
    fn a_list_that_cannot_be_read_at_the_end_forgets_the_pause() {
        let fake = Fake::with(&[("music", Playing)]);
        let mut held = held(&fake);
        held.pause();
        fake.system.lock().unwrap().listing_fails = true;
        held.resume();
        fake.system.lock().unwrap().listing_fails = false;
        // The person pauses it by hand later; a later resume must not undo that.
        held.resume();
        assert_eq!(fake.calls(), ["pause music"]);
    }

    // ---------- the thread ----------

    #[test]
    fn a_pause_that_is_taken_back_at_once_pauses_nothing() {
        // Right Alt+V: the dictation that Right Alt started is dropped by the V.
        let fake = Fake::with(&[("music", Playing)]);
        let (outbox, inbox) = mpsc::channel();
        outbox.send(Command::Pause).unwrap();
        outbox.send(Command::Resume).unwrap();
        drop(outbox);
        run(&mut held(&fake), Duration::from_secs(60), &inbox);
        assert!(fake.calls().is_empty());
        assert_eq!(fake.playback("music"), Playing);
    }

    #[test]
    fn the_worker_pauses_after_the_settle_time_and_resumes_on_request() {
        let fake = Fake::with(&[("music", Playing)]);
        let pings = fake.pings();
        let transport = fake.clone();
        let worker = Worker::start(move || Some(transport), Duration::from_millis(20));
        worker.pause();
        pings.recv_timeout(Duration::from_secs(10)).unwrap();
        assert_eq!(fake.playback("music"), Paused);
        worker.resume();
        drop(worker);
        assert_eq!(fake.calls(), ["pause music", "play music"]);
        assert_eq!(fake.playback("music"), Playing);
    }

    #[test]
    fn the_worker_does_not_pause_for_a_recording_that_ends_within_the_settle_time() {
        let fake = Fake::with(&[("music", Playing)]);
        let transport = fake.clone();
        let worker = Worker::start(move || Some(transport), Duration::from_secs(60));
        worker.pause();
        worker.resume();
        // Dropping the worker waits for the thread to handle both.
        drop(worker);
        assert!(fake.calls().is_empty());
        assert_eq!(fake.playback("music"), Playing);
    }

    #[test]
    fn a_resume_that_comes_while_a_pause_is_under_way_waits_for_it_and_undoes_it() {
        let fake = Fake::with(&[("music", Playing)]);
        let (began, release) = fake.stick();
        let transport = fake.clone();
        let worker = Worker::start(move || Some(transport), Duration::ZERO);
        worker.pause();
        began.recv_timeout(Duration::from_secs(10)).unwrap();
        // The recording is over while the player is still answering the pause.
        worker.resume();
        assert!(fake.calls().is_empty(), "nothing has been resumed yet");
        release.send(()).unwrap();
        drop(worker);
        assert_eq!(fake.calls(), ["pause music", "play music"]);
        assert_eq!(fake.playback("music"), Playing, "nothing stays paused");
    }

    #[test]
    fn a_worker_without_media_controls_does_nothing_and_does_not_fail() {
        let worker = Worker::start(|| None::<Fake>, SETTLE);
        worker.pause();
        worker.resume();
        drop(worker);
    }

    // ---------- the controller's side ----------

    #[derive(Default)]
    struct Calls(Mutex<Vec<&'static str>>);

    impl MediaPauser for Calls {
        fn pause(&self) {
            self.0.lock().unwrap().push("pause");
        }
        fn resume(&self) {
            self.0.lock().unwrap().push("resume");
        }
    }

    impl Calls {
        fn seen(&self) -> Vec<&'static str> {
            self.0.lock().unwrap().clone()
        }
    }

    #[test]
    fn a_hold_pairs_each_pause_with_one_resume() {
        let calls = Arc::new(Calls::default());
        let mut hold = MediaHold::new(calls.clone(), PauseMediaCell::default());
        hold.begin();
        hold.begin();
        hold.end();
        hold.end();
        assert_eq!(calls.seen(), ["pause", "resume"]);
        hold.begin();
        hold.end();
        assert_eq!(calls.seen(), ["pause", "resume", "pause", "resume"]);
    }

    #[test]
    fn a_hold_with_the_setting_off_does_nothing() {
        let calls = Arc::new(Calls::default());
        let enabled = PauseMediaCell::default();
        enabled.set(false);
        let mut hold = MediaHold::new(calls.clone(), enabled);
        hold.begin();
        hold.end();
        assert!(calls.seen().is_empty());
    }

    #[test]
    fn a_setting_changed_during_a_recording_applies_from_the_next_one() {
        let calls = Arc::new(Calls::default());
        let enabled = PauseMediaCell::default();
        let mut hold = MediaHold::new(calls.clone(), enabled.clone());
        hold.begin();
        enabled.set(false);
        hold.end();
        assert_eq!(
            calls.seen(),
            ["pause", "resume"],
            "what was paused is put back"
        );
        hold.begin();
        hold.end();
        assert_eq!(calls.seen(), ["pause", "resume"]);
    }

    #[test]
    fn a_hold_without_media_does_nothing() {
        let mut hold = MediaHold::default();
        hold.begin();
        hold.end();
    }

    #[test]
    fn the_setting_starts_on_like_the_device_settings() {
        assert!(PauseMediaCell::default().get());
        assert_eq!(
            PauseMediaCell::default().get(),
            crate::device_settings::DeviceSettings::default().pause_media
        );
    }

    #[test]
    fn the_log_never_names_a_player() {
        // The log is global, so the source is read: no log call mentions an application.
        let source = include_str!("mod.rs").replace("\r\n", "\n");
        let production = source.split("#[cfg(test)]").next().unwrap();
        let mut calls = 0;
        for call in production.split("log::").skip(1) {
            let call = call.split(");").next().unwrap();
            calls += 1;
            assert!(!call.contains("app"), "log::{call}");
        }
        assert!(calls > 5, "the scan found only {calls} log calls");
    }
}
