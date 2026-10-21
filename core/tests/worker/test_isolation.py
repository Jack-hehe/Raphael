"""The worker process has no key, no database and no plugin, and these tests keep it so.

The worker is the part of Raphael that a tool's result can talk to (a web page, a mail, someone
else's issue), through the model. So it must not be able to reach what is secret even if it is
led astray: the key is read in the core only, the database is the core's, and the plugins' secrets
are in the core's vault. Two checks:

- the modules of `raphael_core.worker` import nothing of the core but the protocol and the stdio
  helpers (read from their source, so a function-level import is seen too);
- a fresh interpreter that imports what `--worker` imports ends up with none of `keyring`,
  `raphael_core.brain.secrets`, `raphael_core.integrations.vault` or `sqlite3` loaded, and none of
  the libraries the core loads (the speech libraries, the HTTP client, the MCP SDK).
"""

import ast
import json
import subprocess
import sys
from pathlib import Path

import raphael_core.worker

PACKAGE = Path(raphael_core.worker.__file__).parent
MODULES = sorted(PACKAGE.glob("*.py"))

# The one thing of the core's the worker may use besides its own package.
ALLOWED_CORE_MODULES = {"raphael_core.protocol", "raphael_core.stdio_server", "raphael_core.worker"}
FORBIDDEN = (
    "keyring",
    "raphael_core.brain.secrets",
    "raphael_core.integrations.vault",
    "sqlite3",
)
# Nothing of these is loaded either: they are the core's, and the worker needs none.
HEAVY = (
    "raphael_core.brain",
    "raphael_core.integrations",
    "raphael_core.agent",
    "raphael_core.background",
    "raphael_core.app",
    "raphael_core.device",
    "httpx",
    "mcp",
    "numpy",
    "opencc",
    "sherpa_onnx",
    "sounddevice",
)


def imported_names(path: Path) -> set[str]:
    """Every module a source file imports, wherever in the file; a relative import is made whole."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    package = "raphael_core.worker"
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                base = package + ("." + base if base else "")
            names.add(base)
            names.update(f"{base}.{alias.name}" for alias in node.names)
    return names


def test_the_package_has_the_modules_this_test_is_about():
    assert {path.name for path in MODULES} >= {
        "__init__.py",
        "channel.py",
        "guards.py",
        "loop.py",
        "runner.py",
    }


def test_no_module_of_the_worker_imports_a_secret_or_a_database():
    for path in MODULES:
        for name in imported_names(path):
            for forbidden in FORBIDDEN:
                assert name != forbidden and not name.startswith(forbidden + "."), (path.name, name)


def test_no_module_of_the_worker_imports_the_core_beyond_the_protocol():
    for path in MODULES:
        for name in imported_names(path):
            if name.startswith("raphael_core"):
                assert any(
                    name == allowed or name.startswith(allowed + ".") for allowed in ALLOWED_CORE_MODULES
                ), (path.name, name)


def loaded_by(code: str) -> set[str]:
    done = subprocess.run(
        [sys.executable, "-I", "-c", code + "\nimport json,sys; print(json.dumps(sorted(sys.modules)))"],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return set(json.loads(done.stdout.splitlines()[-1]))


def test_what_the_worker_process_imports_loads_no_secret_no_database_and_no_core_library():
    # The same imports as `python -m raphael_core --worker`: __main__, then the runner.
    loaded = loaded_by(
        "import raphael_core.__main__\n"
        "from raphael_core.worker.runner import main\n"
        "import raphael_core.worker.loop, raphael_core.worker.guards, raphael_core.worker.channel\n"
    )
    assert "raphael_core.worker.runner" in loaded  # the check looked at the right thing
    for forbidden in FORBIDDEN:
        assert forbidden not in loaded, forbidden
    for heavy in HEAVY:
        assert not any(name == heavy or name.startswith(heavy + ".") for name in loaded), heavy


def test_the_same_check_would_catch_a_worker_that_imported_the_keys():
    # The check above must be able to fail: importing the key store does load all of it.
    loaded = loaded_by("import raphael_core.brain.secrets")
    assert "keyring" in loaded


def test_the_modules_the_core_side_imports_do_load_sqlite_so_the_two_sides_are_told_apart():
    loaded = loaded_by("import raphael_core.background.tasks")
    assert "sqlite3" in loaded
