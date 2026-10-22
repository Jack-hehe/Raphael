"""The packaging smoke test (core/packaging/smoke.py), run against the development core.

The smoke test is made for the frozen executable, which only the release workflow builds, so a
change that broke the script itself would be found at the release. Here a launcher stands in for the
executable: it runs `python -m raphael_core` in this environment. That says nothing about the bundle
(its data files, its DLLs), only that both passes of the script do what they claim. No key is read
and no request leaves the machine: the first pass has the fail-closed keyring, the second the fake
LLM.
"""

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

CORE = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="the smoke test starts the Windows executable"
)


@pytest.fixture(scope="module")
def smoke():
    spec = importlib.util.spec_from_file_location(
        "raphael_packaging_smoke", CORE / "packaging" / "smoke.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def launcher(tmp_path_factory):
    if not sys.executable.isascii():
        pytest.skip("a batch file cannot name a Python in a non-ASCII path")
    path = tmp_path_factory.mktemp("launcher") / "raphael-core.cmd"
    # %* passes on what the smoke test gives the executable (--worker).
    path.write_text(f'@echo off\r\n"{sys.executable}" -m raphael_core %*\r\n', encoding="ascii")
    return path


@pytest.fixture(scope="module")
def report(smoke, launcher):
    """Both passes (a core each), run once for the tests that read what they did."""
    return smoke.smoke(launcher)


def test_both_passes_pass_on_the_development_core(report):
    assert report["passed"] is True and report["paid_api_called"] is False
    assert report["keyring_backend"] == "keyring.backends.fail.Keyring"
    cleanup = report["cleanup_pass"]
    assert cleanup["passed"] is True and cleanup["fake_llm"] is True and cleanup["cleaned"] is True
    # One cleaned dictation for each Chinese variant, each with an OpenCC configuration of its own.
    assert cleanup["languages"] == ["zh-TW", "zh-HK", "zh-CN"]
    assert cleanup["exit_code"] == 0 and report["exit_code"] == 0


def test_the_second_pass_dictates_with_cleanup_and_the_first_without(smoke, report):
    dictations = [
        call["response"]["result"]
        for call in report["calls"]
        if call["method"] == "brain.finalize_dictation"
    ]
    first, *cleaned = dictations
    # The fail-closed keyring raises when the key is asked for, before the prompt is read.
    assert first["cleaned"] is False and first["fallback_reason"] == "error:unexpected"
    assert [item["cleaned"] for item in cleaned] == [True, True, True]
    assert [item["text"] for item in cleaned] == [
        expected for _, _, expected in smoke.CLEANED_VARIANTS
    ]


def test_the_second_pass_fails_when_the_prompt_is_missing_from_the_core(
    smoke, launcher, tmp_path, monkeypatch
):
    # What a bundle without the cleanup prompt would do: every dictation falls back to its raw
    # text and nothing else shows. The core here is a copy of the package without the prompt.
    shadow = tmp_path / "shadow"
    shutil.copytree(
        CORE / "src" / "raphael_core",
        shadow / "raphael_core",
        ignore=shutil.ignore_patterns("__pycache__", "cleanup_system.md"),
    )
    original = smoke.RpcProcess

    class Shadowed(original):
        def __init__(self, executable, root, extra_environment=None):
            extra = {**(extra_environment or {}), "PYTHONPATH": str(shadow)}
            super().__init__(executable, root, extra)

    monkeypatch.setattr(smoke, "RpcProcess", Shadowed)
    with pytest.raises(RuntimeError, match="was not cleaned"):
        smoke.smoke_with_the_fake_llm(launcher)


def test_the_second_pass_hands_a_task_to_a_worker_the_core_starts(report):
    cleanup = report["cleanup_pass"]
    assert cleanup["background_task"] is True
    # The scripted task of the fake LLM, carried out by a second process of the same program.
    asked = [c for c in report["calls"] if c["method"] == "agent.ask"]
    handed = [c["response"]["result"] for c in asked if "Handed" in str(c["response"])]
    assert [h["text"] for h in handed] == ["Handed to the background."]
    steps = [c for c in report["calls"] if c["method"] == "worker.get"]
    assert [s["kind"] for s in steps[0]["response"]["result"]["task"]["steps"]] == [
        "model", "tool", "model",
    ]  # fmt: skip


def test_the_third_pass_starts_the_worker_by_itself_and_it_ends_with_its_stdin(report):
    worker = report["worker_pass"]
    assert worker["passed"] is True and worker["exit_code"] == 0
    assert 0 < worker["start_seconds"] < 20


def test_the_smoke_fails_when_the_bundle_cannot_start_a_worker(smoke, launcher, tmp_path, monkeypatch):
    # A bundle that left the worker out: the executable cannot start with --worker, and a task for
    # the background would never be carried out.
    shadow = tmp_path / "shadow"
    shutil.copytree(
        CORE / "src" / "raphael_core",
        shadow / "raphael_core",
        ignore=shutil.ignore_patterns("__pycache__", "runner.py"),
    )
    original = smoke.RpcProcess

    class Shadowed(original):
        def __init__(self, executable, root, extra_environment=None):
            extra = {**(extra_environment or {}), "PYTHONPATH": str(shadow)}
            super().__init__(executable, root, extra)

    monkeypatch.setattr(smoke, "RpcProcess", Shadowed)
    with pytest.raises(RuntimeError, match="did not carry the task out"):
        smoke.smoke_with_the_fake_llm(launcher)
    with pytest.raises(RuntimeError, match="The worker"):
        smoke.smoke_the_worker(launcher, {"PYTHONPATH": str(shadow)})


def test_the_voice_pass_decodes_an_opus_clip(report):
    assert report["voice_pass"]["passed"] is True


def test_the_voice_pass_fails_when_the_decoder_does_not_work(smoke, tmp_path):
    broken = tmp_path / "broken-core.cmd"
    broken.write_text("@echo off\r\necho voice decoder failed\r\nexit /b 1\r\n", encoding="ascii")
    with pytest.raises(RuntimeError, match="audio decoder"):
        smoke.smoke_the_voice_decoder(broken)
