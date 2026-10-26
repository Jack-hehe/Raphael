//! The Python core as a child process: starting it, keeping it running, and tying its life to the
//! app's.
//!
//! [`CoreProcess::start`] runs the packaged core (or `python -m raphael_core` in development)
//! with stdin, stdout and stderr piped, inside
//! a Windows Job Object that kills it when the last handle to the job closes. The OS closes that
//! handle when the app exits, even by crashing, so the core cannot outlive the app.
//! (The core also exits by itself when its stdin closes, which happens at the same moment.)
//!
//! Requests go through an [`RpcClient`] to the core's stdin. One thread reads the core's stdout
//! (replies and notifications) and another its stderr (its log, passed on as `core: <line>`).
//!
//! When the core's stdout ends, the core is gone. The calls waiting for it fail at once with
//! `CORE_EXITED`, and so does every call made until it is back. A supervisor thread starts it
//! again after a second and then tells the subscribers `core.restarted`, so that whoever tracks the
//! state of a dictation can reset it. After 5 restarts within 60 s it gives up and sends
//! `core.down`.

use std::collections::VecDeque;
#[cfg(any(debug_assertions, test))]
use std::ffi::OsString;
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStderr, ChildStdout, Command, ExitStatus, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, mpsc};
use std::thread;
use std::time::{Duration, Instant};

use serde_json::{Value, json};
use tokio::sync::broadcast;

pub use crate::rpc::CoreApi;
use crate::rpc::{CORE_EXITED, Notification, RpcClient, RpcError, lock};

/// Sent to the subscribers each time the core has been started again, once `core.hello` has been
/// tried on the new process. If that process died at once (the log says so), another
/// `core.restarted`, or `core.down`, follows.
pub const CORE_RESTARTED: &str = "core.restarted";
/// Sent to the subscribers when the core has died too often and will not be started again.
pub const CORE_DOWN: &str = "core.down";
/// Sent to the subscribers the moment the core's output ends (it exited, or is about to), before
/// any restart: what the core was doing is over, and nothing done for it may go on.
pub const CORE_GONE: &str = "core.gone";

/// What is called, on the thread that reads the core's output, the moment that output ends.
type ExitHook = Box<dyn Fn() + Send + Sync>;

/// The protocol version this app speaks (`PROTOCOL_VERSION` in the core's `protocol.py`).
const PROTOCOL_VERSION: u64 = 1;

/// How long a call to `method` may take before it fails with `TIMEOUT`.
pub fn timeout_for(method: &str) -> Duration {
    let seconds = match method {
        // The last stretch of speech-to-text (the rest was transcribed while the recording went
        // on), then the cleanup request to the LLM. The first dictation after launch also waits
        // for the speech engine to load (about 31 s). The controller gives a long recording more
        // ([`dictation_stop_timeout`]): its cleanup takes longer.
        "dictation.stop" => 150,
        // A narrated conversation may work for thirty minutes before its final reply or next
        // permission question. Leave time for routing and transport; Esc still stops it earlier.
        "agent.ask" | "agent.confirm" => 1830,
        // Starts the plugin (a program that may first have to be fetched), reads its tools.
        "integrations.refresh" => 40,
        // Waits for the user to log in in the browser: up to five minutes (the core ends it at
        // 330 s with a kind of failure), or, for a login with a code (GitHub), until the code
        // runs out: 900 s at most, and the core ends it at 930 s. Then reads the tools.
        "integrations.connect" => 960,
        // Managed app OAuth leaves time for a human browser round trip and final tool discovery.
        "connectors.connect" => 780,
        "connectors.google.connect" => 180,
        "connectors.catalog"
        | "connectors.categories"
        | "connectors.status"
        | "connectors.disconnect"
        | "connectors.tools"
        | "connectors.set_tools"
        | "connectors.set_key" => 120,
        // Downloads speech models.
        "models.ensure" | "models.local_use" => 3600,
        _ => 15,
    };
    Duration::from_secs(seconds)
}

/// How long the `dictation.stop` of a recording that lasted `recorded` may take: the method's
/// usual time, which covers a recording of a few minutes, and a second more for every 3 s it
/// lasted. A recording has no limit, and its speech is transcribed while it goes on, so at the stop
/// only the last stretch is left to transcribe whatever its length; but the cleanup of the whole
/// text is given 10 s and a second for every 3 s of speech (`cleanup.timeout_for`, with no
/// ceiling), and that wait has to fit in this one.
pub fn dictation_stop_timeout(recorded: Duration) -> Duration {
    timeout_for("dictation.stop") + recorded / 3
}

/// What to run to start the core.
#[derive(Debug, Clone)]
pub struct CoreCommand {
    pub program: PathBuf,
    pub args: Vec<String>,
    pub cwd: PathBuf,
    /// Added to the environment this app was started with.
    pub env: Vec<(String, String)>,
}

/// The environment variable that replaces the interpreter that runs the core.
#[cfg(any(debug_assertions, test))]
const PYTHON_OVERRIDE: &str = "RAPHAEL_CORE_PYTHON";

/// Release builds run the packaged core from Tauri's resources. Development builds run
/// `core/.venv/Scripts/python.exe -m raphael_core` from this checkout; `RAPHAEL_CORE_PYTHON`
/// can replace that development interpreter.
pub fn resolve_app_core_command(app: &tauri::AppHandle) -> Result<CoreCommand, String> {
    #[cfg(debug_assertions)]
    {
        let _ = app;
        resolve_core_command()
    }
    #[cfg(not(debug_assertions))]
    {
        use tauri::Manager;

        let resources = app
            .path()
            .resource_dir()
            .map_err(|err| format!("could not locate the packaged core's resources: {err}"))?;
        packaged_command_in(&resources)
    }
}

/// Resolves a development checkout's core command. This entry point is absent from release apps.
#[cfg(any(debug_assertions, test))]
pub fn resolve_core_command() -> Result<CoreCommand, String> {
    command_in(&repo_root()?, std::env::var_os(PYTHON_OVERRIDE))
}

/// The repo root: two levels above this crate's `Cargo.toml`, which is in `app/src-tauri`.
#[cfg(any(debug_assertions, test))]
fn repo_root() -> Result<PathBuf, String> {
    let manifest_dir = env!("CARGO_MANIFEST_DIR");
    Path::new(manifest_dir)
        .ancestors()
        .nth(2)
        .map(Path::to_path_buf)
        .ok_or_else(|| format!("{manifest_dir} is not two levels below the repo root"))
}

