//! Tells holding the dictation key from tapping it.
//!
//! One key does both jobs and nobody has to choose a mode:
//!
//! - **Hold**: press starts recording, release stops it.
//! - **Tap**: a press shorter than [`TAP_MAX_MS`] starts recording and leaves it running. The next
//!   press stops it.
//!
//! The controller feeds the key's down and up events (with a millisecond clock of its own) to
//! [`HoldTap`] and does what the returned [`Action`] says. The type has no clock and no threads,
//! so it is plain to test.

/// A press shorter than this is a tap. A press this long or longer is a hold.
pub const TAP_MAX_MS: u64 = 350;

/// What the controller should do after a key event.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Action {
    StartRecording,
    StopRecording,
    /// Nothing to do: the event only moved the detector along.
    None,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
enum State {
    /// Not recording, key up.
    #[default]
    Idle,
    /// Key down; this press started the recording at `since_ms`. Whether it was a hold or a tap
    /// is decided when the key comes up.
    Pressed { since_ms: u64 },
    /// A tap started the recording, key up. The next press stops it.
    Latched,
    /// Key down; this press stopped a latched recording. Its release changes nothing.
    Stopping,
}

/// The hold-or-tap detector for the dictation key.
#[derive(Debug, Clone, Copy, Default)]
pub struct HoldTap {
    state: State,
}

impl HoldTap {
    pub fn new() -> Self {
        Self::default()
    }

    /// The dictation key went down at `now_ms`.
    pub fn on_down(&mut self, now_ms: u64) -> Action {
        match self.state {
            State::Idle => {
                self.state = State::Pressed { since_ms: now_ms };
                Action::StartRecording
            }
            State::Latched => {
                self.state = State::Stopping;
                Action::StopRecording
            }
            // A second down without an up in between is key auto-repeat. The press keeps its
            // first timestamp.
            State::Pressed { .. } | State::Stopping => Action::None,
        }
    }

    /// The dictation key came up at `now_ms`.
    pub fn on_up(&mut self, now_ms: u64) -> Action {
        match self.state {
            State::Pressed { since_ms } => {
                if now_ms.saturating_sub(since_ms) >= TAP_MAX_MS {
                    self.state = State::Idle;
                    Action::StopRecording
                } else {
                    self.state = State::Latched;
                    Action::None
                }
            }
            State::Stopping => {
                self.state = State::Idle;
                Action::None
            }
            // An up we have no down for (for example the key was held when the app started, or
            // the recording was cancelled) is ignored.
            State::Idle | State::Latched => Action::None,
        }
    }

    /// Forget everything and wait for a fresh press. The controller calls this when a recording
    /// ends for any reason other than the key (Esc, a core restart, Right Alt+V).
    pub fn reset(&mut self) {
        self.state = State::Idle;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hold_starts_then_stops_on_release() {
        let mut h = HoldTap::new();
        assert_eq!(h.on_down(0), Action::StartRecording);
        assert_eq!(h.on_up(800), Action::StopRecording);
    }

    #[test]
    fn tap_toggles() {
        let mut h = HoldTap::new();
        assert_eq!(h.on_down(0), Action::StartRecording);
        assert_eq!(h.on_up(120), Action::None);
        assert_eq!(h.on_down(3000), Action::StopRecording);
        assert_eq!(h.on_up(3100), Action::None);
        assert_eq!(h.on_down(5000), Action::StartRecording);
    }

    #[test]
    fn reset_returns_to_idle() {
        let mut h = HoldTap::new();
        h.on_down(0);
        h.reset();
        assert_eq!(h.on_up(50), Action::None);
        assert_eq!(h.on_down(100), Action::StartRecording);
    }

    #[test]
    fn a_press_of_exactly_tap_max_is_a_hold() {
        let mut h = HoldTap::new();
        h.on_down(1000);
        assert_eq!(
            h.on_up(1000 + TAP_MAX_MS - 1),
            Action::None,
            "one ms short is a tap"
        );

        let mut h = HoldTap::new();
        h.on_down(1000);
        assert_eq!(h.on_up(1000 + TAP_MAX_MS), Action::StopRecording);
    }

    #[test]
    fn a_hold_leaves_the_detector_idle() {
        let mut h = HoldTap::new();
        h.on_down(0);
        assert_eq!(h.on_up(900), Action::StopRecording);
        // Not latched: a second long press is a new hold, not a "stop".
        assert_eq!(h.on_down(2000), Action::StartRecording);
        assert_eq!(h.on_up(3000), Action::StopRecording);
    }

    #[test]
    fn the_press_that_stops_a_tap_recording_can_be_long() {
        let mut h = HoldTap::new();
        h.on_down(0);
        h.on_up(100);
        assert_eq!(h.on_down(5000), Action::StopRecording);
        assert_eq!(
            h.on_up(9000),
            Action::None,
            "releasing the stopping press does nothing"
        );
        assert_eq!(h.on_down(9500), Action::StartRecording);
    }

    #[test]
    fn auto_repeat_keeps_the_first_timestamp() {
        let mut h = HoldTap::new();
        assert_eq!(h.on_down(0), Action::StartRecording);
        assert_eq!(h.on_down(100), Action::None);
        assert_eq!(h.on_down(200), Action::None);
        // Measured from 0, not from the last repeat at 200.
        assert_eq!(h.on_up(TAP_MAX_MS), Action::StopRecording);
    }

    #[test]
    fn auto_repeat_while_stopping_does_not_start_again() {
        let mut h = HoldTap::new();
        h.on_down(0);
        h.on_up(50);
        assert_eq!(h.on_down(1000), Action::StopRecording);
        assert_eq!(h.on_down(1100), Action::None);
        assert_eq!(h.on_up(1200), Action::None);
    }

    #[test]
    fn an_up_without_a_down_is_ignored() {
        let mut h = HoldTap::new();
        assert_eq!(h.on_up(10), Action::None);
        // Still idle afterwards.
        assert_eq!(h.on_down(20), Action::StartRecording);
    }

    #[test]
    fn an_extra_up_while_latched_is_ignored() {
        let mut h = HoldTap::new();
        h.on_down(0);
        h.on_up(100);
        assert_eq!(h.on_up(200), Action::None);
        assert_eq!(h.on_down(300), Action::StopRecording, "still latched");
    }

    #[test]
    fn reset_clears_a_latched_recording() {
        let mut h = HoldTap::new();
        h.on_down(0);
        h.on_up(100);
        h.reset();
        assert_eq!(
            h.on_down(200),
            Action::StartRecording,
            "a fresh start, not a stop"
        );
    }

    #[test]
    fn a_clock_that_goes_backwards_counts_as_a_tap() {
        let mut h = HoldTap::new();
        h.on_down(1000);
        assert_eq!(h.on_up(500), Action::None);
    }
}
