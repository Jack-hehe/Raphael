"""The tools of a run on the owner's desktop, as the model is offered them.

Each is a `ToolSpec`: a name, what it is for, the JSON schema of its arguments, and where it may be
offered. Every one of them `needs_presence`: it acts on the desktop the owner sits at, so it is
offered only to a run that a request made at this computer started (the fast, foreground lane),
never to the background worker, a scheduled job or a message from a phone. `PRESENCE_TOOLS` is the
list the worker's snapshot and the gate check against (`background.snapshot`, `agent.gate`).

- `computer_apps`: the windows of the apps that are open;
- `computer_open`: open an app of the Start menu by its name;
- `computer_look`: a snapshot of one window (asks the owner first, for an app not allowed yet);
- `computer_act`: one step in a window (click, type, keys, scroll, focus), after which the model
  is shown the window again;
- `computer_hand_over`: the next step is the owner's (a sign-in, a password, a payment, an
  administrator's prompt), and the run stops there.

A later phase adds a `computer_see` (a screenshot for a vision model) beside `computer_look`; the
run's dispatch has the place for it.
"""

from dataclasses import dataclass, field
from typing import Any

from ..agent.policy import Tier

FAST = "fast"


@dataclass(frozen=True)
class ToolSpec:
    """One tool: `name`, `description` and the JSON schema of its `parameters`, and the lanes it
    may be offered in. `needs_presence`: only while the owner is at this computer."""

    name: str
    description: str
    parameters: dict[str, Any]
    lanes: frozenset[str] = field(default_factory=lambda: frozenset({FAST}))
    needs_presence: bool = True

    def definition(self) -> dict[str, Any]:
        """The tool in the chat format the model is offered it in."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


ACTIONS = ("click", "type", "keys", "scroll", "focus")
DIRECTIONS = ("up", "down", "left", "right")
HAND_OVER_REASONS = ("sign_in", "password", "payment", "admin", "captcha", "other")
MAX_TEXT_CHARS = 1000

_WINDOW = {"type": "integer", "description": "The window's id, from computer_apps."}

APPS = ToolSpec(
    "computer_apps",
    "List the windows of the apps open on the desktop, with their ids. Call it first, and again "
    "when a new window (a dialog) may have opened.",
    {"type": "object", "properties": {}, "additionalProperties": False},
)
OPEN = ToolSpec(
    "computer_open",
    "Open an installed app from the Start menu by its name (for example Calculator, Excel). Then "
    "call computer_apps to find its window.",
    {
        "type": "object",
        "properties": {"name": {"type": "string", "description": "The app's name."}},
        "required": ["name"],
        "additionalProperties": False,
    },
)
LOOK = ToolSpec(
    "computer_look",
    "Read one window: its elements with their ids, roles, names, values and states. The ids are "
    "valid until the next look at that window.",
    {
        "type": "object",
        "properties": {"window": _WINDOW},
        "required": ["window"],
        "additionalProperties": False,
    },
)
ACT = ToolSpec(
    "computer_act",
    "Do one step in a window, then see the window again. click: press, toggle, select or expand "
    "the element. type: put text into the element (or the focused field). keys: press a key or a "
    "shortcut such as ctrl+s, tab, enter (no Windows key, no alt+tab). scroll: scroll the element. "
    "focus: give the element the keyboard focus. Set final to true when the step sends, submits, "
    "posts, publishes, deletes, buys, orders or pays — pressing Enter to send a message included.",
    {
        "type": "object",
        "properties": {
            "window": _WINDOW,
            "element": {
                "type": "integer",
                "description": "The element's id from the latest look; leave out for keys or "
                "type into the focused field.",
            },
            "action": {"type": "string", "enum": list(ACTIONS)},
            "text": {"type": "string", "description": "The text to type (type only)."},
            "keys": {"type": "string", "description": "The key or shortcut (keys only)."},
            "direction": {"type": "string", "enum": list(DIRECTIONS)},
            "final": {
                "type": "boolean",
                "description": "True when this step sends, submits, deletes, buys or pays.",
            },
        },
        "required": ["window", "action", "final"],
        "additionalProperties": False,
    },
)
HAND_OVER = ToolSpec(
    "computer_hand_over",
    "Stop: the next step is the owner's to do (signing in, a password, a payment, an "
    "administrator prompt, a captcha).",
    {
        "type": "object",
        "properties": {"reason": {"type": "string", "enum": list(HAND_OVER_REASONS)}},
        "required": ["reason"],
        "additionalProperties": False,
    },
)

TOOLS = (APPS, OPEN, LOOK, ACT, HAND_OVER)
PRESENCE_TOOLS = frozenset(spec.name for spec in TOOLS if spec.needs_presence)

# The names the gate knows the steps of a run by, and their tiers: reading is T0, a step T1, a
# step that sends, deletes or buys (`computer_act_final`) and the use of an app not yet allowed
# (`computer_use_app`) T2, which pass the gate only with the approval of their question. None of
# them passes for the background worker (`agent.gate.ToolGate.check`).
USE_APP = "computer_use_app"
ACT_FINAL = "computer_act_final"
GATE_TIERS = {
    APPS.name: Tier.T0,
    LOOK.name: Tier.T0,
    HAND_OVER.name: Tier.T0,
    OPEN.name: Tier.T1,
    ACT.name: Tier.T1,
    ACT_FINAL: Tier.T2,
    USE_APP: Tier.T2,
}


def definitions() -> list[dict[str, Any]]:
    """The tools of a run, in a fixed order, in the chat format."""
    return [spec.definition() for spec in TOOLS]