#[cfg(any(debug_assertions, test))]
fn command_in(repo: &Path, python: Option<OsString>) -> Result<CoreCommand, String> {
    let core_dir = repo.join("core");
    if !core_dir.is_dir() {
        return Err(format!(
            "the core's directory {} does not exist",
            core_dir.display()
        ));
    }
    let program = match python.filter(|python| !python.is_empty()) {
        Some(python) => PathBuf::from(python),
        None => {
            let venv_python = core_dir.join(".venv").join("Scripts").join("python.exe");
            if !venv_python.is_file() {
                return Err(format!(
                    "the core's Python {} does not exist: run `uv sync` in {}, or set \
                     {PYTHON_OVERRIDE} to another interpreter",
                    venv_python.display(),
                    core_dir.display()
                ));
            }
            venv_python
        }
    };
    Ok(CoreCommand {
        program,
        args: vec!["-m".to_owned(), "raphael_core".to_owned()],
        cwd: core_dir,
        // Python encodes what it writes to a pipe in the machine's code page (cp950 on a Taiwan
        // Windows) unless told otherwise; its log is read here as UTF-8.
        env: vec![("PYTHONIOENCODING".to_owned(), "utf-8".to_owned())],
    })
}

#[cfg(any(not(debug_assertions), test))]
fn packaged_command_in(resources: &Path) -> Result<CoreCommand, String> {
    let core_dir = resources.join("core");
    let program = core_dir.join("raphael-core.exe");
    if !program.is_file() {
        return Err(format!(
            "the packaged core executable {} does not exist: reinstall Raphael",
            program.display()
        ));
    }
    let runtime_dir = core_dir.join("_internal");
    if !runtime_dir.is_dir() {
        return Err(format!(
            "the packaged core runtime directory {} does not exist: reinstall Raphael",
            runtime_dir.display()
        ));
    }
    Ok(CoreCommand {
        program,
        args: Vec::new(),
        cwd: core_dir,
        env: vec![("PYTHONIOENCODING".to_owned(), "utf-8".to_owned())],
    })
}

/// When the core is started again after it dies.
#[derive(Debug, Clone, Copy)]
struct RestartPolicy {
    /// How long to wait before starting it again.
    delay: Duration,
    /// How many restarts are allowed within `window`.
    max_restarts: usize,
    window: Duration,
}

impl RestartPolicy {
    const DEFAULT: Self = Self {
        delay: Duration::from_secs(1),
        max_restarts: 5,
        window: Duration::from_secs(60),
    };
}

/// Counts restarts over a sliding window.
struct RestartBudget {
    max: usize,
    window: Duration,
    taken: VecDeque<Instant>,
}

impl RestartBudget {
    fn new(max: usize, window: Duration) -> Self {
        Self {
            max,
            window,
            taken: VecDeque::new(),
        }
    }

    /// Whether a restart at `now` is allowed; if so, it is counted. A refusal counts for nothing.
    fn allow(&mut self, now: Instant) -> bool {
        while let Some(&oldest) = self.taken.front() {
            if now.saturating_duration_since(oldest) < self.window {
                break;
            }
            self.taken.pop_front();
        }
        if self.taken.len() >= self.max {
            return false;
        }
        self.taken.push_back(now);
        true
    }
}

/// The Job Object that the core runs in.
#[cfg(windows)]
mod job {
    use std::os::windows::io::AsRawHandle;
    use std::process::Child;

    use windows::Win32::Foundation::{CloseHandle, HANDLE};
    use windows::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION,
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, JOBOBJECT_BASIC_LIMIT_INFORMATION,
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JobObjectExtendedLimitInformation,
        SetInformationJobObject,
    };
    use windows::core::PCWSTR;

    /// A Job Object whose processes are killed when its last handle closes. The handle is closed
    /// when the `Job` is dropped, or by the OS when the app exits.
    pub struct Job(HANDLE);

    // SAFETY: a job handle refers to a kernel object that any thread may use.
    unsafe impl Send for Job {}
    // SAFETY: as above; the functions used on it are thread-safe.
    unsafe impl Sync for Job {}

    impl Job {
        pub fn new() -> Result<Self, String> {
            // SAFETY: no attributes and no name; the handle is owned by the `Job` made from it.
            let handle = unsafe { CreateJobObjectW(None, PCWSTR::null()) }
                .map_err(|err| format!("could not create the Job Object: {err}"))?;
            let job = Self(handle); // from here on, an error return closes the handle

            // KILL_ON_JOB_CLOSE is the point of the job. DIE_ON_UNHANDLED_EXCEPTION keeps Windows
            // Error Reporting from putting up a "has stopped working" window for a core that
            // crashed and leaving it alive behind it, which would stop the restart.
            let limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION {
                BasicLimitInformation: JOBOBJECT_BASIC_LIMIT_INFORMATION {
                    LimitFlags: JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                        | JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION,
                    ..Default::default()
                },
                ..Default::default()
            };
            // SAFETY: `limits` is a JOBOBJECT_EXTENDED_LIMIT_INFORMATION and the size says so.
            unsafe {
                SetInformationJobObject(
                    job.0,
                    JobObjectExtendedLimitInformation,
                    (&raw const limits).cast(),
                    size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
                )
            }
            .map_err(|err| format!("could not set up the Job Object: {err}"))?;
            Ok(job)
        }

        /// Puts `child`, and from now on everything it starts, into the job.
        pub fn add(&self, child: &Child) -> Result<(), String> {
            // SAFETY: both handles are valid; `child` keeps its process handle open.
            unsafe { AssignProcessToJobObject(self.0, HANDLE(child.as_raw_handle())) }
                .map_err(|err| err.to_string())
        }

        #[cfg(test)]
        pub fn handle(&self) -> HANDLE {
            self.0
        }
    }

    impl Drop for Job {
        fn drop(&mut self) {
            // SAFETY: the handle is ours and is closed only here. With the last handle to the job
            // closed, the OS kills the processes in it.
            let _ = unsafe { CloseHandle(self.0) };
        }
    }
}

/// Off Windows there is no Job Object. The core still ends when the app does, because it exits
/// when its stdin closes and the OS closes that pipe with the app.
#[cfg(not(windows))]
mod job {
    use std::process::Child;

    pub struct Job;

    impl Job {
        pub fn new() -> Result<Self, String> {
            Ok(Self)
        }

