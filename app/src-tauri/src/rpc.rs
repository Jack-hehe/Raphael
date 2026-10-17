//! JSON-RPC 2.0 over the core's stdio: one JSON object per line.
//!
//! [`RpcClient`] owns no process and no thread. Whoever runs the core gives it the pipe to write
//! requests to ([`RpcClient::set_writer`]) and hands it every line the core prints
//! ([`RpcClient::handle_line`]). That keeps it testable without a process, and lets the process
//! manager swap the pipe when the core restarts.
//!
//! Never log a request line or its params: `secrets.set_openrouter_key` carries the OpenRouter key.

use std::collections::HashMap;
use std::fmt;
use std::future::Future;
use std::io::Write;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Mutex, MutexGuard, PoisonError};
use std::time::Duration;

use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use tokio::sync::{broadcast, oneshot};

/// The core exited, or is not running, so a call could not be sent or will never be answered.
pub const CORE_EXITED: i64 = -32000;
/// The core did not answer in time.
pub const TIMEOUT: i64 = -32001;

/// How many notifications a subscriber may fall behind before it starts to lose the oldest ones.
/// The core sends `audio.level` 30 to 60 times a second, so this is a few seconds of backlog.
const NOTIFICATION_BACKLOG: usize = 1024;

/// An error that crosses the boundary: the `error` of a response, or one made up here for a call
/// that could not be completed ([`CORE_EXITED`], [`TIMEOUT`]). The core's own codes are 1001 and
/// up (`protocol.py`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RpcError {
    pub code: i64,
    pub message: String,
    #[serde(default)]
    pub data: Option<Value>,
}

impl RpcError {
    pub fn new(code: i64, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
            data: None,
        }
    }
}

impl fmt::Display for RpcError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        // `data` is left out: it can hold anything the core's handler put there.
        write!(f, "[{}] {}", self.code, self.message)
    }
}

impl std::error::Error for RpcError {}

/// A message the core sends by itself (`audio.level`), or one this app makes up about the core
/// (`core.restarted`, see [`RpcClient::notify_local`]).
#[derive(Debug, Clone)]
pub struct Notification {
    pub method: String,
    pub params: Value,
}

/// One line from the core, understood.
#[derive(Debug, Clone)]
pub enum Incoming {
    Response {
        id: u64,
        result: Result<Value, RpcError>,
    },
    Notification(Notification),
}

/// Reads one line the core printed. A line that is not a reply to one of our calls or a
/// notification gives `None`: not JSON, not JSON-RPC 2.0, an id that is not a number we could have
/// sent (the core answers a line it cannot parse with `"id": null`), a malformed `error`, or a
/// request from the core (it makes none, and we could not answer it).
pub fn parse_line(line: &str) -> Option<Incoming> {
    let Value::Object(mut message) = serde_json::from_str::<Value>(line).ok()? else {
        return None;
    };
    if message.get("jsonrpc").and_then(Value::as_str) != Some("2.0") {
        return None;
    }

    if let Some(method) = message.remove("method") {
        if message.contains_key("id") {
            return None;
        }
        let Value::String(method) = method else {
            return None;
        };
        let params = message.remove("params").unwrap_or(Value::Null);
        return Some(Incoming::Notification(Notification { method, params }));
    }

    let id = message.remove("id")?.as_u64()?;
    if let Some(error) = message.remove("error") {
        let error = serde_json::from_value(error).ok()?;
        return Some(Incoming::Response {
            id,
            result: Err(error),
        });
    }
    // `"result": null` is a result (a method that returns nothing), so only a missing key fails.
    let result = message.remove("result")?;
    Some(Incoming::Response {
        id,
        result: Ok(result),
    })
}

/// What the rest of the app needs from the core: send a request, wait for its answer. It is a
/// trait so the dictation controller can be tested against a fake core.
pub trait CoreApi: Send + Sync + 'static {
    fn call(
        &self,
        method: &str,
        params: Value,
    ) -> impl Future<Output = Result<Value, RpcError>> + Send;
}

