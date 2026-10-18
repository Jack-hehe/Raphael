//! Explicitly armed, post-paste UIA learning against an isolated real core.
//! No window, keyboard input, paste delivery, microphone, or paid API is driven here.

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex, PoisonError};
use std::time::Duration;

use serde_json::{Value, json};
use tokio::sync::oneshot;

use super::{AfterPaste, FieldProbe, Learner, UiaProbe, WatchScope, WatchedField};
use crate::core_process::{CoreProcess, resolve_core_command};
use crate::overlay::{Mode, OverlayApi, Progress};
use crate::paste::{PasteReceipt, Paster, Target, WindowsPaster};
use crate::rpc::CoreApi;

const PASTED: &str = "Please ask Allice to confirm the meeting tomorrow and send the agenda.";
const WORD: &str = "Alice";
const MISHEARD: &str = "Allice";

fn checked_fixture_data_dir(path: &Path) -> Result<PathBuf, &'static str> {
    if !path.is_absolute() {
        return Err("data_dir_not_absolute");
    }
    let path = path.canonicalize().map_err(|_| "data_dir_unavailable")?;
    if !path.is_dir() {
        return Err("data_dir_not_directory");
    }
    let parent = path.parent().ok_or("fixture_parent_unavailable")?;
    let marker = std::fs::read_to_string(parent.join(".native-qa-fixture"))
        .map_err(|_| "fixture_marker_missing")?;
    if marker.trim() != "raphael-native-qa-v1" {
        return Err("fixture_marker_invalid");
    }
    // A misplaced marker must never authorize the normal user's production database.
    if let Some(appdata) = std::env::var_os("APPDATA")
        && let Ok(production) = PathBuf::from(appdata).join("Raphael").canonicalize()
        && path.starts_with(production)
    {
        return Err("production_data_dir_refused");
    }
    if !path.join("raphael.db").is_file() {
        return Err("fixture_database_missing");
    }
    Ok(path)
}

fn install_metadata_logger() {
    struct MetadataLogger;
    impl log::Log for MetadataLogger {
        fn enabled(&self, metadata: &log::Metadata<'_>) -> bool {
            metadata.target().ends_with("::learn::uia") || metadata.target().ends_with("::learn")
        }

        fn log(&self, record: &log::Record<'_>) {
            if !self.enabled(record.metadata()) {
                return;
            }
            let message = record.args().to_string();
            if [
                "learn: probe stopped (stage:",
                "learn: field probe (sources:",
                "learn: focus identity (",
                "learn: focus event (",
                "learn: initial focus metadata (",
                "learn: failed focus snapshot (",
                "learn: the watch ended (",
                "learn: the pasted text (",
                "learn: the field was left before",
                "learn: the focused field cannot be read;",
            ]
            .iter()
            .any(|prefix| message.starts_with(prefix))
            {
                eprintln!("{message}");
            }
        }

        fn flush(&self) {}
    }
    static LOGGER: MetadataLogger = MetadataLogger;
    assert!(
        log::set_logger(&LOGGER).is_ok(),
        "native learner roundtrip: reason=logger_already_installed; run this test alone"
    );
    log::set_max_level(log::LevelFilter::Info);
}

struct ReadyProbe {
    ready: Mutex<Option<oneshot::Sender<bool>>>,
}

impl FieldProbe for ReadyProbe {
    fn focused(&self, target: &Target) -> Option<Box<dyn WatchedField>> {
        UiaProbe.focused(target)
    }

    fn after_paste(
        &self,
        target: &Target,
        pasted: &str,
        scope: WatchScope,
    ) -> Option<Box<dyn WatchedField>> {
        let field = UiaProbe.after_paste(target, pasted, scope);
        let available = field.is_some();
        if let Some(ready) = self
            .ready
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .take()
        {
            eprintln!(
                "native learner roundtrip: {}",
                if available {
                    "watch_ready"
                } else {
                    "probe_unavailable"
                }
            );
            let _ = ready.send(available);
        }
        field
    }
}

#[derive(Default)]
struct AuditOverlay {
    learned_count: AtomicUsize,
    ordinary_count: AtomicUsize,
    expected_notice: AtomicBool,
}

impl OverlayApi for AuditOverlay {
    fn show(&self, _mode: Mode) {}
    fn set_mode(&self, _mode: Mode) {}
    fn level(&self, _value: f32) {}
    fn hide(&self) {}
    fn progress(&self, _progress: Progress) {}

    fn notice(&self, _zh: &str, _ja: &str) {
        self.ordinary_count.fetch_add(1, Ordering::SeqCst);
    }

    fn learned_notice(&self, zh: &str, ja: &str) {
        let (expected_zh, expected_ja) = crate::notices::learned(&[WORD.to_owned()]);
        self.expected_notice
            .store(zh == expected_zh && ja == expected_ja, Ordering::SeqCst);
        let count = self.learned_count.fetch_add(1, Ordering::SeqCst) + 1;
        eprintln!("native learner roundtrip: learned_notice_count={count}");
    }
}

async fn call(core: &CoreProcess, method: &str) -> Value {
    core.call(method, json!({})).await.unwrap_or_else(|error| {
        panic!(
            "native learner roundtrip: reason=rpc_failed method={method} code={}",
            error.code
        )
    })
}