        pub fn add(&self, _child: &Child) -> Result<(), String> {
            Ok(())
        }
    }
}

use job::Job;

/// Keeps Windows from opening a console window for the core.
#[cfg(windows)]
fn hide_console_window(command: &mut Command) {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    command.creation_flags(CREATE_NO_WINDOW);
}

#[cfg(not(windows))]
fn hide_console_window(_command: &mut Command) {}

/// What the threads of one [`CoreProcess`] share.
struct Shared {
    command: CoreCommand,
    policy: RestartPolicy,
    rpc: RpcClient,
    /// Kept for as long as the app runs: the OS kills the core when the app's handle to it closes.
    job: Job,
    /// The running core. `None` between its death and its restart.
    child: Mutex<Option<Child>>,
    /// Set when the [`CoreProcess`] is dropped, so that nothing starts the core again.
    stopping: AtomicBool,
    /// Called the moment the core's output ends ([`CoreProcess::on_exit`]).
    exit_hooks: Mutex<Vec<ExitHook>>,
}

impl Shared {
    fn is_stopping(&self) -> bool {
        self.stopping.load(Ordering::SeqCst)
    }

    /// Kills the running core, if there is one.
    fn kill_child(&self) {
        if let Some(child) = lock(&self.child).as_mut() {
            let _ = child.kill();
        }
    }

    /// Takes the dead core's process out of its slot, makes sure it is gone and returns how it
    /// ended, if that is known.
    fn reap_child(&self) -> Option<ExitStatus> {
        let mut child = lock(&self.child).take()?;
        // Its stdout closed, so it has exited or is about to. Give it a moment to finish on its
        // own, so that the exit code that gets logged is its own.
        for _ in 0..20 {
            match child.try_wait() {
                Ok(Some(status)) => return Some(status),
                Ok(None) => thread::sleep(Duration::from_millis(10)),
                Err(_) => break,
            }
        }
        // Still running without a stdout: of no use to anyone.
        kill_and_reap(&mut child)
    }

    fn shut_down(&self) {
        self.stopping.store(true, Ordering::SeqCst);
        self.rpc.set_writer(None); // closes the core's stdin, which tells it to exit
        self.kill_child();
    }
}

fn kill_and_reap(child: &mut Child) -> Option<ExitStatus> {
    let _ = child.kill();
    child.wait().ok()
}

/// Starts one core process and connects it: job, request pipe, and the threads that read its
/// output. When this returns `Ok`, the stdout thread will send one `()` on `exits` when the core's
/// output ends. When it returns an error, nothing of the process is left and nothing is sent, so
/// there is never more than one core, or one pending exit, at a time.
fn spawn_core(shared: &Arc<Shared>, exits: &mpsc::Sender<()>) -> Result<(), String> {
    let command = &shared.command;
    let mut process = Command::new(&command.program);
    process
        .args(&command.args)
        .current_dir(&command.cwd)
        .envs(command.env.iter().map(|(name, value)| (name, value)))
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    hide_console_window(&mut process);

    log::info!(
        "starting the core: {} {}",
        command.program.display(),
        command.args.join(" ")
    );
    let mut child = process.spawn().map_err(|err| {
        format!(
            "could not start {} in {}: {err}",
            command.program.display(),
            command.cwd.display()
        )
    })?;
    let (Some(stdin), Some(stdout), Some(stderr)) =
        (child.stdin.take(), child.stdout.take(), child.stderr.take())
    else {
        kill_and_reap(&mut child);
        return Err("the core was started without its pipes".to_owned());
    };

    // Joined to the job at once. (A venv's python.exe is a launcher that starts the real
    // interpreter; the launcher has a kill-on-close job of its own, so the interpreter dies with
    // the launcher even if it was started before this call.)
    if let Err(err) = shared.job.add(&child) {
        if matches!(child.try_wait(), Ok(Some(_))) {
            // It failed at once and is gone, which a job cannot take. The restart logic handles
            // that like any other death.
            log::warn!("the core exited before it could join its Job Object ({err})");
        } else {
            kill_and_reap(&mut child);
            return Err(format!("could not put the core in its Job Object: {err}"));
        }
    }

    {
        let mut slot = lock(&shared.child);
        // Checked under the lock that `kill_child` takes: if the app is shutting down, either that
        // call finds this child in the slot and kills it, or this sees the flag.
        if shared.is_stopping() {
            kill_and_reap(&mut child);
            return Err("the core is being shut down".to_owned());
        }
        *slot = Some(child);
    }

    shared.rpc.set_writer(Some(Box::new(stdin)));
    let threads = thread::Builder::new()
        .name("core-stderr".to_owned())
        .spawn(move || log_stderr(stderr))
        .and_then(|_| {
            let (shared, exits) = (shared.clone(), exits.clone());
            thread::Builder::new()
                .name("core-stdout".to_owned())
                .spawn(move || read_stdout(&shared, stdout, &exits))
        });
    if let Err(err) = threads {
        shared.rpc.set_writer(None);
        if let Some(mut child) = lock(&shared.child).take() {
            kill_and_reap(&mut child);
        }
        return Err(format!(
            "could not start the threads that read the core's output: {err}"
        ));
    }
    Ok(())
}

/// Feeds the core's stdout to the RPC client until it ends, then cleans up after the core.
fn read_stdout(shared: &Shared, stdout: ChildStdout, exits: &mpsc::Sender<()>) {
    let mut lines = BufReader::new(stdout);
    let mut line = Vec::new();
    loop {
        line.clear();
        match lines.read_until(b'\n', &mut line) {
            Ok(0) => break,
            Ok(_) => shared.rpc.handle_line(&String::from_utf8_lossy(&line)),
            Err(err) => {
                log::error!("could not read the core's output: {err}");
                break;
            }
        }
    }
    // The core is gone. What was done for it stops first, here and now (a run on the desktop is
    // revoked before any job queued for it can act), then nothing may be written to it any more,
    // and the calls still waiting for an answer will never get one. This happens here, not in the
    // supervisor, which may be busy or waiting to restart: nothing must wait for the restart to
    // learn that the core died.
    for hook in lock(&shared.exit_hooks).iter() {
        hook();
    }
    shared.rpc.set_writer(None);
    shared
        .rpc
        .fail_all(RpcError::new(CORE_EXITED, "the core exited"));
    shared.rpc.notify_local(local_notification(CORE_GONE));
    // Err: the supervisor has stopped, because the app is shutting down or the core was given up.
    let _ = exits.send(());
}

