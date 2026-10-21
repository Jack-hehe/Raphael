# -*- mode: python ; coding: utf-8 -*-
"""Windows x64 onedir bundle. All additional inputs come from locked distributions."""

import sys
from importlib.metadata import distribution
from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
    copy_metadata,
    get_package_paths,
)

packaging_dir = Path(SPECPATH)
core_dir = packaging_dir.parent
sys.path.insert(0, str(packaging_dir))
from notices import runtime_distributions

binaries = []
datas = []
hiddenimports = [
    "keyring.backends.Windows", "keyring.backends.fail", "keyring.backends.null",
    "opencc.clib.opencc_clib", "sherpa_onnx.lib._sherpa_onnx",
]
for package in ("sherpa_onnx", "numpy", "soxr", "opencc"):
    binaries += collect_dynamic_libs(package)
for package in ("soxr", "win32ctypes"):
    hiddenimports += collect_submodules(package, filter=lambda name: "tests" not in name.split("."))

# sherpa-onnx-core is a distribution, not an importable package. Keep its DLLs beside the binding.
sherpa_core = distribution("sherpa-onnx-core")
for item in sherpa_core.files or []:
    relative = Path(str(item))
    if relative.parts[:2] == ("sherpa_onnx", "lib") and relative.suffix.lower() == ".dll":
        binaries.append((str(sherpa_core.locate_file(item)), "sherpa_onnx/lib"))

# Delvewheel places OpenCC and NumPy runtime DLLs next to, rather than in, their packages.
for package in ("opencc", "numpy"):
    parent, _ = get_package_paths(package)
    library_dir = Path(parent) / f"{package}.libs"
    binaries += [(str(path), library_dir.name) for path in library_dir.glob("*.dll")]

datas += collect_data_files("opencc", includes=["clib/share/opencc/*.json", "clib/share/opencc/*.ocd2"])
datas += collect_data_files("raphael_core.brain.prompts", includes=["*.md"])
for name in runtime_distributions():
    datas += copy_metadata(name)
datas.append((str(core_dir / "build" / "notices"), "licenses"))

analysis = Analysis(
    [str(packaging_dir / "entrypoint.py")],
    pathex=[str(core_dir / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[str(packaging_dir / "hooks")],
    runtime_hooks=[],
    excludes=[
        "pytest", "_pytest", "setuptools", "pkg_resources", "pygments",
        "raphael_core.evaluate", "tkinter", "unittest", "win32ctypes.tests", "numpy.testing",
        # NumPy's own PyInstaller hook adds this C test helper; only NumPy's tests import it.
        "numpy._core._multiarray_tests",
    ],
    noarchive=False,
)


def unneeded(item):
    """Files that are collected as a side effect but that the core never loads at run time.

    The pyinstaller-hooks-contrib OpenCC hook copies the whole wheel, including its command-line
    tools, C++ headers and the static libraries opencc.lib and marisa.lib (about 8 MB); the
    conversion itself needs only opencc_clib.pyd and the dictionaries under clib/share/opencc.
    NumPy's C test helpers (_multiarray_tests, _umath_tests, ...) are imported only by its tests.
    """
    destination = item[0].replace("\\", "/")
    stem = destination.rsplit("/", 1)[-1].split(".")[0]
    return (
        destination.endswith("direct_url.json")  # local editable-install provenance
        or destination.startswith(("opencc/clib/bin/", "opencc/clib/include/", "opencc/clib/lib/"))
        or (destination.endswith((".pyd", ".dll")) and stem.endswith(("_test", "_tests")))
    )


analysis.datas = [item for item in analysis.datas if not unneeded(item)]
analysis.binaries = [item for item in analysis.binaries if not unneeded(item)]
for name, _, _ in analysis.pure:
    if name.split(".")[0] in {"pytest", "_pytest", "setuptools", "pygments"}:
        raise RuntimeError(f"Unexpected development module in bundle: {name}")
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz, analysis.scripts, [], exclude_binaries=True, name="raphael-core",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=True, disable_windowed_traceback=False, contents_directory="_internal",
)
collect = COLLECT(exe, analysis.binaries, analysis.datas, strip=False, upx=False, name="raphael-core")
