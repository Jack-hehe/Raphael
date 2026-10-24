"""The tools a task may use: a snapshot taken when the task starts.

The worker is offered the tools by an alias and a schema and nothing else; the table that says which
tool an alias is stays here in the core. The snapshot holds:

- the tools of the plugins the user connected that are on (`PluginHub.offered`), whether they read or
  change something there: the user turned off what a task must not use, and a tool that is on runs
  without asking, for a task as for the assistant, unless one of the owner's safety rules asks
  first. A tool at T2 is offered too: its call pauses the task and asks the owner in the app (the
  waiting question, `service`), and goes on only after a yes there. A tool at T3 is left out.
- Raphael's own lookups on the web: the weather, a web search (by the search service the owner
  chose), the time, and the reading of a page (`web_read`, by the reader chosen). A page is read
  only at an address the task has seen: in its request, or in what one of its tools returned
  (`run.TaskRun`, which refuses any other with "url_not_seen"), so a task cannot be made to send
  what it knows to an address of someone's making;
- when the task came with files (an image, a PDF the owner handed over), `read_attachment`, which
  reads one of them by its number: never a path;
- and, when there is a memory, its two tools that only read (`memory_read`, `history_search`): a
  task may read a note or look up what was said before. It never writes one: a task reads what
  services return, and a memory is written only from the owner's own words.
- the team tools (`agent.team`): `ask_member`, when there is someone to ask (for Raphael, a member;
  for a member, Raphael), and for Raphael `create_member`.

A task of a member of the owner's team is offered only what the member was given (`Speaker.uses`,
`Speaker.uses_plugin`): Raphael's own web tools on its list, and the connections on it.

Not the commands for this computer (open a site, the volume, lock the screen, copy): those stay with
the fast tier, which the user speaks to; a task in the background must not open a page or lock the
screen while the user is typing. Nor the tools that operate the owner's desktop
(`computer.tools.PRESENCE_TOOLS`, which `needs_presence`): no task is offered them, no plugin's
tool takes one of their names, and the gate refuses them to the worker in any case.

A snapshot is the offer, not the permission. Every call is judged again by the gate when it is made
(`agent.gate`), against the tier the tool has then, so a tool whose tier was raised, taken away or
changed since the snapshot is refused.
"""

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Any

from ..agent.gate import ATTACHMENT_TOOLS, MEMORY_READ_TOOLS, WEB_TOOLS
from ..agent.history import Turn
from ..agent.ledger import Ledger
from ..agent.plugins import MAX_OFFERED, Offer, PluginHub, make_offer
from ..agent.policy import Tier
from ..agent.speaker import RAPHAEL, Speaker
from ..agent.team import TEAM_TIER, TEAM_TOOLS, worker_definition
from ..agent.harness_tools import DEFINITIONS as HARNESS_DEFINITIONS, tool as harness_tool
from ..computer.tools import PRESENCE_TOOLS
from ..agent.toolpick import (
    PICK_MAX,
    ToolPicker,
    first_sentence,
    scored,
    shortlist,
    widen,
)

# The highest tier a task is offered: T2, whose calls ask the owner through the waiting question
# (and so only after a yes in the app). A tool that cannot be undone at all (T3) is never offered.
SNAPSHOT_MAX_TIER = Tier.T2

BUILTIN = "builtin"
MCP = "mcp"
FIND = "find"  # the tool that looks for more tools in the snapshot
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
    "web_read": {
        "description": (
            "[Raphael] Read a web page as text. Only an address you have seen in the task or in "
            "what a tool of this task returned (a search result, a page read) can be read."
        ),
        "parameters": {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "The page's address."}},
            "required": ["url"],
        },
    },
    "read_attachment": {
        "description": (
            "[Raphael] Read a file the user handed over with the task, by its number (1 for "
            "the first): images, PDF/text/Office documents, or transcribed audio/video. Unsupported "
            "formats return metadata and a reading limitation. Ask a question about it to "
            "say what to look for."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "index": {"type": "integer", "description": "The file's number, from 1."},
                "question": {"type": "string", "description": "What to look for in it."},
            },
            "required": ["index"],
        },
    },
    "find_tools": {
        "description": (
            "[Raphael] Look for more tools of the connected services by what you want to do "
            "(for example: search pages, update a page). The tools it finds can be called from "
            "then on."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What you want a tool to do."}
            },
            "required": ["query"],
        },
    },
    "local_time": {
        "description": "[Raphael] The date and the time on the user's computer.",
        "parameters": {"type": "object", "properties": {}},
    },
    "memory_read": {
        "description": (
            "[Raphael] Read your own notes and shared user facts in full, by their names (the names in "
            "the index of notes you were given)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "names": {"type": "array", "items": {"type": "string"}, "description": "Names."}
            },
            "required": ["names"],
        },
    },
    "history_search": {
        "description": (
            "[Raphael] Search what the user and you said in this speaker's earlier conversations, by a few "
            "words. Returns the matching turns with their dates."
        ),
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "A few words."}},
            "required": ["query"],
        },
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


# The most tools one find_tools call adds.
FIND_MAX = 6


