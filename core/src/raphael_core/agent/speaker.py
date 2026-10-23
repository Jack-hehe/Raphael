"""Who answers: Raphael, or one of the members of the owner's team (raphael_core.members).

A member is not a second assistant with paths of its own. It is a parameter of the one assistant:
the same router, the same answer, the same gate and tiers, the same background worker, given a
`Speaker` that says whose persona speaks (`name`, `role` and the owner's `instructions` for it),
which of the tools and connections it may use (`tools`), which model answers (`model`, "" for the
answer model) and whose conversation and memory are read (`id`). Raphael is the speaker with id 0:
he is not a row of the members table, he has every tool, and nothing about him is changed here.

`depth` is how the speaker was reached, and `chain` who asked on the way. 0 is the owner (a message
in its thread, a routine, a task handed to the background); 1 is another assistant asking it
something through `ask_member`, 2 one that the asked one asked in turn. A chain is at most MAX_DEPTH
deep (A asks B, B asks C, and C asks nobody), and has no cycle: nobody already in it, the speaker
itself included, can be asked again (`can_ask`).

Nothing here logs a name, a role or an instruction.
"""

from dataclasses import dataclass, replace

from .policy import PLUGIN_PREFIX

RAPHAEL_ID = 0
# Raphael's name in each language: what `ask_member` takes for him, and what the member column shows.
RAPHAEL_NAMES = {"zh-TW": "拉斐爾", "en": "Raphael", "ja": "ラファエル"}
# The tools of Raphael's own that a member may be given or not, by id (the 工具 tab's checklist).
# The memory's tools, reading the files the owner hands over and asking another member are not on
# the list: a member always has its own memory, the owner's files, and the depth it was asked at.
BUILTIN_TOOL_IDS = ("web_search", "web_read", "weather", "local_time")
# How deep a chain of `ask_member` may go: the owner's assistant asks one (1), which may ask one
# more (2), which asks nobody.
MAX_DEPTH = 2


def is_raphael_name(name: str) -> bool:
    """Whether `name` is one of Raphael's names, whatever the case and the spaces around it."""
    folded = " ".join(name.split()).casefold()
    return any(folded == known.casefold() for known in RAPHAEL_NAMES.values())


def plugin_server(key: str) -> str | None:
    """The id of the connection a plugin tool's key (`mcp:<server>/<tool>`) belongs to."""
    if not key.startswith(PLUGIN_PREFIX):
        return None
    server, sep, _tool = key.removeprefix(PLUGIN_PREFIX).partition("/")
    return server if sep and server else None


@dataclass(frozen=True)
class Speaker:
    """Who answers a request, and with what (see the module). The default is Raphael."""

    id: int = RAPHAEL_ID
    name: str = ""
    role: str = ""
    instructions: str = ""
    model: str = ""
    # The tool ids (BUILTIN_TOOL_IDS) and the connection ids or plugin tool keys it may use; None
    # for every one that is on, which is what Raphael and a member with no list of its own have.
    tools: frozenset[str] | None = None
    depth: int = 0
    # The ids of the assistants that asked on the way to this one (not this one itself).
    chain: frozenset[int] = frozenset()

    @property
    def is_raphael(self) -> bool:
        return self.id == RAPHAEL_ID

    def uses(self, tool: str) -> bool:
        """Whether it may use the tool of Raphael's own `tool` (a name of BUILTIN_TOOL_IDS); a tool
        that is not on that list (the memory's, reading the owner's files) is always its own."""
        return self.tools is None or tool not in BUILTIN_TOOL_IDS or tool in self.tools

    def uses_plugin(self, key: str) -> bool:
        """Whether it may use the plugin tool `key`: every one, or the ones of a connection it was
        given, or that tool by itself."""
        if self.tools is None or key in self.tools:
            return True
        server = plugin_server(key)
        return server is not None and server in self.tools

    @property
    def may_ask(self) -> bool:
        """Whether it may ask another assistant something (`ask_member`): not at the end of a
        chain that is as deep as it may be."""
        return self.depth < MAX_DEPTH

    def can_ask(self, other: "Speaker") -> bool:
        """Whether it may ask `other`: it may ask at all, `other` is not itself, and `other` is not
        already in the chain that reached it (no cycle)."""
        return self.may_ask and other.id != self.id and other.id not in self.chain

    def asked_by(self, asker: "Speaker") -> "Speaker":
        """The same speaker, asked something by `asker` through `ask_member`: one step deeper in
        the chain, with `asker` and those before it in it."""
        return replace(self, depth=asker.depth + 1, chain=asker.chain | {asker.id})

    def model_or(self, default: str) -> str:
        """The model that answers for it: its own, or `default` (the answer model)."""
        return self.model.strip() or default


RAPHAEL = Speaker()
