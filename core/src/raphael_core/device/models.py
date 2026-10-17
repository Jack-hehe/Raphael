"""The speech models Raphael runs on the user's machine, and the code that fetches them.

Each model lives in its own directory, `<root>/<name>`, and is ready when the files its spec lists
exist there. The root is `paths.models_dir()` in the app: outside the repository, and shared by
every checkout. A model is either an archive (a .tar.bz2 that is unpacked) or a list of single
files, and `ModelManager.ensure` downloads whatever is missing.

Nothing is installed half way. An archive is downloaded and unpacked under `<root>/.tmp` and then
moves into place with one rename. A single file is written to a `.part` file next to its place
and renamed once it is complete. An install that fails raises ModelDownloadError and leaves
nothing behind in `.tmp`, and no `.part` file.
"""

import contextlib
import logging
import os
import shutil
import tarfile
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

# Installs run one at a time. They share the files under the root (the names in .tmp, the model
# directories), whichever ModelManager does the work, and a second caller for a model that is being
# installed does better to wait for it than to download it again.
_install_lock = threading.Lock()

Progress = Callable[[int, int], None]  # called with (bytes done, bytes in all; 0 if unknown)

_RELEASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
_BREEZE = "https://huggingface.co/MediaTek-Research/Breeze-ASR-25-onnx-250806/resolve/main/"
_QWEN3_ASR = "sherpa-onnx-qwen3-asr-0.6B-int8-2026-03-25"
_SENSE_VOICE = "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17"

_TMP = ".tmp"
# Progress is reported after each chunk, so this is the most that arrives between two reports.
_CHUNK = 512 * 1024
# Each request waits up to 30 s to connect and 120 s for each piece of data, whatever the client's
# own settings: httpx's default of 5 s is too short for a server that is slow to start.
_TIMEOUT = httpx.Timeout(connect=30.0, read=120.0, write=30.0, pool=30.0)
# The files are already compressed. Asking for them as they are keeps the byte count honest.
_HEADERS = {"Accept-Encoding": "identity"}
# A rename is tried this many times, a little longer apart each time (about a second in all).
_RENAME_TRIES = 5


@dataclass(frozen=True)
class ModelFile:
    url: str
    path: str  # relative to the model directory
    size: int | None  # in bytes; None when it is not pinned


@dataclass(frozen=True)
class ModelSpec:
    """One speech model.

    `files` are the paths, relative to the model directory, that exist once the model is ready.
    It comes from an archive (`archive_url`; `archive_root` is the folder in the tarball whose
    contents become the model directory, None for the top level) or from `downloads`.
    """

    name: str
    files: tuple[str, ...]
    archive_url: str | None = None
    archive_size: int | None = None
    archive_root: str | None = None
    downloads: tuple[ModelFile, ...] = ()


def _breeze(file: str) -> ModelFile:
    return ModelFile(_BREEZE + file, file, None)


REGISTRY: dict[str, ModelSpec] = {
    spec.name: spec
    for spec in (
        # Silero VAD v5, not the v6 the design names: sherpa-onnx ships only v4 and v5. v5 needs a
        # window of 512 samples.
        ModelSpec(
            name="silero-vad",
            files=("silero_vad_v5.onnx",),
            downloads=(
                ModelFile(_RELEASE + "silero_vad_v5.onnx", "silero_vad_v5.onnx", 2_313_101),
            ),
        ),
        ModelSpec(
            name="qwen3-asr",
            files=(
                "conv_frontend.onnx",
                "encoder.int8.onnx",
                "decoder.int8.onnx",
                "tokenizer/vocab.json",
                "tokenizer/merges.txt",
                "tokenizer/tokenizer_config.json",
            ),
            archive_url=f"{_RELEASE}{_QWEN3_ASR}.tar.bz2",
            archive_size=878_702_423,
            archive_root=_QWEN3_ASR,
        ),
        ModelSpec(
            name="sensevoice",
            files=("model.int8.onnx", "tokens.txt"),
            archive_url=f"{_RELEASE}{_SENSE_VOICE}.tar.bz2",
            archive_size=163_002_883,
            archive_root=_SENSE_VOICE,
        ),
        # For evaluation only, and large (about 1.8 GB): the app does not download it.
        ModelSpec(
            name="breeze-asr-25",
            files=(
                "breeze-asr-25-half-encoder.int8.onnx",
                "breeze-asr-25-half-decoder.int8.onnx",
                "breeze-asr-25-half-tokens.txt",
            ),
            downloads=(
                _breeze("breeze-asr-25-half-encoder.int8.onnx"),
                _breeze("breeze-asr-25-half-decoder.int8.onnx"),
                _breeze("breeze-asr-25-half-tokens.txt"),
            ),
        ),
    )
}


