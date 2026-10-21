"""The tools a task may use: a snapshot taken when the task starts.

The worker is offered the tools by an alias and a schema and nothing else; the table that says which
tool an alias is stays here in the core. The snapshot holds:

- the tools of the plugins the user connected that have a tier of T0 or T1 (`PluginHub.offered`):
  the ones that read, and the ones that change this computer in a way that is easily undone. The tools
  at T2 and T3 need a yes at the overlay, which the worker cannot ask for yet, so they are left out;
- Raphael's own lookups on the web: the weather, a web search and the time.

Not the commands for this computer (open a site, the volume, lock the screen, copy): those stay with
the fast tier, which the user speaks to; a task in the background must not open a page or lock the
screen while the user is typing.

A snapshot is the offer, not the permission. Every call is judged again by the gate when it is made
(`agent.gate`), against the tier the tool has then, so a tool whose tier was raised, taken away or
changed since the snapshot is refused.
"""

from dataclasses import dataclass
from typing import Any

from ..agent.gate import WEB_TOOLS
from ..agent.plugins import PluginHub, make_offer
from ..agent.policy import Tier

# The most a task is offered at the highest tier of its snapshot.
SNAPSHOT_MAX_TIER = Tier.T1

BUILTIN = "builtin"
MCP = "mcp"
BUILTIN_SERVER = "Raphael"

_DEFINITIONS: dict[str, dict[str, Any]] = {
    "weather": {
        "description": (
            "[Raphael] The weather now, today and tomorrow in a city. Leave the city out for the "
            "user's own city."
        ),
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string", "description": "The name of the city."}},
        },
    },
    "web_search": {
        "description": (
            "[Raphael] Search the web and get a short answer with the pages it drew on. Use it "
            "for current or specific facts; the weather and the time have tools of their own."
        ),
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "What to look up."}},
            "required": ["query"],
        },
    },
    "local_time": {
        "description": "[Raphael] The date and the time on the user's computer.",
        "parameters": {"type": "object", "properties": {}},
    },
}


@dataclass(frozen=True)
class ToolRef:
    """What an alias stands for. `key` is the name the gate knows the tool by."""

    kind: str  # MCP or BUILTIN
    key: str
    server: str
    name: str
    tier: Tier  # at the moment of the snapshot; the gate asks again at the call


@dataclass
class Snapshot:
    refs: dict[str, ToolRef]
    definitions: list[dict[str, Any]]


def take_snapshot(plugins: PluginHub | None) -> Snapshot:
    """The tools a task starting now may use. A plugin that fails to list its tools is left out."""
    refs: dict[str, ToolRef] = {}
    definitions: list[dict[str, Any]] = []
    for name in WEB_TOOLS:
        refs[name] = ToolRef(BUILTIN, name, BUILTIN_SERVER, name, Tier.T0)
        definitions.append({"type": "function", "function": {"name": name, **_DEFINITIONS[name]}})
    if plugins is not None:
        try:
            tools = [tool for tool in plugins.offered() if tool.tier <= SNAPSHOT_MAX_TIER]
        except Exception:  # a plugin that breaks leaves its tools out, not the task
            tools = []
        offer = make_offer(tools)
        clash = set(offer.by_alias) & set(WEB_TOOLS)
        if clash:  # a plugin's tool never takes the name of one of Raphael's own
            offer = make_offer([tool for alias, tool in offer.by_alias.items() if alias not in clash])
        for alias, tool in offer.by_alias.items():
            refs[alias] = ToolRef(MCP, tool.key, tool.server, tool.name, tool.tier)
        definitions.extend(offer.definitions())
    return Snapshot(refs, definitions)
