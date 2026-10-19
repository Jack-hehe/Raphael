"""Collect installed runtime distribution metadata and license files without repository data."""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from importlib.metadata import distribution
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


def runtime_distributions() -> list[str]:
    pending = ["raphael-core"]
    seen: set[str] = set()
    while pending:
        name = canonicalize_name(pending.pop())
        if name in seen:
            continue
        seen.add(name)
        for raw in distribution(name).requires or []:
            requirement = Requirement(raw)
            if requirement.marker is None or requirement.marker.evaluate({"extra": ""}):
                pending.append(requirement.name)
    return sorted(seen)


def write_notices(target: Path) -> None:
    expected = Path(__file__).resolve().parent.parent / "build" / "notices"
    if target.resolve() != expected.resolve():
        raise RuntimeError("Notices must be generated inside this core's build directory")
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    inventory = []
    lines = [
        "Third-party components in the frozen Raphael core", "",
        "License files below are copied verbatim from the locked installed distributions.",
        "The Python runtime and PyInstaller bootloader license are included separately.",
        "Speech models, ASIO binaries, recordings, credentials and private assets are not bundled.",
        "This inventory covers the Python core; the desktop installer has additional components.", "",
    ]
    for name in [*runtime_distributions(), "pyinstaller"]:
        dist = distribution(name)
        if name == "raphael-core":
            continue
        folder = target / name
        folder.mkdir(exist_ok=True)
        (folder / "METADATA").write_text(dist.read_text("METADATA") or "", encoding="utf-8")
        licenses = []
        for item in dist.files or []:
            relative = Path(str(item))
            if ".." in relative.parts or not any(
                word in relative.name.lower() for word in ("license", "copying", "notice", "authors")
            ):
                continue
            source = Path(dist.locate_file(item))
            if not source.is_file():
                continue
            destination = folder / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            licenses.append(destination.relative_to(target).as_posix())
        inventory.append({"name": name, "version": dist.version, "license_files": licenses})
        lines.append(f"- {name} {dist.version}: {', '.join(licenses) or 'see METADATA and upstream sources below'}")
    python_folder = target / "python"
    python_folder.mkdir(exist_ok=True)
    shutil.copyfile(Path(sys.base_prefix) / "LICENSE.txt", python_folder / "LICENSE.txt")
    lines.extend([
        "", "## Native dependencies", "",
        "sherpa-onnx-core installs its DLLs into sherpa_onnx/lib; it has no separate license file in its wheel.",
        "Its Apache-2.0 declaration is preserved in METADATA; sherpa-onnx's Apache license is included.",
        "The release notice includes the upstream ONNX Runtime 1.28.2 third-party notices.",
        "PortAudio's upstream license is included; the wheel's PortAudio README is preserved in _internal.",
        "NumPy and python-soxr wheel licenses include their bundled native dependency notices.",
        "", "Upstream sources:",
        "- https://github.com/k2-fsa/sherpa-onnx/blob/v1.13.8/LICENSE",
        "- https://github.com/k2-fsa/sherpa-onnx/blob/v1.13.8/cmake/onnxruntime-win-x64.cmake",
        "- https://github.com/microsoft/onnxruntime/blob/v1.28.2/LICENSE",
        "- https://github.com/microsoft/onnxruntime/blob/v1.28.2/ThirdPartyNotices.txt",
        "- https://github.com/PortAudio/portaudio/blob/v19.7.0/LICENSE.txt", "",
    ])
    upstream = Path(__file__).parent / "licenses"
    source_manifest = json.loads((upstream / "sources.json").read_text(encoding="utf-8"))
    for item in source_manifest:
        source = upstream / item["file"]
        if hashlib.sha256(source.read_bytes()).hexdigest() != item["sha256"]:
            raise RuntimeError(f"Upstream license checksum mismatch: {item['file']}")
    shutil.copytree(upstream, target / "upstream", dirs_exist_ok=True)
    # Provide one self-contained text file for release assets and the installer notice.
    license_paths = [target / item for dist in inventory for item in dist["license_files"]]
    license_paths += [python_folder / "LICENSE.txt"]
    license_paths += [target / "upstream" / item["file"] for item in source_manifest]
    for path in license_paths:
        lines.extend(["", "=" * 72, path.relative_to(target).as_posix(), "=" * 72,
                      path.read_text(encoding="utf-8", errors="replace")])
    (target / "THIRD_PARTY_NOTICES.txt").write_text("\n".join(lines), encoding="utf-8")
    (target / "distributions.json").write_text(json.dumps(inventory, indent=2) + "\n", encoding="utf-8")