class ModelDownloadError(Exception):
    """A model that could not be installed: no connection, an HTTP error, a file of the wrong
    size, an archive that is damaged or unsafe or lacks a file, a disk that refuses the write."""


def _described(exc: Exception) -> str:
    # httpx gives a timeout no message at all.
    return str(exc) or type(exc).__name__


def _registered(name: str) -> ModelSpec:
    try:
        return REGISTRY[name]
    except KeyError:
        raise ValueError(f"Unknown speech model: {name}") from None


def _missing(directory: Path, spec: ModelSpec) -> list[str]:
    return [file for file in spec.files if not (directory / file).exists()]


def _require(directory: Path, spec: ModelSpec, label: str) -> None:
    missing = _missing(directory, spec)
    if missing:
        raise ModelDownloadError(f"{label} lacks {', '.join(missing)}")


def _content_length(response: httpx.Response) -> int:
    try:
        return max(int(response.headers.get("Content-Length", "0")), 0)
    except ValueError:
        return 0


def _discard(path: Path) -> None:
    """Delete a file if it is there. One that cannot be deleted is only logged: the error that
    brought us here matters more than the leftover."""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("Could not delete %s", path)


def _replace(source: Path, destination: Path) -> None:
    """os.replace, tried again for a moment when Windows refuses it. A virus scanner or the search
    indexer can hold a file it has just seen written, and a rename fails while it does."""
    for attempt in range(1, _RENAME_TRIES + 1):
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if attempt == _RENAME_TRIES:
                raise
            time.sleep(0.1 * attempt)


def _download(
    client: httpx.Client,
    url: str,
    destination: Path,
    expected: int | None,
    progress: Progress | None,
    label: str,
) -> None:
    """Stream `url` into `destination`, through a `.part` file that becomes `destination` only
    once it is complete.

    `progress(done, total)` is called after every chunk, so at least every megabyte, and at the
    end; `total` is `expected`, else the Content-Length, else 0. A body whose size is not
    `expected` is an error.
    """
    part = destination.with_name(destination.name + ".part")
    logger.info("Downloading %s", label)
    try:
        try:
            with client.stream(
                "GET", url, headers=_HEADERS, follow_redirects=True, timeout=_TIMEOUT
            ) as response:
                response.raise_for_status()
                total = expected if expected is not None else _content_length(response)
                done = 0
                with part.open("wb") as out:
                    for chunk in response.iter_bytes(_CHUNK):
                        out.write(chunk)
                        done += len(chunk)
                        if progress is not None:
                            progress(done, total)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            raise ModelDownloadError(f"Could not download {label}: HTTP {status}") from exc
        except httpx.HTTPError as exc:
            raise ModelDownloadError(f"Could not download {label}: {_described(exc)}") from exc
        if expected is not None and done != expected:
            raise ModelDownloadError(f"Downloaded {done} bytes of {label}, expected {expected}")
        if progress is not None and done == 0:
            progress(0, total)  # an empty body: no chunk reported anything
        _replace(part, destination)
    finally:
        _discard(part)


def _unpack(archive: Path, destination: Path, label: str) -> None:
    """Extract a .tar.bz2 into `destination`. The "data" filter refuses members that would land
    outside it (absolute paths, "..", links that lead out) and device files."""
    try:
        with tarfile.open(archive, "r:bz2") as tar:
            tar.extractall(destination, filter="data")
    except (tarfile.TarError, EOFError, OSError) as exc:
        raise ModelDownloadError(f"Could not unpack {label}: {_described(exc)}") from exc


