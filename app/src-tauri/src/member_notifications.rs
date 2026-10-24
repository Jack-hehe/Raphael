//! Desktop notices for opted-in members, delivered directly from the core even when the main
//! window is hidden. Only the member's display name and a local status line leave the app: a
//! task's answer, question and tool arguments never become a Windows notification or a log.

use std::sync::Arc;
use tauri::{AppHandle, Manager};
use tauri_plugin_notification::{NotificationExt, PermissionState};
use tokio::sync::broadcast::{self, error::RecvError};

use crate::{core_process::CoreProcess, notices::LanguageCell, rpc::Notification};

#[derive(Debug, PartialEq, Eq)]
struct MemberNotice {
    title: String,
    body: &'static str,
}

fn notice_from(event: &Notification, language: &str) -> Option<MemberNotice> {
    if event.method != "member.notice" || !event.params.get("notify")?.as_bool()? {
        return None;
    }
    let member_id = event.params.get("member_id")?.as_u64()?;
    let name = event.params.get("name")?.as_str()?.trim();
    if member_id == 0
        || name.is_empty()
        || name.chars().count() > 160
        || name.chars().any(char::is_control)
    {
        return None;
    }
    let body = match (event.params.get("kind")?.as_str()?, language) {
        ("finished", "en") => "Finished the task.",
        ("finished", "ja") => "作業が完了しました。",
        ("finished", _) => "工作已完成。",
        ("needs_input", "en") => "Needs your reply.",
        ("needs_input", "ja") => "返信を待っています。",
        ("needs_input", _) => "需要你的回覆。",
        _ => return None,
    };
    Some(MemberNotice {
        title: name.to_owned(),
        body,
    })
}

/// Kept behind a small seam so tests exercise the actual event listener without contacting the
/// OS. Errors carry no strings: an OS error can quote the notification's title.
trait Notifier {
    fn permitted(&self) -> Result<bool, ()>;
    fn show(&self, notice: MemberNotice) -> Result<(), ()>;
}

struct DesktopNotifier(AppHandle);

impl Notifier for DesktopNotifier {
    fn permitted(&self) -> Result<bool, ()> {
        self.0
            .notification()
            .permission_state()
            .map(|state| state == PermissionState::Granted)
            .map_err(|_| ())
    }

    fn show(&self, notice: MemberNotice) -> Result<(), ()> {
        self.0
            .notification()
            .builder()
            .title(notice.title)
            .body(notice.body)
            .show()
            .map_err(|_| ())
    }
}

async fn listen<N: Notifier>(
    mut events: broadcast::Receiver<Notification>,
    notifier: N,
    language: LanguageCell,
) {
    loop {
        match events.recv().await {
            Ok(event) => {
                let Some(notice) = notice_from(&event, &language.get()) else {
                    continue;
                };
                match notifier.permitted() {
                    Ok(true) => {
                        if notifier.show(notice).is_err() {
                            log::warn!("could not deliver a member desktop notification");
                        }
                    }
                    Ok(false) => {}
                    Err(()) => log::warn!("could not check desktop notification permission"),
                }
            }
            // Never reconstruct a lost notice from private task contents or replay old notices.
            Err(RecvError::Lagged(missed)) => {
                log::warn!("member notices: missed {missed} core events")
            }
            Err(RecvError::Closed) => break,
        }
    }
}