/// Passes the core's stderr, which is where it logs, on to our log.
fn log_stderr(stderr: ChildStderr) {
    let mut lines = BufReader::new(stderr);
    let mut line = Vec::new();
    loop {
        line.clear();
        match lines.read_until(b'\n', &mut line) {
            Ok(0) | Err(_) => break,
            Ok(_) => {
                let text = String::from_utf8_lossy(&line);
                let text = text.trim_end();
                if !text.is_empty() {
                    log::info!("core: {text}");
                }
            }
        }
    }
}

/// Asks the core for its protocol version and logs the answer.
async fn say_hello(rpc: &RpcClient) {
    match rpc
        .call("core.hello", json!({}), timeout_for("core.hello"))
        .await
    {
        Ok(hello) => match hello.get("protocol").and_then(Value::as_u64) {
            Some(PROTOCOL_VERSION) => log::info!(
                "core.hello: protocol {PROTOCOL_VERSION}, core version {}",
                hello.get("version").and_then(Value::as_str).unwrap_or("?")
            ),
            other => log::error!(
                "the core speaks protocol {other:?}, but this app needs protocol {PROTOCOL_VERSION}"
            ),
        },
        Err(err) => log::error!("core.hello failed: {err}"),
    }
}

fn local_notification(method: &str) -> Notification {
    Notification {
        method: method.to_owned(),
        params: json!({}),
    }
}

/// Starts the core again whenever it dies, until it has died too often.
fn supervise(shared: &Arc<Shared>, exits: &mpsc::Receiver<()>, spawn_exits: &mpsc::Sender<()>) {
    let mut budget = RestartBudget::new(shared.policy.max_restarts, shared.policy.window);
    // `spawn_exits` keeps the channel open, so this waits for an exit for as long as it has to.
    while exits.recv().is_ok() {
        if shared.is_stopping() {
            return;
        }
        match shared.reap_child() {
            Some(status) => log::warn!("the core exited ({status})"),
            None => log::warn!("the core exited"),
        }

        loop {
            if !budget.allow(Instant::now()) {
                log::error!(
                    "the core exited more than {} times within {:?}; not starting it again",
                    shared.policy.max_restarts,
                    shared.policy.window
                );
                shared.rpc.notify_local(local_notification(CORE_DOWN));
                return;
            }
            thread::sleep(shared.policy.delay);
            if shared.is_stopping() {
                return;
            }
            log::info!("restarting the core");
            match spawn_core(shared, spawn_exits) {
                Ok(()) => break,
                Err(err) => log::error!("could not restart the core: {err}"),
            }
        }

        tauri::async_runtime::block_on(say_hello(&shared.rpc));
        shared.rpc.notify_local(local_notification(CORE_RESTARTED));
    }
}

/// The running core and everything that keeps it running. Dropping it stops the core for good.
pub struct CoreProcess {
    shared: Arc<Shared>,
}

impl CoreProcess {
    /// Starts the core and the thread that restarts it when it dies. Returns once the process has
    /// started, without waiting for it to be ready: `core.hello` is sent in the background and its
    /// answer is logged.
    pub fn start(command: CoreCommand) -> Result<Arc<Self>, String> {
        Self::start_with(command, RestartPolicy::DEFAULT)
    }

    fn start_with(command: CoreCommand, policy: RestartPolicy) -> Result<Arc<Self>, String> {
        let shared = Arc::new(Shared {
            command,
            policy,
            rpc: RpcClient::new(),
            job: Job::new()?,
            child: Mutex::new(None),
            stopping: AtomicBool::new(false),
            exit_hooks: Mutex::new(Vec::new()),
        });
        let (exits_tx, exits_rx) = mpsc::channel();
        spawn_core(&shared, &exits_tx)?;

        let supervisor = {
            let shared = shared.clone();
            thread::Builder::new()
                .name("core-supervisor".to_owned())
                .spawn(move || supervise(&shared, &exits_rx, &exits_tx))
        };
        if let Err(err) = supervisor {
            shared.shut_down();
            return Err(format!(
                "could not start the thread that restarts the core: {err}"
            ));
        }

        let hello = shared.clone();
        tauri::async_runtime::spawn(async move { say_hello(&hello.rpc).await });
        Ok(Arc::new(Self { shared }))
    }

    /// A receiver for the core's notifications (`audio.level`, ...) and for `core.gone`,
    /// `core.restarted` and `core.down`, which this app makes up. One that falls too far behind
    /// loses the oldest.
    pub fn subscribe(&self) -> broadcast::Receiver<Notification> {
        self.shared.rpc.subscribe()
    }

    /// Calls `hook` each time the core's output ends (it exited, or is about to), at once, on the
    /// thread that reads it, before the calls waiting on the core fail and before any restart. A
    /// hook must be quick and must not call the core.
    pub fn on_exit(&self, hook: impl Fn() + Send + Sync + 'static) {
        lock(&self.shared.exit_hooks).push(Box::new(hook));
    }
}

impl Drop for CoreProcess {
    fn drop(&mut self) {
        self.shared.shut_down();
    }
}

impl CoreApi for CoreProcess {
    async fn call(&self, method: &str, params: Value) -> Result<Value, RpcError> {
        self.shared
            .rpc
            .call(method, params, timeout_for(method))
            .await
    }

    async fn call_within(
        &self,
        method: &str,
        params: Value,
        timeout: Duration,
    ) -> Result<Value, RpcError> {
        self.shared.rpc.call(method, params, timeout).await
    }
}

#[cfg(test)]
impl CoreProcess {
    /// Kills the core's process the way a crash would.
    pub fn kill_child_for_test(&self) {
        self.shared.kill_child();
    }

