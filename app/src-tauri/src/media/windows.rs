//! The Windows media controls as a [`Transport`]: `Windows.Media.Control`, the system's list of
//! players that publish what they play (browsers, Spotify, the Media Player, VLC and the like).
//!
//! Everything here runs on the worker's thread, which has the Windows Runtime to itself
//! ([`Apartment`]). A request to a player is asynchronous; it is waited for with a time limit, so
//! that a player that hangs holds nothing up for long.

use std::sync::mpsc::{self, RecvTimeoutError};
use std::time::{Duration, Instant};

use windows::Media::Control::{
    GlobalSystemMediaTransportControlsSession as Session,
    GlobalSystemMediaTransportControlsSessionManager as Manager,
    GlobalSystemMediaTransportControlsSessionPlaybackStatus as Status,
};
use windows::Win32::Foundation::E_FAIL;
use windows::Win32::System::WinRT::{RO_INIT_MULTITHREADED, RoInitialize, RoUninitialize};
use windows::core::RuntimeType;
use windows_future::IAsyncOperation;

use super::{Failure, Listed, MediaPauser, Outcome, Playback, SETTLE, Transport, Worker};

/// How long the system's list of players may take to be handed over. It is asked for once, and
/// the first time can be slow.
const MANAGER_TIMEOUT: Duration = Duration::from_secs(2);
/// How long all the players together may take to answer a pause or a play.
const PLAYERS_TIMEOUT: Duration = Duration::from_secs(1);

/// Pauses and resumes the media of this computer. See [`Worker`].
pub struct WindowsMedia(Worker);

impl WindowsMedia {
    /// Starts the thread that talks to the media controls. Nothing is asked of them until the
    /// first recording.
    pub fn start() -> Self {
        Self(Worker::start(Winrt::new, SETTLE))
    }
}

impl MediaPauser for WindowsMedia {
    fn pause(&self) {
        self.0.pause();
    }

    fn resume(&self) {
        self.0.resume();
    }
}

/// The thread's membership in the Windows Runtime, ended when it is dropped.
struct Apartment;

impl Apartment {
    fn enter() -> windows::core::Result<Self> {
        // SAFETY: balanced by the `RoUninitialize` in `drop`, on this same thread.
        unsafe { RoInitialize(RO_INIT_MULTITHREADED) }?;
        Ok(Self)
    }
}

impl Drop for Apartment {
    fn drop(&mut self) {
        // SAFETY: balanced with the successful `RoInitialize` of `enter`, on the same thread.
        unsafe { RoUninitialize() };
    }
}

struct Winrt {
    /// The system's list of players, once it has been asked for. It is dropped when the system
    /// stops answering for it (the service restarted, say), and asked for again.
    manager: Option<Manager>,
    /// Last, so that it ends after the objects above.
    _apartment: Apartment,
}

impl Winrt {
    fn new() -> Option<Self> {
        match Apartment::enter() {
            Ok(apartment) => Some(Self {
                manager: None,
                _apartment: apartment,
            }),
            Err(err) => {
                log::warn!(
                    "media: could not start the Windows Runtime ({})",
                    failure(&err)
                );
                None
            }
        }
    }

    fn manager(&mut self) -> Result<Manager, Failure> {
        if let Some(manager) = &self.manager {
            return Ok(manager.clone());
        }
        let deadline = Instant::now() + MANAGER_TIMEOUT;
        let manager = Pending::start(Manager::RequestAsync()).wait(deadline)?;
        self.manager = Some(manager.clone());
        Ok(manager)
    }
}

impl Transport for Winrt {
    type Session = Session;

    fn list(&mut self) -> Result<Vec<Listed<Session>>, Failure> {
        let manager = self.manager()?;
        let sessions = manager.GetSessions().map_err(|err| {
            self.manager = None;
            failure(&err)
        })?;
        let size = sessions.Size().map_err(|err| failure(&err))?;
        let mut listed = Vec::new();
        for at in 0..size {
            // A player that closes while the list is read has nothing to offer: it is left out.
            let Ok(session) = sessions.GetAt(at) else {
                continue;
            };
            let Ok(app) = session.SourceAppUserModelId() else {
                continue;
            };
            let status = session
                .GetPlaybackInfo()
                .and_then(|info| info.PlaybackStatus());
            let playback = match status {
                Ok(status) if status == Status::Playing => Playback::Playing,
                Ok(status) if status == Status::Paused => Playback::Paused,
                _ => Playback::Other,
            };
            listed.push(Listed {
                app: app.to_string_lossy(),
                playback,
                session,
            });
        }
        Ok(listed)
    }

    fn pause(&mut self, sessions: Vec<Session>) -> Vec<Outcome> {
        answers(sessions.iter().map(Session::TryPauseAsync))
    }

    fn play(&mut self, sessions: Vec<Session>) -> Vec<Outcome> {
        answers(sessions.iter().map(Session::TryPlayAsync))
    }
}

/// The answers to requests that were all made first, so that a player that is slow does not hold
/// up the others, each waited for until the time for all of them is up.
fn answers(
    requests: impl Iterator<Item = windows::core::Result<IAsyncOperation<bool>>>,
) -> Vec<Outcome> {
    let deadline = Instant::now() + PLAYERS_TIMEOUT;
    let pending: Vec<_> = requests.map(Pending::start).collect();
    pending
        .into_iter()
        .map(|request| request.wait(deadline))
        .collect()
}

/// A request to the system that has been made and not answered.
struct Pending<T>(Result<mpsc::Receiver<windows::core::Result<T>>, Failure>);

impl<T: RuntimeType + Send + 'static> Pending<T> {
    fn start(request: windows::core::Result<IAsyncOperation<T>>) -> Self {
        let operation = match request {
            Ok(operation) => operation,
            Err(err) => return Self(Err(failure(&err))),
        };
        let (done, answer) = mpsc::channel();
        // The answer comes from a thread of the system's pool. If nobody waits for it any more,
        // it is dropped there.
        let started = operation.when(move |result| {
            let _ = done.send(result);
        });
        match started {
            Ok(()) => Self(Ok(answer)),
            Err(err) => Self(Err(failure(&err))),
        }
    }

    fn wait(self, deadline: Instant) -> Result<T, Failure> {
        let answer = self.0?;
        match answer.recv_timeout(deadline.saturating_duration_since(Instant::now())) {
            Ok(result) => result.map_err(|err| failure(&err)),
            Err(RecvTimeoutError::Timeout) => Err(Failure::TimedOut),
            Err(RecvTimeoutError::Disconnected) => Err(Failure::Os(E_FAIL.0)),
        }
    }
}

fn failure(err: &windows::core::Error) -> Failure {
    Failure::Os(err.code().0)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Reads the real list of players of this computer and counts them. It asks nothing of them:
    /// it never pauses or plays anything. Run by hand (`cargo test -- --ignored media`) to see
    /// that the Windows Runtime can be reached from the worker's thread.
    #[test]
    #[ignore = "reads the media players of the computer it runs on"]
    fn the_real_list_can_be_read() {
        let mut winrt = Winrt::new().expect("the Windows Runtime starts");
        let listed = winrt.list().expect("the list of players is handed over");
        let playing = listed
            .iter()
            .filter(|player| player.playback == Playback::Playing)
            .count();
        let paused = listed
            .iter()
            .filter(|player| player.playback == Playback::Paused)
            .count();
        println!(
            "{} players listed, {playing} playing, {paused} paused",
            listed.len()
        );
    }
}
