"""Toolsets: the tools Raphael can use, in groups the user sees on the 「連接」 tab.

A group is a `ToolSet`: a name, a label, a description, the tools it holds and the groups it
includes (`resolve` flattens them; a group that includes itself, directly or not, is read once).
Raphael's own tools are two groups with a fixed tier each, shown and never changed: 「網路查詢」
(`web`: the weather, a web search, the time; all T0) and 「這台電腦」 (`computer`: the commands
for this computer, T1 or T2 as `agent.commands` has them). Every plugin server is one more
group, whose tools have the tier the user gave them (`IntegrationsService` builds those).

Giving a whole group a tier (`integrations.set_group_tier`) is a one-time act on the tools the
server has now: a tool the server adds later has no tier, so it stays blocked until the user has
looked at it. Nothing here logs a name, an argument or a result.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field

from ..agent.commands import COMMANDS
from ..agent.policy import Tier


@dataclass(frozen=True)
class ToolSet:
    name: str
    label: str
    description: str
    tools: tuple[str, ...] = ()
    includes: tuple[str, ...] = ()
    # Built in: shown, never switched off, never given another tier.
    builtin: bool = False
    tiers: Mapping[str, str] = field(default_factory=dict)


# What the weather, the search and the clock are called in the assistant (`agent.service`).
WEB_TOOLS = ("weather", "web_search", "local_time")

BUILTIN_SETS: dict[str, ToolSet] = {
    "web": ToolSet(
        name="web",
        label="Web lookups",
        description="The weather, a web search and the time. They only read.",
        tools=WEB_TOOLS,
        builtin=True,
        tiers={name: Tier.T0.name for name in WEB_TOOLS},
    ),
    "computer": ToolSet(
        name="computer",
        label="This computer",
        description="Open a website or an app, the volume, the media keys, lock the screen, copy.",
        tools=tuple(COMMANDS),
        builtin=True,
        tiers={name: command.tier.name for name, command in COMMANDS.items()},
    ),
}


def resolve(name: str, sets: Mapping[str, ToolSet] = BUILTIN_SETS) -> list[str]:
    """The tools of group `name` and of the groups it includes, in order, each once. A name that
    is not a group has no tools."""
    found: list[str] = []
    seen: set[str] = set()

    def walk(current: str) -> None:
        if current in seen or current not in sets:
            return
        seen.add(current)
        for tool in sets[current].tools:
            if tool not in found:
                found.append(tool)
        for included in sets[current].includes:
            walk(included)

    walk(name)
    return found


def builtin_group(toolset: ToolSet) -> dict:
    """A built-in group as the window sees it: always on, nothing to switch, tiers fixed."""
    return {
        "id": f"builtin:{toolset.name}",
        "kind": "builtin",
        "name": toolset.name,
        "label": toolset.label,
        "description": toolset.description,
        "enabled": True,
        "switchable": False,
        "tier_fixed": True,
        "status": "builtin",
        "error": None,
        "tools": [
            {
                "name": tool,
                "description": "",
                "tier": toolset.tiers.get(tool),
                "changed": False,
                "suggested": None,
            }
            for tool in resolve(toolset.name)
        ],
    }


def builtin_groups() -> list[dict]:
    return [builtin_group(toolset) for toolset in BUILTIN_SETS.values()]