    fn child_id_for_test(&self) -> Option<u32> {
        lock(&self.shared.child).as_ref().map(Child::id)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::{Value, json};
    use std::ffi::OsString;
    use std::time::{Duration, Instant};
    use tokio::sync::broadcast::{Receiver, error::RecvError, error::TryRecvError};

    // ---- the command that starts the core ----

    #[test]
    fn the_repo_root_holds_the_core() {
        assert!(
            repo_root()
                .unwrap()
                .join("core")
                .join("pyproject.toml")
                .is_file()
        );
    }

    /// A repo-shaped temp dir, with or without the core's virtual environment.
    fn fake_repo(with_venv: bool) -> tempfile::TempDir {
        let repo = tempfile::tempdir().unwrap();
        let scripts = repo.path().join("core").join(".venv").join("Scripts");
        std::fs::create_dir_all(&scripts).unwrap();
        if with_venv {
            std::fs::write(scripts.join("python.exe"), b"").unwrap();
        }
        repo
    }

    #[test]
    fn the_command_runs_the_core_module_from_the_venv() {
        let repo = fake_repo(true);
        let command = command_in(repo.path(), None).unwrap();
        let core_dir = repo.path().join("core");
        assert_eq!(
            command.program,
            core_dir.join(".venv").join("Scripts").join("python.exe")
        );
        assert_eq!(command.args, ["-m", "raphael_core"]);
        assert_eq!(command.cwd, core_dir);
        // Python writes its log to stderr in UTF-8 whatever the machine's code page is.
        assert!(
            command
                .env
                .contains(&("PYTHONIOENCODING".to_owned(), "utf-8".to_owned()))
        );
    }

    #[test]
    fn raphael_core_python_overrides_the_interpreter() {
        let repo = fake_repo(false); // no venv at all: the override is used as given
        let python = OsString::from(r"C:\Python312\python.exe");
        let command = command_in(repo.path(), Some(python)).unwrap();
        assert_eq!(command.program, PathBuf::from(r"C:\Python312\python.exe"));
        assert_eq!(command.args, ["-m", "raphael_core"]);
        assert_eq!(command.cwd, repo.path().join("core"));
    }

    #[test]
    fn an_empty_override_means_no_override() {
        let repo = fake_repo(true);
        let command = command_in(repo.path(), Some(OsString::new())).unwrap();
        assert!(command.program.ends_with("python.exe"));
        assert!(command.program.starts_with(repo.path()));
    }

    #[test]
    fn a_missing_venv_is_reported_with_the_way_out() {
        let repo = fake_repo(false);
        let err = command_in(repo.path(), None).unwrap_err();
        assert!(err.contains("python.exe"), "{err}");
        assert!(err.contains("uv sync"), "{err}");
        assert!(err.contains("RAPHAEL_CORE_PYTHON"), "{err}");
    }

    #[test]
    fn a_missing_core_directory_is_an_error() {
        let repo = tempfile::tempdir().unwrap(); // no core/ at all
        let err = command_in(repo.path(), Some(OsString::from("python"))).unwrap_err();
        assert!(err.contains("core"), "{err}");
    }

    #[test]
    fn packaged_core_runs_from_a_resource_directory_with_spaces() {
        let dir = tempfile::tempdir().unwrap();
        let resources = dir.path().join("Raphael install resources");
        let core_dir = resources.join("core");
        std::fs::create_dir_all(core_dir.join("_internal")).unwrap();
        std::fs::write(core_dir.join("raphael-core.exe"), b"").unwrap();

        let command = packaged_command_in(&resources).unwrap();
        assert_eq!(command.program, core_dir.join("raphael-core.exe"));
        assert_eq!(command.cwd, core_dir);
        assert!(command.args.is_empty());
        assert!(
            command
                .env
                .contains(&("PYTHONIOENCODING".to_owned(), "utf-8".to_owned()))
        );
    }

    #[test]
    fn packaged_core_missing_executable_cannot_fall_back_to_a_development_venv() {
        let resources = fake_repo(true);
        let err = packaged_command_in(resources.path()).unwrap_err();
        assert!(
            err.contains(
                &resources
                    .path()
                    .join("core")
                    .join("raphael-core.exe")
                    .display()
                    .to_string()
            ),
            "{err}"
        );
        assert!(!err.contains("uv sync"), "{err}");
        assert!(!err.contains(PYTHON_OVERRIDE), "{err}");
    }

    #[test]
    fn packaged_core_requires_the_adjacent_runtime_directory() {
        let resources = tempfile::tempdir().unwrap();
        let core_dir = resources.path().join("core");
        std::fs::create_dir_all(&core_dir).unwrap();
        std::fs::write(core_dir.join("raphael-core.exe"), b"").unwrap();

        let err = packaged_command_in(resources.path()).unwrap_err();
        assert!(
            err.contains(&core_dir.join("_internal").display().to_string()),
            "{err}"
        );
    }

    // ---- timeouts ----

    #[test]
    fn timeouts_follow_the_method() {
        assert_eq!(timeout_for("connectors.connect"), Duration::from_secs(780));
        assert_eq!(
            timeout_for("connectors.google.connect"),
            Duration::from_secs(180)
        );
        assert_eq!(timeout_for("connectors.status"), Duration::from_secs(120));
        assert_eq!(timeout_for("dictation.stop"), Duration::from_secs(150));
        assert_eq!(timeout_for("models.ensure"), Duration::from_secs(3600));
        assert_eq!(timeout_for("models.local_use"), Duration::from_secs(3600));
        assert_eq!(timeout_for("agent.ask"), Duration::from_secs(1830));
        assert_eq!(timeout_for("agent.confirm"), Duration::from_secs(1830));
        assert_eq!(timeout_for("integrations.refresh"), Duration::from_secs(40));
        assert_eq!(
            timeout_for("integrations.connect"),
            Duration::from_secs(960)
        );
        for method in [
            "secrets.set_openrouter_key",
            "status.check_openrouter",
            "core.hello",
            "history.list",
            "agent.cancel",
            "agent.history.list",
            "integrations.add",
            "integrations.connect_cancel",
            "integrations.catalog",
            "anything.else",
        ] {
            assert_eq!(timeout_for(method), Duration::from_secs(15), "{method}");
        }
    }

    #[test]
    fn the_stop_of_a_long_recording_is_given_time_for_its_cleanup() {
        let stop = |seconds| dictation_stop_timeout(Duration::from_secs(seconds));
        // A short dictation: the method's usual time.
        assert_eq!(stop(0), Duration::from_secs(150));
        assert_eq!(stop(30), Duration::from_secs(160));
        // An hour: the cleanup alone is given 10 s + 3600 / 3 (cleanup.timeout_for), which fits.
        assert_eq!(stop(3600), Duration::from_secs(1350));
        assert!(stop(3600) > Duration::from_secs(10 + 3600 / 3));
    }

    // ---- how often the core may be restarted ----

    #[test]
    fn five_restarts_a_minute_and_no_more() {
        let start = Instant::now();
        let at = |seconds| start + Duration::from_secs(seconds);
        let mut budget = RestartBudget::new(5, Duration::from_secs(60));
        for second in 0..5 {
            assert!(budget.allow(at(second)), "restart at {second} s");
        }
        // the sixth within the minute is refused, and refusing does not use up the budget
        assert!(!budget.allow(at(5)));
        assert!(!budget.allow(at(59)));
        // at 60 s the restart from 0 s is a minute old and no longer counts
        assert!(budget.allow(at(60)));
        assert!(!budget.allow(at(60)));
        assert!(budget.allow(at(61)));
    }

    // ---- a stand-in core: the test binary itself ----
    //
    // `fake_core_command` starts this test binary again and runs only `fake_core_entry`, which in
    // that child process (and only there, because of the environment variable) becomes a small
    // JSON-RPC server on stdio. That lets the process manager be tested with a real child process,
    // a real Job Object and real pipes, without Python. `fake_app_entry` does the same for the
    // app: a process that starts a core and waits to be killed.

    const FAKE_CORE: &str = "RAPHAEL_TEST_FAKE_CORE";
    /// A file the stand-in core adds a line to each time it starts.
    const FAKE_CORE_LOG: &str = "RAPHAEL_TEST_FAKE_CORE_LOG";
    const FAKE_APP: &str = "RAPHAEL_TEST_FAKE_APP";

    #[test]
    fn fake_core_entry() {
        let Ok(mode) = std::env::var(FAKE_CORE) else {
            return; // an ordinary test run
        };
        run_fake_core(&mode);
    }

    /// Writes one protocol line. A pipe that nobody reads any more is not an error here: the app
    /// that was reading may be the one a test has just killed.
    fn send(message: &Value) {
        use std::io::Write;
        let mut out = std::io::stdout().lock();
        let _ = writeln!(out, "{message}");
        let _ = out.flush();
    }

    /// `serve` answers requests until its stdin closes; `stubborn` does too but then carries on
    /// running; `die` exits at once.
    fn run_fake_core(mode: &str) -> ! {
        use std::io::{BufRead, Write};

        // The test harness has printed "test ... " without ending the line; end it, so that every
        // protocol line starts a line of its own.
        println!();
        eprintln!("fake core ({mode}) started");
        if let Some(log) = std::env::var_os(FAKE_CORE_LOG) {
            let mut log = std::fs::OpenOptions::new()
                .create(true)
                .append(true)
                .open(log)
                .unwrap();
            writeln!(log, "started").unwrap();
        }
        if mode == "die" {
            std::process::exit(3);
        }
        for line in std::io::stdin().lock().lines() {
            let Ok(line) = line else { break };
            let Ok(request) = serde_json::from_str::<Value>(&line) else {
                continue;
            };
            let id = request["id"].clone();
            let reply = match request["method"].as_str() {
                Some("core.hello") => {
                    json!({"jsonrpc": "2.0", "id": id, "result": {"protocol": 1, "version": "fake"}})
                }
                Some("test.echo") => {
                    json!({"jsonrpc": "2.0", "id": id, "result": request["params"]})
                }
                Some("test.notify") => {
                    send(
                        &json!({"jsonrpc": "2.0", "method": "audio.level", "params": {"level": 0.5}}),
                    );
                    json!({"jsonrpc": "2.0", "id": id, "result": {}})
                }
                Some("test.crash") => std::process::exit(7),
                _ => {
                    json!({"jsonrpc": "2.0", "id": id, "error": {"code": -32601, "message": "Method not found"}})
                }
            };
            send(&reply);
        }
        if mode == "stubborn" {
            // A core that never notices that its input has closed. (Not for ever, so that a test
            // that fails does not leave it running.)
            std::thread::sleep(Duration::from_secs(60));
            std::process::exit(1);
        }
        std::process::exit(0) // stdin closed: the real core exits then too
    }

    fn fake_core_command(mode: &str) -> CoreCommand {
        CoreCommand {
            program: std::env::current_exe().unwrap(),
            args: [
                "core_process::tests::fake_core_entry",
                "--exact",
                "--nocapture",
                "--test-threads=1",
            ]
            .map(String::from)
            .to_vec(),
            cwd: std::env::current_dir().unwrap(),
            env: vec![(FAKE_CORE.to_owned(), mode.to_owned())],
        }
    }

    /// The real restart policy, except for how long it waits before a restart.
    fn quick_restarts(delay_ms: u64) -> RestartPolicy {
        RestartPolicy {
            delay: Duration::from_millis(delay_ms),
            ..RestartPolicy::DEFAULT
        }
    }

    /// The next notification called `method`, skipping the others.
    async fn wait_for(
        notifications: &mut Receiver<Notification>,
        method: &str,
        within: Duration,
    ) -> Notification {
        let found = tokio::time::timeout(within, async {
            loop {
                match notifications.recv().await {
                    Ok(notification) if notification.method == method => return notification,
                    Ok(_) | Err(RecvError::Lagged(_)) => {}
                    Err(RecvError::Closed) => panic!("the notification channel closed"),
                }
            }
        })
        .await;
        found.unwrap_or_else(|_| panic!("no {method} notification within {within:?}"))
    }

    #[cfg(windows)]
    fn process_is_running(pid: u32) -> bool {
        use windows::Win32::Foundation::CloseHandle;
        use windows::Win32::System::Threading::{
            GetExitCodeProcess, OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION,
        };
        const STILL_ACTIVE: u32 = 259;
        // SAFETY: the handle is opened, used and closed here.
        unsafe {
            let Ok(process) = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid) else {
                return false; // no such process any more
            };
            let mut code = 0;
            let alive = GetExitCodeProcess(process, &mut code).is_ok() && code == STILL_ACTIVE;
            let _ = CloseHandle(process);
            alive
        }
    }