@dataclass
class Snapshot:
    """The tools of a task. `refs` and `definitions` are what the worker may call now. `pool` is
    every tool of the plugins that could be offered, by alias: when it holds more than the task was
    shown, the rest can be found (`find`) and shown from then on."""

    refs: dict[str, ToolRef]
    definitions: list[dict[str, Any]]
    pool: Offer | None = None

    def hidden(self) -> list[str]:
        """The aliases of the pool that the task has not been shown."""
        return [alias for alias in (self.pool.by_alias if self.pool else {}) if alias not in self.refs]

    def show(self, aliases: Sequence[str]) -> list[dict[str, Any]]:
        """Show the task these tools of the pool; returns the definitions that are new."""
        if self.pool is None:
            return []
        added = []
        for alias in aliases:
            tool = self.pool.by_alias.get(alias)
            if tool is None or alias in self.refs:
                continue
            self.refs[alias] = ToolRef(MCP, tool.key, tool.server, tool.name, tool.tier)
            definition = self.pool.definition(alias)
            self.definitions.append(definition)
            added.append(definition)
        return added

    def find(self, query: str) -> tuple[list[dict[str, Any]], list[str]]:
        """Show the task the tools of the pool that fit `query` best (not the ones it has): returns
        their definitions and a line about each for the worker to read."""
        hidden = self.hidden()
        if self.pool is None or not hidden:
            return [], []
        rows = scored([self.pool.by_alias[a] for a in hidden], query)
        alias_of = {id(tool): alias for alias, tool in self.pool.by_alias.items()}
        chosen = [alias_of[id(tool)] for score, tool in rows[:FIND_MAX] if score > 0]
        added = self.show(chosen)
        lines = [
            f"{alias}: {first_sentence(self.pool.by_alias[alias].description)}"
            for alias in chosen
        ]
        return added, lines


def take_snapshot(
    plugins: PluginHub | None,
    question: str = "",
    *,
    memory: bool = False,
    attachments: bool = False,
    speaker: Speaker = RAPHAEL,
    team: Sequence[str] = (),
    max_tier: Tier = SNAPSHOT_MAX_TIER,
    interactive: bool = False,
    turned_off: Collection[str] = (),
) -> Snapshot:
    """The tools a task starting now may use: with `memory`, the memory's tools that read too, and
    with `attachments` (the task came with files) the tool that reads them. A
    plugin that fails to list its tools is left out. When the plugins have more tools than fit, the
    task is shown the ones that share the most words with `question` (`refine` chooses with the
    model) and can find the rest. Every speaker shares the enabled tools and connections;
    `team` are the team tools allowed by the request's depth and cycle guards
    (`agent.team`: asking another member, adding one), T1 like the plugins' tools that are on.
    `turned_off` are the tools of Raphael's own that the owner switched off (`Tools.turned_off`):
    they are not offered, whatever the task or the speaker."""
    refs: dict[str, ToolRef] = {}
    definitions: list[dict[str, Any]] = []
    pool: Offer | None = None
    builtin = (
        *(name for name in WEB_TOOLS if speaker.uses(name) and name not in turned_off),
        *(MEMORY_READ_TOOLS if memory else ()),
        *(name for name in ATTACHMENT_TOOLS if attachments and name not in turned_off),
    )
    for name in builtin:
        refs[name] = ToolRef(BUILTIN, name, BUILTIN_SERVER, name, Tier.T0)
        definitions.append({"type": "function", "function": {"name": name, **_DEFINITIONS[name]}})
    for name in team:
        if name in TEAM_TOOLS:
            refs[name] = ToolRef(BUILTIN, name, BUILTIN_SERVER, name, TEAM_TIER)
            definitions.append(worker_definition(name))
    snapshot = Snapshot(refs, definitions)
    if interactive:
        for name, item in HARNESS_DEFINITIONS.items():
            native = harness_tool(name)
            refs[name] = ToolRef(BUILTIN, name, BUILTIN_SERVER, name, native.tier)
            definitions.append({"type": "function", "function": {"name": name, **item}})
    if plugins is not None:
        try:
            tools = [
                tool
                for tool in plugins.offered()
                if tool.tier <= max_tier and speaker.uses_plugin(tool.key)
            ]
        except Exception:  # a plugin that breaks leaves its tools out, not the task
            tools = []
        pool = make_offer(tools)
        taken = (
            set(WEB_TOOLS)
            | set(MEMORY_READ_TOOLS)
            | set(ATTACHMENT_TOOLS)
            | set(TEAM_TOOLS)
            | {"find_tools"}
            | PRESENCE_TOOLS
            | set(HARNESS_DEFINITIONS)
        )
        clash = set(pool.by_alias) & taken
        if clash:  # a plugin's tool never takes the name of one of Raphael's own
            pool = make_offer([tool for alias, tool in pool.by_alias.items() if alias not in clash])
        snapshot.pool = pool
        everything = list(pool.by_alias)
        snapshot.show(
            everything if len(everything) <= MAX_OFFERED else widen(pool, shortlist(pool, question, PICK_MAX))
        )
        if snapshot.hidden():
            refs["find_tools"] = ToolRef(FIND, "find_tools", BUILTIN_SERVER, "find_tools", Tier.T0)
            definitions.append(
                {"type": "function", "function": {"name": "find_tools", **_DEFINITIONS["find_tools"]}}
            )
    return snapshot


async def refine(
    snapshot: Snapshot,
    picker: ToolPicker,
    question: str,
    context: Sequence[Turn],
    *,
    model: str,
    ledger: Ledger,
) -> None:
    """Replace the tools a task is shown with the ones the model chooses for `question`, when the
    plugins have more tools than fit. The keyword choice `take_snapshot` made stays if the model's
    fails, and the rest can still be found."""
    pool = snapshot.pool
    if pool is None or len(pool.by_alias) <= MAX_OFFERED:
        return
    chosen = await picker.pick(pool, question, context, model=model, ledger=ledger)
    keep = {alias: ref for alias, ref in snapshot.refs.items() if ref.kind != MCP}
    snapshot.refs.clear()
    snapshot.refs.update(keep)
    snapshot.definitions[:] = [
        d for d in snapshot.definitions if d["function"]["name"] in keep
    ]
    snapshot.show(chosen)
