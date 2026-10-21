//! Two launches of the app, as processes of their own (this test executable, started again), with
//! the app's first plugins on Tauri's mock runtime and an identifier that no other run uses, so
//! the test never meets an installed or running Raphael.
#![cfg(windows)]

use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

/// Set in the launches: `first` or `second`.
const LAUNCH: &str = "RAPHAEL_TEST_LAUNCH";
/// The identifier both launches use, which names the single-instance lock.
const IDENTIFIER: &str = "RAPHAEL_TEST_LAUNCH_IDENTIFIER";
/// The folder where the launches leave files that say what they did.
const MARKS: &str = "RAPHAEL_TEST_LAUNCH_MARKS";

/// One launch: starts the first plugins the way the app does, with a stand-in for the data
/// migration that says it ran. The first launch then serves its window messages, which is how a
/// second launch reaches it, until the test is done.
#[test]
fn launch_entry() {
    let Ok(launch) = std::env::var(LAUNCH) else {
        return; // an ordinary test run
    };
    let marks = PathBuf::from(std::env::var_os(MARKS).unwrap());
    let mut context = tauri::test::mock_context(tauri::test::noop_assets());
    context.config_mut().identifier = std::env::var(IDENTIFIER).unwrap();
    let told = marks.join("first told");
    let started = marks.join(format!("{launch} started"));
    let _app = app_lib::startup_plugins_for_tests(
        tauri::test::mock_builder(),
        move |_app, args| fs::write(&told, args.join("\n")).unwrap(),
        move || fs::write(&started, "").unwrap(),
    )
    .build(context)
    .unwrap();
    if launch == "second" {
        // It should have handed over and exited inside `build`.
        std::process::exit(3);
    }
    serve_messages_until(&marks.join("done"));
    std::process::exit(0);
}

fn serve_messages_until(done: &Path) {
    use windows::Win32::UI::WindowsAndMessaging::{
        DispatchMessageW, MSG, PM_REMOVE, PeekMessageW, TranslateMessage,
    };

    let deadline = Instant::now() + Duration::from_secs(60);
    while !done.exists() && Instant::now() < deadline {
        let mut message = MSG::default();
        // SAFETY: the message is read into a local and dispatched on this thread, which owns the
        // windows it can be for.
        while unsafe { PeekMessageW(&mut message, None, 0, 0, PM_REMOVE) }.as_bool() {
            unsafe {
                let _ = TranslateMessage(&message);
                DispatchMessageW(&message);
            }
        }
        std::thread::sleep(Duration::from_millis(10));
    }
}

/// A launch that is killed if the test ends first.
struct Launch(Child);

impl Drop for Launch {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

fn launch(which: &str, identifier: &str, marks: &Path) -> Launch {
    Launch(
        Command::new(std::env::current_exe().unwrap())
            .args(["launch_entry", "--exact", "--nocapture", "--test-threads=1"])
            .env(LAUNCH, which)
            .env(IDENTIFIER, identifier)
            .env(MARKS, marks)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .unwrap(),
    )
}

fn wait_for(path: &Path, within: Duration) -> bool {
    let deadline = Instant::now() + within;
    while !path.exists() {
        if Instant::now() > deadline {
            return false;
        }
        std::thread::sleep(Duration::from_millis(20));
    }
    true
}

#[test]
fn a_second_launch_hands_over_before_anything_starts() {
    let marks = tempfile::tempdir().unwrap();
    let marks = marks.path();
    let identifier = format!("app.raphael.test.launch.{}", uuid::Uuid::new_v4().simple());

    let mut first = launch("first", &identifier, marks);
    assert!(
        wait_for(&marks.join("first started"), Duration::from_secs(20)),
        "the first launch did not start"
    );

    let mut second = launch("second", &identifier, marks);
    let deadline = Instant::now() + Duration::from_secs(20);
    let status = loop {
        if let Some(status) = second.0.try_wait().unwrap() {
            break status;
        }
        assert!(Instant::now() < deadline, "the second launch did not exit");
        std::thread::sleep(Duration::from_millis(20));
    };
    assert_eq!(status.code(), Some(0), "the second launch carried on");
    assert!(
        !marks.join("second started").exists(),
        "the second launch migrated the data folders before it handed over"
    );

    assert!(
        wait_for(&marks.join("first told"), Duration::from_secs(10)),
        "the first launch was not told"
    );
    let args = fs::read_to_string(marks.join("first told")).unwrap();
    assert!(
        args.contains("launch_entry"),
        "the arguments of the second launch: {args:?}"
    );

    fs::write(marks.join("done"), "").unwrap();
    let deadline = Instant::now() + Duration::from_secs(20);
    while first.0.try_wait().unwrap().is_none() {
        assert!(Instant::now() < deadline, "the first launch did not end");
        std::thread::sleep(Duration::from_millis(20));
    }
}