type Reply = Result<Value, RpcError>;

/// The app's side of the conversation with the core.
pub struct RpcClient {
    next_id: AtomicU64,
    /// Calls waiting for their answer, by request id.
    pending: Mutex<HashMap<u64, oneshot::Sender<Reply>>>,
    /// Where requests go: the core's stdin. `None` while there is no core to talk to.
    writer: Mutex<Option<Box<dyn Write + Send>>>,
    notifications: broadcast::Sender<Notification>,
}

impl Default for RpcClient {
    fn default() -> Self {
        Self::new()
    }
}

impl RpcClient {
    pub fn new() -> Self {
        Self {
            next_id: AtomicU64::new(1),
            pending: Mutex::new(HashMap::new()),
            writer: Mutex::new(None),
            notifications: broadcast::channel(NOTIFICATION_BACKLOG).0,
        }
    }

    /// Sets where requests are written, or `None` when there is no core: calls then fail at once
    /// with [`CORE_EXITED`] instead of waiting for a timeout. The old writer is dropped, which
    /// closes the pipe if it was one.
    pub fn set_writer(&self, writer: Option<Box<dyn Write + Send>>) {
        let old = std::mem::replace(&mut *lock(&self.writer), writer);
        drop(old);
    }

    /// Sends a request and waits for its answer, for at most `timeout`. Fails with [`CORE_EXITED`]
    /// if there is no core, if it cannot be written to, or if it exits first ([`Self::fail_all`]),
    /// and with [`TIMEOUT`] if no answer comes in time. However the call ends, even by being
    /// dropped, its entry in the pending map is gone.
    pub async fn call(
        &self,
        method: &str,
        params: Value,
        timeout: Duration,
    ) -> Result<Value, RpcError> {
        let id = self.next_id.fetch_add(1, Ordering::Relaxed);
        let (reply_tx, reply_rx) = oneshot::channel();
        // Registered before the request is written, so the answer cannot arrive first.
        lock(&self.pending).insert(id, reply_tx);
        let _registration = Registration { client: self, id };

        self.send(id, method, params)?;

        match tokio::time::timeout(timeout, reply_rx).await {
            Ok(Ok(reply)) => reply,
            // The sender was dropped without a reply. `fail_all` always sends one, so this is
            // only the client itself going away.
            Ok(Err(_)) => Err(RpcError::new(
                CORE_EXITED,
                format!("the core never answered {method}"),
            )),
            Err(_) => Err(RpcError::new(
                TIMEOUT,
                format!("{method} got no answer within {timeout:?}"),
            )),
        }
    }

    fn send(&self, id: u64, method: &str, params: Value) -> Result<(), RpcError> {
        let mut request = json!({ "jsonrpc": "2.0", "id": id, "method": method });
        if !params.is_null() {
            request["params"] = params;
        }
        let mut line = request.to_string();
        line.push('\n');

        // Nothing here logs the line or the params: they may carry the OpenRouter key.
        // Requests are small, and the core reads its stdin on a thread of its own, so this write
        // does not wait for the core to be busy with something else.
        let mut writer = lock(&self.writer);
        let Some(writer) = writer.as_mut() else {
            return Err(RpcError::new(CORE_EXITED, "the core is not running"));
        };
        writer
            .write_all(line.as_bytes())
            .and_then(|()| writer.flush())
            .map_err(|err| {
                RpcError::new(
                    CORE_EXITED,
                    format!("could not send {method} to the core: {err}"),
                )
            })
    }

    /// Takes one line the core printed: completes the call it answers, or passes the notification
    /// on to the subscribers. Anything else is logged and dropped.
    pub fn handle_line(&self, line: &str) {
        let line = line.trim();
        if line.is_empty() {
            return;
        }
        match parse_line(line) {
            Some(Incoming::Response { id, result }) => {
                let waiting = lock(&self.pending).remove(&id);
                match waiting {
                    // Err: the caller stopped waiting between the lookup and now.
                    Some(reply) => drop(reply.send(result)),
                    None => log::debug!("ignoring the reply to call {id}: nobody is waiting"),
                }
            }
            // Err: nobody is subscribed.
            Some(Incoming::Notification(notification)) => {
                drop(self.notifications.send(notification))
            }
            None => log::warn!(
                "ignoring a line from the core that is not a JSON-RPC reply or notification: {}",
                preview(line)
            ),
        }
    }

