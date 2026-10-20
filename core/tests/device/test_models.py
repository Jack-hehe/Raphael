"""Tests for the speech model registry and downloader.

Every request goes to an httpx.MockTransport, so no test touches the network.
"""

import io
import os
import tarfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from raphael_core.device import models
from raphael_core.device.models import (
    REGISTRY,
    ModelDownloadError,
    ModelFile,
    ModelManager,
    ModelSpec,
)

MB = 1_000_000


def make_tarball(files: dict[str, bytes]) -> bytes:
    """A .tar.bz2 holding `files` (member name -> content), built in memory."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:bz2") as tar:
        for name, content in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


@pytest.fixture
def manager(tmp_path):
    """`manager(handler, root=None)` is a ModelManager (rooted in tmp_path unless `root` says
    otherwise) whose HTTP goes to `handler`. The httpx clients close when the test ends."""
    opened: list[httpx.Client] = []

    def make(handler, root=None):
        client = httpx.Client(transport=httpx.MockTransport(handler))
        opened.append(client)
        return ModelManager(tmp_path if root is None else root, client=client)

    yield make
    for client in opened:
        client.close()


def serving(body: bytes, seen: list | None = None):
    """A handler that answers every request with `body` and records the requests in `seen`."""

    def handler(request):
        if seen is not None:
            seen.append(request)
        return httpx.Response(200, content=body)

    return handler


def archive_spec(data: bytes, **overrides) -> ModelSpec:
    """A spec for model `m` that comes from the archive `data`, whose files are under `pkg/`.

    It is ready when `a.onnx` exists. Keyword arguments override any field."""
    fields = {
        "name": "m",
        "files": ("a.onnx",),
        "archive_url": "https://h/m.tar.bz2",
        "archive_size": len(data),
        "archive_root": "pkg",
    }
    return ModelSpec(**{**fields, **overrides})


def file_spec(size: int | None, name: str = "m") -> ModelSpec:
    """A spec for one downloaded file, `v.onnx`."""
    return ModelSpec(
        name=name,
        files=("v.onnx",),
        downloads=(ModelFile("https://h/v.onnx", "v.onnx", size),),
    )


def leftovers(root) -> list[str]:
    """What is in root/.tmp: nothing, after any install, whether it worked or not."""
    return sorted(path.name for path in (root / ".tmp").iterdir())


# --- Installing: the download, its size and a safe unpacking ---


def test_ensure_downloads_and_extracts(tmp_path):
    data = make_tarball({"pkg/a.onnx": b"x", "pkg/tokens.txt": b"y"})
    spec = ModelSpec(
        name="m",
        files=("a.onnx", "tokens.txt"),
        archive_url="https://h/m.tar.bz2",
        archive_size=len(data),
        archive_root="pkg",
    )
    seen = []
    mgr = ModelManager(
        tmp_path,
        client=httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, content=data))
        ),
    )
    path = mgr.ensure_spec(spec, progress=lambda d, t: seen.append((d, t)))
    assert (path / "a.onnx").read_bytes() == b"x" and mgr.is_ready_spec(spec)
    assert seen[-1] == (len(data), len(data))


def test_size_mismatch_fails_cleanly(tmp_path):
    data = make_tarball({"pkg/a.onnx": b"x"})
    spec = ModelSpec(
        name="m",
        files=("a.onnx",),
        archive_url="https://h/m.tar.bz2",
        archive_size=len(data) + 1,
        archive_root="pkg",
    )
    mgr = ModelManager(
        tmp_path,
        client=httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, content=data))
        ),
    )
    with pytest.raises(ModelDownloadError):
        mgr.ensure_spec(spec)
    assert not (tmp_path / "m").exists()


def test_rejects_path_traversal(tmp_path):
    data = make_tarball({"../evil.txt": b"!"})
    spec = ModelSpec(
        name="m",
        files=("evil.txt",),
        archive_url="https://h/m.tar.bz2",
        archive_size=len(data),
        archive_root=".",
    )
    mgr = ModelManager(
        tmp_path,
        client=httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, content=data))
        ),
    )
    with pytest.raises(Exception):
        mgr.ensure_spec(spec)
    assert not (tmp_path.parent / "evil.txt").exists()


# --- The registry ---


def test_the_registry_pins_the_models_and_their_files():
    release = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
    breeze_base = "https://huggingface.co/MediaTek-Research/Breeze-ASR-25-onnx-250806/resolve/main/"
    assert set(REGISTRY) == {"silero-vad", "qwen3-asr", "sensevoice", "breeze-asr-25"}
    assert all(name == spec.name for name, spec in REGISTRY.items())

    silero = REGISTRY["silero-vad"]
    assert silero.files == ("silero_vad_v5.onnx",)
    assert silero.archive_url is None
    assert silero.downloads == (
        ModelFile(release + "silero_vad_v5.onnx", "silero_vad_v5.onnx", 2_313_101),
    )

    qwen = REGISTRY["qwen3-asr"]
    assert qwen.archive_url == release + "sherpa-onnx-qwen3-asr-0.6B-int8-2026-03-25.tar.bz2"
    assert qwen.archive_size == 878_702_423
    assert qwen.archive_root == "sherpa-onnx-qwen3-asr-0.6B-int8-2026-03-25"
    assert qwen.files == (
        "conv_frontend.onnx",
        "encoder.int8.onnx",
        "decoder.int8.onnx",
        "tokenizer/vocab.json",
        "tokenizer/merges.txt",
        "tokenizer/tokenizer_config.json",
    )
    assert qwen.downloads == ()

    sense = REGISTRY["sensevoice"]
    assert sense.archive_url == (
        release + "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2"
    )
    assert sense.archive_size == 163_002_883
    assert sense.archive_root == "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17"
    assert sense.files == ("model.int8.onnx", "tokens.txt")
    assert sense.downloads == ()

    breeze_files = (
        "breeze-asr-25-half-encoder.int8.onnx",
        "breeze-asr-25-half-decoder.int8.onnx",
        "breeze-asr-25-half-tokens.txt",
    )
    breeze = REGISTRY["breeze-asr-25"]
    assert breeze.files == breeze_files
    assert breeze.archive_url is None
    assert breeze.downloads == tuple(ModelFile(breeze_base + f, f, None) for f in breeze_files)


def test_a_registry_model_comes_from_an_archive_or_from_downloads_that_cover_its_files():
    for spec in REGISTRY.values():
        if spec.archive_url is not None:
            assert spec.archive_root and spec.archive_size and not spec.downloads
        else:
            assert {file.path for file in spec.downloads} >= set(spec.files)


# --- Already installed, and asking about it ---


def test_a_ready_model_is_returned_without_any_request(tmp_path, manager):
    (tmp_path / "m").mkdir()
    (tmp_path / "m" / "a.onnx").write_bytes(b"x")
    requests, reports = [], []
    mgr = manager(serving(b"unused", requests))
    spec = archive_spec(b"unused")
    assert mgr.is_ready_spec(spec)
    assert mgr.ensure_spec(spec, progress=lambda d, t: reports.append((d, t))) == tmp_path / "m"
    assert requests == [] and reports == []
    assert not (tmp_path / ".tmp").exists()


def test_the_root_is_created_when_the_first_download_needs_it_not_before(tmp_path, manager):
    data = make_tarball({"pkg/a.onnx": b"x"})
    root = tmp_path / "AppData" / "Raphael" / "models"
    mgr = manager(serving(data), root=root)
    spec = archive_spec(data)
    assert mgr.dir("m") == root / "m"
    assert not mgr.is_ready_spec(spec)
    assert not root.exists()  # asking creates nothing

    assert mgr.ensure_spec(spec) == root / "m"
    assert (root / "m" / "a.onnx").read_bytes() == b"x"
    assert leftovers(root) == []


def test_the_by_name_wrappers_look_the_model_up_in_the_registry(tmp_path, manager, monkeypatch):
    data = make_tarball({"pkg/a.onnx": b"x"})
    monkeypatch.setitem(REGISTRY, "m", archive_spec(data))
    mgr = manager(serving(data))
    reports = []
    assert not mgr.is_ready("m")
    assert mgr.ensure("m", progress=lambda d, t: reports.append((d, t))) == tmp_path / "m"
    assert mgr.is_ready("m")
    assert reports[-1] == (len(data), len(data))


def test_the_manager_closes_the_client_it_made_but_not_one_it_was_given(tmp_path, monkeypatch):
    data = make_tarball({"pkg/a.onnx": b"x"})
    real_client = httpx.Client
    made = []

    def client_for_tests():
        client = real_client(transport=httpx.MockTransport(serving(data)))
        made.append(client)
        return client

    monkeypatch.setattr(models.httpx, "Client", client_for_tests)
    ModelManager(tmp_path / "own").ensure_spec(archive_spec(data))
    assert [client.is_closed for client in made] == [True]

    given = real_client(transport=httpx.MockTransport(serving(data)))
    ModelManager(tmp_path / "given", client=given).ensure_spec(archive_spec(data))
    assert not given.is_closed
    given.close()


def test_an_unknown_model_name_is_a_value_error_that_names_it(tmp_path):
    mgr = ModelManager(tmp_path)
    with pytest.raises(ValueError, match="nope"):
        mgr.ensure("nope")
    with pytest.raises(ValueError, match="nope"):
        mgr.is_ready("nope")


@pytest.mark.parametrize(
    "spec",
    [
        ModelSpec(name="m", files=("a.onnx",)),
        ModelSpec(
            name="m",
            files=("a.onnx",),
            archive_url="https://h/m.tar.bz2",
            downloads=(ModelFile("https://h/a.onnx", "a.onnx", None),),
        ),
    ],
    ids=["no source", "two sources"],
)
def test_a_spec_needs_an_archive_or_downloads_and_not_both(tmp_path, manager, spec):
    requests = []
    with pytest.raises(ValueError, match="m: give an archive or downloads"):
        manager(serving(b"", requests)).ensure_spec(spec)
    assert requests == [] and not (tmp_path / "m").exists()


# --- Downloading one file at a time (silero-vad, breeze-asr-25) ---


def test_a_downloaded_file_lands_in_the_model_directory(tmp_path, manager):
    body = b"onnx bytes" * 100
    requests, reports = [], []
    mgr = manager(serving(body, requests))
    spec = file_spec(len(body), name="vad")
    path = mgr.ensure_spec(spec, progress=lambda d, t: reports.append((d, t)))
    assert path == tmp_path / "vad"
    assert (path / "v.onnx").read_bytes() == body
    assert [p.name for p in path.iterdir()] == ["v.onnx"]  # no .part file left
    assert reports[-1] == (len(body), len(body))
    assert [str(r.url) for r in requests] == ["https://h/v.onnx"]
    assert mgr.is_ready_spec(spec)


def test_a_file_in_a_subdirectory_gets_its_directory(tmp_path, manager):
    spec = ModelSpec(
        name="m",
        files=("tok/vocab.json",),
        downloads=(ModelFile("https://h/vocab.json", "tok/vocab.json", None),),
    )
    path = manager(serving(b"{}")).ensure_spec(spec)
    assert (path / "tok" / "vocab.json").read_bytes() == b"{}"


def test_a_file_of_the_wrong_size_is_not_kept(tmp_path, manager):
    with pytest.raises(ModelDownloadError, match=r"10 bytes.*11"):
        manager(serving(b"x" * 10)).ensure_spec(file_spec(11))
    assert not (tmp_path / "m").exists()  # not even the empty directory
    assert leftovers(tmp_path) == []


def test_a_connection_that_drops_midway_leaves_no_partial_file(tmp_path, manager):
    def dropping():
        yield b"abc"
        raise httpx.ReadError("connection dropped")

    mgr = manager(lambda request: httpx.Response(200, content=dropping()))
    with pytest.raises(ModelDownloadError, match="connection dropped"):
        mgr.ensure_spec(file_spec(None))
    assert not (tmp_path / "m").exists()


def test_a_download_replaces_an_older_file_of_the_same_name(tmp_path, manager):
    (tmp_path / "m").mkdir()
    (tmp_path / "m" / "v.onnx").write_bytes(b"old and wrong")
    spec = ModelSpec(
        name="m",
        files=("v.onnx", "w.onnx"),  # w.onnx is missing, so the model is not ready
        downloads=(
            ModelFile("https://h/v.onnx", "v.onnx", 3),
            ModelFile("https://h/w.onnx", "w.onnx", 3),
        ),
    )
    path = manager(serving(b"new")).ensure_spec(spec)
    assert (path / "v.onnx").read_bytes() == b"new"
    assert (path / "w.onnx").read_bytes() == b"new"


def test_downloads_that_do_not_supply_a_listed_file_are_an_error(tmp_path, manager):
    spec = ModelSpec(
        name="m",
        files=("v.onnx", "w.onnx"),
        downloads=(ModelFile("https://h/v.onnx", "v.onnx", None),),
    )
    with pytest.raises(ModelDownloadError, match=r"w\.onnx"):
        manager(serving(b"x")).ensure_spec(spec)


# --- Progress ---


def test_progress_comes_at_least_every_megabyte_and_ends_complete(tmp_path, manager):
    body = bytes(2_500_000)
    reports = []
    mgr = manager(serving(body))
    mgr.ensure_spec(file_spec(None), progress=lambda d, t: reports.append((d, t)))
    done = [d for d, _ in reports]
    assert done == sorted(done) and done[-1] == len(body)
    assert max(b - a for a, b in zip([0, *done], done, strict=False)) <= MB  # gaps between reports
    # No pinned size: the total is the Content-Length of the reply.
    assert {t for _, t in reports} == {len(body)}


def test_the_total_is_the_pinned_size_even_when_the_reply_says_another(tmp_path, manager):
    def handler(request):
        return httpx.Response(200, content=b"abc", headers={"Content-Length": "99"})

    reports = []
    manager(handler).ensure_spec(file_spec(3), progress=lambda d, t: reports.append((d, t)))
    assert reports[-1] == (3, 3)


def test_the_total_is_zero_when_nothing_says_how_big_the_file_is(tmp_path, manager):
    def handler(request):
        return httpx.Response(200, content=iter([b"abc", b"def"]))  # sent in pieces: no length

    reports = []
    manager(handler).ensure_spec(file_spec(None), progress=lambda d, t: reports.append((d, t)))
    assert reports[-1] == (6, 0)


def test_an_empty_body_still_gets_its_final_report(tmp_path, manager):
    reports = []
    path = manager(serving(b"")).ensure_spec(
        file_spec(None), progress=lambda d, t: reports.append((d, t))
    )
    assert (path / "v.onnx").read_bytes() == b""
    assert reports == [(0, 0)]


def test_a_progress_callback_that_raises_aborts_the_download_cleanly(tmp_path, manager):
    data = make_tarball({"pkg/a.onnx": b"x"})

    def stop(done, total):
        raise RuntimeError("stop")

    with pytest.raises(RuntimeError, match="stop"):
        manager(serving(data)).ensure_spec(archive_spec(data), progress=stop)
    assert not (tmp_path / "m").exists()
    assert leftovers(tmp_path) == []


# --- The requests ---


def test_redirects_are_followed_and_the_bytes_are_not_compressed(tmp_path, manager):
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.host == "github.test":
            return httpx.Response(302, headers={"Location": "https://cdn.test/blob?sig=1"})
        return httpx.Response(200, content=b"onnx")

    spec = ModelSpec(
        name="m",
        files=("v.onnx",),
        downloads=(ModelFile("https://github.test/v.onnx", "v.onnx", 4),),
    )
    path = manager(handler).ensure_spec(spec)  # the client itself does not follow redirects
    assert (path / "v.onnx").read_bytes() == b"onnx"
    assert [r.url.host for r in requests] == ["github.test", "cdn.test"]
    assert all(r.headers["accept-encoding"] == "identity" for r in requests)


def test_an_error_status_is_a_model_download_error_that_names_it(tmp_path, manager):
    data = make_tarball({"pkg/a.onnx": b"x"})
    with pytest.raises(ModelDownloadError, match="404"):
        manager(lambda request: httpx.Response(404)).ensure_spec(archive_spec(data))
    assert not (tmp_path / "m").exists()
    assert leftovers(tmp_path) == []


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        (httpx.ConnectError("no route to host"), "no route to host"),
        (httpx.ReadTimeout(""), "ReadTimeout"),  # httpx gives a timeout no message at all
    ],
    ids=["no connection", "timeout"],
)
def test_a_network_failure_is_a_model_download_error(tmp_path, manager, failure, message):
    def handler(request):
        raise failure

    data = make_tarball({"pkg/a.onnx": b"x"})
    with pytest.raises(ModelDownloadError, match=message):
        manager(handler).ensure_spec(archive_spec(data))
    assert leftovers(tmp_path) == []


# --- Archives ---


@pytest.mark.parametrize("name", ["../evil.txt", "../../evil.txt", "../../../evil.txt"])
def test_an_archive_cannot_write_outside_its_folder(tmp_path, manager, name):
    root = tmp_path / "root"
    data = make_tarball({name: b"!", "pkg/a.onnx": b"x"})
    with pytest.raises(ModelDownloadError, match="outside"):
        manager(serving(data), root=root).ensure_spec(archive_spec(data))
    assert list(tmp_path.parent.glob("evil.txt")) == []
    assert list(tmp_path.rglob("evil.txt")) == []
    assert not (root / "m").exists()
    assert leftovers(root) == []


@pytest.mark.parametrize(
    "payload",
    [
        b"this is not a tarball",
        make_tarball({"pkg/a.onnx": bytes(range(256)) * 40})[:-20],
    ],
    ids=["not an archive", "cut short"],
)
def test_a_damaged_archive_is_a_model_download_error(tmp_path, manager, payload):
    spec = archive_spec(payload)
    with pytest.raises(ModelDownloadError, match="Could not unpack m"):
        manager(serving(payload)).ensure_spec(spec)
    assert not (tmp_path / "m").exists()
    assert leftovers(tmp_path) == []


@pytest.mark.parametrize(
    ("members", "needle"),
    [
        ({"pkg/a.onnx": b"x"}, "tokens.txt"),  # a file the spec lists is not in the archive
        ({"other/a.onnx": b"x", "other/tokens.txt": b"y"}, "pkg"),  # the root folder is missing
    ],
    ids=["missing file", "missing root"],
)
def test_an_archive_that_lacks_what_the_spec_needs_is_not_installed(
    tmp_path, manager, members, needle
):
    data = make_tarball(members)
    spec = archive_spec(data, files=("a.onnx", "tokens.txt"))
    with pytest.raises(ModelDownloadError, match=needle):
        manager(serving(data)).ensure_spec(spec)
    assert not (tmp_path / "m").exists()
    assert leftovers(tmp_path) == []


def test_everything_under_the_archive_root_is_kept(tmp_path, manager):
    # The tests of the speech engines read the sample recordings that ship in test_wavs/.
    data = make_tarball(
        {"pkg/a.onnx": b"x", "pkg/test_wavs/zh.wav": b"RIFF", "pkg/tokenizer/vocab.json": b"{}"}
    )
    path = manager(serving(data)).ensure_spec(archive_spec(data))
    assert (path / "test_wavs" / "zh.wav").read_bytes() == b"RIFF"
    assert (path / "tokenizer" / "vocab.json").read_bytes() == b"{}"


def test_an_archive_without_a_root_folder_installs_its_top_level(tmp_path, manager):
    data = make_tarball({"a.onnx": b"x"})
    path = manager(serving(data)).ensure_spec(archive_spec(data, archive_root=None))
    assert (path / "a.onnx").read_bytes() == b"x"
    assert leftovers(tmp_path) == []


def test_a_partial_model_directory_is_replaced(tmp_path, manager):
    data = make_tarball({"pkg/a.onnx": b"new", "pkg/tokens.txt": b"y"})
    stale = tmp_path / "m"
    stale.mkdir()
    (stale / "a.onnx").write_bytes(b"old")  # not ready: tokens.txt is missing
    (stale / "junk.txt").write_bytes(b"?")
    mgr = manager(serving(data))
    spec = archive_spec(data, files=("a.onnx", "tokens.txt"))
    assert not mgr.is_ready_spec(spec)
    path = mgr.ensure_spec(spec)
    assert sorted(p.name for p in path.iterdir()) == ["a.onnx", "tokens.txt"]
    assert (path / "a.onnx").read_bytes() == b"new"
    assert leftovers(tmp_path) == []


def test_leftovers_of_an_earlier_crash_do_not_get_in_the_way(tmp_path, manager):
    data = make_tarball({"pkg/a.onnx": b"x"})
    crashed = tmp_path / ".tmp" / "m.extract" / "pkg"
    crashed.mkdir(parents=True)
    (crashed / "stale.bin").write_bytes(b"?")
    path = manager(serving(data)).ensure_spec(archive_spec(data))
    assert [p.name for p in path.iterdir()] == ["a.onnx"]
    assert leftovers(tmp_path) == []


# --- Threads ---


def test_two_threads_asking_for_the_same_model_download_it_once(tmp_path, manager):
    data = make_tarball({"pkg/a.onnx": b"x"})
    requests = []
    started, release = threading.Event(), threading.Event()

    def handler(request):
        requests.append(request)
        started.set()
        assert release.wait(10)
        return httpx.Response(200, content=data)

    # Two managers on one root, as when two services each make their own: the files are shared,
    # so the exclusion has to be too.
    first_manager, second_manager = manager(handler), manager(handler)
    spec = archive_spec(data)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(first_manager.ensure_spec, spec)
        assert started.wait(10)
        second = pool.submit(second_manager.ensure_spec, spec)
        time.sleep(0.2)  # long enough for the second call to reach the lock
        release.set()
        results = [first.result(10), second.result(10)]  # a failure in a thread is raised here
    assert results == [tmp_path / "m", tmp_path / "m"]
    assert len(requests) == 1


# --- Windows: a virus scanner or indexer can hold a file for a moment ---


def test_a_rename_that_is_refused_for_a_moment_is_tried_again(tmp_path, manager, monkeypatch):
    real_replace = os.replace
    refused = []

    def busy_twice(source, destination):
        if len(refused) < 2:
            refused.append(source)
            raise PermissionError(13, "The process cannot access the file")
        real_replace(source, destination)

    monkeypatch.setattr(models.os, "replace", busy_twice)
    monkeypatch.setattr(models.time, "sleep", lambda seconds: None)
    data = make_tarball({"pkg/a.onnx": b"x"})
    path = manager(serving(data)).ensure_spec(archive_spec(data))
    assert (path / "a.onnx").read_bytes() == b"x"
    assert len(refused) == 2


def test_a_rename_that_stays_refused_fails_the_install_cleanly(tmp_path, manager, monkeypatch):
    attempts = []

    def always_busy(source, destination):
        attempts.append(source)
        raise PermissionError(13, "The process cannot access the file")

    monkeypatch.setattr(models.os, "replace", always_busy)
    monkeypatch.setattr(models.time, "sleep", lambda seconds: None)
    data = make_tarball({"pkg/a.onnx": b"x"})
    with pytest.raises(ModelDownloadError, match="cannot access"):
        manager(serving(data)).ensure_spec(archive_spec(data))
    assert 2 <= len(attempts) <= 10  # it gave up after a few tries
    assert not (tmp_path / "m").exists()
    assert leftovers(tmp_path) == []
