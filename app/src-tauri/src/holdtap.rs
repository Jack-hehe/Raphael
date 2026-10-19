//! What the dictation key's presses and releases mean, for the mode the person chose.
//!
//! There are two modes ([`DictationMode`], set in the settings page), and the key means the same
//! thing in a mode however long it is held:
//!
//! - **Toggle** (the default): a press starts the recording, its release does nothing; the next
//!   press stops it, and that press's release does nothing either.
//! - **Hold**: a press starts the recording, the release stops it.
//!
//! The controller feeds the key's down and up events to [`HoldTap`] and does what the returned
//! [`Action`] says. The type has no clock and no threads, so it is plain to test. The agent key
//! (the one that asks Raphael something) follows the same two modes, with a detector of its own.
//!
//! The mode is read when a dictation begins (the press that leaves the idle state) and kept until
//! that dictation is over, so changing the setting during a dictation takes effect from the next
//! one.

use crate::device_settings::DictationMode;

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
    /// Hold mode: key down, recording. The release stops it.
    Held,
    /// Toggle mode: key down; this press started the recording. Its release changes nothing.
    Starting,
    /// Toggle mode: recording, key up. The next press stops it.
    Listening,
    /// Toggle mode: key down; this press stopped the recording. Its release changes nothing.
    Stopping,
}

/// The state of the dictation key for the controller.
#[derive(Debug, Clone, Copy, Default)]
pub struct HoldTap {
    state: State,
}

impl HoldTap {
    pub fn new() -> Self {
        Self::default()
    }

    /// The dictation key went down. `mode` is the mode in force now; it decides what this press
    /// means only if it begins a dictation, and a dictation that is under way keeps its own.
    pub fn on_down(&mut self, mode: DictationMode) -> Action {
        match self.state {
            State::Idle => {
                self.state = match mode {
                    DictationMode::Toggle => State::Starting,
                    DictationMode::Hold => State::Held,
                };
                Action::StartRecording
            }
            State::Listening => {
                self.state = State::Stopping;
                Action::StopRecording
            }
            // A second down without an up in between is key auto-repeat.
            State::Held | State::Starting | State::Stopping => Action::None,
        }
    }

