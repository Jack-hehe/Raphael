//! One long-lived OS thread owns the Windows execution-state request. Windows associates this
//! request with the calling thread, so applying it on arbitrary async pool threads is incorrect.
//! The worker acknowledges changes after Windows accepts them and the preference is saved. The
//! OS clears the request when this process exits; turning it off explicitly restores ES_CONTINUOUS.
use std::sync::{OnceLock, mpsc};
use std::time::Duration;

type Reply = mpsc::Sender<Result<bool, String>>;
enum Request { Get(Reply), Set(bool, Reply) }
static WORKER: OnceLock<Result<mpsc::Sender<Request>, String>> = OnceLock::new();

fn flags(enabled: bool) -> u32 { 0x80000000 | if enabled { 1 } else { 0 } }

#[cfg(windows)]
fn apply(enabled: bool) -> Result<(), String> {
    use windows::Win32::System::Power::{SetThreadExecutionState, EXECUTION_STATE};
    let previous = unsafe { SetThreadExecutionState(EXECUTION_STATE(flags(enabled))) };
    if previous.0 == 0 { Err("Windows could not change the scheduler awake request".into()) } else { Ok(()) }
}
#[cfg(not(windows))]
fn apply(enabled: bool) -> Result<(), String> {
    if enabled { Err("keeping the system awake is supported on Windows".into()) } else { Ok(()) }
}

fn worker() -> Result<&'static mpsc::Sender<Request>, String> {
    WORKER.get_or_init(|| {
        let path = crate::device_settings::data_dir()?.join("schedule-awake.json");
        let saved = match std::fs::read(&path) {
            Ok(bytes) => serde_json::from_slice::<bool>(&bytes).unwrap_or(false),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => false,
            Err(error) => return Err(format!("could not read scheduler awake setting: {error}")),
        };
        let (send, receive) = mpsc::channel();
        std::thread::Builder::new().name("scheduler-awake".into()).spawn(move || {
            let mut enabled = saved && apply(true).is_ok();
            for request in receive {
                match request {
                    Request::Get(reply) => { let _ = reply.send(Ok(enabled)); }
                    Request::Set(value, reply) => {
                        let result = apply(value).and_then(|()| {
                            let save = || -> Result<(), String> {
                                if let Some(parent) = path.parent() { std::fs::create_dir_all(parent).map_err(|e| e.to_string())?; }
                                let part = path.with_extension("part");
                                std::fs::write(&part, if value { b"true".as_slice() } else { b"false".as_slice() }).map_err(|e| e.to_string())?;
                                std::fs::rename(&part, &path).map_err(|e| e.to_string())?;
                                Ok(())
                            };
                            if let Err(error) = save() { let _ = apply(enabled); return Err(error); }
                            enabled = value;
                            Ok(value)
                        });
                        let _ = reply.send(result);
                    }
                }
            }
            let _ = apply(false);
        }).map_err(|e| e.to_string())?;
        Ok(send)
    }).as_ref().map_err(Clone::clone)
}
fn request(make: impl FnOnce(Reply) -> Request) -> Result<bool, String> {
    let (send, receive) = mpsc::channel();
    worker()?.send(make(send)).map_err(|e| e.to_string())?;
    receive.recv_timeout(Duration::from_secs(5)).map_err(|e| e.to_string())?
}
pub fn get() -> Result<bool, String> { request(Request::Get) }
pub fn set(enabled: bool) -> Result<bool, String> { request(|reply| Request::Set(enabled, reply)) }

#[cfg(test)]
mod tests {
    #[test]
    fn awake_request_keeps_system_running_without_forcing_display_on() {
        assert_eq!(super::flags(true), 0x80000001);
        assert_eq!(super::flags(false), 0x80000000);
        assert_eq!(super::flags(true) & 2, 0);
    }
    #[cfg(windows)]
    #[test]
    fn windows_accepts_and_releases_the_request_on_its_owner_thread() {
        std::thread::spawn(|| { super::apply(true).unwrap(); super::apply(false).unwrap(); }).join().unwrap();
    }
}
