"""The two tools that reach the owner's team (raphael_core.members): `ask_member` and `create_member`.

- `ask_member {member, request}` has another assistant answer a request, and gives the answer back
  as tool data. Every assistant may ask any other (Raphael by one of his names): asking is how what
  one of them knows reaches another, since their conversations are private. The one asked answers
  through the same answer path as always (`AgentService.answer_for`), as itself: from its own
  conversation, its own memory and the shared one, with its own tools, as if the owner had asked
  it, but never on the desktop. Nobody asks itself, a chain is at most two deep (A asks B, B may ask
  C, C asks nobody) and has no cycle (`Speaker.can_ask`). The exchange is written in the two threads
  of the two who had it, as one line each (`route` "exchange"), and nowhere else.
- `create_member {name, role, instructions, group?}` adds a member when the owner asks for one
  (「幫我加一個管行程的成員」). Raphael's alone, at the owner's request (never at a member's).

Both are T1 in the gate (`gate.TEAM_TOOLS`): they run at once, through the gate like every tool.
Adding a member changes something, so the owner's safety rules judge it first, as they judge a
plugin's tool (`agent.safety`, by the name `create_member`): a rule may forbid it or have it asked
about, in the fast tier at the overlay and in the background with the waiting question. Asking
another is not judged: what the one asked does is judged as it does it. The
fast tier offers them to the call that picks tools only when the request is about the team (it names
a member, or Raphael for a member, or asks for a new member: `team_wanted`), so that a question about
something else costs nothing more; the background worker is offered them whenever there is someone to
ask.

What a member answers is data, not instructions: it may hold what the web or a service returned to
that member, so a reply that was shown it writes no note (`ToolResult.untrusted`).

Nothing here logs a name, a request or an answer.
"""

import re
from collections.abc import Sequence
from typing import Any, Protocol

from .plugins import PluginTool
from .policy import Tier
from .speaker import RAPHAEL_NAMES, Speaker

ASK_MEMBER = "ask_member"
CREATE_MEMBER = "create_member"
TEAM_TOOLS = (ASK_MEMBER, CREATE_MEMBER)
TEAM_SERVER = "Raphael"
TEAM_TIER = Tier.T1
# The most of a request one member hands another, and of the answer that comes back, in characters.
REQUEST_MAX_CHARS = 2000
ANSWER_MAX_CHARS = 4000

# The words of a request for a new member: 「加一個…的成員」, "add a member who…", 「メンバーを追加」.
_CREATE = re.compile(
    r"(?:加|新增|增加|建立|建一個|做一個|開一個|招|找).{0,12}(?:成員|助手|隊員|同事)|"
    r"(?:成員|助手).{0,4}(?:加|新增|建立)|"
    r"メンバー.{0,8}(?:追加|作|加え|増や)|(?:追加|作).{0,8}メンバー|"
    r"(?i:\b(?:add|create|make|new|set up)\b.{0,30}\b(?:member|assistant|teammate)\b)"
)


def asks_to_create_member(text: str) -> bool:
    """Whether the owner's words ask for a new member of the team."""
    return bool(_CREATE.search(text or ""))


def names_raphael(text: str) -> bool:
    """Whether the words name Raphael, in any of his languages."""
    folded = (text or "").casefold()
    return any(name.casefold() in folded for name in RAPHAEL_NAMES.values())


def _definition(name: str) -> dict[str, Any]:
    if name == ASK_MEMBER:
        return {
            "description": (
                "Ask another assistant of the owner's team (a member by its name, or Raphael) to "
                "answer a request or do something within its role and tools, and get its answer "
                "back. It answers from what it knows: its own conversation and notes, which you do "
                "not see."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "member": {"type": "string", "description": "The member's name."},
                    "request": {
                        "type": "string",
                        "description": "What to ask, in full: the member sees nothing else.",
                    },
                },
                "required": ["member", "request"],
            },
        }
    return {
        "description": (
            "Add a member to the owner's team, only when the owner asks for one: its name (at most "
            "16 characters), its role in a few words, the instructions it follows, and the name of "
            "a group to put it in, if the owner said one."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "The member's name."},
                "role": {"type": "string", "description": "Its role, at most 24 characters."},
                "instructions": {"type": "string", "description": "What it is to do, and how."},
                "group": {"type": "string", "description": "The name of a group, or empty."},
            },
            "required": ["name", "role", "instructions"],
        },
    }


def description(name: str) -> str:
    """What the team tool `name` does, as the model and the safety rules' judge are told."""
    return str(_definition(name)["description"])


def team_tool(name: str) -> PluginTool:
    """A team tool as the fast tier's picking call is offered it (beside the plugins' tools)."""
    definition = _definition(name)
    return PluginTool(
        key=name,
        server=TEAM_SERVER,
        name=name,
        description=definition["description"],
        schema=definition["parameters"],
        tier=TEAM_TIER,
    )


def worker_definition(name: str) -> dict[str, Any]:
    """A team tool as the background worker is offered it."""
    definition = _definition(name)
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": f"[Raphael] {definition['description']}",
            "parameters": definition["parameters"],
        },
    }


class Team(Protocol):
    """What the assistant knows of the team (`members.service.MembersService` is one)."""

    def speaker(self, member_id: int) -> Speaker | None:
        """The member `member_id` as a speaker; Raphael for 0; None for nobody."""
        ...

    def find(self, name: str) -> Speaker | None:
        """The member called `name` (Raphael, for one of his names); None for nobody."""
        ...

    def named_in(self, text: str) -> Sequence[Speaker]:
        """The members whose names `text` holds."""
        ...

    def has_members(self) -> bool:
        ...

    def create(self, *, name: Any, role: Any, instructions: Any, group: Any) -> Speaker:
        """Add a member; `members.store.MemberError` when it cannot be added."""
        ...


def team_tools_for(
    team: Team | None, speaker: Speaker, text: str = "", *, always: bool = False
) -> list[str]:
    """The team tools `speaker` may be offered for the request `text`: `ask_member` when there is
    someone it may ask (Raphael, for a member; another member), and `create_member` for Raphael at
    the owner's request (not when he was asked by another). Without `always` (the fast tier) only
    the ones the request is about: it names someone it may ask, or asks for a new member."""
    if team is None or not speaker.may_ask:
        return []
    tools: list[str] = []
    if always:
        anyone = not speaker.is_raphael or team.has_members()
    else:
        named = [other for other in team.named_in(text) if speaker.can_ask(other)]
        raphael = not speaker.is_raphael and names_raphael(text) and 0 not in speaker.chain
        anyone = bool(named) or raphael
    if anyone:
        tools.append(ASK_MEMBER)
    if speaker.is_raphael and speaker.depth == 0 and (always or asks_to_create_member(text)):
        tools.append(CREATE_MEMBER)
    return tools
