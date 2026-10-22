//! The background worker as the shell sees it: its reports, and whether a task is under way.
//!
//! The core hands a task to its worker and answers 《了。》 at once; some time later it sends
//! `worker.report {task_id, state, prefix, text}`, Raphael's report of what became of it, and tells
//! of each move with `worker.changed {task_id, state, running, ...}`. Three things are done with
//! them.
//!
//! - **The report is said on the overlay, when the person is not in the middle of something.** A
//!   report that comes while they dictate or ask a question (or while Raphael is answering) waits in
//!   a queue, and is said once everything is quiet again ([`ReportStage::try_say`] is the
//!   controller's answer to that). It is not said over a recording or over a question. The reports are
//!   said one at a time, each for as long as an answer is, in the order they came.
//! - **The tray offers 「中止背景任務」 while a task runs** ([`tray::set_background_running`]).
//! - **The main window's conversation view reads the conversation again** at each move
//!   (`MainEvent::WorkerChanged`, in commands.rs), and shows each task, with its steps, under the
//!   turn that handed it over. It can stop the task it shows, or run one that did not finish again
//!   (`worker.cancel` and `worker.rerun`, each with the task's id).
//!
//! Only the overlay (the person's own screen) gets the words of a report; nothing here logs them.
//! The core is the only source of the tasks.

use std::collections::VecDeque;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use serde_json::Value;
use tauri::AppHandle;
use tokio::sync::broadcast::{self, error::RecvError};

use crate::core_process::{CORE_DOWN, CORE_RESTARTED, CoreApi, CoreProcess};
use crate::rpc::{Notification, lock};
use crate::tray;

/// The core's report of a task that ended (done, stopped, interrupted, out of a limit ...).
pub const WORKER_REPORT: &str = "worker.report";
/// The core's note that a task moved on; `running` is whether one is under way.
pub const WORKER_CHANGED: &str = "worker.changed";

/// How often a report that cannot be said yet looks again.
const RETRY: Duration = Duration::from_millis(500);
/// The pause between two reports said one after the other, on top of the time the first stays.
const GAP: Duration = Duration::from_millis(700);
/// How many reports wait at most; when more come, the oldest are dropped (they are all in the
/// conversation of the main window anyway).
pub const MAX_WAITING: usize = 8;

/// Raphael's report of a task: `《prefix。text》`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Report {
    pub prefix: String,
    pub text: String,
}

/// What a notification of the core means for the shell.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Effect {
    /// Say this when it is quiet.
    Say(Report),
    /// A task is under way (true) or none is (false): the tray follows.
    Running(bool),
    Nothing,
}

/// The report in `worker.report`'s params; None when it has no prefix to say it with.
pub fn parse_report(params: &Value) -> Option<Report> {
    let prefix = params.get("prefix")?.as_str()?.trim();
    let text = params.get("text")?.as_str()?;
    (!prefix.is_empty()).then(|| Report {
        prefix: prefix.to_owned(),
        text: text.to_owned(),
    })
}

/// Whether a task is under way, from `worker.changed`'s params.
pub fn parse_running(params: &Value) -> Option<bool> {
    params.get("running")?.as_bool()
}

/// What a notification of the core asks of the shell. The core restarting or going down ends the
/// task it had (the worker was its child), so the tray's item goes.
pub fn effect_of(notification: &Notification) -> Effect {
    match notification.method.as_str() {
        WORKER_REPORT => parse_report(&notification.params).map_or(Effect::Nothing, Effect::Say),
        WORKER_CHANGED => {
            parse_running(&notification.params).map_or(Effect::Nothing, Effect::Running)
        }
        CORE_RESTARTED | CORE_DOWN => Effect::Running(false),
        _ => Effect::Nothing,
    }
}

/// Where a report is said: the dictation controller, which knows whether the person is busy.
pub trait ReportStage {
    /// Say the report now if nothing is going on, and return how long it stays on screen; None
    /// if it cannot be said now (the person is dictating or asking, or an answer is on screen).
    fn try_say(&self, prefix: &str, text: &str) -> Option<Duration>;
}

