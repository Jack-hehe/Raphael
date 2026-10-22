//! The shell's half of a run in the app: the core's requests answered, its states shown, the
//! owner's input watched.
//!
//! [`spawn`] starts the desktop's thread ([`super::uia`]) and a task that listens to the core's
//! notifications:
//!
//! - `computer.request`: checked ([`super::parse_request`]) and given its [`Ticket`] at once, in
//!   the order the core's notifications come in, then done on a blocking thread (the desktop's
//!   calls wait for UI Automation), and answered with `computer.reply`. `allow` adds the app to
//!   the always-allowed list of the device settings (`commands::allow_computer_app`).
//! - `computer.state`: `begin` makes its run the shell's ([`Runs`]), puts the watch's hooks in and
//!   arms them, and the overlay shows 「操作中」 with the step (when the hooks do not go in, the
//!   run is refused at once instead: nothing of it reaches the desktop, every request of it fails
//!   with `watch`, and the core ends it with that reason); `step` changes the step on the
//!   overlay (and arms the watch again after a question); `pause` disarms the watch for the
//!   question on screen; `end` revokes the run before anything else (no job of it does anything
//!   more on the desktop), takes the hooks out, drops the elements the looks kept and, when the
//!   core sends a line (the run was stopped by Esc, which hid the overlay), says it as a notice.
//!   A state of a run that is not the shell's (the late `end` of a run a newer one replaced)
//!   changes nothing.
//! - the core restarting or going away ends a run that was under way, as `end` would.
//!
//! When the watch sees the owner's own input, the run is revoked there and then, on the hook's
//! thread, and the core is told with `computer.interrupt`.

use std::sync::Arc;

use serde_json::{Value, json};
use tauri::{AppHandle, Manager};
use tokio::sync::broadcast::error::RecvError;
use tokio::sync::mpsc;

use super::uia::UiaDesktop;
use super::watch::hook::Watch;
use super::{Failure, Phase, Request, Runs, Service, Ticket, parse_request, parse_state, reply};
use crate::core_process::{CORE_DOWN, CORE_RESTARTED, CoreProcess};
use crate::device_settings::DeviceStore;
use crate::overlay::{Overlay, OverlayApi};

type DesktopService = Service<UiaDesktop>;

/// Starts the shell's half of a run, if the core is running. Without a UI Automation client the
/// requests are answered with a failure, and the core says it cannot operate the computer.
pub fn spawn(app: &AppHandle) {
    let Some(core) = app
        .try_state::<Arc<CoreProcess>>()
        .map(|core| core.inner().clone())
    else {
        return;
    };
    let service =
        UiaDesktop::start().map(|desktop| Arc::new(Service::new(desktop, std::process::id())));
    let overlay = app
        .try_state::<Arc<Overlay>>()
        .map(|overlay| overlay.inner().clone());
    let notifications = core.subscribe();
    let app = app.clone();
    let runs = Arc::new(Runs::new());
    tauri::async_runtime::spawn(run(app, core, service, overlay, runs, notifications));
}

/// The watch while a run is under way, and whether a question has it disarmed.
struct Running {
    watch: Option<Watch>,
    paused: bool,
}

async fn run(
    app: AppHandle,
    core: Arc<CoreProcess>,
    service: Option<Arc<DesktopService>>,
    overlay: Option<Arc<Overlay>>,
    runs: Arc<Runs>,
    mut notifications: tokio::sync::broadcast::Receiver<crate::rpc::Notification>,
) {
    let (interrupts, mut interrupted) = mpsc::unbounded_channel::<()>();
    let mut running: Option<Running> = None;
    loop {
        tokio::select! {
            notification = notifications.recv() => match notification {
                Ok(notification) => match notification.method.as_str() {
                    "computer.request" => {
                        let Some(request) = parse_request(&notification.params) else {
                            log::warn!("computer: a request that cannot be read");
                            continue;
                        };
                        // Here, in the order of the notifications: a request the core sent
                        // before the run's `end` is of the run, and dies with it.
                        let ticket = runs.ticket(request.run.as_deref());
                        let (app, core, service) = (app.clone(), core.clone(), service.clone());
                        tauri::async_runtime::spawn(async move {
                            answer(&app, &core, service, request, ticket).await;
                        });
                    }
                    "computer.state" => {
                        let Some((phase, run)) = parse_state(&notification.params) else {
                            log::warn!("computer: a state of a run that cannot be read");
                            continue;
                        };
                        let shown = Shown {
                            interrupts: &interrupts,
                            overlay: overlay.as_deref(),
                            service: service.as_ref(),
                            runs: &runs,
                        };
                        on_state(phase, &run, &mut running, &shown);
                    }
                    CORE_RESTARTED | CORE_DOWN if running.is_some() => {
                        log::info!("computer: the core went away during a run: it is over");
                        runs.revoke();
                        finish(&mut running, None, None, service.as_ref());
                    }
                    _ => {}
                },
                Err(RecvError::Lagged(missed)) => {
                    log::warn!("computer: missed {missed} notifications of the core");
                }
                Err(RecvError::Closed) => break,
            },
            Some(()) = interrupted.recv() => {
                log::info!("computer: the owner's own input stops the run");
                let core = core.clone();
                tauri::async_runtime::spawn(async move {
                    if let Err(err) = crate::rpc::CoreApi::call(core.as_ref(), "computer.interrupt", json!({})).await {
                        log::warn!("computer: computer.interrupt failed (code {})", err.code);
                    }
                });
            }
        }
    }
}