    #[cfg(windows)]
    fn process_is_in_job(pid: u32, job: &Job) -> bool {
        use windows::Win32::Foundation::CloseHandle;
        use windows::Win32::System::JobObjects::IsProcessInJob;
        use windows::Win32::System::Threading::{OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION};
        use windows::core::BOOL;
        // SAFETY: the handle is opened, used and closed here; the job's handle outlives the call.
        unsafe {
            let Ok(process) = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid) else {
                return false;
            };
            let mut in_job = BOOL(0);
            let known = IsProcessInJob(process, Some(job.handle()), &mut in_job).is_ok();
            let _ = CloseHandle(process);
            known && in_job.as_bool()
        }
    }

    #[tokio::test]
    async fn talks_to_the_core_it_started() {
        let core = CoreProcess::start(fake_core_command("serve")).unwrap();
        let hello = core.call("core.hello", json!({})).await.unwrap();
        assert_eq!(hello["protocol"], 1);

        // text survives the pipes both ways
        let echoed = core
            .call("test.echo", json!({"text": "明天下午三點開會"}))
            .await
            .unwrap();
        assert_eq!(echoed, json!({"text": "明天下午三點開會"}));

        // an error from the core is the call's error
        let err = core.call("no.such.method", json!({})).await.unwrap_err();
        assert_eq!(err.code, -32601);
    }

    #[tokio::test]
    async fn notifications_from_the_core_reach_subscribers() {
        let core = CoreProcess::start(fake_core_command("serve")).unwrap();
        let mut notifications = core.subscribe();
        // the stand-in core sends audio.level before it answers
        core.call("test.notify", json!({})).await.unwrap();
        let level = wait_for(&mut notifications, "audio.level", Duration::from_secs(5)).await;
        assert_eq!(level.params, json!({"level": 0.5}));
    }

    #[tokio::test]
    async fn restarts_a_core_that_crashed() {
        // With the real restart policy: wait 1 s, start again.
        let core = CoreProcess::start(fake_core_command("serve")).unwrap();
        let mut notifications = core.subscribe();
        assert_eq!(
            core.call("core.hello", json!({})).await.unwrap()["protocol"],
            1
        );

        // The core dies while it has a call to answer: that call ends at once, not at its timeout.
        let crashed = core.call("test.crash", json!({})).await.unwrap_err();
        assert_eq!(crashed.code, CORE_EXITED);

        // Until the new core is up, calls fail at once too, instead of writing to a dead pipe.
        let asked = Instant::now();
        let during = core.call("core.hello", json!({})).await.unwrap_err();
        assert_eq!(during.code, CORE_EXITED);
        assert!(asked.elapsed() < Duration::from_millis(500));

        let restarted = wait_for(&mut notifications, CORE_RESTARTED, Duration::from_secs(10)).await;
        assert_eq!(restarted.params, json!({}));
        assert_eq!(
            core.call("core.hello", json!({})).await.unwrap()["protocol"],
            1
        );
    }

    #[cfg(windows)]
    #[tokio::test]
    async fn what_was_done_for_a_core_that_died_stops_at_once_not_at_its_restart() {
        // a long wait before the restart: the hooks and `core.gone` come long before it
        let core =
            CoreProcess::start_with(fake_core_command("serve"), quick_restarts(5_000)).unwrap();
        let mut notifications = core.subscribe();
        core.call("core.hello", json!({})).await.unwrap();
        let revoked = Arc::new(AtomicBool::new(false));
        let seen = revoked.clone();
        core.on_exit(move || seen.store(true, Ordering::SeqCst));
        let killed = Instant::now();

        core.kill_child_for_test();

        wait_for(&mut notifications, CORE_GONE, Duration::from_secs(4)).await;
        assert!(
            revoked.load(Ordering::SeqCst),
            "the hook ran before core.gone"
        );
        assert!(killed.elapsed() < Duration::from_secs(4));
        // nothing of the restart yet
        assert!(matches!(notifications.try_recv(), Err(TryRecvError::Empty)));
    }

    #[cfg(windows)]
    #[tokio::test]
    async fn restarts_a_core_that_was_killed() {
        let core = CoreProcess::start_with(fake_core_command("serve"), quick_restarts(50)).unwrap();
        let mut notifications = core.subscribe();
        core.call("core.hello", json!({})).await.unwrap();
        let first = core.child_id_for_test().unwrap();

        core.kill_child_for_test();

        wait_for(&mut notifications, CORE_RESTARTED, Duration::from_secs(10)).await;
        assert_eq!(
            core.call("core.hello", json!({})).await.unwrap()["protocol"],
            1
        );
        assert_ne!(core.child_id_for_test().unwrap(), first);
        assert!(!process_is_running(first));
    }

    #[tokio::test]
    async fn gives_up_after_five_restarts_and_says_so() {
        let core = CoreProcess::start_with(fake_core_command("die"), quick_restarts(100)).unwrap();
        let mut notifications = core.subscribe();

        let (mut restarts, mut gone) = (0, 0);
        loop {
            let next = tokio::time::timeout(Duration::from_secs(20), notifications.recv())
                .await
                .expect("the core should be given up on within 20 s")
                .unwrap();
            match next.method.as_str() {
                CORE_GONE => gone += 1,
                CORE_RESTARTED => restarts += 1,
                CORE_DOWN => break,
                other => panic!("unexpected notification {other}"),
            }
        }
        assert_eq!(restarts, 5);
        // each death is told at once: the first core's, and each restarted one's
        assert_eq!(gone, 6);

        // after that, calls fail at once and nothing starts the core again
        let err = core.call("core.hello", json!({})).await.unwrap_err();
        assert_eq!(err.code, CORE_EXITED);
        tokio::time::sleep(Duration::from_millis(500)).await;
        assert!(matches!(notifications.try_recv(), Err(TryRecvError::Empty)));
    }

    #[test]
    fn a_program_that_cannot_start_is_an_error_not_a_panic() {
        let command = CoreCommand {
            program: PathBuf::from(r"Z:\no\such\python.exe"),
            args: vec!["-m".to_owned(), "raphael_core".to_owned()],
            cwd: std::env::temp_dir(),
            env: Vec::new(),
        };
        let Err(err) = CoreProcess::start(command) else {
            panic!("a program that does not exist cannot start");
        };
        assert!(err.contains(r"Z:\no\such\python.exe"), "{err}");
    }

    #[cfg(windows)]
    #[tokio::test]
    async fn dropping_the_process_manager_stops_the_core_for_good() {
        let dir = tempfile::tempdir().unwrap();
        let starts = dir.path().join("starts.log");
        let mut command = fake_core_command("serve");
        command
            .env
            .push((FAKE_CORE_LOG.to_owned(), starts.display().to_string()));
        let core = CoreProcess::start_with(command, quick_restarts(50)).unwrap();
        core.call("core.hello", json!({})).await.unwrap();
        let pid = core.child_id_for_test().unwrap();
        assert!(process_is_running(pid));

        drop(core);

        let gone = tokio::time::timeout(Duration::from_secs(5), async {
            while process_is_running(pid) {
                tokio::time::sleep(Duration::from_millis(20)).await;
            }
        })
        .await;
        assert!(
            gone.is_ok(),
            "the core is still running after its manager was dropped"
        );

        // It is not started again either, though a restart would only wait 50 ms.
        tokio::time::sleep(Duration::from_millis(600)).await;
        let started = std::fs::read_to_string(&starts).unwrap().lines().count();
        assert_eq!(started, 1, "the core was started again after the drop");
    }

    #[cfg(windows)]
    #[tokio::test]
    async fn every_core_runs_inside_the_job() {
        let core = CoreProcess::start_with(fake_core_command("serve"), quick_restarts(50)).unwrap();
        let mut notifications = core.subscribe();
        core.call("core.hello", json!({})).await.unwrap();
        let first = core.child_id_for_test().unwrap();
        assert!(process_is_in_job(first, &core.shared.job));

        // and so is the core that replaces it
        core.kill_child_for_test();
        wait_for(&mut notifications, CORE_RESTARTED, Duration::from_secs(10)).await;
        let second = core.child_id_for_test().unwrap();
        assert_ne!(second, first);
        assert!(process_is_in_job(second, &core.shared.job));
    }

    /// The app as a process of its own: starts a core that ignores the end of its input, reports
    /// the core's process id, and then waits until it is killed (or its stdin closes, which
    /// happens if the test dies).
    #[test]
    fn fake_app_entry() {
        use std::io::Write;

        if std::env::var_os(FAKE_APP).is_none() {
            return; // an ordinary test run
        }
        let core = CoreProcess::start(fake_core_command("stubborn")).unwrap();
        // Report only once the core is up and idle, waiting for its next request.
        let hello = tauri::async_runtime::block_on(core.call("core.hello", json!({}))).unwrap();
        assert_eq!(hello["protocol"], 1);
        println!();
        println!("CORE_PID {}", core.child_id_for_test().unwrap());
        std::io::stdout().flush().unwrap();
        let _ = std::io::stdin().lock().lines().next();
        std::process::exit(0);
    }

    #[cfg(windows)]
    #[test]
    fn the_core_dies_with_the_app_even_if_the_app_crashes() {
        let mut app = Command::new(std::env::current_exe().unwrap())
            .args([
                "core_process::tests::fake_app_entry",
                "--exact",
                "--nocapture",
                "--test-threads=1",
            ])
            .env(FAKE_APP, "1")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .spawn()
            .unwrap();
        // Read on a thread, so that an app that hangs cannot hang the test.
        let output = BufReader::new(app.stdout.take().unwrap());
        let (reported, core_pid) = mpsc::channel();
        thread::spawn(move || {
            for line in output.lines().map_while(Result::ok) {
                if let Some(pid) = line.strip_prefix("CORE_PID ") {
                    let _ = reported.send(pid.trim().parse::<u32>().unwrap());
                    return;
                }
            }
        });
        let core_pid = core_pid
            .recv_timeout(Duration::from_secs(10))
            .expect("the app should report the process id of its core");
        assert!(process_is_running(core_pid));

        // The app "crashes": TerminateProcess runs no destructor and no shutdown code. (The core
        // here ignores the end of its input, so only the job can end it.)
        app.kill().unwrap();
        app.wait().unwrap();

        let deadline = Instant::now() + Duration::from_secs(5);
        while process_is_running(core_pid) {
            assert!(Instant::now() < deadline, "the core outlived the app");
            thread::sleep(Duration::from_millis(20));
        }
    }

    // Needs core/.venv (`uv sync` in core/), whose Python runs `python -m raphael_core`:
    // `cargo test -- --ignored restarts_after_the_core_dies`
    #[tokio::test]
    #[ignore = "needs the real core: core/.venv and `python -m raphael_core`"]
    async fn restarts_after_the_core_dies() {
        let data_dir = tempfile::tempdir().unwrap();
        let mut command = resolve_core_command().unwrap();
        command.env.push((
            "RAPHAEL_DATA_DIR".to_owned(),
            data_dir.path().display().to_string(),
        ));
        let core = CoreProcess::start(command).unwrap();
        let mut notifications = core.subscribe();

        // core.hello → protocol 1
        let hello = core.call("core.hello", json!({})).await.unwrap();
        assert_eq!(hello["protocol"], 1);

        // kill the child; expect a "core.restarted" notification within 5 s
        core.kill_child_for_test();
        wait_for(&mut notifications, CORE_RESTARTED, Duration::from_secs(5)).await;

        // core.hello works again
        let hello = core.call("core.hello", json!({})).await.unwrap();
        assert_eq!(hello["protocol"], 1);
    }
}

