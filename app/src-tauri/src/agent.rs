//! Raphael's answers as the controller shows them: what the core's `agent.ask` and `agent.confirm`
//! reply with, and how long an answer stays on screen.
//!
//! Everything here is a pure function of the reply. The words are the core's and are shown as they
//! come; nothing in this file logs them.

use serde_json::Value;

use crate::overlay::{AskPrompt, Tier};

/// How long a question waits for its answer when the core names no time (`timeout_ms`). The core
/// lets a confirmation expire after the same 30 s.
pub const ASK_TIMEOUT_DEFAULT_MS: u64 = 30_000;
/// The longest a question may wait, whatever the core says: Esc is held for as long as one is open.
pub const ASK_TIMEOUT_MAX_MS: u64 = 120_000;

/// The YES to a `T3` question (one that cannot be undone) is armed by a long press on the page
/// (600 ms). The controller does not take one sooner than this after the question was shown, so a
/// page that arms it too early, or by mistake, cannot approve by itself.
pub const T3_ARMS_AFTER_MS: u64 = 600;

/// An answer stays on screen for this long, and for [`SAY_PER_CHAR_MS`] more for every character
/// of its text, but never longer than [`SAY_MAX_MS`].
pub const SAY_BASE_MS: u64 = 1_500;
pub const SAY_PER_CHAR_MS: u64 = 150;
pub const SAY_MAX_MS: u64 = 12_000;

/// What the core answered, ready for the overlay.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Reply {
    /// Raphael's answer, shown as 《prefix。text》.
    Say { prefix: String, text: String },
    /// A question to answer YES or NO before anything is done.
    Ask(AskPrompt),
}

/// A reply the controller cannot show. When it holds a confirmation, `confirm_id` is the id of
/// that confirmation: nobody will ever see it, so it is denied instead of being left to expire.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct Malformed {
    pub confirm_id: Option<String>,
}

/// The reply of `agent.ask`: `{"status": "done", "prefix", "text", ..}` or
/// `{"status": "confirm", "confirm": {"id", "tier", "prefix", "text", "yes", "no",
/// "timeout_ms"}, ..}`.
pub fn parse_reply(reply: &Value) -> Result<Reply, Malformed> {
    match reply.get("status").and_then(Value::as_str) {
        Some("done") => parse_say(reply),
        Some("confirm") => parse_ask(reply.get("confirm")),
        _ => Err(Malformed::default()),
    }
}

/// The reply of `agent.confirm` when the turn may ask no more: an answer, `{"status": "done",
/// "prefix", "text", ..}`. A question in its place is refused (and denied by the controller). A
/// turn asks again only for a run on the desktop, and only so many times
/// (`dictation::MAX_QUESTIONS_IN_A_TURN`); up to then the reply is read with [`parse_reply`].
pub fn parse_answer(reply: &Value) -> Result<Reply, Malformed> {
    match parse_reply(reply)? {
        say @ Reply::Say { .. } => Ok(say),
        Reply::Ask(prompt) => Err(Malformed {
            confirm_id: Some(prompt.id),
        }),
    }
}

fn parse_say(reply: &Value) -> Result<Reply, Malformed> {
    let prefix = reply.get("prefix").and_then(Value::as_str);
    let text = reply.get("text").and_then(Value::as_str);
    match (prefix, text) {
        (Some(prefix), Some(text)) if !prefix.trim().is_empty() => Ok(Reply::Say {
            prefix: prefix.to_owned(),
            text: text.to_owned(),
        }),
        _ => Err(Malformed::default()),
    }
}

fn parse_ask(confirm: Option<&Value>) -> Result<Reply, Malformed> {
    let Some(confirm) = confirm else {
        return Err(Malformed::default());
    };
    let id = confirm
        .get("id")
        .and_then(Value::as_str)
        .filter(|id| !id.is_empty());
    let words = |name: &str| {
        confirm
            .get(name)
            .and_then(Value::as_str)
            .filter(|words| !words.trim().is_empty())
    };
    let (Some(id), Some(prefix), Some(text), Some(yes), Some(no)) = (
        id,
        words("prefix"),
        words("text"),
        words("yes"),
        words("no"),
    ) else {
        return Err(Malformed {
            confirm_id: id.map(str::to_owned),
        });
    };
    Ok(Reply::Ask(AskPrompt {
        id: id.to_owned(),
        tier: Tier::parse(confirm.get("tier").and_then(Value::as_str).unwrap_or("")),
        prefix: prefix.to_owned(),
        text: text.to_owned(),
        yes: yes.to_owned(),
        no: no.to_owned(),
        timeout_ms: ask_timeout_ms(confirm.get("timeout_ms")),
        always: words("always").map(str::to_owned),
    }))
}