/// The reports that wait to be said, and the task that says them.
#[derive(Clone, Default)]
pub struct Reports {
    queue: Arc<Mutex<VecDeque<Report>>>,
    draining: Arc<AtomicBool>,
}

impl Reports {
    /// How many reports wait.
    #[cfg(test)]
    pub fn waiting(&self) -> usize {
        lock(&self.queue).len()
    }

    /// Queue a report and make sure that a task is saying them. Must be called within a Tokio
    /// runtime.
    pub fn push<S>(&self, report: Report, stage: &Arc<S>)
    where
        S: ReportStage + Send + Sync + 'static,
    {
        {
            let mut queue = lock(&self.queue);
            if queue.len() >= MAX_WAITING {
                queue.pop_front();
            }
            queue.push_back(report);
        }
        if !self.draining.swap(true, Ordering::AcqRel) {
            let (queue, draining, stage) =
                (self.queue.clone(), self.draining.clone(), stage.clone());
            tokio::spawn(async move { drain(queue, draining, stage).await });
        }
    }
}

async fn drain<S: ReportStage>(
    queue: Arc<Mutex<VecDeque<Report>>>,
    draining: Arc<AtomicBool>,
    stage: Arc<S>,
) {
    // The report that is next, taken out of the queue: reports that come while it waits can push
    // the oldest of the queue out, and never this one.
    let mut held: Option<Report> = None;
    loop {
        let next = held.take().or_else(|| lock(&queue).pop_front());
        let Some(report) = next else {
            // Nothing waits. A report may have been queued since: it saw `draining` still set and
            // started no task, so look again after letting go.
            draining.store(false, Ordering::Release);
            if lock(&queue).is_empty() || draining.swap(true, Ordering::AcqRel) {
                return;
            }
            continue;
        };
        match stage.try_say(&report.prefix, &report.text) {
            Some(stays) => tokio::time::sleep(stays + GAP).await,
            None => {
                held = Some(report);
                tokio::time::sleep(RETRY).await;
            }
        }
    }
}

/// Asks the core to say the reports of tasks that were interrupted while the app was away
/// (`worker.announce_pending`). It is asked here, once the reports have a listener, and not by the
/// core when it says hello: a report sent before anyone listens is lost, and the core would have
/// counted it as said. It is asked again each time the core is started again.
pub fn announce_pending(
    core: Arc<CoreProcess>,
    mut notifications: broadcast::Receiver<Notification>,
) {
    tauri::async_runtime::spawn(async move {
        loop {
            if let Err(err) = core
                .call("worker.announce_pending", serde_json::json!({}))
                .await
            {
                log::warn!("background: could not ask for the interrupted reports: {err}");
            }
            loop {
                match notifications.recv().await {
                    Ok(notification) if restarts_the_core(&notification) => break,
                    Ok(_) | Err(RecvError::Lagged(_)) => {}
                    Err(RecvError::Closed) => return,
                }
            }
        }
    });
}

/// Whether a notification says the core was started again.
fn restarts_the_core(notification: &Notification) -> bool {
    notification.method == CORE_RESTARTED
}