#[cfg(all(test, windows))]
mod job_tests {
    use super::job::Job;
    use std::os::windows::io::AsRawHandle;
    use std::os::windows::process::CommandExt;
    use std::process::{Child, Command, Stdio};
    use std::time::{Duration, Instant};
    use windows::Win32::Foundation::HANDLE;
    use windows::Win32::System::JobObjects::IsProcessInJob;
    use windows::core::BOOL;

    /// A process that lives until it is killed, with a grandchild (`ping`) as `cmd` runs it.
    fn sleeper() -> Child {
        Command::new("cmd")
            .args(["/c", "ping", "-n", "60", "127.0.0.1"])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .creation_flags(0x0800_0000) // CREATE_NO_WINDOW
            .spawn()
            .unwrap()
    }

    #[test]
    fn a_process_in_the_job_dies_when_the_job_closes() {
        let job = Job::new().unwrap();
        let mut child = sleeper();
        job.add(&child).unwrap();

        let mut in_job = BOOL(0);
        // SAFETY: both handles are valid for the call.
        unsafe {
            IsProcessInJob(
                HANDLE(child.as_raw_handle()),
                Some(job.handle()),
                &mut in_job,
            )
            .unwrap();
        }
        assert!(in_job.as_bool(), "the child should be in the job");
        assert!(
            child.try_wait().unwrap().is_none(),
            "it runs while the job is open"
        );

        // What the OS does when the app exits or crashes: close the last handle to the job.
        drop(job);

        // (The exit status says nothing here: the OS ends the processes of a closed job with 0.)
        let deadline = Instant::now() + Duration::from_secs(5);
        while child.try_wait().unwrap().is_none() {
            assert!(Instant::now() < deadline, "the child outlived its job");
            std::thread::sleep(Duration::from_millis(20));
        }
    }
}
