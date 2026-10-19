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


@pytest.fixture
def launcher(tmp_path):
    if not sys.executable.isascii():
        pytest.skip("a batch file cannot name a Python in a non-ASCII path")
    path = tmp_path / "raphael-core.cmd"
    path.write_text(f'@echo off\r\n"{sys.executable}" -m raphael_core\r\n', encoding="ascii")
    return path


def test_both_passes_pass_on_the_development_core(smoke, launcher):
    report = smoke.smoke(launcher)
    assert report["passed"] is True and report["paid_api_called"] is False
    assert report["keyring_backend"] == "keyring.backends.fail.Keyring"
    cleanup = report["cleanup_pass"]
    assert cleanup["passed"] is True and cleanup["fake_llm"] is True and cleanup["cleaned"] is True
    # One cleaned dictation for each Chinese variant, each with an OpenCC configuration of its own.
    assert cleanup["languages"] == ["zh-TW", "zh-HK", "zh-CN"]
    assert cleanup["exit_code"] == 0 and report["exit_code"] == 0


def test_the_second_pass_dictates_with_cleanup_and_the_first_without(smoke, launcher):
    report = smoke.smoke(launcher)
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
