"""Repository boundaries run on every contributor's checkout, without local hooks."""

import ast
import re
from pathlib import Path

from raphael_core.agent.harness_tools import DEFINITIONS, definitions_for
from raphael_core.agent.team_admin import DEFINITIONS as ADMIN

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "core/src/raphael_core"


def parsed(path):
    return ast.parse(path.read_text(encoding="utf-8"))


def test_members_and_rooms_do_not_create_brain_loops():
    for path in (SRC / "members").rglob("*.py"):
        tree = parsed(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not any(part in (node.module or "").split(".") for part in ("openrouter", "narration", "cli")), path
            if isinstance(node, ast.Call):
                assert getattr(node.func, "id", "") not in {"NarratedLoop", "CliLoop", "OpenRouterClient"}, path


def test_administration_is_raphael_only():
    assert set(ADMIN) <= dict(definitions_for(0)).keys()
    for member in (1, 2, 999):
        assert not set(ADMIN) & dict(definitions_for(member)).keys()
        assert "member_id" not in dict(definitions_for(member))["routine_create"]["parameters"]["properties"]


def test_shared_loop_and_definition_sources():
    for path in (SRC / "agent/service.py", SRC / "evaluate/guardian.py"):
        tree = parsed(path)
        called = {getattr(node.func, "id", "") for node in ast.walk(tree) if isinstance(node, ast.Call)}
        assert {"take_snapshot", "CliLoop", "NarratedLoop"} <= called


def test_one_mcp_server():
    assert (SRC / "agent/mcp_server.py").read_text().count("class RaphaelMcp:") == 1
    for folder in (SRC / "agent/cli", SRC / "evaluate"):
        for path in folder.rglob("*.py"):
            for node in ast.walk(parsed(path)):
                assert not isinstance(node, ast.ClassDef) or node.name != "RaphaelMcp", path


def test_evaluator_credentials_are_ci_scoped(monkeypatch):
    from raphael_core.evaluate.credentials import key_provider
    from raphael_core.brain.secrets import CredentialKeyStore
    monkeypatch.setattr(CredentialKeyStore, "get", lambda self: "vault-fake")
    monkeypatch.setenv("OPENROUTER_API_KEY", "ci-fake")
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    assert key_provider() == "vault-fake"
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert key_provider() == "ci-fake"
    for path in (SRC / "evaluate").glob("*.py"):
        tree = parsed(path)
        if path.name != "credentials.py":
            assert not any(isinstance(node, ast.Constant) and node.value == "OPENROUTER_API_KEY" for node in ast.walk(tree))


def test_settings_have_one_unique_registry():
    tree = parsed(SRC / "brain/settings.py")
    registry = next(node.value for node in tree.body if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "DEFAULTS")
    keys = [node.value for node in registry.keys]
    assert len(set(keys)) == len(keys)


def test_open_has_no_site_allowlist():
    schema = DEFINITIONS["open"]["parameters"]
    assert set(schema["properties"]) == {"target_kind", "target"}
    assert "enum" not in schema["properties"]["target"]


async def test_shell_named_rpc_methods_have_registered_core_handlers(tmp_path):
    from raphael_core.app import App

    source = (ROOT / "app/src-tauri/src/commands.rs").read_text(encoding="utf-8")
    block = re.search(r"const ALLOWED_METHODS:[^=]+?=\s*&?\[(.*?)\];", source, re.S)
    assert block
    methods = set(re.findall(r'^\s*"([a-z][a-z0-9_.]+)"', block[1], re.M))
    assert methods
    app = App(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory", fake_llm=True)
    try:
        assert not methods - set(app.methods())
    finally:
        await app.aclose()