/// Called after the device settings have loaded. There is no webview command that sends a
/// notification: the subscriber belongs to the shell and accepts only the core's event stream.
pub fn spawn(app: &AppHandle) {
    let Some(core) = app.try_state::<Arc<CoreProcess>>() else {
        return;
    };
    let language = app
        .try_state::<LanguageCell>()
        .map(|cell| cell.inner().clone())
        .unwrap_or_default();
    tauri::async_runtime::spawn(listen(
        core.subscribe(),
        DesktopNotifier(app.clone()),
        language,
    ));
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::rpc::lock;
    use serde_json::json;
    use std::sync::{
        Mutex,
        atomic::{AtomicUsize, Ordering},
    };

    fn event(kind: &str) -> Notification {
        Notification {
            method: "member.notice".into(),
            params: json!({
                "member_id": 3, "name": "Ada", "kind": kind, "notify": true,
                "question": "private question", "answer": "private answer",
            }),
        }
    }

    struct FakeNotifier {
        permission: Result<bool, ()>,
        failures: bool,
        checked: Arc<AtomicUsize>,
        shown: Arc<Mutex<Vec<MemberNotice>>>,
    }

    impl Notifier for FakeNotifier {
        fn permitted(&self) -> Result<bool, ()> {
            self.checked.fetch_add(1, Ordering::SeqCst);
            self.permission
        }
        fn show(&self, notice: MemberNotice) -> Result<(), ()> {
            lock(&self.shown).push(notice);
            if self.failures { Err(()) } else { Ok(()) }
        }
    }

    #[test]
    fn only_explicit_opt_in_and_known_member_notices_are_accepted() {
        for kind in ["finished", "needs_input"] {
            assert!(notice_from(&event(kind), "en").is_some());
        }
        let valid = event("finished");
        for (field, value) in [
            ("notify", json!(false)),
            ("notify", json!(null)),
            ("notify", json!(1)),
            ("kind", json!("failed")),
            ("member_id", json!(0)),
            ("member_id", json!(-1)),
            ("name", json!("  ")),
            ("name", json!("Ada\nprivate")),
            ("name", json!("x".repeat(161))),
        ] {
            let mut invalid = valid.clone();
            invalid.params[field] = value;
            assert!(notice_from(&invalid, "en").is_none(), "{field}");
        }
        for field in ["notify", "kind", "member_id", "name"] {
            let mut invalid = valid.clone();
            invalid.params.as_object_mut().unwrap().remove(field);
            assert!(notice_from(&invalid, "en").is_none(), "{field}");
        }
        for method in [
            "agent.turn",
            "background.question",
            "members.changed",
            "member.notice.send",
        ] {
            let mut other = valid.clone();
            other.method = method.into();
            assert!(notice_from(&other, "en").is_none());
        }
    }

    #[test]
    fn notices_use_local_translations_and_never_include_task_contents() {
        for (language, finished, waiting) in [
            ("en", "Finished the task.", "Needs your reply."),
            ("ja", "作業が完了しました。", "返信を待っています。"),
            ("zh-TW", "工作已完成。", "需要你的回覆。"),
            ("unknown", "工作已完成。", "需要你的回覆。"),
        ] {
            for (kind, expected) in [("finished", finished), ("needs_input", waiting)] {
                assert_eq!(
                    notice_from(&event(kind), language),
                    Some(MemberNotice {
                        title: "Ada".into(),
                        body: expected,
                    })
                );
            }
        }
    }

    #[tokio::test]
    async fn core_listener_delivers_both_kinds_without_a_window_or_page() {
        let (sender, receiver) = broadcast::channel(8);
        let checked = Arc::new(AtomicUsize::new(0));
        let shown = Arc::new(Mutex::new(Vec::new()));
        sender.send(event("finished")).unwrap();
        sender.send(event("needs_input")).unwrap();
        let mut disabled = event("finished");
        disabled.params["notify"] = json!(false);
        sender.send(disabled).unwrap();
        let mut page_event = event("finished");
        page_event.method = "agent.turn".into();
        sender.send(page_event).unwrap();
        drop(sender);
        let language = LanguageCell::default();
        language.set("ja");
        listen(
            receiver,
            FakeNotifier {
                permission: Ok(true),
                failures: false,
                checked: checked.clone(),
                shown: shown.clone(),
            },
            language,
        )
        .await;
        assert_eq!(checked.load(Ordering::SeqCst), 2);
        assert_eq!(
            *lock(&shown),
            vec![
                MemberNotice {
                    title: "Ada".into(),
                    body: "作業が完了しました。"
                },
                MemberNotice {
                    title: "Ada".into(),
                    body: "返信を待っています。"
                },
            ]
        );
    }

    #[tokio::test]
    async fn denied_permissions_and_os_errors_do_not_stop_the_listener() {
        for permission in [Ok(false), Err(()), Ok(true)] {
            let (sender, receiver) = broadcast::channel(4);
            let checked = Arc::new(AtomicUsize::new(0));
            let shown = Arc::new(Mutex::new(Vec::new()));
            sender.send(event("finished")).unwrap();
            sender.send(event("needs_input")).unwrap();
            drop(sender);
            listen(
                receiver,
                FakeNotifier {
                    permission,
                    failures: true,
                    checked: checked.clone(),
                    shown: shown.clone(),
                },
                LanguageCell::default(),
            )
            .await;
            assert_eq!(checked.load(Ordering::SeqCst), 2);
            assert_eq!(
                lock(&shown).len(),
                if permission == Ok(true) { 2 } else { 0 }
            );
        }
    }
}
