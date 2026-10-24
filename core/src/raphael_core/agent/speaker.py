"""Who answers: Raphael, or one of the members of the owner's team (raphael_core.members).

A member is not a second assistant with paths of its own. It is a parameter of the one assistant:
the same router, the same answer, the same gate and tiers, the same background worker, given a
`Speaker` that says whose persona speaks (`name`, `role`, `personality` and work `instructions`),
which model answers (`model`, "" for the answer model) and whose conversation and memory are read
(`id`). Every speaker has the same capabilities under the same safety gate. Stored `tools` lists
are retained for compatibility and do not restrict tools. Raphael is the speaker with id 0.

`depth` is how the speaker was reached, and `chain` who asked on the way. 0 is the owner (a message
in its thread, a routine, a task handed to the background); 1 is another assistant asking it
something through `ask_member`, 2 one that the asked one asked in turn. A chain is at most MAX_DEPTH
deep (A asks B, B asks C, and C asks nobody), and has no cycle: nobody already in it, the speaker
itself included, can be asked again (`can_ask`).

Nothing here logs a name, a role, a personality or an instruction.
"""

from dataclasses import dataclass, replace

from .policy import PLUGIN_PREFIX

RAPHAEL_ID = 0
# Raphael's name in each language: what `ask_member` takes for him, and what the member column shows.
RAPHAEL_NAMES = {"zh-TW": "拉斐爾", "en": "Raphael", "ja": "ラファエル"}
# Legacy tool ids retained for the stored member settings API.
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
    # Compatibility data; capability decisions ignore this field.
    tools: frozenset[str] | None = None
    depth: int = 0
    # The ids of the assistants that asked on the way to this one (not this one itself).
    chain: frozenset[int] = frozenset()
    personality: str = ""

    @property
    def is_raphael(self) -> bool:
        return self.id == RAPHAEL_ID

    def uses(self, tool: str) -> bool:
        """All speakers share the built-in tools; authorization belongs to the safety gate."""
        return True  # Stored per-member tool lists are compatibility data, not capability gates.

    def uses_plugin(self, key: str) -> bool:
        """All speakers may use enabled connections under the same tool tiers and approvals."""
        return True

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


RAPHAEL = Speaker()
