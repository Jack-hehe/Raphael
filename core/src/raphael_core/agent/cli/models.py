"""Public CLI model metadata. Discovery never starts a conversation."""

import asyncio
import json
import re
import tomllib

from .process import Child

# Alias display names are deliberately centralized for CLI releases that expose no model-list API.
CLAUDE_MODELS = (
    ("sonnet", "Claude Sonnet 5.5", "Balanced speed and reasoning", ("low", "medium", "high")),
    ("opus", "Claude Opus 5.5", "Complex reasoning and demanding work", ("low", "medium", "high", "max")),
    ("haiku", "Claude Haiku 5.5", "Fast everyday tasks", ()),
)
EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")


def read_settings(path, *, toml=False):
    try:
        text = path.read_text(encoding="utf-8-sig")
        value = tomllib.loads(text) if toml else json.loads(text)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def claude_models(folder):
    config = read_settings(folder / "settings.json")
    default = config.get("model") or "sonnet"
    models = [{"id": f"cli:claude:{alias}", "name": name, "description": description,
               "reasoning_efforts": list(efforts), "default_effort": "high" if efforts else None,
               "is_default": default == alias}
              for alias, name, description, efforts in CLAUDE_MODELS]
    if not any(model["is_default"] for model in models) and isinstance(default, str) and re.fullmatch(r"[\w./-]{1,200}", default):
        models.append({"id": f"cli:claude:{default}", "name": default.replace("-", " ").title(),
                       "description": "Configured CLI model", "is_default": True})
    return models


def agy_models(output, settings):
    """Group public model variants while retaining the exact runnable model refs."""
    default = read_settings(settings).get("model")
    families = {}
    for line in output.splitlines():
        identifier, sep, display = line.partition("\t")
        if not sep or not re.fullmatch(r"[\w./-]{1,200}", identifier):
            continue
        match = re.fullmatch(r"(.+)-(low|medium|high|xhigh|max)", identifier)
        family, effort = match.groups() if match else (identifier, None)
        maker = "Anthropic" if family.startswith("claude-") else "OpenAI" if family.startswith("gpt-") else "Google" if family.startswith("gemini-") else ""
        model = families.setdefault(family, {"id": f"cli:gemini:{identifier}", "provider": maker,
            "name": re.sub(r"\s+(Low|Medium|High|Xhigh|Max)$", "", display, flags=re.I) if effort else display,
            "reasoning_efforts": [], "effort_models": {}, "is_default": False})
        if effort and effort not in model["reasoning_efforts"]:
            model["id"] = f"cli:gemini:{family}"
            model["reasoning_efforts"].append(effort)
            model["effort_models"][effort] = f"cli:gemini:{identifier}"
        if default == display:
            model.update(is_default=True, default_effort=effort)
    for model in families.values():
        model["reasoning_efforts"].sort(key=EFFORTS.index)
    return list(families.values())


async def antigravity_models(command, environ, settings):
    child = Child([*command, "models"], environ=environ)
    try:
        async with asyncio.timeout(12):
            await child.start()
            output = bytearray()
            while chunk := await child.process.stdout.read(65536):
                output.extend(chunk)
                if len(output) > 2_000_000:
                    raise ValueError("model_list_unavailable")
            await child.process.wait()
            if child.process.returncode:
                raise ValueError("model_list_unavailable")
            return agy_models(output.decode("utf-8-sig", errors="replace"), settings)
    finally:
        await child.kill()


async def codex_models(command, environ, config_path):
    child = Child([*command, "app-server"], environ=environ)
    sequence = 0

    async def request(method, params):
        nonlocal sequence
        sequence += 1
        await child.send({"jsonrpc": "2.0", "id": sequence, "method": method, "params": params})
        while True:
            line = await child.process.stdout.readline()
            if not line:
                raise ValueError("model_list_unavailable")
            packet = json.loads(line)
            if packet.get("id") == sequence:
                if "error" in packet:
                    raise ValueError("model_list_unavailable")
                return packet.get("result") or {}

    try:
        async with asyncio.timeout(12):
            await child.start()
            await request("initialize", {"clientInfo": {"name": "raphael", "version": "0.2.0"}, "capabilities": {}})
            await child.send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
            rows, cursor = [], None
            for _ in range(20):
                page = await request("model/list", {"cursor": cursor, "limit": 100})
                rows.extend(page.get("data", []))
                cursor = page.get("nextCursor")
                if not cursor:
                    break
    finally:
        await child.kill()
    config = read_settings(config_path, toml=True)
    models = []
    for row in rows:
        identifier = row.get("model") or row.get("id")
        if row.get("hidden") or not isinstance(identifier, str) or not re.fullmatch(r"[\w./-]{1,200}", identifier):
            continue
        levels = [item["reasoningEffort"] for item in row.get("supportedReasoningEfforts", [])
                  if isinstance(item, dict) and item.get("reasoningEffort") in EFFORTS]
        default = identifier == config["model"] if config.get("model") else bool(row.get("isDefault"))
        effort = config.get("model_reasoning_effort") if default else None
        effort = effort if effort in levels else row.get("defaultReasoningEffort")
        models.append({"id": f"cli:codex:{identifier}", "name": row.get("displayName") or identifier,
                       "description": row.get("description") or "", "is_default": default,
                       "reasoning_efforts": levels, "default_effort": effort if effort in levels else None})
    return models