    /// Ends every waiting call with `err`. The process manager calls it when the core exits.
    pub fn fail_all(&self, err: RpcError) {
        let waiting: Vec<_> = lock(&self.pending).drain().collect();
        for (_, reply) in waiting {
            drop(reply.send(Err(err.clone())));
        }
    }

    /// A receiver for the core's notifications and for [`Self::notify_local`]. One that falls more
    /// than [`NOTIFICATION_BACKLOG`] messages behind loses the oldest (`RecvError::Lagged`).
    pub fn subscribe(&self) -> broadcast::Receiver<Notification> {
        self.notifications.subscribe()
    }

    /// Tells the subscribers something the core could not say itself (`core.restarted`).
    pub fn notify_local(&self, notification: Notification) {
        // Err: nobody is subscribed.
        drop(self.notifications.send(notification));
    }
}

/// Removes a call's entry from the pending map when the call ends, however it ends: a reply
/// (already removed by then), a timeout, or the caller dropping the future.
struct Registration<'a> {
    client: &'a RpcClient,
    id: u64,
}

impl Drop for Registration<'_> {
    fn drop(&mut self) {
        lock(&self.client.pending).remove(&self.id);
    }
}

pub(crate) fn lock<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    // A thread that panicked while holding the lock must not take every later call down with it:
    // what these locks guard (a map of waiting calls, a pipe) is still consistent.
    mutex.lock().unwrap_or_else(PoisonError::into_inner)
}