#[tokio::test]
#[ignore = "requires an explicitly prepared non-sensitive foreground field and marked isolated data"]
async fn foreground_correction_reaches_real_core_and_isolated_dictionary() {
    let Some(data_dir) = std::env::var_os("RAPHAEL_NATIVE_QA_DATA_DIR") else {
        eprintln!("native learner roundtrip: result=skipped reason=fixture_env_missing");
        return;
    };
    let data_dir = checked_fixture_data_dir(Path::new(&data_dir))
        .unwrap_or_else(|reason| panic!("native learner roundtrip: result=failed reason={reason}"));
    install_metadata_logger();

    let mut command = resolve_core_command()
        .unwrap_or_else(|_| panic!("native learner roundtrip: reason=core_command_unavailable"));
    command.env.extend([
        (
            "RAPHAEL_DATA_DIR".to_owned(),
            data_dir.display().to_string(),
        ),
        (
            "RAPHAEL_MODELS_DIR".to_owned(),
            data_dir.join("models").display().to_string(),
        ),
        ("RAPHAEL_FAKE_LLM".to_owned(), "1".to_owned()),
        ("RAPHAEL_FAKE_AUDIO".to_owned(), String::new()),
        (
            "PYTHON_KEYRING_BACKEND".to_owned(),
            "keyring.backends.fail.Keyring".to_owned(),
        ),
        (
            "PYTHONPATH".to_owned(),
            command.cwd.join("src").display().to_string(),
        ),
    ]);
    let core = CoreProcess::start(command)
        .unwrap_or_else(|_| panic!("native learner roundtrip: reason=core_start_failed"));
    let hello = call(core.as_ref(), "core.hello").await;
    assert!(
        hello["protocol"].as_u64() == Some(1),
        "native learner roundtrip: reason=protocol_mismatch"
    );
    let settings = call(core.as_ref(), "settings.get").await;
    assert!(
        settings
            .pointer("/settings/learn_corrections")
            .and_then(Value::as_bool)
            == Some(true),
        "native learner roundtrip: reason=learning_disabled"
    );
    let history = call(core.as_ref(), "history.latest").await;
    assert!(
        history.pointer("/item/id").and_then(Value::as_u64) == Some(1)
            && history.pointer("/item/text").and_then(Value::as_str) == Some(PASTED),
        "native learner roundtrip: reason=history_fixture_mismatch"
    );
    let before = call(core.as_ref(), "dictionary.list").await;
    let before = before["entries"]
        .as_array()
        .expect("native learner roundtrip: reason=dictionary_shape");
    assert!(
        before
            .iter()
            .all(|entry| entry["word"].as_str() != Some(WORD)
                && entry["word"].as_str() != Some(MISHEARD)),
        "native learner roundtrip: reason=fixture_word_already_present"
    );

    let target = WindowsPaster::new()
        .foreground()
        .expect("native learner roundtrip: reason=foreground_unavailable");
    assert!(
        !target.is_elevated,
        "native learner roundtrip: reason=elevated_target"
    );
    let (ready, ready_rx) = oneshot::channel();
    let probe = Arc::new(ReadyProbe {
        ready: Mutex::new(Some(ready)),
    });
    let overlay = Arc::new(AuditOverlay::default());
    let learner = Learner::new(core.clone(), overlay.clone(), probe);
    // The fixture supplies an already prepared field; no real paste or hardware hotkey is claimed.
    // The receipt has no input checkpoint, so only the real UIA field route can pass.
    let mut watch = learner
        .start_with_receipt(PASTED, &target, PasteReceipt::new())
        .expect("native learner roundtrip: reason=watch_not_started");
    let deadline = tokio::time::Instant::now() + Duration::from_secs(45);
    let outcome = async {
        let ready = tokio::time::timeout_at(deadline, ready_rx)
            .await
            .map_err(|_| "ready_timeout")?
            .map_err(|_| "ready_channel_closed")?;
        if !ready {
            return Err("probe_unavailable");
        }
        tokio::time::timeout_at(deadline, &mut watch)
            .await
            .map_err(|_| "watch_timeout")?
            .map_err(|_| "watch_panicked")?;
        Ok::<(), &'static str>(())
    }
    .await;
    // Even on timeout, stop the native field thread and abort the detached continuation.
    learner.dictation_started();
    watch.abort();
    assert!(
        outcome.is_ok(),
        "native learner roundtrip: result=failed reason={:?}",
        outcome.err()
    );

    let after = call(core.as_ref(), "dictionary.list").await;
    let entries = after["entries"]
        .as_array()
        .expect("native learner roundtrip: reason=dictionary_shape");
    let learned = entries
        .iter()
        .find(|entry| entry["word"].as_str() == Some(WORD))
        .expect("native learner roundtrip: reason=fixture_word_not_learned");
    assert!(
        learned["source"].as_str() == Some("learned"),
        "native learner roundtrip: reason=source_mismatch"
    );
    assert!(
        learned["misheard"]
            .as_array()
            .is_some_and(|forms| forms.len() == 1 && forms[0].as_str() == Some(MISHEARD)),
        "native learner roundtrip: reason=misheard_mismatch"
    );
    assert!(
        entries.len() == before.len() + 1,
        "native learner roundtrip: reason=entry_count_mismatch"
    );
    assert!(
        overlay.learned_count.load(Ordering::SeqCst) == 1
            && overlay.ordinary_count.load(Ordering::SeqCst) == 0
            && overlay.expected_notice.load(Ordering::SeqCst),
        "native learner roundtrip: reason=dedicated_notice_mismatch"
    );
    eprintln!("native learner roundtrip: result=passed learned_count=1 notice_count=1");
}