/// How long a question waits, in milliseconds: the core's `timeout_ms`, or the default when it
/// names none (or none that makes sense), and never more than [`ASK_TIMEOUT_MAX_MS`].
pub fn ask_timeout_ms(timeout_ms: Option<&Value>) -> u64 {
    let named = timeout_ms.and_then(|value| {
        value.as_u64().or_else(|| {
            value
                .as_f64()
                .filter(|number| number.is_finite() && *number >= 1.0)
                .map(|number| number as u64)
        })
    });
    match named {
        Some(0) | None => ASK_TIMEOUT_DEFAULT_MS,
        Some(named) => named.min(ASK_TIMEOUT_MAX_MS),
    }
}

/// How long Raphael's words stay on screen, in milliseconds: 1.5 s and 0.15 s for every character
/// (a code point, as the page counts them), at most 12 s. The controller keeps the cancel keys for
/// as long as that, so that Esc can still dismiss the answer.
pub fn say_hold_ms(text: &str) -> u64 {
    let chars = u64::try_from(text.chars().count()).unwrap_or(u64::MAX);
    SAY_BASE_MS
        .saturating_add(SAY_PER_CHAR_MS.saturating_mul(chars))
        .min(SAY_MAX_MS)
}

/// Whether the reply of `agent.ask` says that Raphael also wrote down a memory, which the overlay
/// tells after the answer ([`crate::notices::REMEMBERED`]): `"notice": "remembered"`.
pub fn remembered(reply: &Value) -> bool {
    reply.get("notice").and_then(Value::as_str) == Some("remembered")
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn a_reply_says_when_a_memory_was_written_beside_the_answer() {
        let done = json!({"status": "done", "prefix": "答", "text": "好。", "turn_id": 3});
        assert!(!remembered(&done));
        let mut noted = done.clone();
        noted["notice"] = json!("remembered");
        assert!(remembered(&noted));
        for other in [json!("REMEMBERED"), json!(true), json!(null), json!("x")] {
            noted["notice"] = other;
            assert!(!remembered(&noted));
        }
    }

    fn confirm(extra: Value) -> Value {
        let mut confirm = json!({
            "id": "c-1",
            "tier": "T2",
            "prefix": "問",
            "text": "要複製到剪貼簿嗎？",
            "yes": "是",
            "no": "否",
            "timeout_ms": 30_000,
        });
        for (name, value) in extra.as_object().unwrap() {
            if value.is_null() {
                confirm.as_object_mut().unwrap().remove(name);
            } else {
                confirm[name] = value.clone();
            }
        }
        json!({"status": "confirm", "turn_id": 4, "confirm": confirm})
    }

    #[test]
    fn a_done_reply_is_raphaels_answer() {
        let reply = json!({"status": "done", "turn_id": 3, "prefix": "答", "text": "三點開會。"});
        assert_eq!(
            parse_reply(&reply),
            Ok(Reply::Say {
                prefix: "答".into(),
                text: "三點開會。".into()
            })
        );
        // an acknowledgement may have no words besides its prefix
        let reply = json!({"status": "done", "prefix": "了", "text": ""});
        assert_eq!(
            parse_reply(&reply),
            Ok(Reply::Say {
                prefix: "了".into(),
                text: String::new()
            })
        );
    }

    #[test]
    fn a_done_reply_needs_a_prefix_and_a_text() {
        for reply in [
            json!({"status": "done"}),
            json!({"status": "done", "text": "好"}),
            json!({"status": "done", "prefix": "答"}),
            json!({"status": "done", "prefix": "", "text": "好"}),
            json!({"status": "done", "prefix": "  ", "text": "好"}),
            json!({"status": "done", "prefix": 1, "text": "好"}),
            json!({"status": "done", "prefix": "答", "text": null}),
        ] {
            assert_eq!(parse_reply(&reply), Err(Malformed::default()), "{reply}");
        }
    }

    #[test]
    fn a_confirm_reply_is_a_question() {
        assert_eq!(
            parse_reply(&confirm(json!({}))),
            Ok(Reply::Ask(AskPrompt {
                id: "c-1".into(),
                tier: Tier::T2,
                prefix: "問".into(),
                text: "要複製到剪貼簿嗎？".into(),
                yes: "是".into(),
                no: "否".into(),
                timeout_ms: 30_000,
                always: None,
            }))
        );
        let Ok(Reply::Ask(prompt)) = parse_reply(&confirm(json!({"tier": "T3"}))) else {
            panic!("not a question");
        };
        assert_eq!(prompt.tier, Tier::T3);
    }

    #[test]
    fn a_question_about_an_app_has_a_third_answer() {
        let Ok(Reply::Ask(prompt)) = parse_reply(&confirm(json!({"always": "一律允許"})))
        else {
            panic!("not a question");
        };
        assert_eq!(prompt.always.as_deref(), Some("一律允許"));
        for none in [json!(null), json!(""), json!("  "), json!(3)] {
            let Ok(Reply::Ask(prompt)) = parse_reply(&confirm(json!({ "always": none }))) else {
                panic!("not a question");
            };
            assert_eq!(prompt.always, None, "{none}");
        }
    }

    #[test]
    fn a_question_with_an_odd_tier_is_the_careful_one() {
        for tier in [json!("T9"), json!(""), json!(2), json!(null)] {
            let Ok(Reply::Ask(prompt)) = parse_reply(&confirm(json!({"tier": tier}))) else {
                panic!("not a question");
            };
            assert_eq!(prompt.tier, Tier::T3, "{tier}");
        }
    }

    #[test]
    fn a_question_that_cannot_be_shown_still_names_its_id() {
        for missing in ["prefix", "text", "yes", "no"] {
            let reply = confirm(json!({ missing: null }));
            assert_eq!(
                parse_reply(&reply),
                Err(Malformed {
                    confirm_id: Some("c-1".into())
                }),
                "{missing}"
            );
        }
        let reply = confirm(json!({"yes": "  "}));
        assert_eq!(
            parse_reply(&reply),
            Err(Malformed {
                confirm_id: Some("c-1".into())
            })
        );
        // without an id there is nothing to deny
        for id in [json!(null), json!(""), json!(3)] {
            assert_eq!(
                parse_reply(&confirm(json!({"id": id}))),
                Err(Malformed::default()),
                "{id}"
            );
        }
        assert_eq!(
            parse_reply(&json!({"status": "confirm", "turn_id": 1})),
            Err(Malformed::default())
        );
        assert_eq!(
            parse_reply(&json!({"status": "confirm", "confirm": "c-1"})),
            Err(Malformed::default())
        );
    }

    #[test]
    fn other_replies_cannot_be_shown() {
        for reply in [
            json!({}),
            json!(null),
            json!("done"),
            json!({"status": "error", "prefix": "否", "text": "x"}),
            json!({"status": 1}),
        ] {
            assert_eq!(parse_reply(&reply), Err(Malformed::default()), "{reply}");
        }
    }

    #[test]
    fn the_answer_to_a_confirmation_is_an_answer_and_never_another_question() {
        let reply = json!({"status": "done", "turn_id": 5, "prefix": "了", "text": "已複製。"});
        assert_eq!(
            parse_answer(&reply),
            Ok(Reply::Say {
                prefix: "了".into(),
                text: "已複製。".into()
            })
        );
        // a core that asks again: that question is denied, not shown
        assert_eq!(
            parse_answer(&confirm(json!({"id": "c-2"}))),
            Err(Malformed {
                confirm_id: Some("c-2".into())
            })
        );
        assert_eq!(parse_answer(&json!({})), Err(Malformed::default()));
    }

    #[test]
    fn a_question_waits_as_long_as_the_core_says_within_reason() {
        let wait = |value: Value| ask_timeout_ms(Some(&value));
        assert_eq!(wait(json!(30_000)), 30_000);
        assert_eq!(wait(json!(5_000)), 5_000);
        assert_eq!(wait(json!(1)), 1);
        assert_eq!(wait(json!(45_000.0)), 45_000);
        assert_eq!(wait(json!(ASK_TIMEOUT_MAX_MS)), ASK_TIMEOUT_MAX_MS);
        // too long, or no time worth the name
        assert_eq!(wait(json!(ASK_TIMEOUT_MAX_MS + 1)), ASK_TIMEOUT_MAX_MS);
        assert_eq!(wait(json!(u64::MAX)), ASK_TIMEOUT_MAX_MS);
        for odd in [json!(0), json!(-5), json!(0.4), json!("30000"), json!(null)] {
            assert_eq!(wait(odd.clone()), ASK_TIMEOUT_DEFAULT_MS, "{odd}");
        }
        assert_eq!(ask_timeout_ms(None), ASK_TIMEOUT_DEFAULT_MS);
    }

    #[test]
    fn an_answer_stays_for_a_second_and_a_half_and_a_bit_more_for_every_character() {
        assert_eq!(say_hold_ms(""), 1_500);
        assert_eq!(say_hold_ms("好"), 1_650);
        assert_eq!(say_hold_ms("三點開會。"), 1_500 + 5 * 150);
        // a code point counts once, however it is written
        assert_eq!(say_hold_ms("𠮷"), 1_650);
        assert_eq!(say_hold_ms("a\u{301}"), 1_500 + 2 * 150);
        assert_eq!(say_hold_ms(&"字".repeat(70)), 12_000);
    }

    #[test]
    fn an_answer_never_stays_longer_than_twelve_seconds() {
        assert_eq!(say_hold_ms(&"字".repeat(70)), SAY_MAX_MS);
        assert_eq!(say_hold_ms(&"字".repeat(69)), 1_500 + 69 * 150);
        assert_eq!(say_hold_ms(&"x".repeat(100_000)), SAY_MAX_MS);
    }
}