/// The start of a line, for a log message.
fn preview(line: &str) -> String {
    let mut shown: String = line.chars().take(120).collect();
    if shown.len() < line.len() {
        shown.push('…');
    }
    shown
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::{Value, json};
    use std::io;
    use std::sync::{Arc, Mutex};
    use std::time::Duration;

    type Lines = Arc<Mutex<Vec<String>>>;

    /// A `Write` that keeps every complete line written to it, the way the core's stdin sees them.
    struct CaptureWriter {
        partial: Vec<u8>,
        lines: Lines,
    }

    impl io::Write for CaptureWriter {
        fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
            self.partial.extend_from_slice(buf);
            while let Some(end) = self.partial.iter().position(|&b| b == b'\n') {
                let line: Vec<u8> = self.partial.drain(..=end).collect();
                let line = String::from_utf8(line).expect("requests are UTF-8");
                self.lines.lock().unwrap().push(line.trim_end().to_owned());
            }
            Ok(buf.len())
        }

        fn flush(&mut self) -> io::Result<()> {
            Ok(())
        }
    }

    fn capture_writer() -> (CaptureWriter, Lines) {
        let lines = Lines::default();
        let writer = CaptureWriter {
            partial: Vec::new(),
            lines: lines.clone(),
        };
        (writer, lines)
    }

    /// A pipe whose other end is gone.
    struct BrokenWriter;

    impl io::Write for BrokenWriter {
        fn write(&mut self, _: &[u8]) -> io::Result<usize> {
            Err(io::Error::new(
                io::ErrorKind::BrokenPipe,
                "the pipe is closed",
            ))
        }

        fn flush(&mut self) -> io::Result<()> {
            Ok(())
        }
    }

    /// Waits until the client has written `count` request lines, and returns them parsed.
    async fn wait_for_requests(lines: &Lines, count: usize) -> Vec<Value> {
        for _ in 0..400 {
            {
                let lines = lines.lock().unwrap();
                if lines.len() >= count {
                    return lines
                        .iter()
                        .map(|line| serde_json::from_str(line).expect("a request is JSON"))
                        .collect();
                }
            }
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
        panic!("the client wrote fewer than {count} request lines");
    }

    /// The id of the first request the client wrote.
    async fn wait_for_request_id(lines: &Lines) -> u64 {
        wait_for_requests(lines, 1).await[0]["id"]
            .as_u64()
            .expect("a request has a numeric id")
    }

    fn pending(rpc: &RpcClient) -> usize {
        rpc.pending.lock().unwrap().len()
    }

    fn client_with_capture() -> (Arc<RpcClient>, Lines) {
        let rpc = Arc::new(RpcClient::new());
        let (writer, lines) = capture_writer();
        rpc.set_writer(Some(Box::new(writer)));
        (rpc, lines)
    }

    #[test]
    fn parses_result_error_and_notification() {
        assert!(matches!(
            parse_line(r#"{"jsonrpc":"2.0","id":3,"result":{"ok":true}}"#),
            Some(Incoming::Response {
                id: 3,
                result: Ok(_)
            })
        ));
        assert!(matches!(
            parse_line(r#"{"jsonrpc":"2.0","id":4,"error":{"code":1004,"message":"no mic"}}"#),
            Some(Incoming::Response {
                id: 4,
                result: Err(RpcError { code: 1004, .. })
            })
        ));
        assert!(matches!(
            parse_line(r#"{"jsonrpc":"2.0","method":"audio.level","params":{"level":0.4}}"#),
            Some(Incoming::Notification(_))
        ));
        assert!(parse_line("not json").is_none());
    }

    #[test]
    fn a_null_result_is_still_a_result() {
        // A method that returns nothing is answered with "result": null.
        let parsed = parse_line(r#"{"jsonrpc":"2.0","id":5,"result":null}"#);
        assert!(matches!(
            parsed,
            Some(Incoming::Response {
                id: 5,
                result: Ok(Value::Null)
            })
        ));
    }

    #[test]
    fn keeps_error_data_and_notification_params() {
        let Some(Incoming::Response {
            result: Err(err), ..
        }) = parse_line(
            r#"{"jsonrpc":"2.0","id":6,"error":{"code":1002,"message":"over budget","data":{"spent":10.5}}}"#,
        )
        else {
            panic!("expected an error response");
        };
        assert_eq!(err.message, "over budget");
        assert_eq!(err.data, Some(json!({"spent": 10.5})));

        let Some(Incoming::Notification(n)) = parse_line(
            r#"{"jsonrpc":"2.0","method":"models.progress","params":{"name":"qwen3-asr","done":1,"total":4}}"#,
        ) else {
            panic!("expected a notification");
        };
        assert_eq!(n.method, "models.progress");
        assert_eq!(
            n.params,
            json!({"name": "qwen3-asr", "done": 1, "total": 4})
        );

        // params may be left out
        let Some(Incoming::Notification(n)) =
            parse_line(r#"{"jsonrpc":"2.0","method":"core.ready"}"#)
        else {
            panic!("expected a notification");
        };
        assert_eq!(n.params, Value::Null);
    }

    #[test]
    fn rejects_lines_that_are_not_replies_or_notifications() {
        for line in [
            "",
            "   ",
            "[1,2,3]",
            "42",
            r#"{"hello":"world"}"#,
            // not JSON-RPC 2.0
            r#"{"id":1,"result":{}}"#,
            r#"{"jsonrpc":"1.0","id":1,"result":{}}"#,
            // neither a result nor an error
            r#"{"jsonrpc":"2.0","id":1}"#,
            // ids we never send: the core answers a line it cannot parse with "id": null
            r#"{"jsonrpc":"2.0","id":null,"error":{"code":-32700,"message":"Parse error"}}"#,
            r#"{"jsonrpc":"2.0","id":"abc","result":{}}"#,
            r#"{"jsonrpc":"2.0","id":-1,"result":{}}"#,
            r#"{"jsonrpc":"2.0","id":1.5,"result":{}}"#,
            // an error object without a code
            r#"{"jsonrpc":"2.0","id":1,"error":{"message":"oops"}}"#,
            // a request from the core: the core makes none, and we could not answer it
            r#"{"jsonrpc":"2.0","id":7,"method":"audio.level","params":{}}"#,
            r#"{"jsonrpc":"2.0","method":42}"#,
        ] {
            assert!(parse_line(line).is_none(), "should be rejected: {line}");
        }
    }

    #[test]
    fn rpc_error_reads_as_code_and_message() {
        let err = RpcError::new(1004, "no mic");
        assert_eq!(err.to_string(), "[1004] no mic");
        let _: &dyn std::error::Error = &err;
        // `data` is optional on the wire
        let parsed: RpcError = serde_json::from_str(r#"{"code":1,"message":"x"}"#).unwrap();
        assert_eq!(parsed, RpcError::new(1, "x"));
    }

    #[tokio::test]
    async fn call_resolves_with_matching_response() {
        let rpc = Arc::new(RpcClient::new());
        let (writer, lines) = capture_writer(); // test helper: Write impl that stores lines
        rpc.set_writer(Some(Box::new(writer)));
        let r2 = rpc.clone();
        let call = tokio::spawn(async move {
            r2.call("core.hello", json!({}), Duration::from_secs(1))
                .await
        });
        let id = wait_for_request_id(&lines).await; // reads the id from the written request line
        rpc.handle_line(&format!(
            r#"{{"jsonrpc":"2.0","id":{id},"result":{{"protocol":1}}}}"#
        ));
        assert_eq!(call.await.unwrap().unwrap(), json!({"protocol": 1}));
        assert_eq!(pending(&rpc), 0);
    }

    #[tokio::test]
    async fn call_writes_one_json_rpc_request_per_line() {
        let (rpc, lines) = client_with_capture();
        let r2 = rpc.clone();
        let call = tokio::spawn(async move {
            r2.call(
                "audio.set_device",
                json!({"name": "麥克風"}),
                Duration::from_secs(1),
            )
            .await
        });
        let request = wait_for_requests(&lines, 1).await.remove(0);
        assert_eq!(request["jsonrpc"], "2.0");
        assert_eq!(request["method"], "audio.set_device");
        assert_eq!(request["params"], json!({"name": "麥克風"}));
        let id = request["id"].as_u64().expect("a numeric id");
        rpc.handle_line(&format!(r#"{{"jsonrpc":"2.0","id":{id},"result":{{}}}}"#));
        call.await.unwrap().unwrap();

        // `null` params are left out, which the core reads as {}
        let r2 = rpc.clone();
        let call = tokio::spawn(async move {
            r2.call("core.hello", Value::Null, Duration::from_secs(1))
                .await
        });
        let request = wait_for_requests(&lines, 2).await.remove(1);
        assert_eq!(request["method"], "core.hello");
        assert!(request.get("params").is_none());
        assert_ne!(
            request["id"].as_u64().unwrap(),
            id,
            "every call gets its own id"
        );
        call.abort();
    }

    #[tokio::test]
    async fn call_returns_the_error_of_an_error_response() {
        let (rpc, lines) = client_with_capture();
        let r2 = rpc.clone();
        let call = tokio::spawn(async move {
            r2.call("dictation.start", json!({}), Duration::from_secs(1))
                .await
        });
        let id = wait_for_request_id(&lines).await;
        rpc.handle_line(&format!(
            r#"{{"jsonrpc":"2.0","id":{id},"error":{{"code":1004,"message":"no mic"}}}}"#
        ));
        assert_eq!(
            call.await.unwrap().unwrap_err(),
            RpcError::new(1004, "no mic")
        );
        assert_eq!(pending(&rpc), 0);
    }

    #[tokio::test]
    async fn responses_may_arrive_out_of_order() {
        let (rpc, lines) = client_with_capture();
        let r1 = rpc.clone();
        let first = tokio::spawn(async move {
            r1.call("history.list", json!({}), Duration::from_secs(5))
                .await
        });
        wait_for_requests(&lines, 1).await;
        let r2 = rpc.clone();
        let second = tokio::spawn(async move {
            r2.call("history.latest", json!({}), Duration::from_secs(5))
                .await
        });
        let requests = wait_for_requests(&lines, 2).await;
        let id_of = |method: &str| {
            requests
                .iter()
                .find(|request| request["method"] == method)
                .and_then(|request| request["id"].as_u64())
                .expect("the request was written")
        };
        // answer the second request first
        rpc.handle_line(&format!(
            r#"{{"jsonrpc":"2.0","id":{},"result":"latest"}}"#,
            id_of("history.latest")
        ));
        rpc.handle_line(&format!(
            r#"{{"jsonrpc":"2.0","id":{},"result":"list"}}"#,
            id_of("history.list")
        ));
        assert_eq!(first.await.unwrap().unwrap(), json!("list"));
        assert_eq!(second.await.unwrap().unwrap(), json!("latest"));
    }

    #[tokio::test]
    async fn fail_all_and_timeout() {
        let (rpc, lines) = client_with_capture();

        // a pending call ends with CORE_EXITED after fail_all, and so do all the others
        let (r1, r2) = (rpc.clone(), rpc.clone());
        let stop = tokio::spawn(async move {
            r1.call("dictation.stop", json!({}), Duration::from_secs(5))
                .await
        });
        let hello = tokio::spawn(async move {
            r2.call("core.hello", json!({}), Duration::from_secs(5))
                .await
        });
        wait_for_requests(&lines, 2).await;
        assert_eq!(pending(&rpc), 2);
        rpc.fail_all(RpcError::new(CORE_EXITED, "the core exited"));
        for call in [stop, hello] {
            let err = call.await.unwrap().unwrap_err();
            assert_eq!(err.code, CORE_EXITED);
            assert_eq!(err.message, "the core exited");
        }
        assert_eq!(pending(&rpc), 0);

        // a call with a 50 ms timeout and no reply ends with TIMEOUT, and its entry is gone
        let started = std::time::Instant::now();
        let err = rpc
            .call(
                "status.check_openrouter",
                json!({}),
                Duration::from_millis(50),
            )
            .await
            .unwrap_err();
        assert_eq!(err.code, TIMEOUT);
        assert!(started.elapsed() < Duration::from_secs(2));
        assert_eq!(pending(&rpc), 0);
    }

    #[tokio::test]
    async fn a_late_reply_is_ignored() {
        let (rpc, lines) = client_with_capture();
        let err = rpc
            .call("core.hello", json!({}), Duration::from_millis(30))
            .await
            .unwrap_err();
        assert_eq!(err.code, TIMEOUT);
        let late_id = wait_for_request_id(&lines).await;

        // the answer comes after the caller gave up: nothing happens
        rpc.handle_line(&format!(
            r#"{{"jsonrpc":"2.0","id":{late_id},"result":{{"protocol":1}}}}"#
        ));
        assert_eq!(pending(&rpc), 0);

        // and the next call is not confused by it
        let r2 = rpc.clone();
        let next = tokio::spawn(async move {
            r2.call("core.hello", json!({}), Duration::from_secs(1))
                .await
        });
        let requests = wait_for_requests(&lines, 2).await;
        let next_id = requests[1]["id"].as_u64().unwrap();
        assert_ne!(next_id, late_id);
        rpc.handle_line(&format!(
            r#"{{"jsonrpc":"2.0","id":{next_id},"result":"fresh"}}"#
        ));
        assert_eq!(next.await.unwrap().unwrap(), json!("fresh"));
    }

    #[tokio::test]
    async fn a_call_that_is_dropped_leaves_nothing_pending() {
        let (rpc, lines) = client_with_capture();
        let r2 = rpc.clone();
        let call = tokio::spawn(async move {
            r2.call("dictation.stop", json!({}), Duration::from_secs(30))
                .await
        });
        wait_for_requests(&lines, 1).await;
        assert_eq!(pending(&rpc), 1);
        call.abort(); // the caller stops waiting (a select! branch lost, a task cancelled)
        assert!(call.await.unwrap_err().is_cancelled());
        assert_eq!(pending(&rpc), 0);
    }

    #[tokio::test]
    async fn without_a_writer_a_call_fails_at_once() {
        let rpc = RpcClient::new();
        // A 30 s timeout: if the call waited for it, the 1 s bound below would trip.
        let outcome = tokio::time::timeout(
            Duration::from_secs(1),
            rpc.call("core.hello", json!({}), Duration::from_secs(30)),
        )
        .await
        .expect("a call with no core to talk to must not wait");
        assert_eq!(outcome.unwrap_err().code, CORE_EXITED);
        assert_eq!(pending(&rpc), 0);
    }

    #[tokio::test]
    async fn a_broken_pipe_fails_the_call_with_core_exited() {
        let rpc = RpcClient::new();
        rpc.set_writer(Some(Box::new(BrokenWriter)));
        let outcome = tokio::time::timeout(
            Duration::from_secs(1),
            rpc.call("core.hello", json!({}), Duration::from_secs(30)),
        )
        .await
        .expect("a failed write must not wait for the timeout");
        assert_eq!(outcome.unwrap_err().code, CORE_EXITED);
        assert_eq!(pending(&rpc), 0);
    }

    #[tokio::test]
    async fn a_new_writer_takes_over() {
        let (rpc, old_lines) = client_with_capture();
        let (writer, new_lines) = capture_writer();
        rpc.set_writer(Some(Box::new(writer)));
        let r2 = rpc.clone();
        let call = tokio::spawn(async move {
            r2.call("core.hello", json!({}), Duration::from_secs(5))
                .await
        });
        wait_for_requests(&new_lines, 1).await;
        assert!(
            old_lines.lock().unwrap().is_empty(),
            "the replaced writer must not be used"
        );
        call.abort();

        rpc.set_writer(None);
        assert_eq!(
            rpc.call("core.hello", json!({}), Duration::from_secs(5))
                .await
                .unwrap_err()
                .code,
            CORE_EXITED
        );
    }

    #[tokio::test]
    async fn notifications_go_to_every_subscriber() {
        let rpc = RpcClient::new();
        let mut first = rpc.subscribe();
        let mut second = rpc.subscribe();
        rpc.handle_line(r#"{"jsonrpc":"2.0","method":"audio.level","params":{"level":0.4}}"#);
        rpc.notify_local(Notification {
            method: "core.restarted".into(),
            params: json!({}),
        });
        for receiver in [&mut first, &mut second] {
            let level = receiver.recv().await.unwrap();
            assert_eq!(level.method, "audio.level");
            assert_eq!(level.params, json!({"level": 0.4}));
            let restarted = receiver.recv().await.unwrap();
            assert_eq!(restarted.method, "core.restarted");
            assert_eq!(restarted.params, json!({}));
        }
    }

    #[test]
    fn handle_line_survives_noise() {
        let rpc = RpcClient::new();
        let mut notifications = rpc.subscribe();
        for line in [
            "",
            "   ",
            "not json",
            "[1,2]",
            r#"{"hello":"world"}"#,
            // a reply nobody waits for
            r#"{"jsonrpc":"2.0","id":99,"result":1}"#,
            // a Windows line ending left on the line
            "\r",
        ] {
            rpc.handle_line(line);
        }
        assert!(matches!(
            notifications.try_recv(),
            Err(tokio::sync::broadcast::error::TryRecvError::Empty)
        ));
    }

    #[test]
    fn handle_line_accepts_a_line_ending() {
        let rpc = RpcClient::new();
        let mut notifications = rpc.subscribe();
        rpc.handle_line("{\"jsonrpc\":\"2.0\",\"method\":\"audio.level\",\"params\":{}}\r\n");
        assert_eq!(notifications.try_recv().unwrap().method, "audio.level");
    }
}