/// What a state of a run reaches.
struct Shown<'a> {
    interrupts: &'a mpsc::UnboundedSender<()>,
    overlay: Option<&'a Overlay>,
    service: Option<&'a Arc<DesktopService>>,
    runs: &'a Arc<Runs>,
}

fn on_state(phase: Phase, run: &str, running: &mut Option<Running>, shown: &Shown<'_>) {
    let Shown {
        interrupts,
        overlay,
        service,
        runs,
    } = *shown;
    if matches!(phase, Phase::Step(_) | Phase::Pause) && !runs.is_active(run) {
        // A step of a run revoked by the owner's own input, sent before the core heard of it:
        // the watch is not armed again for it.
        log::info!("computer: a state of a run that is not the shell's");
        return;
    }
    match phase {
        Phase::Begin(line) => {
            runs.begin(run);
            let told = interrupts.clone();
            let revoked = runs.clone();
            let watch = running.take().and_then(|old| old.watch).or_else(|| {
                Watch::start(Box::new(move || {
                    // Nothing more of the run reaches the desktop from now, whatever the core
                    // is doing when it hears of it.
                    revoked.revoke();
                    let _ = told.send(());
                }))
            });
            let Some(watch) = watch else {
                // The owner could not stop a run by touching the mouse or the keys: none is
                // operated. Every request of it fails with `watch`, and the core ends it.
                log::warn!("computer: the watch on the owner's input did not start: no run");
                runs.refuse(run);
                *running = None;
                return;
            };
            watch.arm();
            *running = Some(Running {
                watch: Some(watch),
                paused: false,
            });
            log::info!("computer: a run begins");
            if let Some(overlay) = overlay {
                overlay.operate(&line);
            }
        }
        Phase::Step(line) => {
            if let Some(run) = running.as_mut()
                && run.paused
            {
                run.paused = false;
                if let Some(watch) = &run.watch {
                    watch.arm();
                }
            }
            if let Some(overlay) = overlay {
                overlay.operate(&line);
            }
        }
        Phase::Pause => {
            if let Some(run) = running.as_mut() {
                run.paused = true;
                if let Some(watch) = &run.watch {
                    watch.disarm();
                }
            }
        }
        Phase::End(line) => {
            // Revoked first, at once: a step still queued for the desktop, or under way there,
            // does nothing more.
            if !runs.end(run) {
                log::info!("computer: the end of a run that a newer one replaced");
                return;
            }
            finish(running, line, overlay, service);
        }
    }
}

/// The run is over (revoked already): the watch's hooks out, the elements the looks kept dropped,
/// and the line the core sent said.
fn finish(
    running: &mut Option<Running>,
    line: Option<String>,
    overlay: Option<&Overlay>,
    service: Option<&Arc<DesktopService>>,
) {
    if let Some(Running {
        watch: Some(watch), ..
    }) = running.take()
    {
        watch.stop();
    }
    if let Some(service) = service {
        let service = service.clone();
        tauri::async_runtime::spawn_blocking(move || service.desktop_forget());
    }
    log::info!("computer: the run is over");
    if let (Some(line), Some(overlay)) = (line, overlay) {
        overlay.notice(&line);
    }
}

/// Does one request of the core's, for the run of `ticket`, and sends the reply.
async fn answer(
    app: &AppHandle,
    core: &CoreProcess,
    service: Option<Arc<DesktopService>>,
    request: Request,
    ticket: Ticket,
) {
    let Request { id, op, args, .. } = request;
    let outcome = if op == "allow" {
        allow(app, &args).await
    } else if let Some(service) = service {
        let allowed: Vec<String> = match app.try_state::<DeviceStore>() {
            Some(store) => store
                .get()
                .await
                .computer_apps
                .into_iter()
                .map(|a| a.exe)
                .collect(),
            None => Vec::new(),
        };
        let op_name = op.clone();
        tauri::async_runtime::spawn_blocking(move || {
            service.handle(&op_name, &args, &allowed, &ticket)
        })
        .await
        .unwrap_or(Err(Failure::Failed))
    } else {
        Err(Failure::Failed)
    };
    if let Err(failure) = &outcome {
        log::info!("computer: {op} failed ({})", failure.kind());
    }
    if let Err(err) = crate::rpc::CoreApi::call(core, "computer.reply", reply(&id, outcome)).await {
        log::warn!("computer: computer.reply failed (code {})", err.code);
    }
}

async fn allow(app: &AppHandle, args: &Value) -> Result<Value, Failure> {
    let text = |name: &str| args.get(name).and_then(Value::as_str).unwrap_or_default();
    match crate::commands::allow_computer_app(app, text("exe"), text("name")).await {
        Ok(()) => Ok(json!({})),
        Err(_) => Err(Failure::BadRequest),
    }
}

impl DesktopService {
    fn desktop_forget(&self) {
        use super::Desktop;
        self.desktop().forget();
    }
}