class ModelManager:
    """Finds and installs the speech models under `root`, one directory per model.

    `client` is the httpx client to download with, which the caller then owns. Without one, each
    install makes a client of its own and closes it. Asking about a model (`dir`, `is_ready`) never
    touches the disk beyond looking; the root is created by the first install that needs it.
    """

    def __init__(self, root: Path, client: httpx.Client | None = None) -> None:
        self._root = Path(root)
        self._client = client

    def dir(self, name: str) -> Path:
        return self._root / name

    def is_ready(self, name: str) -> bool:
        return self.is_ready_spec(_registered(name))

    def is_ready_spec(self, spec: ModelSpec) -> bool:
        return not _missing(self.dir(spec.name), spec)

    def ensure(self, name: str, progress: Progress | None = None) -> Path:
        return self.ensure_spec(_registered(name), progress)

    def ensure_spec(self, spec: ModelSpec, progress: Progress | None = None) -> Path:
        """The model's directory, after downloading the model if it is not ready.

        A model that is ready is returned at once, without any request. `progress(done, total)`
        reports the bytes of the file being downloaded (of the archive, if there is one), which for
        a model made of several files starts again with each file; unpacking reports nothing. A
        callback that raises stops the download and its exception comes out of here, with nothing
        left behind. Callers that ask for a model while another is being installed wait their turn.
        """
        directory = self.dir(spec.name)
        if self.is_ready_spec(spec):
            return directory
        from_archive = spec.archive_url is not None
        if from_archive == bool(spec.downloads):
            raise ValueError(f"{spec.name}: give an archive or downloads, not both and not neither")
        with _install_lock:
            if self.is_ready_spec(spec):  # the caller we waited for has installed it
                return directory
            try:
                (self._root / _TMP).mkdir(parents=True, exist_ok=True)
                with self._http() as client:
                    if from_archive:
                        self._install_archive(client, spec, progress)
                    else:
                        self._install_files(client, spec, progress)
            except OSError as exc:  # a full disk, a file in use, no permission
                message = f"Could not install {spec.name}: {_described(exc)}"
                raise ModelDownloadError(message) from exc
        logger.info("%s is ready", spec.name)
        return directory

    @contextlib.contextmanager
    def _http(self) -> Iterator[httpx.Client]:
        if self._client is not None:
            yield self._client
        else:
            with httpx.Client() as client:
                yield client

    def _install_archive(
        self, client: httpx.Client, spec: ModelSpec, progress: Progress | None
    ) -> None:
        tmp = self._root / _TMP
        archive = tmp / f"{spec.name}.download"
        unpacked = tmp / f"{spec.name}.extract"
        try:
            shutil.rmtree(unpacked, ignore_errors=True)  # left by a run that crashed
            _download(client, spec.archive_url, archive, spec.archive_size, progress, spec.name)
            unpacked.mkdir()
            _unpack(archive, unpacked, spec.name)
            source = unpacked / (spec.archive_root or ".")
            if not source.is_dir():
                folder = spec.archive_root
                raise ModelDownloadError(f"The archive of {spec.name} has no folder {folder!r}")
            _require(source, spec, f"The archive of {spec.name}")
            target = self.dir(spec.name)
            if target.exists():
                shutil.rmtree(target)  # a partial install: a ready model never gets here
            _replace(source, target)
        finally:
            _discard(archive)
            shutil.rmtree(unpacked, ignore_errors=True)

    def _install_files(
        self, client: httpx.Client, spec: ModelSpec, progress: Progress | None
    ) -> None:
        directory = self.dir(spec.name)
        try:
            for file in spec.downloads:
                destination = directory / file.path
                destination.parent.mkdir(parents=True, exist_ok=True)
                label = f"{spec.name}/{file.path}"
                _download(client, file.url, destination, file.size, progress, label)
            _require(directory, spec, f"The downloads of {spec.name}")
        finally:
            with contextlib.suppress(OSError):
                directory.rmdir()  # only an empty one goes: a failed first try leaves nothing