    /// The dictation key came up.
    pub fn on_up(&mut self) -> Action {
        match self.state {
            State::Held => {
                self.state = State::Idle;
                Action::StopRecording
            }
            State::Starting => {
                self.state = State::Listening;
                Action::None
            }
            State::Stopping => {
                self.state = State::Idle;
                Action::None
            }
            // An up we have no down for (for example the key was held when the app started, or
            // the recording was cancelled) is ignored.
            State::Idle | State::Listening => Action::None,
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
    use DictationMode::{Hold, Toggle};

    // ---------- toggle: press to start, press again to stop ----------

    #[test]
    fn toggle_press_starts_and_the_next_press_stops() {
        let mut h = HoldTap::new();
        assert_eq!(h.on_down(Toggle), Action::StartRecording);
        assert_eq!(h.on_up(), Action::None, "the release does not stop it");
        assert_eq!(h.on_down(Toggle), Action::StopRecording);
        assert_eq!(
            h.on_up(),
            Action::None,
            "nor does the release of the stopping press"
        );
        // and round again
        assert_eq!(h.on_down(Toggle), Action::StartRecording);
        assert_eq!(h.on_up(), Action::None);
        assert_eq!(h.on_down(Toggle), Action::StopRecording);
    }

    #[test]
    fn toggle_holding_the_key_long_changes_nothing() {
        let mut h = HoldTap::new();
        assert_eq!(h.on_down(Toggle), Action::StartRecording);
        // a long hold: the keyboard repeats the key down many times
        for _ in 0..50 {
            assert_eq!(h.on_down(Toggle), Action::None);
        }
        assert_eq!(
            h.on_up(),
            Action::None,
            "a long press is still just a start"
        );
        assert_eq!(h.on_down(Toggle), Action::StopRecording, "still recording");
    }

    #[test]
    fn toggle_the_stopping_press_can_be_held_long_too() {
        let mut h = HoldTap::new();
        h.on_down(Toggle);
        h.on_up();
        assert_eq!(h.on_down(Toggle), Action::StopRecording);
        for _ in 0..50 {
            assert_eq!(
                h.on_down(Toggle),
                Action::None,
                "auto-repeat does not start again"
            );
        }
        assert_eq!(h.on_up(), Action::None);
        assert_eq!(
            h.on_down(Toggle),
            Action::StartRecording,
            "a fresh dictation"
        );
    }

    #[test]
    fn toggle_auto_repeat_after_the_start_does_not_stop_it() {
        let mut h = HoldTap::new();
        assert_eq!(h.on_down(Toggle), Action::StartRecording);
        assert_eq!(h.on_down(Toggle), Action::None);
        assert_eq!(h.on_down(Toggle), Action::None);
        assert_eq!(h.on_up(), Action::None);
        assert_eq!(h.on_down(Toggle), Action::StopRecording);
    }

    #[test]
    fn toggle_an_up_without_a_down_is_ignored() {
        let mut h = HoldTap::new();
        assert_eq!(h.on_up(), Action::None);
        assert_eq!(h.on_down(Toggle), Action::StartRecording, "still idle");
    }

    #[test]
    fn toggle_an_extra_up_while_listening_is_ignored() {
        let mut h = HoldTap::new();
        h.on_down(Toggle);
        h.on_up();
        assert_eq!(h.on_up(), Action::None);
        assert_eq!(h.on_up(), Action::None);
        assert_eq!(h.on_down(Toggle), Action::StopRecording, "still listening");
    }

    #[test]
    fn toggle_reset_returns_to_idle_from_every_state() {
        // while the starting press is still down
        let mut h = HoldTap::new();
        h.on_down(Toggle);
        h.reset();
        assert_eq!(h.on_up(), Action::None, "the release of a cancelled press");
        assert_eq!(h.on_down(Toggle), Action::StartRecording);

        // while listening: the next press is a start, not a stop
        let mut h = HoldTap::new();
        h.on_down(Toggle);
        h.on_up();
        h.reset();
        assert_eq!(
            h.on_down(Toggle),
            Action::StartRecording,
            "a fresh start, not a stop"
        );

        // while the stopping press is still down
        let mut h = HoldTap::new();
        h.on_down(Toggle);
        h.on_up();
        h.on_down(Toggle);
        h.reset();
        assert_eq!(h.on_up(), Action::None);
        assert_eq!(h.on_down(Toggle), Action::StartRecording);
    }

    // ---------- hold: hold to talk, release to stop ----------

    #[test]
    fn hold_press_starts_and_release_stops() {
        let mut h = HoldTap::new();
        assert_eq!(h.on_down(Hold), Action::StartRecording);
        assert_eq!(h.on_up(), Action::StopRecording);
    }

    #[test]
    fn hold_a_quick_press_stops_too_however_short() {
        // The old detector took a short press for a tap and left the recording running.
        let mut h = HoldTap::new();
        assert_eq!(h.on_down(Hold), Action::StartRecording);
        assert_eq!(h.on_up(), Action::StopRecording);
        // and the detector is idle again, not latched: the next press is a new start
        assert_eq!(h.on_down(Hold), Action::StartRecording);
        assert_eq!(h.on_up(), Action::StopRecording);
    }

    #[test]
    fn hold_auto_repeat_is_ignored() {
        let mut h = HoldTap::new();
        assert_eq!(h.on_down(Hold), Action::StartRecording);
        for _ in 0..50 {
            assert_eq!(h.on_down(Hold), Action::None);
        }
        assert_eq!(h.on_up(), Action::StopRecording);
        assert_eq!(h.on_up(), Action::None, "only one stop per press");
    }

    #[test]
    fn hold_an_up_without_a_down_is_ignored() {
        let mut h = HoldTap::new();
        assert_eq!(h.on_up(), Action::None);
        assert_eq!(h.on_down(Hold), Action::StartRecording, "still idle");
    }

    #[test]
    fn hold_reset_forgets_the_press() {
        let mut h = HoldTap::new();
        h.on_down(Hold);
        h.reset();
        assert_eq!(
            h.on_up(),
            Action::None,
            "the release after Esc does nothing"
        );
        assert_eq!(h.on_down(Hold), Action::StartRecording);
    }

    // ---------- the mode is fixed for the dictation under way ----------

    #[test]
    fn a_toggle_dictation_keeps_toggle_when_the_setting_changes_meanwhile() {
        let mut h = HoldTap::new();
        assert_eq!(h.on_down(Toggle), Action::StartRecording);
        // the setting is now Hold; the press that is down and the ones after it go by Toggle
        assert_eq!(h.on_down(Hold), Action::None, "auto-repeat");
        assert_eq!(
            h.on_up(),
            Action::None,
            "the release does not stop a toggle dictation"
        );
        assert_eq!(
            h.on_down(Hold),
            Action::StopRecording,
            "the next press stops it"
        );
        assert_eq!(h.on_up(), Action::None);
        // the next dictation uses the new mode
        assert_eq!(h.on_down(Hold), Action::StartRecording);
        assert_eq!(h.on_up(), Action::StopRecording);
    }

    #[test]
    fn a_hold_dictation_keeps_hold_when_the_setting_changes_meanwhile() {
        let mut h = HoldTap::new();
        assert_eq!(h.on_down(Hold), Action::StartRecording);
        // the setting is now Toggle: a repeat is still a repeat, and the release still stops
        assert_eq!(h.on_down(Toggle), Action::None);
        assert_eq!(h.on_up(), Action::StopRecording);
        // the next dictation uses the new mode
        assert_eq!(h.on_down(Toggle), Action::StartRecording);
        assert_eq!(h.on_up(), Action::None);
        assert_eq!(h.on_down(Toggle), Action::StopRecording);
    }
}
