"""What the assistant knows of the plugins: the tools it may offer a model, and the door to call one.

The plugins themselves (`raphael_core.integrations`) are MCP servers the user connected, and each
of their tools has a tier only because the user gave it one. This module is the assistant's side of
that: a `PluginHub` says which tools may be offered to a model at all (those with a tier, on a
plugin that is on, that are still the tool the user gave the tier to), what tier a tool has now,
and calls one. The assistant never reaches a plugin any other way.

How a model gets to the tools. For a request that may need them, one call to the language model
sees the request, the last few turns, and only the tools that may be offered, and answers with the
calls it wants (`plan`). It does not see a result: the calls are made by the program, within the
tiers, and their results go to the call that words the answer, as data. So nothing a plugin
returns can make the model call another tool; that takes a new request from the user.

Nothing here logs a request, an argument or a result.
"""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from .policy import Tier

KEY_PREFIX = "mcp:"
# The most tools offered to a model in one request, and the most calls it may ask for.
MAX_OFFERED = 40
MAX_CALLS = 3
# The most the arguments of one call may come to, as JSON, in characters.
MAX_ARGUMENT_CHARS = 16000
# What of a tool's description a model is shown, in characters.
DESCRIPTION_CHARS = 400
# How much of the arguments the user is shown in the question that asks whether to run a call.
PREVIEW_CHARS = 60

_ALIAS_UNSAFE = re.compile(r"[^A-Za-z0-9_-]+")


def tool_key(server_id: str, tool: str) -> str:
    """The name of a plugin tool as the policy knows it."""
    return f"{KEY_PREFIX}{server_id}/{tool}"


def is_plugin_key(name: str) -> bool:
    return name.startswith(KEY_PREFIX)


@dataclass(frozen=True)
class PluginTool:
    """A tool that may be offered to a model."""

    key: str
    server: str  # the name of the plugin, as the user knows it
    name: str  # the name of the tool on its server
    description: str
    schema: dict[str, Any]
    tier: Tier


@dataclass
class PluginResult:
    """What came of calling a tool. `kind` says why it did not work ("blocked", "timeout",
    "tool_error", "network", ...); `text` is what the tool returned, which nobody has vetted."""

    ok: bool
    kind: str | None = None
    text: str = ""
    truncated: bool = False


class PluginHub(Protocol):
    def offered(self) -> list[PluginTool]:
        """The tools that may be offered to a model now."""
        ...

    def tier_of(self, key: str) -> Tier | None:
        """The tier the user gave the tool now, or None when it has none (or is not usable)."""
        ...

    async def call(self, key: str, arguments: Mapping[str, Any], *, approver: str) -> PluginResult:
        """Call a tool and write the audit. `approver` is "auto" for a call that needs no yes, and
        "user" for one the user said yes to; a call at T2 or T3 is refused without "user"."""
        ...

    def record_unrun(self, key: str, *, outcome: str) -> None:
        """Write the audit of a call that was asked about and not made: "denied" or "expired"."""
        ...


@dataclass(frozen=True)
class PlannedCall:
    """A call a model asked for, resolved to a tool that may be offered."""

    tool: PluginTool
    arguments: dict[str, Any]


@dataclass
class Offer:
    """The tools of one request, by the names a model calls them."""

    tools: list[PluginTool] = field(default_factory=list)
    by_alias: dict[str, PluginTool] = field(default_factory=dict)

    def definitions(self) -> list[dict[str, Any]]:
        """The tools in the form a chat model takes them."""
        out = []
        for alias, tool in self.by_alias.items():
            out.append(
                {
                    "type": "function",
                    "function": {
                        "name": alias,
                        "description": f"[{tool.server}] {tool.description[:DESCRIPTION_CHARS]}".strip(),
                        "parameters": tool.schema if tool.schema.get("type") == "object" else _EMPTY,
                    },
                }
            )
        return out


_EMPTY: dict[str, Any] = {"type": "object", "properties": {}}


def make_offer(tools: list[PluginTool]) -> Offer:
    """What to offer a model out of the tools that may be: the safest first, at most MAX_OFFERED,
    each under a name of letters, digits, `_` and `-` (at most 64) that no other has."""
    ordered = sorted(tools, key=lambda t: (t.tier, t.server.casefold(), t.name))[:MAX_OFFERED]
    offer = Offer(tools=ordered)
    for tool in ordered:
        base = _ALIAS_UNSAFE.sub("_", f"{tool.server}__{tool.name}").strip("_")[:60] or "tool"
        alias, n = base, 1
        while alias in offer.by_alias:
            n += 1
            alias = f"{base[:56]}_{n}"
        offer.by_alias[alias] = tool
    return offer


def resolve_calls(offer: Offer, requested: list[tuple[str, Any]]) -> list[PlannedCall]:
    """The calls a model asked for as calls of the tools it was offered. A call to a name that was
    not offered, or with arguments that are not an object or are far too long, is dropped; so is
    everything past MAX_CALLS."""
    planned: list[PlannedCall] = []
    for alias, arguments in requested:
        tool = offer.by_alias.get(alias)
        if tool is None or not isinstance(arguments, dict):
            continue
        if len(json.dumps(arguments, ensure_ascii=False)) > MAX_ARGUMENT_CHARS:
            continue
        planned.append(PlannedCall(tool, arguments))
        if len(planned) == MAX_CALLS:
            break
    return planned


def preview(arguments: Mapping[str, Any]) -> str:
    """The arguments of a call as a short line for the question that asks whether to run it: the
    values, one line, cut. It goes on the overlay and nowhere else."""
    parts = []
    for name, value in arguments.items():
        shown = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        parts.append(f"{name}: {shown}")
    line = " ".join(" ".join(parts).split())
    line = "".join(char for char in line if char.isprintable())
    return line if len(line) <= PREVIEW_CHARS else line[: PREVIEW_CHARS - 1] + "…"
