"""Build and audit a locked Windows x64 core, independent of the caller's working directory."""

from __future__ import annotations

import hashlib
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from notices import write_notices


CORE = Path(__file__).resolve().parent.parent
BUNDLE = CORE / "dist" / "raphael-core"


def audit_bundle(bundle: Path) -> dict:
    required = [
        "raphael-core.exe", "_internal/python312.dll",
        "_internal/sherpa_onnx/lib/onnxruntime.dll",
        "_internal/sherpa_onnx/lib/sherpa-onnx-c-api.dll",
        "_internal/sherpa_onnx/lib/sherpa-onnx-cxx-api.dll",
        "_internal/_sounddevice_data/portaudio-binaries/libportaudio64bit.dll",
        "_internal/raphael_core/brain/prompts/cleanup_system.md",
        "_internal/licenses/python/LICENSE.txt",
        "_internal/licenses/pyinstaller/pyinstaller-6.22.3.dist-info/licenses/COPYING.txt",
        "_internal/opencc/clib/share/opencc/s2twp.json",
    ]
    missing = [name for name in required if not (bundle / name).is_file()]
    if missing:
        raise RuntimeError(f"Required bundled files are missing: {missing}")
    forbidden_parts = {"tests", "test", "fixtures", "models", "assets-private", "icons-private", "raphael_ref", ".venv"}
    forbidden_suffixes = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".mp4", ".onnx", ".pth", ".ckpt", ".safetensors", ".db", ".sqlite", ".key"}
    files = []
    for path in sorted(bundle.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(bundle)
        if any(part.lower() in forbidden_parts for part in relative.parts) or path.suffix.lower() in forbidden_suffixes or path.name.startswith(".env"):
            raise RuntimeError(f"Private or development data in bundle: {relative}")
        files.append({"path": relative.as_posix(), "bytes": path.stat().st_size,
                      "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return {"format": 1, "python": platform.python_version(), "architecture": platform.machine(),
            "total_bytes": sum(item["bytes"] for item in files), "files": files}


def main() -> None:
    if sys.platform != "win32" or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise SystemExit("Build this artifact with Windows x64 Python.")
    if sys.version_info[:2] != (3, 12):
        raise SystemExit("The locked core requires Python 3.12.")
    write_notices(CORE / "build" / "notices")
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--distpath", str(CORE / "dist"), "--workpath", str(CORE / "build" / "pyinstaller"),
        str(CORE / "packaging" / "raphael-core.spec"),
    ], cwd=CORE, check=True)
    shutil.copyfile(CORE / "build" / "notices" / "THIRD_PARTY_NOTICES.txt", BUNDLE / "THIRD_PARTY_NOTICES.txt")
    manifest = audit_bundle(BUNDLE)
    (BUNDLE / "bundle-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"bundle": str(BUNDLE), "total_bytes": manifest["total_bytes"], "files": len(manifest["files"])}, indent=2))


if __name__ == "__main__":
    main()