/// Follows the core's notifications: says the reports on `stage` and keeps the tray's item right.
pub fn spawn<S>(
    app: &AppHandle,
    stage: Arc<S>,
    mut notifications: broadcast::Receiver<Notification>,
) where
    S: ReportStage + Send + Sync + 'static,
{
    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        let reports = Reports::default();
        loop {
            match notifications.recv().await {
                Ok(notification) => match effect_of(&notification) {
                    Effect::Say(report) => reports.push(report, &stage),
                    Effect::Running(running) => tray::set_background_running(&app, running),
                    Effect::Nothing => {}
                },
                // A worker.changed may have been among those lost: the next one corrects the tray.
                Err(RecvError::Lagged(missed)) => {
                    log::warn!("background: missed {missed} notifications from the core");
                }
                Err(RecvError::Closed) => break,
            }
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::sync::atomic::AtomicUsize;

    fn notification(method: &str, params: Value) -> Notification {
        Notification {
            method: method.to_owned(),
            params,
        }
    }

    fn report(prefix: &str, text: &str) -> Report {
        Report {
            prefix: prefix.to_owned(),
            text: text.to_owned(),
        }
    }

    #[test]
    fn only_a_restart_of_the_core_asks_for_the_interrupted_reports_again() {
        assert!(restarts_the_core(&notification(CORE_RESTARTED, json!({}))));
        for method in [CORE_DOWN, WORKER_REPORT, WORKER_CHANGED] {
            assert!(
                !restarts_the_core(&notification(method, json!({}))),
                "{method}"
            );
        }
    }

    // ---- reading the core's notifications ----

    #[test]
    fn a_report_has_a_prefix_and_words() {
        let params = json!({"task_id": 3, "state": "done", "prefix": "告", "text": "三項待辦。"});
        assert_eq!(parse_report(&params), Some(report("告", "三項待辦。")));
        // an acknowledgement may have no words besides its prefix
        assert_eq!(
            parse_report(&json!({"prefix": "了", "text": ""})),
            Some(report("了", ""))
        );
    }

    #[test]
    fn a_report_that_cannot_be_said_is_nothing() {
        for params in [
            json!({}),
            json!({"text": "x"}),
            json!({"prefix": "告"}),
            json!({"prefix": "  ", "text": "x"}),
            json!({"prefix": 1, "text": "x"}),
            json!({"prefix": "告", "text": null}),
            json!("report"),
        ] {
            assert_eq!(parse_report(&params), None, "{params}");
        }
    }

    #[test]
    fn whether_a_task_runs_is_a_boolean_or_nothing() {
        assert_eq!(parse_running(&json!({"running": true})), Some(true));
        assert_eq!(parse_running(&json!({"running": false})), Some(false));
        for params in [json!({}), json!({"running": 1}), json!({"running": "yes"})] {
            assert_eq!(parse_running(&params), None, "{params}");
        }
    }

    #[test]
    fn the_notifications_mean_what_they_say() {
        assert_eq!(
            effect_of(&notification(
                WORKER_REPORT,
                json!({"prefix": "告", "text": "完成。"})
            )),
            Effect::Say(report("告", "完成。"))
        );
        assert_eq!(
            effect_of(&notification(WORKER_CHANGED, json!({"running": true}))),
            Effect::Running(true)
        );
        assert_eq!(
            effect_of(&notification(WORKER_CHANGED, json!({"state": "queued"}))),
            Effect::Nothing
        );
        assert_eq!(
            effect_of(&notification(WORKER_REPORT, json!({"text": "no prefix"}))),
            Effect::Nothing
        );
        for method in ["audio.level", "dictation.progress", "agent.something"] {
            assert_eq!(effect_of(&notification(method, json!({}))), Effect::Nothing);
        }
    }

    #[test]
    fn a_core_that_restarts_or_goes_down_has_no_task_under_way() {
        for method in [CORE_RESTARTED, CORE_DOWN] {
            assert_eq!(
                effect_of(&notification(method, json!({}))),
                Effect::Running(false)
            );
        }
    }

    // ---- saying them when it is quiet ----

    /// A stage that is busy until it is told otherwise, and keeps what it was made to say.
    #[derive(Default)]
    struct FakeStage {
        busy: AtomicBool,
        said: Mutex<Vec<(String, String)>>,
        asked: AtomicUsize,
    }

    impl FakeStage {
        fn said(&self) -> Vec<(String, String)> {
            lock(&self.said).clone()
        }
    }

    impl ReportStage for FakeStage {
        fn try_say(&self, prefix: &str, text: &str) -> Option<Duration> {
            self.asked.fetch_add(1, Ordering::SeqCst);
            if self.busy.load(Ordering::SeqCst) {
                return None;
            }
            lock(&self.said).push((prefix.to_owned(), text.to_owned()));
            Some(Duration::from_secs(3))
        }
    }

    async fn let_time_pass(seconds: u64) {
        tokio::time::sleep(Duration::from_secs(seconds)).await;
    }

    #[tokio::test(start_paused = true)]
    async fn a_report_is_said_at_once_when_it_is_quiet() {
        let stage = Arc::new(FakeStage::default());
        let reports = Reports::default();
        reports.push(report("告", "完成。"), &stage);
        let_time_pass(1).await;
        assert_eq!(stage.said(), [("告".to_owned(), "完成。".to_owned())]);
        assert_eq!(reports.waiting(), 0);
    }

    #[tokio::test(start_paused = true)]
    async fn a_report_waits_while_the_person_dictates_or_asks_and_is_said_when_it_is_quiet() {
        let stage = Arc::new(FakeStage::default());
        stage.busy.store(true, Ordering::SeqCst);
        let reports = Reports::default();
        reports.push(report("告", "完成。"), &stage);
        let_time_pass(20).await;
        assert!(stage.said().is_empty(), "said over a recording");
        // (the report that waits is held by the task that says them, no longer in the queue)
        assert_eq!(reports.waiting(), 0);
        assert!(stage.asked.load(Ordering::SeqCst) > 1, "it keeps looking");
        stage.busy.store(false, Ordering::SeqCst);
        let_time_pass(1).await;
        assert_eq!(stage.said(), [("告".to_owned(), "完成。".to_owned())]);
        assert_eq!(reports.waiting(), 0);
    }

    #[tokio::test(start_paused = true)]
    async fn reports_are_said_one_at_a_time_in_the_order_they_came() {
        let stage = Arc::new(FakeStage::default());
        let reports = Reports::default();
        reports.push(report("告", "第一"), &stage);
        reports.push(report("告", "第二"), &stage);
        reports.push(report("否", "第三"), &stage);
        let_time_pass(1).await;
        // The first is on screen; the others wait for it to go.
        assert_eq!(stage.said().len(), 1);
        let_time_pass(4).await;
        assert_eq!(stage.said().len(), 2);
        let_time_pass(4).await;
        let said: Vec<_> = stage.said().into_iter().map(|(_, text)| text).collect();
        assert_eq!(said, ["第一", "第二", "第三"]);
    }

    #[tokio::test(start_paused = true)]
    async fn when_too_many_wait_the_oldest_are_dropped() {
        let stage = Arc::new(FakeStage::default());
        stage.busy.store(true, Ordering::SeqCst);
        let reports = Reports::default();
        for n in 0..MAX_WAITING + 3 {
            reports.push(report("告", &format!("報告{n}")), &stage);
        }
        assert_eq!(reports.waiting(), MAX_WAITING);
        stage.busy.store(false, Ordering::SeqCst);
        let_time_pass(60).await;
        let said: Vec<_> = stage.said().into_iter().map(|(_, text)| text).collect();
        assert_eq!(said.len(), MAX_WAITING);
        assert_eq!(said[0], "報告3");
        assert_eq!(said.last().unwrap(), &format!("報告{}", MAX_WAITING + 2));
    }

    #[tokio::test(start_paused = true)]
    async fn a_report_that_is_waiting_to_be_said_is_not_pushed_out_by_the_ones_that_follow() {
        let stage = Arc::new(FakeStage::default());
        stage.busy.store(true, Ordering::SeqCst);
        let reports = Reports::default();
        reports.push(report("告", "最早"), &stage);
        let_time_pass(2).await; // the first is now the one that waits to be said
        for n in 0..MAX_WAITING + 2 {
            reports.push(report("告", &format!("後來{n}")), &stage);
        }
        stage.busy.store(false, Ordering::SeqCst);
        let_time_pass(120).await;
        let said: Vec<_> = stage.said().into_iter().map(|(_, text)| text).collect();
        assert_eq!(said[0], "最早", "{said:?}");
        assert_eq!(said.len(), 1 + MAX_WAITING);
    }

    #[tokio::test(start_paused = true)]
    async fn a_report_that_comes_after_the_queue_ran_dry_is_said_too() {
        let stage = Arc::new(FakeStage::default());
        let reports = Reports::default();
        reports.push(report("告", "一"), &stage);
        let_time_pass(30).await; // the task has finished and let go
        reports.push(report("告", "二"), &stage);
        let_time_pass(1).await;
        let said: Vec<_> = stage.said().into_iter().map(|(_, text)| text).collect();
        assert_eq!(said, ["一", "二"]);
    }
}
