"""The voice assistant as RPC methods: `agent.ask`, `agent.confirm`, `agent.cancel` and the history.

A request travels like this. The app records the user with `dictation.start {purpose: "agent"}`,
gets the transcript from `dictation.stop`, and sends it with `agent.ask {session_id, text,
ui_language, target_app}`. The router (Jev, and the language model when Jev fails or is unsure;
`routing`) says what it is:

- a command runs at once if it is T0 or T1 (`commands`, `policy`) and Raphael says 《了。》: every
  command for this computer is T1, copying to the clipboard too. A command at T2 or T3, if one is
  ever added, does not run: the answer is a question for a yes or a no (`status: "confirm"`),
  which `agent.confirm` answers within 30 seconds;
- the command `open` opens a web page, an app or a page of the Windows settings, and what it opens
  is found with no list of sites (`opening`): a domain said outright, a settings page named with
  the word for settings, an app of the Start menu, a name that opened before (`targets`), a
  settings page by its name, and only then one call to the language model, which names the site,
  the app or the search; what it names is checked as strictly as the rest, and learned when it
  opened. It runs only from the user's own request, never from what a service or the web returned;
- an answer looks up what it needs, together (`tools`: the weather, a web search, the time), and one
  call to the language model words it (`answers`); a discussion and small talk are worded the same
  way, with no lookup. Every one of them reads the one continuous conversation
  (`memory.conversation`): the summary of the earlier turns and the latest turns as they were said
  (a discussion all the turns after the summary, an answer and small talk the last few);
- with a memory (`raphael_core.memory`), every answer also reads the memory's index, and its reply
  may write or delete a note (`memory_write`, `memory_delete`, T1: at once, with no yes) when the
  owner asks it to remember or forget something, or corrects it: Raphael then says 《了。已記下。》
  (the reply's `notice`, which the overlay shows after the line). That is taken only from the
  owner's own turn: a reply that was shown what the web or a connected service returned writes
  nothing. The reply may also ask to read a note in full or to search the past turns (`memory_read`,
  `history_search`, T0) before it answers, which it then does with what was found. A request to
  remember that the router took for a task or a command is answered as small talk, where the
  note is written; an unclear request goes to the narrated harness, which can write it too;
- when the user has connected plugins (`integrations`), an answer may use the tools that are on:
  one call to the language model picks the tools it needs from those alone (`plugins`), the
  program makes the calls, and the results are given to the call that words the answer as data,
  not instructions. A tool that is on runs at once (`integrations.service.ON_TIER`, T1); a hub
  that gives a tool a higher tier (the tests' do) makes its call a question for a yes or a no
  like a command at that tier, one to a turn, made when the yes comes. Every call of a tool goes
  through the one gate (`gate`), the worker's too. A tool that is off is never offered, and a
  model that takes no tools (`plugins.ToolSupport`) leaves the plugins out of the request;
- before a call of a plugin's tool that its server does not say only reads, the owner's safety
  rules are read (`safety`, through the gate's `screen`, one paid call to Jev when there are
  rules): a rule that forbids it is a 否 reply that names the rule, with nothing done; one that asks
  makes it a question for a yes or a no (that names the rule) even at T1; one that allows it
  changes nothing that is built in (a T2 tool still asks). A judge that fails counts as one that
  asks. The commands for this computer are not judged (see `safety`);
- a task done by operating an app on the screen (`operate`) is a run on the owner's desktop
  (`raphael_core.computer`), and only for a request made at this computer, spoken or typed: the
  model looks at the app's window and acts one step at a time through the shell, asking the owner
  before it uses an app for the first time and before every step that sends, deletes or buys. Its
  questions are the replies of `agent.ask` and `agent.confirm` like any other, so one turn can ask
  several in a row (`agent.confirm {id, approved, always}`, `always` for 「一律允許」), and its
  steps are the turn's. A run reads the conversation as a quick answer does (the summary, the last
  turns) and the memory's index, as data, but never writes a note: what it saw on the screen is
  untrusted;
- a task for the background (`delegate`: several steps, or a connected service) is handed to the
  background worker (`raphael_core.background`) and answered at once with 《了。》; the worker does
  it step by step through the same gate, and reports on the overlay when it is done;
- a voice request the router cannot place, or could not route, goes to the narrated harness
  with the conversation, just as a typed request does. Routing and answering together have a
  45-second deadline while the overlay waits; an empty request gets a fixed negative reply.

Whatever goes wrong is answered, never raised: no key, the monthly limit reached, no network, a
model that fails each get a short 否 reason in the user's language, and the commands still work (a
list of words finds them, `local`). Every paid call is written to the spend with the purpose
"agent". Every turn is written to the history (`agent.history.list`, `agent.history.search`,
`agent.history.clear`), with
its steps (`steps`): each lookup, plugin call and command it made, how it ended, how long it took
and, when it failed, why in one word. `agent.history.list` gives each turn its steps and, for a turn
that handed a task to the worker, the task and the worker's steps, so the app can show what happened
between the question and the answer. `agent.history.search {query, limit}` finds the answered turns
that hold the words in the whole history (the search of the memory, `AgentHistory.search`), and
gives them in the same shape. `agent.turn {turn_id}` tells the app a turn was written or
completed. `agent.ask` takes an optional `source`, where the request came from ("voice", the
default; "app", typed in the conversation page of the app; or a chat app such as "telegram").

The services of the tools (`raphael_core.services`, chosen by the owner) come in three ways. A web
search uses the search service chosen: "openrouter" as before, or a search engine whose results the
answer words (its step says which, in `detail`, never what was searched). An http(s) address in the
owner's own request (typed, spoken, or sent from a chat app) is read with the page reader chosen
before an answer, a discussion or small talk is worded, at most MAX_REQUEST_URLS of them; an
address that a tool returned is never read by the fast tier. And `agent.ask` takes `attachments`,
the images and PDFs the owner handed over (`services.attachments`, `from_wire`; a list that cannot
be taken fails the request as a bad request, with its kind in the error's data): the images go to
the vision model together, with the question, and each PDF is read by the PDF service chosen; what
they hold is data for the call that words the answer. A request of only files (no words) asks
「看這個」 in the language of the request. The files are kept in memory for the turn, and for the
task when the request is handed to the background worker: never in the database, on disk or in a
log. The history keeps their names and types (`attachments: [{name, mime}]` of a turn), for the
conversation view. A request routed to `operate` or to a command makes nothing of them.

The members of the owner's team (`raphael_core.members`) are a parameter of all this, not a path of
their own (`speaker.Speaker`). `agent.ask` takes `member_id` (0, Raphael, by default): a message
typed in the app or a chat connection is answered by the same narrated harness, gate and worker,
as the chosen speaker: its persona, model, conversation and memory. Every member has the same
tools as Raphael; desktop operations require the owner's local presence regardless of speaker.
The voice alone may use the fast router, with recent conversation included. The team tools (`team`)
let any assistant ask any other (`ask_member`) or add a member at the owner's request
(`create_member`, 《了。已建立「…」。》). The one asked answers through `answer_for`, as itself and
never on the desktop, within a chain at most two deep and with no cycle (`Speaker.can_ask`); the
exchange is written in the two threads alone. Nobody waits at the overlay for such an answer, so
when one of its calls needs a yes (a tier, or a safety rule that asks) none of its calls is made
there: the request goes to the background as a task of the one asked (`ask_in_background`), which
stops at that call with a question waiting in that thread, answered in the app alone (the
background's waiting question), and the one asked says back that it waits there. Adding a member is
judged by the safety rules like a plugin's tool (`create_member`): a rule may forbid it or have it
asked about first. `agent.history.list` and `agent.history.search` take `member_id` too, and read
that thread alone; `agent.turn` names the thread of the turn (`member_id`).

`agent.cancel {session_id}` ends a request that is still being thought about and denies the
question that session is waiting on, and stops its run on the desktop. A new request replaces any
question that waits, and any run.

Threads: this runs on the event loop, which owns the database. The device actions (a key, a page, the
clipboard, PowerShell) run on worker threads. Nothing here logs what the user said, what Raphael
answered, a city or a key: only routes, kinds, latencies and costs.
"""

import asyncio
import json
import logging
import re
import sqlite3
import time
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, tzinfo
from typing import TYPE_CHECKING, Any

import httpx

from ..background.brief import background_block, background_line
from ..background.tasks import Refused
from ..background.snapshot import FIND, take_snapshot
from ..brain.catalog import CatalogEntry
from ..clock import wall_clock_ms
from ..computer.run import ComputerRun, ComputerRuns, Final, Question, conversation_block
from ..computer.words import answers as answer_labels
from ..integrations.pool import McpPool
from ..brain.providers.base import ModelProvider
from ..brain.providers.registry import ProviderRouter
from ..brain.secrets import KeyStore
from ..brain.settings import DEFAULTS, SettingsStore
from ..brain.spend import SpendTracker
from ..protocol import INVALID_PARAMS, RpcError, invalid_params
from ..router import Router as RpcRouter
from ..memory.conversation import Conversation, asks_to_remember
from ..memory.store import MemoryIndex
from ..services.attachment_store import AttachmentStore
from ..services.attachments import MAX_ATTACHMENTS, Attachment, AttachmentError, from_wire
from ..services.urls import urls_in
from ..services.urls import SeenUrls
from ..messaging.words import compose
from ..worker.loop import Stop, ToolOutcome, WAITING
from ..worker.guards import MAX_ARGUMENT_CHARS, canonical
from .answers import Reply, Voice, build_user_message
from .attribution import labelled_lines, owner_lines, relayed_answer
from .narration import NarratedLoop
from .cli.discovery import CliBrains, brain_ref
from .cli.loop import MEMORY_TOOLS, CliLoop, Sessions as CliSessions
from .persona import cli_system_prompt
from ..brain.owner import owner_context
from .cities import cities_in
from .commands import COMMANDS, AppCatalog, run_command
from .device import DeviceActions
from .failure import AgentFailure, failure_kind
from .gate import BUILTIN_SERVER, ToolGate
from .history import VOICE, AgentHistory, Turn
from .conversations import Conversations
from .quota import Quotas
from .quotes import quote_from_wire
from .ledger import Ledger
from .harness_tools import (
    DEFINITIONS as HARNESS_TOOLS, ROUTINE_TOOLS, HarnessToolError,
    routine_event, tool as harness_tool,
)
from .local import extract_target, match_command
from .opening import Opening, clean_target, match_settings, parse_domain, validate_url
from .persona import (
    DEFAULT_LANGUAGE,
    PREFIXES,
    normalize_language,
    parenthesized,
    phrase,
    member_system_prompt,
    spoken_line,
    system_prompt,
)
from .plugins import (
    MAX_OFFERED,
    PLAN_CONTEXT_TURNS,
    Offer,
    PlannedCall,
    Planner,
    PluginHub,
    PluginResult,
    PluginTool,
    ToolsNotSupported,
    ToolSupport,
    make_offer,
    preview,
)
from .toolpick import ToolPicker
from .policy import (
    CONFIRM_TIMEOUT_MS,
    Action,
    Approval,
    Confirmations,
    NotApproved,
    Outcome,
    Pending,
    Tier,
    UnknownTool,
)
from .routing import OpenAnswer, RouteDecision, Router
from .speaker import RAPHAEL, RAPHAEL_NAMES, Speaker
from .safety import ASK, DENY, PASS, Proposed, Screening, asked_with, forbidden, needs_yes, summarize
from .steps import Step, reason_of
from .targets import OpenTargets
from .team import (
    ANSWER_MAX_CHARS,
    ASK_MEMBER,
    CREATE_MEMBER,
    REQUEST_MAX_CHARS,
    TEAM_SERVER,
    TEAM_TOOLS,
    SKILL_READ,
    Team,
    asks_to_create_member,
    names_raphael,
    roster_prompt,
    team_tool,
    team_tools_for,
)
from .tools import ToolResult, Tools

if TYPE_CHECKING:
    from ..background.service import BackgroundService
    from ..services.hub import ServiceHub
    from ..services.speaker import Speaking
    from ..memory.reflection import Applied
    from ..memory.service import MemoryService

logger = logging.getLogger(__name__)

HISTORY_DEFAULT_LIMIT = 50
# A session agent.cancel named is remembered this long: its request may still be on its way (Esc
# and the request can cross on the pipe), and it is then answered as cancelled, with nothing run.
CANCELLED_MEMORY_S = 60.0
HISTORY_MAX_LIMIT = 200
# The search of the conversation page: how many turns it gives, by default and at most, and the
# longest words it takes.
HISTORY_SEARCH_DEFAULT_LIMIT = 50
HISTORY_SEARCH_MAX_LIMIT = 100
HISTORY_QUERY_MAX_CHARS = 200
# Where a request may come from: a short lower-case word ("voice", "app", "telegram").
_SOURCE = re.compile(r"[a-z]{1,20}")
# A question typed in the app's conversation page: asked at this computer, like a spoken one.
TYPED = "app"
# The sources of the requests made at this computer: a new one replaces every question that waits.
_AT_THIS_COMPUTER = frozenset({VOICE, TYPED})
# Routing and answering share this deadline while the voice overlay waits.
VOICE_TURN_TIMEOUT_S = 45.0
# The statuses of a turn that ended with a denial or a question that ran out: Raphael does not say
# these aloud, as the owner knows (a "no", an Esc) or was not there.
_UNSPOKEN = ("denied", "expired")
# The routes that read the whole of the conversation after its summary; the others read its last
# few turns (`memory.conversation.QUICK_TURNS`), which keeps a quick answer cheap.
_FULL_CONTEXT_ROUTES = ("discuss",)
# A connection to a plugin's server that nobody uses is closed after this long.
PLUGIN_IDLE_S = 120.0
_DAY_MS = 86_400_000
# The most addresses of the owner's request that are read before the answer.
MAX_REQUEST_URLS = 2
# What a request of only files asks, in the language of the request.
_LOOK_AT_THIS = {"zh-TW": "看這個", "en": "Look at this", "ja": "これを見て"}
# The routes that word a reply, which read the pages and the files of the request first.
_READING_ROUTES = ("answer", "discuss", "chat")
# The routes that give an answer a later "copy that" can copy. Small talk ("不客氣。") is none.
_ANSWER_ROUTES = ("answer", "discuss")
# What Raphael says (a phrase of persona.Phrases) after a confirmed action; none says only 了.
_DONE_PHRASE = {"copy_text": "copied"}
_QUOTES = (
    re.compile(r"「([^」]+)」"),
    re.compile(r"『([^』]+)』"),
    re.compile(r"“([^”]+)”"),
    re.compile(r'"([^"]+)"'),
)


class _Silent(dict):
    """The reply of `agent.ask` or `agent.confirm` that Raphael does not speak: the owner's own
    "no" or Esc answered back, a question that ran out, a run that was stopped. It is an ordinary
    dict on the wire; only the voice (`AgentService._said`) tells it apart."""


def _ms_since(began: float) -> int:
    return round((time.perf_counter() - began) * 1000)


def _plugin_outcome(result: PluginResult) -> str:
    """How a call of a plugin's tool ended, in the words of the steps and the audit."""
    if result.ok:
        return "ok"
    if result.kind == "timeout":
        return "timeout"
    if result.kind in ("blocked", "changed", "gone"):
        return "blocked"
    if result.kind == "cancelled":
        return "cancelled"
    return "error"


def _step_view(step: Step) -> dict[str, Any]:
    return {
        "kind": step.kind,
        "tool": step.tool,
        "server": step.server,
        "outcome": step.outcome,
        "ms": step.ms,
        "reason": step.reason,
        "at_ms": step.at_ms,
        "detail": step.detail,
    }


def _unmade(call: PlannedCall) -> ToolResult:
    """The result for a call that was not made because the turn can ask about one only."""
    return ToolResult(_label(call), False, None, "not_made")


def _label(call: PlannedCall) -> str:
    return f"{call.tool.server}/{call.tool.name}"


def _tool_result(call: PlannedCall, outcome: PluginResult) -> ToolResult:
    """What a plugin's tool answered, as the answer is shown it: data that nobody vetted. A tool
    that said it failed has its words kept, since they tell what went wrong; the failures of the
    call itself have a kind only."""
    if outcome.ok:
        data = {"content": outcome.text, "cut_short": outcome.truncated}
        return ToolResult(_label(call), True, data, untrusted=True)
    if outcome.kind == "tool_error":
        return ToolResult(
            _label(call), False, {"content": outcome.text}, "tool_error", untrusted=True
        )
    return ToolResult(_label(call), False, None, outcome.kind or "error")


def _copy_text(turn: Turn) -> str:
    """What "copy that" copies of an answer: its line and its detail, each once. The detail is
    the whole answer when the line had to be cut, and what the line left out otherwise."""
    text, detail = turn.text or "", turn.detail or ""
    if not detail or detail == text:
        return text
    if not text or text.rstrip("…") in detail:
        return detail
    return f"{text}\n{detail}"


def _prefix(language: str, which: str) -> str:
    """The prefix of `language` that `which` names ("understood", "negative", ...); `which` itself
    when it is a prefix already, as the model wrote it."""
    return PREFIXES[language]._asdict().get(which, which)


def _text_or(value: Any, default: str) -> str:
    """`value` without the spaces around it, or `default` if it is not text or is blank."""
    return value.strip() if isinstance(value, str) and value.strip() else default


def _str(params: dict, name: str, *, blank: bool = False) -> str:
    value = params.get(name)
    if not isinstance(value, str) or (not blank and not value):
        raise invalid_params(f"{name} must be {'a string' if blank else 'a non-empty string'}")
    return value


def _opt_int(params: dict, name: str) -> int | None:
    value = params.get(name)
    if value is None:
        return None
    if type(value) is not int:
        raise invalid_params(f"{name} must be a whole number")
    return value


@dataclass
class _Ask:
    session_id: str
    text: str
    language: str
    conversation_id: int | None = None
    original_text: str | None = None
    source: str = VOICE
    started_at_ms: int = field(default_factory=wall_clock_ms)
    call_base: int = 0
    # What was done for the request so far; written with its turn.
    steps: list[Step] = field(default_factory=list)
    # The files handed with the request, in memory only.
    attachments: list[Attachment] = field(default_factory=list)
    quote: dict[str, str] | None = None
    # Who answers: Raphael, or a member of the team (`speaker`), in whose thread the turn is.
    speaker: Speaker = RAPHAEL
    # For a request one member hands another (`answer_for`): the one who asked. The turn is then an
    # exchange, written in both threads, and nothing in it is the owner's own words.
    peer: int | None = None
    # The same direction in both copies of an exchange, independent of whose thread owns it.
    exchange_speakers: tuple[str, str] | None = None
    exchange_owner_sources: tuple[str, ...] = ()
    exchange_answer: str = ""
    # What the ledger held before this request: a request answered inside another's shares its
    # ledger, and its turn costs only what was added.
    cost_base: float = 0.0
    turn_id: int = 0
    recorded_cost: float = 0.0
    recorded_steps: int = 0
    replies: list[str] = field(default_factory=list)
    ledger: Ledger | None = None
    # Whether Raphael will say the answer aloud (`AgentService._voiced`): then the model is asked
    # for its Japanese rendering too, and the turn is announced to the app when the voice is ready.
    voiced: bool = False
    # The turn whose announcement to the app is held until the voice is ready (`_turn_changed`).
    held: int | None = None

    @property
    def nested(self) -> bool:
        return self.peer is not None


# The source of an exchange between two members: neither the owner's voice nor a chat app.
EXCHANGE_SOURCE = "member"
# How long a member that another asked something may take to answer, in seconds.
EXCHANGE_TIMEOUT_S = 45.0


@dataclass
class _Settings:
    model: str
    city: str
    limit_usd: float


@dataclass
class _Followup:
    """What a call of a plugin's tool that waits for a yes needs to go on once it comes: the
    request, the call, and what the answer already has (the results of the calls that needed no
    yes, and of the lookups)."""

    ask: _Ask
    call: PlannedCall
    results: list[ToolResult]
    loop: NarratedLoop | CliLoop | None = None
    memory_reply: tuple[str, Reply, Ledger] | None = None


class AgentService:
    """The voice assistant: it keeps the history, the questions that wait for a yes, and the
    requests that are being thought about.

    `conn` is the open database (used on the event loop only), `client` the OpenRouter client,
    `keys` where the key lives, and `actions` what is done to this computer. `now_ms` is the wall
    clock and `tz` the time zone of the months of the spend; `clock` is a monotonic clock in
    seconds (the thirty seconds of a question), `now` the local time. `http` is the httpx client for
    Open-Meteo, which the caller then owns; `decider` is the router of requests; `plugins` is the
    hub of the tools the user connected (none: the assistant has only its own); `models` gives the
    catalog of OpenRouter's models as it was last read (`Brain.known_models`), by which a request
    is told whether the answer model takes tools. `gate` is the one gate every tool call goes
    through, shared with the background worker (one is made when none is given), and `background`
    is the worker that takes the tasks of the `delegate` route. `notify` sends a notification to
    the app (`agent.turn`). `computer` holds the run on the owner's desktop (the `operate` route);
    without it nothing on the desktop is operated. `memory` is Raphael's memory (`memory.service`),
    which also keeps the conversation's summary; without one there is no memory, and the
    conversation is read without a summary (its last turns, within the same limits). `services` is
    the hub of the services the owner chose for the tools (`services.hub`); without it the search is
    OpenRouter's, and no page or file is read (a file handed over is answered as unreadable).
    `speaker` is Raphael's voice (`services.speaker.Speaking`): the answer of every request made to
    Raphael himself at this computer (spoken or typed) is said aloud, with its prefix, in Japanese
    (the voice speaks nothing else: the line itself on a Japanese screen, else a fixed phrase's
    Japanese wording or the Japanese the answer model wrote in the same call), and the reply is
    returned, and its turn announced to the app, when the voice is ready to begin (at most
    `speaker.FIRST_AUDIO_WAIT_S` later; with no voice that can be heard nothing waits). A member's
    reply, an exchange between members, a routine's result, a cancel, a denial, a question that ran out, a request from a messaging app, a report of the background
    worker and a question waiting for a yes are not. Any new request at this computer
    and `agent.cancel` stop what is being said.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        client: ModelProvider,
        keys: KeyStore,
        *,
        actions: DeviceActions,
        now_ms: Callable[[], int] = wall_clock_ms,
        tz: tzinfo | None = None,
        clock: Callable[[], float] = time.monotonic,
        http: httpx.AsyncClient | None = None,
        now: Callable[[], datetime] | None = None,
        decider: Router | None = None,
        plugins: PluginHub | None = None,
        models: Callable[[], Sequence[CatalogEntry] | None] | None = None,
        gate: ToolGate | None = None,
        background: "BackgroundService",
        notify: Callable[[str, dict], None] | None = None,
        computer: ComputerRuns | None = None,
        memory: "MemoryService | None" = None,
        services: "ServiceHub | None" = None,
        team: Team | None = None,
        speaker: "Speaking | None" = None,
        cli_brains: CliBrains | None = None,
        attachment_store: AttachmentStore | None = None,
    ) -> None:
        self._attachment_store = attachment_store
        self._conn = conn
        self._keys = keys
        # Raphael's voice (`services.speaker`): says the answers of the owner's own requests to
        # Raphael, and is stopped by a new request and by agent.cancel. None: the answers are
        # silent. (Not `_speaker`: that is the lookup of who answers a `member_id`.)
        self._speaking = speaker
        self._client = client
        # The owner's team (raphael_core.members): who a `member_id` is, and the team tools. Without
        # one there is only Raphael.
        self._team = team
        # The members' conversations when there is no memory to keep them.
        self._conversations: dict[int, Conversation] = {}
        # The run on the owner's desktop, through the shell; None where there is no shell to ask.
        self._computer = computer
        self._memory = memory
        self._conversation = (
            memory.conversation
            if memory is not None
            else Conversation(conn, client, now_ms=now_ms, clock=clock)
        )
        self._notify = notify
        self._cli_brains = cli_brains or CliBrains(notify=notify)
        self._cli_sessions = CliSessions(conn)
        self._cli_loops: set[CliLoop] = set()
        self._now_ms = now_ms
        self._actions = actions
        self._settings = SettingsStore(conn)
        self._metrics_conn = conn
        self._spend = SpendTracker(conn, now_ms, tz)
        self._history = AgentHistory(conn, now_ms)
        self.conversations = Conversations(conn, now_ms)
        self.quotas = Quotas(conn, now_ms, notify)
        self._named_conversations: dict[int, Conversation] = {}
        self._confirmations = Confirmations(clock)
        self._plugins = plugins
        self._gate = gate if gate is not None else ToolGate(plugins, conn=conn, now_ms=now_ms)
        self._background = background
        # The connections to the plugins' servers, kept for a short while: a second question about
        # the same service does not start it again.
        self._pool = McpPool(idle_s=PLUGIN_IDLE_S)
        self._planner = Planner(client)
        self._picker = ToolPicker(client)
        self._tool_support = ToolSupport(models, clock=clock)
        # pending id -> what the call of a plugin's tool that waits for that yes needs.
        self._followups: dict[str, _Followup] = {}
        self._catalog = AppCatalog(actions, clock=clock)
        self._targets = OpenTargets(conn, now_ms)
        self._warming: set[asyncio.Task[None]] = set()
        self._tools = Tools(client, http=http, now=now, services=services)
        self._voice = Voice(client)
        self._decider = decider if decider is not None else Router(client)
        self._inflight: dict[str, asyncio.Task[dict]] = {}
        self._inflight_members: dict[str, int] = {}
        self._clock = clock
        # session id -> when agent.cancel named it (the monotonic clock).
        self._cancelled: dict[str, float] = {}
        self._last_language = DEFAULT_LANGUAGE
        # turn id -> the member whose thread it is in (None: not known yet): the turns that were
        # written but are not announced to the app (`agent.turn`) until the voice is ready.
        self._held: dict[int, int | None] = {}
        # turn id -> the Japanese rendering of its line, made by the model in the same call, for
        # the voice. In memory only, until the turn is said: never logged or stored.
        self._japanese: dict[int, str] = {}
        self._pruned_at_ms: int | None = None
        self._prune_if_due()
        # A question waits in this process only: one that was left by the last is long past.
        try:
            self._history.expire_pending()
        except sqlite3.Error as exc:
            logger.warning("Could not expire the old questions: %s", type(exc).__name__)

    @property
    def language(self) -> str:
        """The language of the latest question: what a message from elsewhere is answered in."""
        return self._last_language

    def turn(self, turn_id: int) -> Turn | None:
        """A turn of the history, for a caller that needs the longer text of an answer."""
        try:
            return self._history.get(turn_id)
        except sqlite3.Error:
            return None

    def register(self, router: RpcRouter) -> None:
        """Put the six methods of the assistant on `router`."""
        methods = {
            "agent.ask": self._ask,
            "agent.confirm": self._confirm,
            "agent.cancel": self._cancel,
            "agent.history.list": self._history_list,
            "agent.history.search": self._history_search,
            "agent.history.clear": self._history_clear,
            "agent.history.retract": self._history_retract,
            "agent.history.delete": self._history_delete,
            "conversations.list": self._conversations_list,
            "quota.list": self._quota_list,
            "agent.speech.status": self._speech_status,
            "agent.speak": self._speak_turn,
            "messages.search": self._messages_search,
            "conversations.create": self._conversations_create,
            "conversations.rename": self._conversations_rename,
            "conversations.delete": self._conversations_delete,
            "brains.cli": self._list_clis,
            "usage.allowances": self._allowances,
            "brains.cli_login": self._login_cli,
        }
        for method, handler in methods.items():
            router.register(method, handler)

    async def _allowances(self, params: dict) -> dict:
        from .cli.allowances import read_allowances
        return {**await read_allowances(self._cli_brains), "quotas": self.quotas.list()}

    async def _list_clis(self, params: dict) -> dict:
        return await self._cli_brains.list(params)

    async def _login_cli(self, params: dict) -> dict:
        name = params.get("id")
        if name not in ("claude", "codex", "gemini"):
            raise invalid_params("Unknown CLI")
        try:
            return self._cli_brains.login(name)
        except (OSError, ValueError):
            raise invalid_params("Install the CLI before signing in") from None

    def prewarm(self) -> None:
        """Read the apps of the Start menu now, in the background, so that the first "open" finds
        them read. Needs the running loop; does nothing without one."""
        try:
            task = asyncio.get_running_loop().create_task(self._catalog.warm())
        except RuntimeError:
            return
        self._warming.add(task)
        task.add_done_callback(self._warming.discard)

    async def aclose(self) -> None:
        """End the requests being thought about and close the HTTP client of the tools. A fold of
        the conversation that runs is ended too (the memory's own, by the memory)."""
        await asyncio.gather(*(loop.aclose() for loop in self._cli_loops))
        await self._cli_brains.aclose()
        if self._memory is None:
            await self._conversation.aclose()
            for conversation in self._conversations.values():
                await conversation.aclose()
        for conversation in self._named_conversations.values():
            await conversation.aclose()
        tasks = [*self._inflight.values(), *self._warming]
        refreshing = self._catalog.cancel()
        if refreshing is not None:
            tasks.append(refreshing)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._inflight.clear()
        if self._computer is not None:
            await self._computer.aclose()
        await self._pool.aclose()
        await self._tools.aclose()

    # --- agent.ask ------------------------------------------------------------------------------

    async def _ask(self, params: dict) -> dict:
        """`agent.ask`: the params checked, then `ask`."""
        try:
            attachments = from_wire(params.get("attachments"))
        except AttachmentError as error:
            raise RpcError(
                INVALID_PARAMS, f"Invalid params: attachments ({error.kind})", {"kind": error.kind}
            ) from None
        target_app = params.get("target_app")  # for the apps to come; not used yet
        if target_app is not None and not isinstance(target_app, str):
            raise invalid_params("target_app must be a string or null")
        member_id = params.get("member_id", 0)
        if member_id is None:
            member_id = 0
        if type(member_id) is not int or member_id < 0:
            raise invalid_params("member_id must be a whole number of 0 or more")
        session = _str(params, "session_id")
        text = _str(params, "text", blank=True)
        language = _str(params, "ui_language")
        quote = quote_from_wire(params.get("quote"))
        conversation_id = self.conversations.resolve(member_id, _opt_int(params, "conversation_id"))
        if quote is not None and params.get("source") != "app":
            raise invalid_params("quotes are supported for composer requests")
        replacement = _opt_int(params, "replace_turn_id")
        if replacement is not None and not self._was_cancelled(session):
            if not text.strip() and not attachments:
                raise invalid_params("a replacement must contain text or attachments")
            if self._speaker(member_id) is None:
                raise invalid_params("there is no such member")
            old = self._history.get(replacement)
            if (old is None or old.member_id != member_id or old.conversation_id != conversation_id or old.status in ("running", "confirm")
                    or old.source in ("member", "routine") or params.get("source") != "app"):
                raise invalid_params("replace_turn_id must name a completed owner message in this thread")
            await self._history_retract({"session_id": old.session_id, "turn_id": replacement, "member_id": member_id})
        return await self.ask(
            session,
            text,
            language,
            params.get("source"),
            attachments=attachments,
            member_id=member_id,
            quote=quote,
            conversation_id=conversation_id,
        )

    def _speaker(self, member_id: int) -> Speaker | None:
        """Who `member_id` is: Raphael for 0, a member of the team, or None for nobody."""
        if member_id == 0:
            return RAPHAEL
        if self._team is None:
            return None
        try:
            return self._team.speaker(member_id)
        except sqlite3.Error as exc:
            logger.warning("Could not read the members: %s", type(exc).__name__)
            return None

    async def ask(
        self,
        session_id: str,
        text: str,
        ui_language: str,
        source: str | None = None,
        *,
        attachments: Sequence[Attachment] = (),
        member_id: int = 0,
        quote: dict[str, str] | None = None,
        conversation_id: int | None = None,
    ) -> dict:
        """One request, as `agent.ask` answers it. `attachments` are the files handed with it
        (`services.attachments.Attachment`, at most MAX_ATTACHMENTS), kept in memory only; with
        files and no words, the request asks 「看這個」 in its language. `member_id` is who it is
        for: Raphael (0), or a member of the team addressed by its thread's composer, in writing
        or by voice. The global voice hotkey and chat apps default to Raphael."""
        speaker = self._speaker(member_id)
        if speaker is None:
            raise invalid_params("there is no such member")
        original_text = None
        expand = getattr(self._team, "expand_command", None)
        if expand is not None:
            expanded = expand(member_id, text)
            if expanded != text:
                original_text, text = text, expanded
        ask = _Ask(
            session_id=session_id,
            started_at_ms=self._now_ms(),
            original_text=original_text,
            text=text,
            language=normalize_language(ui_language),
            attachments=list(attachments),
            quote=quote,
            speaker=speaker,
            conversation_id=self.conversations.resolve(member_id, conversation_id),
        )
        if source is not None:
            if not isinstance(source, str) or not _SOURCE.fullmatch(source):
                raise invalid_params("source must be a short lower-case word")
            ask.source = source
        if len(ask.attachments) > MAX_ATTACHMENTS:
            raise RpcError(
                INVALID_PARAMS, "Invalid params: attachments (count)", {"kind": "count"}
            )
        if ask.attachments and not ask.text.strip():
            ask.text = _LOOK_AT_THIS[ask.language]
        self._last_language = ask.language
        if self._was_cancelled(ask.session_id):
            # Esc came first: the request is answered as cancelled and nothing runs or is kept.
            return self._done(
                0, ask.language, "understood", phrase(ask.language, "denied"), silent=True
            )
        if self._attachment_store and ask.attachments:
            ask.attachments = [await asyncio.to_thread(self._attachment_store.save, a) for a in ask.attachments]
        ask.voiced = self._voiced(ask.source, ask.speaker.id)
        # The questions that ran out are expired, and a new request replaces those that still wait.
        self._expire(self._confirmations.sweep())
        if ask.source in _AT_THIS_COMPUTER:
            self._stop_speaking()  # a new request ends what Raphael was saying
            self._deny(self._confirmations.cancel_all())
            if self._computer is not None:
                self._computer.stop_all("replaced")
        else:  # a message from a messaging app leaves a question asked at this computer alone
            self._deny(self._confirmations.cancel_session(ask.session_id))
        self._prune_if_due()
        earlier = self._inflight.pop(ask.session_id, None)
        if earlier is not None:
            earlier.cancel()
        await asyncio.gather(*(loop.aclose() for loop in list(self._cli_loops) if loop.task and loop.task.cancelling()))
        task = asyncio.ensure_future(self._voice_answer(ask) if ask.source == VOICE else self._answer(ask))
        self._inflight[ask.session_id] = task
        self._inflight_members[ask.session_id] = ask.speaker.id
        try:
            result = await task
            return await self._said(result, ask.source, ask.speaker.id, ask.language)
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if task.cancelled() and (current is None or not current.cancelling()):
                # Ended by agent.cancel (or by a newer request of the session), not by the caller.
                return self._done(
                    0, ask.language, "understood", phrase(ask.language, "denied"), silent=True
                )
            raise
        finally:
            if task.cancelled() and ask.turn_id:
                self._finish(ask, "answer", "understood", phrase(ask.language, "denied"),
                             ask.ledger or Ledger(self._spend), status="denied")
            if self._inflight.get(ask.session_id) is task:
                del self._inflight[ask.session_id]
                self._inflight_members.pop(ask.session_id, None)
            self._release(ask.held)  # a turn announced late must never be left unannounced

    async def _voice_answer(self, ask: _Ask) -> dict:
        """Bound the whole voice turn, including routing and any harness tool rounds."""
        try:
            return await asyncio.wait_for(self._answer(ask), VOICE_TURN_TIMEOUT_S)
        except TimeoutError:
            return self._failed(ask, "answer", "timeout", ask.ledger or Ledger(self._spend))

    async def _answer(self, ask: _Ask) -> dict:
        """One request, from the router to the reply. Never raises for a failure of the world."""
        began = time.perf_counter()
        ledger = Ledger(self._spend)
        ask.ledger = ledger
        if not ask.text.strip():
            return self._finish(ask, "unclear", "negative", phrase(ask.language, "unclear"), ledger)
        off = self._files_off(ask)
        if off is not None:
            # A file of a kind the owner turned off is refused in one line, before any model is asked.
            return self._finish(ask, "chat", "negative", phrase(ask.language, off), ledger)
        settings = self._read_settings(ask.speaker)
        blocked = self._blocked(settings)
        if ask.source != VOICE or not ask.speaker.is_raphael:
            if blocked is not None:
                return self._failed(ask, "answer", blocked, ledger)
            self._record(ask, "answer", "", "", "running", None, ledger)
            try:
                return await self._narrated(ask, settings, ledger)
            except AgentFailure as error:
                return self._failed(ask, "answer", error.kind, ledger)
            except Exception as exc:
                logger.warning("The typed harness failed: %s", type(exc).__name__)
                return self._failed(ask, "answer", failure_kind(exc), ledger)
        decision: RouteDecision | None = None
        failure = blocked
        if blocked is None:
            try:
                decision = await self._decider.classify(
                    ask.text, DEFAULTS["agent_model"] if brain_ref(settings.model) else settings.model,
                    ledger, background_line(self._briefs(ask.speaker.id)),
                    context=self._before_run(ask.speaker, ask.conversation_id),
                )
            except AgentFailure as error:
                failure = error.kind
            except Exception as exc:  # a bug in the router must not become an RPC error
                logger.warning("Routing failed unexpectedly: %s", type(exc).__name__)
                failure = "error"
        if decision is None:
            # No model could say what it is: a plain command needs none.
            decision = match_command(ask.text)
            if decision is None and blocked is not None:
                return self._failed(ask, "unclear", blocked, ledger)
            if decision is not None:
                logger.info("A command was found by its words: no model could route (%s)", failure)
        route = "answer" if decision is None or decision.route == "unclear" else decision.route
        source = decision.source if decision is not None else "harness"
        try:
            if decision is None or decision.route == "unclear":
                self._record(ask, "answer", "", "", "running", None, ledger)
                return await self._narrated(ask, settings, ledger)
            result = await self._route(ask, decision, settings, ledger)
        except AgentFailure as error:
            result = self._failed(ask, route, error.kind, ledger)
        except Exception as exc:  # the shell shows no raw errors: this too is a 否 reply
            logger.warning("The %s route failed unexpectedly: %s", route, type(exc).__name__)
            result = self._failed(ask, route, failure_kind(exc), ledger)
        logger.info(
            "Agent turn: route=%s source=%s status=%s cost=%.5f ms=%d",
            route,
            source,
            result["status"],
            ledger.total,
            round((time.perf_counter() - began) * 1000),
        )
        return result

    async def _route(
        self, ask: _Ask, decision: RouteDecision, settings: _Settings, ledger: Ledger
    ) -> dict:
        route = decision.route
        if (
            self._memory is not None
            and asks_to_remember(ask.text)
            and (
                route in ("delegate", "unclear")
                or (route == "command" and COMMANDS.get(decision.command) is None)
            )
        ):
            # "記住…" is no task and no command: it is answered where the note is written.
            route = "chat"
        if route == "unclear" and ask.attachments:
            route = "chat"  # what is unclear is the words: the files say what it is about
        if self._about_the_team(ask) and (
            route in ("unclear", "chat", "discuss")
            or (route == "command" and COMMANDS.get(decision.command) is None)
        ):
            # 「幫我加一個管行程的成員」, 「問小行程明天的事」: answered where the team tools are offered.
            route = "answer"
        if route == "command":
            return await self._command(ask, decision, settings, ledger)
        if brain_ref(settings.model):
            self._record(ask, "answer", "", "", "running", None, ledger)
            return await self._narrated(ask, settings, ledger)
        if route == "answer":
            return await self._answer_with_tools(ask, decision, settings, ledger, self._offer(ask))
        if route == "operate":
            return await self._operate(ask, settings, ledger)
        if route == "delegate":
            return self._hand_over(ask, settings, ledger)
        if route in ("discuss", "chat"):
            inputs = await self._inputs(ask, ledger)
            reply, seen = await self._reply(ask, route, settings, ledger, inputs)
            return self._finish_reply(ask, route, reply, seen, ledger)
        return self._finish(ask, "unclear", "negative", phrase(ask.language, "unclear"), ledger)

    # --- the words of an answer, and the memory ---------------------------------------------------

    async def _reply(
        self,
        ask: _Ask,
        route: str,
        settings: _Settings,
        ledger: Ledger,
        tools: list[ToolResult],
        *,
        background: str = "",
        question: str | None = None,
    ) -> tuple[Reply, list[ToolResult]]:
        """Raphael's words for the request, from the conversation (`memory.conversation`) and
        the memory's index, and the tool results the model was shown in the end. When the model
        asks to read notes or to search the past turns, that is done and it is asked once more,
        with what was found. AgentFailure as `Voice.reply` raises it. A member of the team reads
        its own conversation and memory, and speaks as itself."""
        member_id = ask.speaker.id
        context = self._conversation_of(member_id, ask.conversation_id).context(quick=route not in _FULL_CONTEXT_ROUTES)
        index = None if self._memory is None else self._memory.index(member_id)

        async def word(results: list[ToolResult], looked_up: bool) -> Reply:
            return await self._voice.reply(
                model=settings.model,
                language=ask.language,
                route=route,
                question=ask.text if question is None else question,
                context=context.turns,
                tools=results,
                moment=self._moment(),
                ledger=ledger,
                background=background,
                summary=context.summary,
                memory_index=index,
                looked_up=looked_up,
                speaker=ask.speaker,
                say_ja=ask.voiced and ask.language != "ja",
                roster=roster_prompt(self._team, ask.speaker, ask.language) + owner_context(self._settings),
            )

        reply = await word(tools, False)
        if self._memory is None or not reply.wants_lookup:
            return reply, tools
        shown = [turn.id for turn in context.turns if turn.id is not None]
        found = [*tools, *self._look_up(ask, reply, shown)]
        again = await word(found, True)
        if not again.memory:
            again.memory = reply.memory
        return again, found

    def _look_up(self, ask: _Ask, reply: Reply, shown: list[int]) -> list[ToolResult]:
        """The lookups the reply asked for (`memory_read`, `history_search`), through the gate,
        each a step of the request. The turns the model already reads are not found again."""
        memory = self._memory
        assert memory is not None
        member_id = ask.speaker.id
        results: list[ToolResult] = []
        wanted: list[tuple[str, Callable[[], ToolResult]]] = []
        if reply.read:
            wanted.append(("memory_read", lambda: memory.read(reply.read, member_id)))
        if reply.search:
            wanted.append(
                (
                    "history_search",
                    lambda: memory.search(reply.search, exclude=shown, member_id=member_id, conversation_id=ask.conversation_id),
                )
            )
        for name, look in wanted:
            began = time.perf_counter()
            try:
                self._gate.check(Action(name, {}), None)
                result = look()
            except (UnknownTool, NotApproved):
                result = ToolResult(name, False, None, "blocked")
            outcome = "ok" if result.ok else ("blocked" if result.error == "blocked" else "error")
            ask.steps.append(self._step("tool", name, BUILTIN_SERVER, outcome, began, result.error))
            results.append(result)
        return results

    def _remember(self, ask: _Ask, reply: Reply, seen: list[ToolResult], *, approval: Approval | None = None, action: Action | None = None) -> "Applied | None":
        """Write or delete the notes the reply asked for, if it may: only from the owner's own
        turn, so not when the model was shown what the web or a connected service returned (that
        could have put the words in its mouth). Each change goes through the gate and is a step of
        the request. None when there was nothing to do. A member's notes go to its own memory; a
        request one member handed another is not the owner's turn, and writes none."""
        if self._memory is None or not reply.memory:
            return None
        if ask.nested:
            logger.info("Notes were not written: the request came from another member")
            return None
        if any(r.untrusted or (r.name == "web_search" and r.ok) for r in seen):
            logger.info("Notes were not written: the reply was shown data from outside")
            for item in reply.memory:
                tool = "memory_delete" if item.op == "DELETE" else "memory_write"
                ask.steps.append(
                    self._step("tool", tool, BUILTIN_SERVER, "blocked", time.perf_counter(), "blocked")
                )
            return None
        allowed = []
        for item in reply.memory:
            tool = "memory_delete" if item.op == "DELETE" else "memory_write"
            try:
                self._gate.check(action or Action(tool, {}), approval)
            except (UnknownTool, NotApproved):
                ask.steps.append(
                    self._step("tool", tool, BUILTIN_SERVER, "blocked", time.perf_counter(), "blocked")
                )
                continue
            allowed.append(item)
        began = time.perf_counter()
        applied = self._memory.apply_said(allowed, 0, ask.speaker.id)
        for _ in applied.written:
            ask.steps.append(self._step("tool", "memory_write", BUILTIN_SERVER, "ok", began, None))
        for _ in applied.deleted:
            ask.steps.append(self._step("tool", "memory_delete", BUILTIN_SERVER, "ok", began, None))
        for _ in applied.refused:
            ask.steps.append(
                self._step("tool", "memory_write", BUILTIN_SERVER, "blocked", began, "blocked")
            )
        logger.info(
            "Notes from a turn: written=%d deleted=%d refused=%d",
            len(applied.written),
            len(applied.deleted),
            len(applied.refused),
        )
        return applied

    def _finish_reply(
        self, ask: _Ask, route: str, reply: Reply, seen: list[ToolResult], ledger: Ledger
    ) -> dict:
        """Write the turn of a worded reply down and answer, with the notes it asked for. A
        reply whose line is empty beside a note says what became of the note; one that wrote a
        note and said something else has the notice "remembered", which the overlay shows after
        the line."""
        language = ask.language
        if (self._gate.ask_first and reply.memory and self._memory is not None and not ask.nested
                and not any(r.untrusted or (r.name == "web_search" and r.ok) for r in seen)):
            item = reply.memory[0]
            tool = "memory_delete" if item.op == "DELETE" else "memory_write"
            call = PlannedCall(PluginTool(tool, BUILTIN_SERVER, tool, "Change a requested memory", {}, Tier.T1), asdict(item))
            result = self._ask_plugin(ask, call, seen, ledger)
            self._followups[result["confirm"]["id"]].memory_reply = (route, reply, ledger)
            return result
        applied = self._remember(ask, reply, seen)
        which, text, notice = reply.prefix, reply.text, None
        if applied is not None and (applied.written or applied.deleted):
            if not text:
                which = "understood"
                text = phrase(language, "remembered" if applied.written else "forgotten")
            elif applied.written:
                notice = "remembered"
        elif applied is not None and applied.refused and not text:
            which = "negative"
            secret = any(kind == "secret" for kind in applied.refused)
            text = phrase(language, "secret_not_kept" if secret else "not_remembered")
        if not text:
            raise AgentFailure("error")  # a lookup and nothing after it, or a note it could not take
        ask.replies = reply.messages
        result = self._finish(ask, route, which, text, ledger, detail=reply.detail)
        if text == reply.text:  # not a fixed phrase in its place: the model's own line
            self._keep_japanese(result["turn_id"], reply.say_ja)
        if notice is not None:
            result["notice"] = notice
        if applied is not None and applied.written and self._memory is not None:
            self._memory.note_turn(applied.written, result["turn_id"], ask.speaker.id)
        return result

    # --- the owner's desktop ----------------------------------------------------------------------

    async def _operate(self, ask: _Ask, settings: _Settings, ledger: Ledger, *,
                       narration: NarratedLoop | None = None, request: str | None = None) -> dict:
        """A run on the owner's desktop (`raphael_core.computer.run`). Only for a request made at
        this computer: a message from a phone is a 否 reply, as is a shell that cannot be reached
        and a model that takes no tools. The reply is the run's first question or how it ended."""
        language = ask.language
        if ask.source not in _AT_THIS_COMPUTER:
            return self._finish(ask, "operate", "negative", phrase(language, "computer_away"), ledger)
        if self._computer is None:
            return self._finish(
                ask, "operate", "negative", phrase(language, "computer_unavailable"), ledger
            )
        if self._tool_support.supports(settings.model) is False:
            return self._finish(ask, "operate", "negative", phrase(language, "computer_model"), ledger)
        limit = settings.limit_usd
        run = ComputerRun(
            session_id=ask.session_id,
            language=language,
            request=request or ask.text,
            model=settings.model,
            client=self._client,
            bridge=self._computer.bridge,
            gate=self._gate,
            ledger=ledger,
            over_budget=lambda: self._over_budget(limit),
            open_app=self._open_for_run,
            background=self._before_run(ask.speaker, ask.conversation_id),
            clock=self._clock,
            now_ms=self._now_ms,
        )
        run.context = ask
        run.narration = narration
        self._computer.begin(run)
        assert run.task is not None
        run.task.add_done_callback(lambda _done, run=run: self._settle_unheard(run))
        return await self._next_from_run(run)

    def _before_run(self, speaker: Speaker = RAPHAEL, conversation_id: int | None = None) -> str:
        """What a run is told of what came before its request: the memory's index, and the
        conversation as a quick answer reads it (the summary and the last few turns). A run
        writes no note, now or later: its turn holds what the model read on the screen, which is
        untrusted, and a note is written only from the owner's own words (see `_remember`, and the
        reflection pass, which reads only the owner's question of an `operate` turn)."""
        context = self._conversation_of(speaker.id, conversation_id).context(quick=True)
        index = "" if self._memory is None else (
            self._memory.index() if speaker.is_raphael else self._memory.index(speaker.id)
        )
        if isinstance(index, MemoryIndex):  # the shared notes, then Raphael's own
            index = "\n".join(part for part in (index.shared, index.own) if part)
        persona = "" if speaker.is_raphael else (
            f"Speaker: {speaker.name}\nRole: {speaker.role}\n"
            f"Personality: {speaker.personality}\n"
            f"Owner's instructions: {speaker.instructions}\n\n"
        )
        return persona + owner_context(self._settings) + conversation_block(context.summary, context.turns, index)

    async def _next_from_run(self, run: ComputerRun) -> dict:
        """What the run says next, as the reply of `agent.ask` or `agent.confirm`: its question
        (the turn waits for a yes), or how it ended (the turn is complete). The turn is written
        when the run first asks or ends, and brought up to date after. A wait that is given up
        (Esc, a newer request) stops the run, whose end is then written by itself, as is the end
        of a run stopped while its question waited, which nobody waits for either."""
        run.listening = True
        try:
            event = await run.next_event()
        except asyncio.CancelledError:
            run.listening = False
            run.stop("cancelled")
            self._settle_unheard(run)
            raise
        run.listening = False
        steps = run.take_steps()
        if isinstance(event, Final):
            return self._write_final(run, event, steps)
        return self._run_question(run, event, steps)

    def _run_question(self, run: ComputerRun, question: Question, steps: list[Step]) -> dict:
        ask: _Ask = run.context
        language = ask.language
        prefix = PREFIXES[language].question
        if getattr(run, "narration", None) is not None:
            ask.steps.extend(steps)
            run.turn_id = self._record(ask, "answer", prefix if ask.speaker.is_raphael else "", question.stored, "confirm", None, run.ledger)
        elif run.turn_id is None:
            ask.steps.extend(steps)
            run.turn_id = self._record(ask, "operate", prefix, question.stored, "confirm", None, run.ledger)
            run.recorded_cost = run.ledger.total
        else:
            self._update_run_turn(run, steps, prefix=prefix, text=question.stored, status="confirm")
        confirm = {
            "id": question.id,
            "tier": question.tier.name,
            "prefix": prefix,
            "text": question.text,
            "yes": phrase(language, "yes"),
            "no": phrase(language, "no"),
            "timeout_ms": CONFIRM_TIMEOUT_MS,
        }
        if question.always:
            labels = answer_labels(language)
            confirm.update(yes=labels["once"], no=labels["deny"], always=labels["always"])
        return {"status": "confirm", "turn_id": run.turn_id, "confirm": confirm}

    def _write_final(self, run: ComputerRun, final: Final, steps: list[Step]) -> dict:
        """Complete the turn of a run that ended, and answer with what Raphael says."""
        ask: _Ask = run.context
        run.written = True
        if getattr(run, "narration", None) is not None:
            ask.steps.extend(steps)
            return {"status": final.status, "ok": final.status == "done", "text": final.text, "detail": final.detail}
        if run.turn_id is None:
            ask.steps.extend(steps)
            reply = self._finish(
                ask, "operate", final.which, final.text, run.ledger,
                detail=final.detail, status=final.status,
            )  # fmt: skip
            run.turn_id = reply["turn_id"]
            return _Silent(reply) if final.status in _UNSPOKEN else reply
        prefix = _prefix(ask.language, final.which)
        self._update_run_turn(
            run, steps, prefix=prefix, text=final.text, status=final.status, detail=final.detail
        )
        return self._done(
            run.turn_id, ask.language, final.which, final.text, silent=final.status in _UNSPOKEN
        )

    def _update_run_turn(
        self,
        run: ComputerRun,
        steps: list[Step],
        *,
        prefix: str,
        text: str,
        status: str,
        detail: str | None = None,
    ) -> None:
        turn_id = run.turn_id
        if not turn_id:
            return
        cost, run.recorded_cost = run.ledger.total - run.recorded_cost, run.ledger.total
        if steps:
            self._write(lambda: self._history.add_steps(turn_id, steps))
        self._write(
            lambda: self._history.resolve(
                turn_id, prefix=prefix, text=text, status=status, detail=detail,
                cost_usd=cost, model=run.ledger.model,
            )
        )  # fmt: skip
        self._turn_changed(turn_id)

    def _settle_unheard(self, run: ComputerRun) -> None:
        """A run that ended while nobody waited for it (its request was cancelled, or it was
        stopped while its question waited) has its turn written here."""
        if not run.listening and not run.written and run.final is not None:
            final = self._write_final(run, run.final, run.take_steps())
            if getattr(run, "narration", None) is not None:
                ask = run.context
                turn = self._history.get(ask.turn_id) if ask.turn_id else None
                if turn is not None and turn.status not in ("running", "confirm"):
                    self._record(ask, turn.route, turn.prefix, turn.text, turn.status, turn.detail, run.ledger)
                else:
                    self._finish(ask, "answer", run.final.which, final["text"], run.ledger,
                                 status=run.final.status, detail=run.final.detail)

    def _over_budget(self, limit_usd: float) -> bool:
        return False

    async def _open_for_run(self, name: str) -> str | None:
        """Open the app of the Start menu that `name` names, for a run, through the gate like the
        `open` command: the name it goes by, or None when there is none."""
        app = await self._catalog.find(name, loose=False) or await self._catalog.lookup(name)
        if app is None:
            app = await self._catalog.find(name)
        if app is None:
            return None
        command = COMMANDS["open"]
        args = {"target_kind": "app", "target": app.app_id}
        self._gate.check(Action(command.name, args), None)
        await run_command(command, args, self._actions)
        return app.name

    # --- the background ---------------------------------------------------------------------------

    def _briefs(self, member_id: int = 0) -> list:
        """The tasks in the background as a few facts (running, waiting, or ended in the last half
        hour), for the router and the answer. Empty when the worker cannot say."""
        try:
            return list(self._background.brief_for_assistant(member_id=member_id))
        except Exception as exc:  # what the background knows must not stop a request
            logger.warning("Could not read the background for a request: %s", type(exc).__name__)
            return []

    def _hand_over(self, ask: _Ask, settings: _Settings, ledger: Ledger) -> dict:
        """A task for the background worker: it is queued, and Raphael says 《了。》 at once. The
        worker reports on the overlay when it is done; the turn of the history is then completed
        with the report. A model that takes no tools, a queue that is full and a worker that was
        switched off for crashing too often are a 否 reply with the reason."""
        language = ask.language
        background = self._background
        if self._tool_support.supports(settings.model) is False:
            logger.info("A task was not handed over: the model %s takes no tools", settings.model)
            return self._finish(
                ask, "delegate", "negative", phrase(language, "worker_model"), ledger
            )
        try:
            task_id = background.submit(
                session_id=ask.session_id,
                text=ask.text,
                language=language,
                model=settings.model,
                attachments=ask.attachments,
                member_id=ask.speaker.id,
            )
        except Refused as refused:
            return self._finish(ask, "delegate", "negative", phrase(language, refused.kind), ledger)
        except Exception as exc:  # a worker that cannot take a task is a 否 reply, not a raise
            logger.warning("A task could not be handed over: %s", type(exc).__name__)
            return self._failed(ask, "delegate", "error", ledger)
        reply = self._finish(ask, "delegate", "understood", phrase(language, "handoff"), ledger)
        background.attach_turn(task_id, reply["turn_id"])
        return reply

    # --- answers --------------------------------------------------------------------------------

    def _moment(self) -> Mapping[str, str]:
        return self._tools.local_time().data or {}

    def _message(self, ask: _Ask, kind: str, text: str, prefix: str = "") -> None:
        """Publish only public words; each commit is visible before the next model/tool call."""
        if not ask.turn_id or not text.strip():
            return
        line = compose(prefix, text, None) if ask.speaker.is_raphael and kind in ("progress", "reply") else text
        self._write(lambda: self._history.add_message(ask.turn_id, kind, line))
        # Only the spoken final waits for its voice; progress and events remain live.
        self._turn_changed(ask.turn_id, ask.speaker.id,
                           hold=kind == "reply" and ask.held == ask.turn_id)

    @staticmethod
    def _loop_result(result: ToolResult) -> ToolOutcome:
        return ToolOutcome(result.ok, result.error, json.dumps(result.data, ensure_ascii=False))

    def _computer_result(self, loop: NarratedLoop, result: dict) -> ToolOutcome:
        """Screen-derived answers remain untrusted for memory and future tool input."""
        tool = ToolResult("computer", result["ok"], {"text": result["text"]},
                          None if result["ok"] else result["status"], untrusted=True)
        loop.seen.append(tool)
        return self._loop_result(tool)

    async def _narrated(
        self, ask: _Ask, settings: _Settings, ledger: Ledger, loop: NarratedLoop | CliLoop | None = None
    ) -> dict:
        """A narrated turn, sharing the fast tier's safety gate and permission cards."""
        if loop is None:
            context = self._conversation_of(ask.speaker.id, ask.conversation_id).context(quick=False)
            index = None if self._memory is None else self._memory.index(ask.speaker.id)
            say_ja = ask.voiced and ask.language != "ja"
            if brain_ref(settings.model):
                system = cli_system_prompt(ask.language,
                    name=None if ask.speaker.is_raphael else ask.speaker.name,
                    role=ask.speaker.role, instructions=ask.speaker.instructions,
                    personality=ask.speaker.personality, memory_index=index)
            elif ask.speaker.is_raphael:
                system = system_prompt(ask.language, "answer", index, say_ja=say_ja, narrated=True)
            else:
                system = member_system_prompt(
                    ask.language, "answer", name=ask.speaker.name, role=ask.speaker.role,
                    instructions=ask.speaker.instructions, personality=ask.speaker.personality,
                    memory_index=index, narrated=True,
                )
            system += roster_prompt(self._team, ask.speaker, ask.language) + owner_context(self._settings)
            user = build_user_message(
                labelled_lines(ask.exchange_speakers[0], ask.text) if ask.exchange_speakers else ask.text,
                self._moment(), context.turns, [], ask.language,
                background_block(self._briefs(ask.speaker.id)), summary=context.summary, say_ja=say_ja, narrated=True,
                quote=ask.quote,
            )
            if ask.attachments:
                user += "\nFiles: " + json.dumps([
                    {"index": n, "name": a.name, "mime": a.mime}
                    for n, a in enumerate(ask.attachments, 1)
                ], ensure_ascii=False)
            snapshot = take_snapshot(
                self._plugins, ask.text, memory=self._memory is not None,
                attachments=bool(ask.attachments), speaker=ask.speaker,
                team=team_tools_for(self._team, ask.speaker, always=True), max_tier=Tier.T3,
                interactive=True,
            )
            urls = SeenUrls([ask.text, context.summary, *[
                "\n".join([turn.question, turn.text, turn.detail or "", *turn.messages])
                for turn in context.turns
            ]])
            checked_batch = None
            screenings: dict[str, Screening] = {}

            def plan(name: str, args: dict) -> PlannedCall | None:
                if brain_ref(settings.model) and name in ("memory_write", "memory_delete") and self._memory is not None:
                    return PlannedCall(PluginTool(name, BUILTIN_SERVER, name, "Change an explicitly requested memory", {}, Tier.T1), args)
                if name not in snapshot.refs:
                    return None
                if snapshot.pool is not None and name in snapshot.pool.by_alias:
                    return PlannedCall(snapshot.pool.by_alias[name], args)
                if name in TEAM_TOOLS:
                    return PlannedCall(team_tool(name), args)
                if name in HARNESS_TOOLS and name not in ("show", "computer"):
                    return PlannedCall(harness_tool(name), args)
                return None

            async def execute(call_id: str, name: str, args: dict) -> ToolOutcome:
                nonlocal checked_batch, screenings
                assert loop is not None
                if isinstance(loop, CliLoop) and name == "cli_permission":
                    return await self._cli_permission(ask, args, ledger, loop)
                ref = snapshot.refs.get(name)
                memory_call = isinstance(loop, CliLoop) and name in ("memory_write", "memory_delete") and self._memory is not None
                if ref is None and not memory_call:
                    return ToolOutcome(False, "unknown_tool")
                # Include results supplied after a permission pause, as well as ordinary rounds.
                if loop.seen:
                    urls.add_from(json.dumps(loop.seen[-1].data, ensure_ascii=False))
                if ask.nested and checked_batch is not loop.batch_calls:
                    # A handed-over request must not partially run and then restart in the worker.
                    checked_batch = loop.batch_calls
                    batch = [(call.id, planned) for call in loop.batch_calls
                             if isinstance(call.arguments, dict)
                             and len(canonical(call.arguments)) <= MAX_ARGUMENT_CHARS
                             and (planned := plan(call.name, call.arguments)) is not None]
                    judged = await asyncio.gather(*(
                        self._screen_call(ask, planned, ledger) for _, planned in batch
                    ))
                    screenings = {key: screening for (key, _), screening in zip(batch, judged)}
                    for screening in judged:
                        if screening.verdict == DENY:
                            loop.response = self._finish(ask, "answer", "negative", forbidden(ask.language, screening), ledger)
                            raise Stop("finished")
                    if any(needs_yes((self._tier_now(planned) or Tier.T0) >= Tier.T2, screening)
                           for (_, planned), screening in zip(batch, judged)):
                        loop.response = self.ask_in_background(ask, ledger)
                        raise Stop("finished")
                if name == "computer":
                    self._gate.check(Action(name, args), None)
                    if ask.source not in _AT_THIS_COMPUTER or ask.nested:
                        return ToolOutcome(False, "presence_required")
                    if self._computer is None or self._tool_support.supports(settings.model) is False:
                        return ToolOutcome(False, "unavailable")
                    request = args.get("request")
                    if not isinstance(request, str) or not request.strip():
                        return ToolOutcome(False, "bad_arguments")
                    result = await self._operate(ask, settings, ledger, narration=loop, request=request)
                    if result["status"] == "confirm":
                        loop.response = result
                        raise Stop(WAITING)
                    return self._computer_result(loop, result)
                if ref is not None and ref.kind == FIND:
                    definitions, lines = snapshot.find(str(args.get("query", "")))
                    loop.tools.extend(definitions)
                    return ToolOutcome(True, content="\n".join(lines), tools=definitions)
                planned = plan(name, args)
                if planned is not None:
                    screened = [screenings[call_id]] if call_id in screenings else None
                    outcome = await self._carry_out(ask, [planned], [], ledger, screenings=screened)
                    if isinstance(outcome, dict):
                        loop.response = outcome
                        if outcome["status"] == "confirm":
                            self._followups[outcome["confirm"]["id"]].loop = loop
                            raise Stop(WAITING)
                        raise Stop("finished")
                    result = outcome[-1]
                else:
                    self._gate.check(Action(ref.key, args), None)
                    if name == "local_time":
                        result = self._tools.local_time()
                    elif name == "weather":
                        result = await self._tool(ask, self._tools.weather(str(args.get("city") or settings.city), ask.language), name)
                    elif name == "web_search":
                        query = args.get("query")
                        if not isinstance(query, str) or not query.strip():
                            return ToolOutcome(False, "bad_arguments")
                        search_model = DEFAULTS["agent_model"] if brain_ref(settings.model) else settings.model
                        result = await self._tool(ask, self._tools.web_search(query, search_model, ledger), name)
                    elif name in ("web_read", "show"):
                        url = args.get("url")
                        if not isinstance(url, str) or not urls.seen(url):
                            return ToolOutcome(False, "url_not_seen")
                        operation = self._tools.show(url) if name == "show" else self._tools.web_read(url)
                        result = await self._tool(ask, operation, name)
                        if name == "show" and result.ok:
                            self._message(ask, "media", json.dumps(result.data, ensure_ascii=False))
                    elif name == "read_attachment":
                        number = args.get("index")
                        if type(number) is not int or not 1 <= number <= len(ask.attachments):
                            return ToolOutcome(False, "bad_arguments")
                        result = await self._tool(ask, self._tools.read_attachment(
                            ask.attachments[number - 1], str(args.get("question") or ask.text), ledger, number=number
                        ), name)
                    elif name in ("memory_read", "history_search"):
                        reply = Reply("", "", None, read=args.get("names", []) if name == "memory_read" else [], search=str(args.get("query", "")) if name == "history_search" else "")
                        found = self._look_up(ask, reply, [t.id for t in context.turns if t.id is not None])
                        result = found[0] if found else ToolResult(name, False, None, "not_found")
                    else:
                        return ToolOutcome(False, "unknown_tool")
                loop.seen.append(result)
                if result.ok and name == CREATE_MEMBER:
                    self._message(ask, "event", phrase(ask.language, "member_created", member=str((result.data or {}).get("created", ""))))
                return self._loop_result(result)

            async def cli_execute(call_id: str, name: str, args: dict) -> ToolOutcome:
                began, before = time.perf_counter(), len(ask.steps)
                result = await execute(call_id, name, args)
                if len(ask.steps) == before:
                    ask.steps.append(self._step("tool", name, BUILTIN_SERVER, "ok" if result.ok else "blocked", began, result.kind))
                return result

            common = dict(model=settings.model, ledger=ledger,
                language=ask.language, plain=not ask.speaker.is_raphael,
                system=system, user=user, tools=snapshot.definitions,
                progress=lambda text: self._message(ask, "progress", text, PREFIXES[ask.language].notice),
                thinking=lambda text: self._message(ask, "thinking", text),
                execute=execute,
            )
            if brain_ref(settings.model):
                common["execute"] = cli_execute
                loop = CliLoop(brains=self._cli_brains, sessions=self._cli_sessions,
                    conversation_id=ask.conversation_id, quota=self.quotas.record,
                    member_id=ask.speaker.id, event=lambda text: self._message(ask, "event", text),
                    expire=lambda: self._expire(self._confirmations.sweep()), spoken=ask.voiced and ask.language != "ja", **common)
                if self._memory is not None:
                    loop.tools.extend(MEMORY_TOOLS)
                self._cli_loops.add(loop)
                loop.on_done = self._cli_loops.discard
            else:
                loop = NarratedLoop(client=self._client, **common)
        try:
            outcome = await loop.run()
        finally:
            if isinstance(loop, CliLoop) and loop.task and loop.task.done():
                self._cli_loops.discard(loop)
        if loop.response is not None:
            return loop.response
        reply = loop.reply(outcome)
        if isinstance(loop, CliLoop) and loop.failed:
            return self._finish(ask, "answer", "negative", reply.text, ledger, status="error")
        return self._finish_reply(ask, "exchange" if ask.nested else "answer", reply, loop.seen, ledger)

    async def _cli_permission(self, ask: _Ask, args: dict, ledger: Ledger, loop: CliLoop) -> ToolOutcome:
        if len(canonical(args)) > MAX_ARGUMENT_CHARS:
            return ToolOutcome(False, "bad_arguments")
        if ask.source not in _AT_THIS_COMPUTER or ask.nested:
            return ToolOutcome(False, "presence_required")
        screening = await self._gate.screen(Proposed("cli_permission", "CLI native action", summarize(args)), language=ask.language, ledger=ledger)
        if screening.verdict == DENY:
            self._gate.record_unrun("cli_permission", outcome="denied")
            return ToolOutcome(False, "blocked")
        operation = args.get("command") or args.get("tool_name") or (args.get("toolCall") or {}).get("title") or args.get("method", "CLI action")
        shown = {"operation": operation, "details": args}
        call = PlannedCall(PluginTool("cli_permission", BUILTIN_SERVER, "cli_permission", "CLI native action", {}, Tier.T2), shown)
        loop.response = self._ask_plugin(ask, call, [], ledger, screening)
        self._followups[loop.response["confirm"]["id"]].loop = loop
        raise Stop(WAITING)

    async def _answer_with_tools(
        self,
        ask: _Ask,
        decision: RouteDecision,
        settings: _Settings,
        ledger: Ledger,
        offer: Offer | None = None,
    ) -> dict:
        """An answer, with what it needs looked up first. With an `offer` (the plugin tools that may
        be used, and the team's), a model picks the tools the request needs while the lookups run.
        All speakers share the enabled lookup tools."""
        uses = ask.speaker.uses
        jobs = []
        if decision.needs_weather and uses("weather"):
            # Each city the question names, else the one in the settings.
            for city in cities_in(ask.text) or [settings.city]:
                jobs.append(self._tool(ask, self._tools.weather(city, ask.language), "weather"))
        if decision.needs_search and uses("web_search"):
            jobs.append(
                self._tool(
                    ask, self._tools.web_search(ask.text, settings.model, ledger), "web_search"
                )
            )
        picking = self._pick_tools(ask, settings, ledger, offer)
        looked_up, inputs, picked = await asyncio.gather(
            asyncio.gather(*jobs), self._inputs(ask, ledger), picking
        )
        results: list[ToolResult] = [*inputs, *looked_up]
        if decision.needs_time and uses("local_time"):
            results.append(self._tools.local_time())
        if offer is not None:
            # The answer model takes no tools (None), or the pick failed: the answer goes on
            # without the services.
            if picked is None or isinstance(picked, AgentFailure):
                picked = []
            outcome = await self._carry_out(ask, picked, results, ledger)
            if isinstance(outcome, dict):
                return outcome  # a question for a yes
            results = outcome
            created = [r for r in results if r.name == CREATE_MEMBER]
            if picked and created and len(created) == len(picked) and all(r.ok for r in created):
                # 《了。已建立「小行程」。》: nothing more to word.
                names = "、".join(str((r.data or {}).get("created", "")) for r in created)
                text = phrase(ask.language, "member_created", member=names)
                return self._finish(ask, "answer", "understood", text, ledger)
        reply, seen = await self._reply(
            ask, "answer", settings, ledger, results, background=background_block(self._briefs(ask.speaker.id))
        )
        return self._finish_reply(ask, "answer", reply, seen, ledger)

    async def _tool(self, ask: _Ask, job: Any, name: str) -> ToolResult:
        """A tool's result, whatever happens: one that breaks is a tool that failed. It is a step
        of the request."""
        began = time.perf_counter()
        try:
            result = await job
        except Exception as exc:
            logger.warning("The %s tool failed unexpectedly: %s", name, type(exc).__name__)
            result = ToolResult(name, False, None, "error")
        outcome = "ok" if result.ok else ("timeout" if result.error == "timeout" else "error")
        ask.steps.append(
            self._step("tool", name, BUILTIN_SERVER, outcome, began, result.error, result.detail)
        )
        return result

    async def _inputs(self, ask: _Ask, ledger: Ledger) -> list[ToolResult]:
        """What the request brings besides its words, read before the reply is worded: the pages
        of the http(s) addresses in the owner's own words (at most MAX_REQUEST_URLS), and the files
        handed over (the images together, in one call of the vision model, and each PDF), each a
        step of the request. Never an address a tool returned. Without the services, no page is
        read (and a file is answered as unreadable)."""
        jobs = []
        if self._tools.services is not None and ask.speaker.uses("web_read"):
            for url in urls_in(ask.text, MAX_REQUEST_URLS):
                jobs.append(self._tool(ask, self._tools.web_read(url), "web_read"))
        files = list(enumerate(ask.attachments, start=1))
        images = [(number, attachment) for number, attachment in files if attachment.is_image]
        if images:
            look = self._tools.look(
                [attachment for _, attachment in images],
                ask.text,
                ledger,
                numbers=[number for number, _ in images],
            )
            jobs.append(self._tool(ask, look, "read_attachment"))
        for number, attachment in files:
            if not attachment.is_image:
                jobs.append(
                    self._tool(
                        ask,
                        self._tools.read_attachment(attachment, ask.text, ledger, number=number),
                        "read_attachment",
                    )
                )
        return list(await asyncio.gather(*jobs)) if jobs else []

    def _step(
        self,
        kind: str,
        tool: str,
        server: str | None,
        outcome: str,
        began: float,
        why: str | None,
        detail: str | None = None,
    ) -> Step:
        """A step that ended now, begun at `began` (`time.perf_counter`), with the reason of
        `why`, the kind of its failure, and the service it used (`detail`)."""
        return Step(
            kind=kind,
            tool=tool,
            server=server,
            outcome=outcome,
            ms=_ms_since(began),
            reason=reason_of(outcome, why),
            at_ms=self._now_ms(),
            detail=detail,
        )

    def _context(self, count: int, member_id: int = 0, conversation_id: int | None = None) -> list[Turn]:
        try:
            return self._history.recent(count, member_id=member_id, conversation_id=self.conversations.resolve(member_id, conversation_id))
        except sqlite3.Error as exc:
            logger.warning("Could not read the conversation so far: %s", type(exc).__name__)
            return []

    # --- the tools of the plugins ---------------------------------------------------------------

    def _offer(self, ask: _Ask | None = None) -> Offer | None:
        """The tools a model may be offered now for `ask`, or None when there are none: the plugin
        tools that have a tier (a tool with no tier is never offered) and that the speaker may use,
        and the team tools the request is about (`team.team_tools_for`)."""
        speaker = RAPHAEL if ask is None else ask.speaker
        tools = []
        if self._plugins is not None:
            try:
                tools = [tool for tool in self._plugins.offered() if speaker.uses_plugin(tool.key)]
            except Exception as exc:  # a plugin that breaks must not break the answer
                logger.warning("Could not list the plugins' tools: %s", type(exc).__name__)
        if ask is not None:
            tools += [team_tool(name) for name in self._team_tools(ask)]
        return make_offer(tools) if tools else None

    def _team_tools(self, ask: _Ask) -> list[str]:
        """The team tools the request `ask` is about. Never for a request one member handed another
        (it asks nobody in turn). A team that cannot be read offers none."""
        try:
            return team_tools_for(self._team, ask.speaker, ask.text)
        except sqlite3.Error as exc:
            logger.warning("Could not read the members: %s", type(exc).__name__)
            return []

    def _about_the_team(self, ask: _Ask) -> bool:
        """Whether the request is about the team: it names a member to ask, or Raphael in a
        member's thread, or asks for a new member."""
        return bool(self._team_tools(ask))

    async def _pick_tools(
        self, ask: _Ask, settings: _Settings, ledger: Ledger, offer: Offer | None
    ) -> list[PlannedCall] | AgentFailure | None:
        """The calls the answer model picks for the request out of the tools in `offer`; the
        failure itself when it cannot (the answer decides what that means); None when the model
        takes no tools, in which case the request goes on without the plugins."""
        if offer is None:
            return []
        model = settings.model
        if self._tool_support.supports(model) is False:
            logger.info("Plugin tools left out of a request: the answer model %s takes no tools", model)
            return None
        try:
            if len(offer.by_alias) > MAX_OFFERED:
                # More tools than fit: the request is shown the ones it needs, not the first few.
                chosen = await self._picker.pick(
                    offer,
                    ask.text,
                    self._context(PLAN_CONTEXT_TURNS, ask.speaker.id, ask.conversation_id),
                    model=model,
                    ledger=ledger,
                )
                offer = offer.only(chosen)
            return await self._planner.plan(
                offer,
                ask.text,
                self._context(PLAN_CONTEXT_TURNS, ask.speaker.id, ask.conversation_id),
                model=model,
                ledger=ledger,
            )
        except ToolsNotSupported:
            self._tool_support.refused(model)
            logger.info("Plugin tools left out of a request: the answer model %s refused tools", model)
            return None
        except AgentFailure as failure:
            return failure
        except Exception as exc:
            logger.warning("Picking tools failed unexpectedly: %s", type(exc).__name__)
            return AgentFailure("error")

    def _tier_now(self, call: PlannedCall) -> Tier | None:
        """The tier the user has given the tool of `call` now; None if it has none any more."""
        return self._gate.tier_now(call.tool.key)

    async def _carry_out(
        self, ask: _Ask, calls: list[PlannedCall], results: list[ToolResult], ledger: Ledger,
        *, screenings: list[Screening] | None = None,
    ) -> list[ToolResult] | dict:
        """Make the picked calls that need no yes, together, and add their results to `results`.
        If a call needs a yes the answer waits for it: that is the question to return instead, for
        the first such call; any others are not made (one question to a turn)."""
        # The owner's safety rules judge every call that changes something, together, before any is
        # made: one that a rule forbids ends the turn with Raphael saying so, and no call is made.
        if screenings is None:
            screenings = await asyncio.gather(*(self._screen_call(ask, c, ledger) for c in calls))
        for call, screening in zip(calls, screenings):
            if screening.verdict == DENY:
                ask.steps.append(
                    self._step("tool", call.tool.name, call.tool.server, "blocked", time.perf_counter(), "forbidden")
                )
                text = forbidden(ask.language, screening)
                return self._finish(ask, "answer", "negative", text, ledger)
        automatic: list[PlannedCall] = []
        asking: list[tuple[PlannedCall, Screening]] = []
        for call, screening in zip(calls, screenings):
            tier = self._tier_now(call)
            if tier is not None and needs_yes(tier >= Tier.T2, screening):
                asking.append((call, screening))
            else:
                automatic.append(call)
        if asking and ask.nested:
            # Nobody waits at the overlay for a request one assistant handed another. None of its
            # calls is made here (making the others first would have the task make them again):
            # the request goes to the background as a task of the one asked, which stops at such a
            # call with a question that waits in its thread, answered in the app alone.
            return self.ask_in_background(ask, ledger)
        made = await asyncio.gather(*(self._make_call(ask, c, None, ledger) for c in automatic))
        results = [*results, *made]
        if not asking:
            return results
        (first, why), *rest = asking
        results += [_unmade(call) for call, _ in rest]
        for call, _ in rest:
            ask.steps.append(
                self._step("tool", call.tool.name, call.tool.server, "blocked", time.perf_counter(), "not_made")
            )
        return self._ask_plugin(ask, first, results, ledger, why)

    async def _screen_call(self, ask: _Ask, call: PlannedCall, ledger: Ledger) -> Screening:
        """What the owner's safety rules say of a call of a plugin's tool or a team tool: PASS for a
        tool its server says only reads. Member contact preferences can require the owner's yes."""
        tool = call.tool
        if tool.key in HARNESS_TOOLS:
            if tool.key in ("routine_list", "show", "computer"):
                return PASS
            return await self._gate.screen(Proposed(tool.key, tool.description, summarize(call.arguments)), language=ask.language, ledger=ledger)
        if tool.key in TEAM_TOOLS:
            # Asking another assistant changes nothing (what the one asked does is judged as it
            # does it); adding a member does, and is judged by its own name.
            if tool.key == ASK_MEMBER and ask.speaker.ask_before_members:
                return Screening(ASK)
            if tool.key != CREATE_MEMBER:
                return PASS
            proposed = Proposed(CREATE_MEMBER, tool.description, summarize(call.arguments))
        elif self._gate.reads_only(tool.server, tool.name):
            return PASS
        else:
            proposed = Proposed(f"{tool.server} / {tool.name}", tool.description, summarize(call.arguments))
        return await self._gate.screen(proposed, language=ask.language, ledger=ledger)

    async def _make_call(
        self,
        ask: _Ask,
        call: PlannedCall,
        approval: Approval | None,
        ledger: Ledger,
        action: Action | None = None,
        *,
        steps: list[Step] | None = None,
    ) -> ToolResult:
        """Make a picked call: a team tool through `team_tool` (T1: it needs no yes of a tier), a
        plugin's tool through the gate with `approval`. A step of `steps` (the request's own when
        none is given), whatever came of it."""
        if call.tool.key == "cli_permission":
            began = time.perf_counter()
            try:
                self._gate.check(action or Action(call.tool.key, call.arguments), approval)
                result = ToolResult(call.tool.key, True, {"approved": True})
            except (NotApproved, UnknownTool):
                result = ToolResult(call.tool.key, False, None, "denied")
            (ask.steps if steps is None else steps).append(self._step("tool", call.tool.key, BUILTIN_SERVER, "ok" if result.ok else "blocked", began, result.error))
            self._gate.record_builtin(call.tool.key, outcome="ok" if result.ok else "blocked", approver="user")
            return result
        if call.tool.key in ("memory_write", "memory_delete"):
            return self._cli_memory_call(ask, call, steps, approval, action)
        if call.tool.key in HARNESS_TOOLS:
            return await self._harness_call(ask, call, approval, action, steps=steps)
        if call.tool.key in TEAM_TOOLS:
            return await self._team_call(ask, call, ledger, approval=approval, action=action, steps=steps)
        return await self._plugin_call(call, approval, action, steps=ask.steps if steps is None else steps)

    def _cli_memory_call(self, ask: _Ask, call: PlannedCall, steps: list[Step] | None, approval: Approval | None, action: Action | None) -> ToolResult:
        from .answers import parse_reply
        args = dict(call.arguments)
        args["op"] = "delete" if call.tool.key == "memory_delete" else "write"
        loop = next((loop for loop in self._cli_loops if loop.member_id == ask.speaker.id and loop.conversation_id == ask.conversation_id), None)
        if not asks_to_remember(ask.text) or loop is None:
            return ToolResult(call.tool.key, False, None, "owner_request_required")
        reply = parse_reply({"text": "Memory update", "memory": [args]}, ask.language, plain=True)
        before = len(ask.steps)
        applied = self._remember(ask, reply, loop.seen, approval=approval, action=action)
        if steps is not None:
            steps.extend(ask.steps[before:])
            del ask.steps[before:]
        if applied is None:
            return ToolResult(call.tool.key, False, None, "blocked")
        if applied.written or applied.deleted:
            self._message(ask, "event", phrase(ask.language, "remembered" if applied.written else "forgotten"))
            self._gate.record_builtin(call.tool.key, outcome="ok", approver="user" if approval is not None else "auto")
        return ToolResult(call.tool.key, not applied.refused, {"written": applied.written, "deleted": applied.deleted}, "blocked" if applied.refused else None)

    async def _harness_call(
        self, ask: _Ask, call: PlannedCall, approval: Approval | None, action: Action | None,
        *, steps: list[Step] | None = None,
    ) -> ToolResult:
        """Native writes use the same screening, approval and metadata-only audit as plugins."""
        name, args = call.tool.key, call.arguments
        began = time.perf_counter()
        try:
            self._gate.check(action or Action(name, args), approval)
            if name in ROUTINE_TOOLS:
                if self._team is None:
                    raise HarnessToolError("unavailable")
                data = await self._team.routine_tool(ask.speaker.id, name, args)
                if name != "routine_list":
                    self._message(ask, "event", routine_event(name, data, ask.language))
            elif name in COMMANDS:
                if ask.source not in _AT_THIS_COMPUTER or ask.nested:
                    raise HarnessToolError("presence_required")
                values = dict(args)
                if name == "open":
                    target = values.get("target")
                    if not isinstance(target, str):
                        raise ValueError("missing target")
                    if values.get("target_kind") == "url":
                        values["target"] = validate_url(target, allow_http=True)
                        if values["target"] is None:
                            raise ValueError("invalid URL")
                    elif values.get("target_kind") == "app":
                        app = await self._catalog.find(target, loose=False) or await self._catalog.lookup(target)
                        if app is None:
                            raise HarnessToolError("not_found")
                        values["target"] = app.app_id
                    elif values.get("target_kind") == "settings":
                        values["target"] = match_settings(target, bare=True)
                        if values["target"] is None:
                            raise ValueError("unknown settings page")
                await run_command(COMMANDS[name], values, self._actions)
                data = {"done": name}
            else:
                raise HarnessToolError("unknown_tool")
            result = ToolResult(name, True, data)
        except HarnessToolError as error:
            result = ToolResult(name, False, None, str(error))
        except (ValueError, RpcError, NotApproved, UnknownTool):
            result = ToolResult(name, False, None, "bad_arguments")
        except Exception as exc:
            logger.warning("A harness tool failed: %s", type(exc).__name__)
            result = ToolResult(name, False, None, "error")
        (ask.steps if steps is None else steps).append(self._step("tool", name, BUILTIN_SERVER, "ok" if result.ok else "error", began, result.error))
        self._gate.record_builtin(name, outcome="ok" if result.ok else "error",
                                  approver="auto" if approval is None else "user")
        return result

    async def _plugin_call(
        self,
        call: PlannedCall,
        approval: Approval | None,
        action: Action | None = None,
        *,
        steps: list[Step],
    ) -> ToolResult:
        """Make a call of a plugin's tool through the policy's one door, and give its result as
        the answer is shown it. `approval` is the yes for it, if it had to be asked about; a call
        that the door refuses is not made, and the hub writes down that it was refused. The call
        is added to `steps`, whatever came of it."""
        action = action or Action(call.tool.key, call.arguments)
        began = time.perf_counter()
        outcome = await self._gate.call_plugin(
            call.tool.key, call.arguments, approval=approval, action=action, pool=self._pool
        )
        steps.append(
            self._step(
                "tool", call.tool.name, call.tool.server, _plugin_outcome(outcome), began, outcome.kind
            )
        )
        return _tool_result(call, outcome)

    def _ask_plugin(
        self,
        ask: _Ask,
        call: PlannedCall,
        results: list[ToolResult],
        ledger: Ledger,
        screening: Screening = PASS,
    ) -> dict:
        """The question for a yes before the call is made: which service, which tool, and the
        start of what it is given, after the rule that has it asked, if one does. The call itself
        waits in `_followups`."""
        language = ask.language
        shown = preview(call.arguments)
        text = asked_with(
            language,
            screening,
            phrase(
                language,
                "confirm_plugin",
                server=call.tool.server,
                tool=call.tool.name,
                what=parenthesized(language, shown),
            ),
        )
        action = Action(call.tool.key, dict(call.arguments))
        reply = self._open_question(ask, action, "answer", text, ledger)
        self._followups[reply["confirm"]["id"]] = _Followup(ask, call, results)
        return reply

    def ask_in_background(self, ask: _Ask, ledger: Ledger) -> dict:
        """A request one assistant handed another (`answer_for`) with a call that needs the owner's
        yes: nobody waits at the overlay for it, so it goes to the background worker as a task of
        the one asked, in its thread (`BackgroundService.submit`, with the depth and the chain it
        was asked at, so that it asks no further than it could here). When the task comes to that
        call it pauses with a question that waits in that thread, answered in the app alone (the
        background's waiting question), and goes on after the owner's answer. The thread holds the
        task as a turn of its own, which the report completes. What the one asked says back to the
        asker is that the question waits there; a worker that cannot take the task is a 否 reply
        with the reason, as a hand-over of the owner's is."""
        language = ask.language
        speaker = ask.speaker
        settings = self._read_settings(speaker)
        try:
            task_id = self._background.submit(
                session_id=ask.session_id,
                text=ask.text,
                language=language,
                model=settings.model,
                member_id=speaker.id,
                asked_by=speaker,
            )
        except Refused as refused:
            return self._finish(ask, "answer", "negative", phrase(language, refused.kind), ledger)
        except Exception as exc:  # a worker that cannot take a task is a 否 reply, not a raise
            logger.warning("A request was not handed to the background: %s", type(exc).__name__)
            return self._failed(ask, "answer", "error", ledger)
        try:
            turn_id = self._history.add(
                session_id=ask.session_id,
                question=ask.original_text if ask.original_text is not None else ask.text,
                route="delegate",
                prefix=_prefix(language, "understood") if speaker.is_raphael else "",
                text=phrase(language, "handoff"),
                status="done",
                source=EXCHANGE_SOURCE,
                member_id=speaker.id, conversation_id=ask.conversation_id,
                # who handed it over: the thread shows it as an exchange with that member
                peer_id=ask.peer,
            )
        except sqlite3.Error as exc:
            logger.warning("Could not write the turn to the history: %s", type(exc).__name__)
        else:
            self._background.attach_turn(task_id, turn_id)
            self._turn_changed(turn_id, speaker.id)
        logger.info("A request between members went to the background: task %d", task_id)
        who = speaker.name or RAPHAEL_NAMES[language]
        return self._finish(
            ask, "answer", "understood", phrase(language, "exchange_waits", member=who), ledger
        )

    # --- the team ---------------------------------------------------------------------------------

    def _conversation_of(self, member_id: int, conversation_id: int | None = None) -> Conversation:
        """The conversation of the thread of `member_id`: Raphael's for 0."""
        if conversation_id is not None and conversation_id != self.conversations.main(member_id):
            if conversation_id not in self._named_conversations:
                self._named_conversations[conversation_id] = Conversation(
                    self._conn, self._client, now_ms=self._now_ms, clock=self._clock,
                    reflected_through=lambda: 2**62, member_id=member_id, conversation_id=conversation_id,
                )
            return self._named_conversations[conversation_id]
        if member_id == 0:
            return self._conversation
        if self._memory is not None:
            return self._memory.conversation_for(member_id)
        conversation = self._conversations.get(member_id)
        if conversation is None:
            conversation = Conversation(
                self._conn,
                self._client,
                now_ms=self._now_ms,
                clock=self._clock,
                reflected_through=lambda: 2**62,
                member_id=member_id,
            )
            self._conversations[member_id] = conversation
        return conversation

    async def _team_call(
        self, ask: _Ask, call: PlannedCall, ledger: Ledger, *, approval: Approval | None = None, action: Action | None = None, steps: list[Step] | None = None
    ) -> ToolResult:
        """A team tool the picking call asked for, made for `ask` (see `team_tool`); a step of
        `steps` (the request's own when none is given), whatever came of it."""
        began = time.perf_counter()
        result = await self.team_tool(
            ask.speaker,
            call.tool.key,
            call.arguments,
            language=ask.language,
            ledger=ledger,
            session_id=ask.session_id, conversation_id=ask.conversation_id, approval=approval, action=action,
        )
        outcome = "ok" if result.ok else ("blocked" if result.error == "blocked" else "error")
        if result.error == "timeout":
            outcome = "timeout"
        (ask.steps if steps is None else steps).append(
            self._step("tool", call.tool.key, TEAM_SERVER, outcome, began, result.error)
        )
        return result

    async def team_tool(
        self,
        speaker: Speaker,
        name: str,
        arguments: Mapping[str, Any],
        *,
        language: str,
        ledger: Ledger,
        session_id: str,
        conversation_id: int | None = None,
        approval: Approval | None = None,
        action: Action | None = None,
    ) -> ToolResult:
        """Make the team tool `name` (`team.ASK_MEMBER`, `team.CREATE_MEMBER`) for `speaker`, through
        the gate, and give what came of it as a tool's result. The fast tier calls it for a call
        the picking model asked for, and the background worker for its model's call. Never raises
        for a failure of the world: "blocked" when the speaker may not, "bad_arguments" for
        arguments it cannot take, "not_found" for nobody of that name."""
        team = self._team
        if team is None or name not in TEAM_TOOLS or (name != SKILL_READ and not speaker.may_ask):
            return ToolResult(name, False, None, "blocked")
        if name == CREATE_MEMBER and speaker.depth:
            return ToolResult(name, False, None, "blocked")
        try:
            self._gate.check(action or Action(name, dict(arguments)), approval)
        except (UnknownTool, NotApproved):
            return ToolResult(name, False, None, "blocked")
        if name == SKILL_READ:
            read = getattr(team, "skill_read", None)
            text = None if read is None else read(speaker.id, arguments.get("name"))
            return ToolResult(name, text is not None, {"text": text} if text is not None else None,
                              None if text is not None else "not_found", untrusted=True)
        if name == CREATE_MEMBER:
            try:
                created = team.create(
                    name=arguments.get("name"),
                    role=arguments.get("role", ""),
                    instructions=arguments.get("instructions", ""),
                    group=arguments.get("group"),
                    personality=arguments.get("personality", ""),
                )
            except ValueError as error:  # the store's MemberError: which field, never its text
                return ToolResult(name, False, {"field": getattr(error, "kind", "")}, "bad_arguments")
            except sqlite3.Error as exc:
                logger.warning("Could not add a member: %s", type(exc).__name__)
                return ToolResult(name, False, None, "error")
            logger.info("A member was added by the assistant")
            return ToolResult(name, True, {"created": created.name})
        wanted = arguments.get("member")
        request = arguments.get("request")
        if not isinstance(wanted, str) or not isinstance(request, str) or not request.strip():
            return ToolResult(name, False, None, "bad_arguments")
        try:
            target = team.find(wanted)
        except sqlite3.Error as exc:
            logger.warning("Could not read the members: %s", type(exc).__name__)
            return ToolResult(name, False, None, "error")
        if target is None:
            return ToolResult(name, False, None, "not_found")
        if not speaker.can_ask(target):  # itself, someone already in the chain, or too deep
            return ToolResult(name, False, None, "blocked")
        try:
            ok, said = await asyncio.wait_for(
                self.answer_for(
                    target,
                    request.strip()[:REQUEST_MAX_CHARS],
                    language,
                    asker=speaker,
                    ledger=ledger,
                    session_id=session_id,
                    asker_conversation_id=conversation_id,
                ),
                EXCHANGE_TIMEOUT_S,
            )
        except TimeoutError:
            return ToolResult(name, False, None, "timeout")
        member = target.name or RAPHAEL_NAMES[language]
        data = {"member": member, "answer": said[:ANSWER_MAX_CHARS]}
        return ToolResult(name, ok, data, None if ok else "error", untrusted=True)

    async def answer_for(
        self,
        target: Speaker,
        request: str,
        language: str,
        *,
        asker: Speaker,
        ledger: Ledger,
        session_id: str,
        asker_conversation_id: int | None = None,
    ) -> tuple[bool, str]:
        """`target` answers `request`, which `asker` handed it (`ask_member`): whether it could,
        and what it said. It answers through the same path as a question of the owner's (the
        narrated harness, enabled tools, its own conversation, its own memory and the shared
        one, its persona), as an answer, one step deeper in the chain (it may ask one more only if
        the chain allows: `Speaker.can_ask`). It never acts on the desktop: a request that would is
        answered that the owner must ask for it directly. It writes no note (the request is not
        the owner's), asks no yes at the overlay (a call that needs one sends the request to the
        background, `ask_in_background`), and its costs go to
        `ledger`, the asker's, so they count toward the asker's limits and the month's. The
        exchange is written in the two threads, one line each, and nowhere else."""
        requester = asker.name or "Raphael"
        respondent = target.name or "Raphael"
        sources = owner_lines(self._conversation_of(target.id).context(quick=False).turns)
        ask = _Ask(
            session_id=session_id,
            text=request,
            language=language,
            source=EXCHANGE_SOURCE,
            speaker=target.asked_by(asker),
            conversation_id=self.conversations.main(target.id),
            peer=asker.id,
            exchange_speakers=(requester, respondent),
            exchange_owner_sources=sources,
            cost_base=ledger.total,
            call_base=len(ledger.calls),
        )
        settings = self._read_settings(ask.speaker)
        failure = self._blocked(settings)
        result: dict | None = None
        if failure is None:
            try:
                self._record(ask, "exchange", "", "", "running", None, ledger)
                result = await self._narrated(ask, settings, ledger)
            except AgentFailure as error:
                failure = error.kind
            except Exception as exc:
                logger.warning("A member's answer failed unexpectedly: %s", type(exc).__name__)
                failure = "error"
        ok = failure is None and result is not None and result.get("status") == "done"
        if not ok:
            result = self._failed(ask, "exchange", failure or "error", ledger)
        assert result is not None
        # The saved exchange contains all final replies, with provenance added by _record.
        said = ask.exchange_answer or relayed_answer(respondent, result.get("text", ""), sources)
        mirror = _Ask(
            session_id=session_id,
            text=request,
            language=language,
            source=EXCHANGE_SOURCE,
            speaker=asker,
            conversation_id=self.conversations.resolve(asker.id, asker_conversation_id),
            peer=target.id,
            cost_base=ledger.total,
            call_base=len(ledger.calls),
        )
        mirror.text = labelled_lines(requester, request)
        self._record(mirror, "exchange", "", said, "done" if ok else "error", None, ledger)
        logger.info("An exchange between members: ok=%s", ok)
        return ok, said

    def _drop_followup(self, pending: Pending, outcome: str) -> None:
        """The question of a plugin's call ended without a yes ("denied" or "expired"): its call is
        forgotten, and the audit says it was asked about and not made."""
        followup = self._followups.pop(pending.id, None)
        if followup is not None:
            self._gate.record_unrun(pending.action.tool, outcome=outcome)
            if isinstance(followup.loop, CliLoop):
                followup.loop.cancel()

    async def _confirm_plugin(
        self, pending: Pending, approval: Approval | None, followup: _Followup
    ) -> dict:
        """The yes came: make the call, then word what came of it."""
        language = pending.language
        if followup.memory_reply is not None:
            route, reply, ledger = followup.memory_reply
            applied = self._remember(followup.ask, replace(reply, memory=reply.memory[:1]), followup.results, approval=approval, action=pending.action)
            if applied is None or not (applied.written or applied.deleted):
                return self._outcome(pending, "negative", phrase(language, "not_remembered"), "error")
            self._memory.note_turn(applied.written, pending.turn_id, followup.ask.speaker.id)
            remaining = replace(reply, memory=reply.memory[1:])
            if not remaining.memory and not remaining.text:
                remaining = replace(remaining, prefix="understood", text=phrase(language, "remembered" if applied.written else "forgotten"))
            return self._finish_reply(followup.ask, route, remaining, followup.results, ledger)
        ledger = followup.loop.ledger if followup.loop else Ledger(self._spend)
        call = followup.call
        # The yes is for the tier the question was asked at. A tool that has none now, or a higher
        # one, is not covered by it.
        now = self._tier_now(call)
        if now is None or now > pending.tier:
            approval = None
        steps: list[Step] = []
        if isinstance(followup.loop, CliLoop) and approval is None:
            self._gate.record_unrun(call.tool.key, outcome="denied")
            result = ToolResult(call.tool.key, False, None, "denied")
            steps.append(self._step("tool", call.tool.name, call.tool.server, "blocked", time.perf_counter(), "denied"))
        else:
            result = await self._make_call(
                followup.ask, call, approval, ledger, pending.action, steps=steps
            )
        self._write(lambda: self._history.add_steps(pending.turn_id, steps))
        if followup.loop is not None:
            loop = followup.loop
            loop.seen.append(result)
            if result.ok and call.tool.key == CREATE_MEMBER:
                self._message(followup.ask, "event", phrase(
                    language, "member_created", member=str((result.data or {}).get("created", ""))
                ))
            loop.continue_with(self._loop_result(result))
            # The confirmed step was written above; future snapshots must not duplicate it.
            followup.ask.steps.extend(steps)
            followup.ask.recorded_steps = len(followup.ask.steps)
            self._record(followup.ask, "answer", "", "", "running", None, ledger)
            try:
                return await self._narrated(
                    followup.ask, self._read_settings(followup.ask.speaker), ledger, loop
                )
            except AgentFailure as error:
                return self._failed(followup.ask, "answer", error.kind, ledger)
        if call.tool.key == CREATE_MEMBER and result.ok:
            # 《了。已建立「小行程」。》, after the yes a rule asked for: nothing more to word.
            created = str((result.data or {}).get("created", ""))
            text = phrase(language, "member_created", member=created)
            return self._outcome(pending, "understood", text, "done", cost_usd=ledger.total)
        if not result.ok and result.error == "timeout":
            text = phrase(language, "plugin_unsure", server=call.tool.server)
            return self._outcome(pending, "negative", text, "error")
        if not result.ok and result.error != "tool_error":
            return self._outcome(pending, "negative", phrase(language, "failed"), "error")
        settings = self._read_settings(followup.ask.speaker)
        try:
            if self._blocked(settings) is not None:
                raise AgentFailure("error")
            reply, _ = await self._reply(
                followup.ask,
                "answer",
                settings,
                ledger,
                [*followup.results, result],
                background=background_block(self._briefs(followup.ask.speaker.id)),
            )
            if not reply.text:
                raise AgentFailure("error")
        except AgentFailure:
            # It was done, and Raphael cannot say more than that.
            return self._outcome(pending, "understood", "", "done", cost_usd=ledger.total)
        self._keep_japanese(pending.turn_id, reply.say_ja)
        return self._outcome(
            pending,
            reply.prefix,
            reply.text,
            "done",
            detail=reply.detail,
            cost_usd=ledger.total,
            model=ledger.model,
        )

    # --- commands -------------------------------------------------------------------------------

    async def _command(
        self, ask: _Ask, decision: RouteDecision, settings: _Settings, ledger: Ledger
    ) -> dict:
        command = COMMANDS.get(decision.command)
        language = ask.language
        if command is None:  # none of the fixed commands covers it
            return self._finish(ask, "command", "negative", phrase(language, "cannot"), ledger)
        args: dict[str, Any] = {}
        question = ""
        opening: Opening | None = None
        if command.kind == "open":
            opening = await self._find_opening(ask, decision, settings, ledger)
            if opening is None:
                logger.info("Open: step=none")
                return self._finish(
                    ask, "command", "negative", phrase(language, "open_not_found"), ledger
                )
            logger.info("Open: step=%s kind=%s", opening.step, opening.kind)
            args = {"target_kind": opening.kind, "target": opening.target}
        elif command.kind == "copy":
            copy = self._what_to_copy(ask.text, ask.speaker.id, ask.conversation_id)
            if copy is None:
                return self._finish(
                    ask, "command", "negative", phrase(language, "nothing_to_copy"), ledger
                )
            args, question = {"text": copy[0]}, copy[1]
        action = Action(command.name, args)
        # Not judged by the owner's safety rules (`safety`): a command is T1, local and easily
        # undone, and must stay instant and work with no key.
        if self._gate.needs_confirmation(command.name):
            return self._ask_to_confirm(ask, action, question, ledger)
        began = time.perf_counter()
        try:
            self._gate.check(action, None)
            await run_command(command, args, self._actions)
        except Exception as exc:
            logger.warning("The command %s failed: %s", command.name, type(exc).__name__)
            ask.steps.append(self._step("command", command.name, None, "error", began, None))
            return self._finish(
                ask, "command", "negative", phrase(language, "failed"), ledger, status="error"
            )
        ask.steps.append(self._step("command", command.name, None, "ok", began, None))
        if opening is not None and opening.learn_as:
            self._learn(opening.learn_as, opening.target)  # it opened: now it is known
        done = _DONE_PHRASE.get(command.name)
        return self._finish(
            ask, "command", "understood", phrase(language, done) if done else "", ledger
        )

    async def _find_opening(
        self, ask: _Ask, decision: RouteDecision, settings: _Settings, ledger: Ledger
    ) -> Opening | None:
        """What the request asks to open, or None when nothing is found. AgentFailure if the
        model was needed and failed, and nothing else found it.

        In order: a domain said outright; a settings page named with the word for settings; an
        app of the Start menu whose whole name is in the request; a name that opened before; a
        settings page named alone (after the apps: the camera is the app); one call to the model,
        which sees the request and the names of the apps; and, last, an app that the request
        only half names. Only the fifth step costs anything.
        """
        text = ask.text
        heard = decision.target or extract_target(text)
        target = clean_target(heard) if heard else None
        if target:
            url = parse_domain(target)
            if url is not None:
                return Opening("url", url, "domain")
            page = match_settings(target, bare=False)
            if page is not None:
                return Opening("settings", page, "settings")
        subject = target or text
        app = await self._catalog.find(subject, loose=False)
        if app is not None:
            return Opening("app", app.app_id, "app")
        if target:
            known = self._known(target)
            if known is not None:
                return Opening("url", known, "learned", target)
            page = match_settings(target, bare=True)
            if page is not None:
                return Opening("settings", page, "settings")
        failure: AgentFailure | None = None
        if decision.source != "local":
            try:
                answer = await self._decider.resolve_open(
                    text, await self._catalog.names(), settings.model, ledger
                )
            except AgentFailure as error:
                answer, failure = None, error
            found = None if answer is None else await self._from_answer(answer, target, text)
            if found is not None:
                return found
        app = await self._catalog.find(subject)
        if app is None and target:
            app = await self._catalog.lookup(target)
        if app is not None:
            return Opening("app", app.app_id, "app")
        if failure is not None:
            raise failure
        return None

    async def _from_answer(
        self, answer: OpenAnswer, target: str | None, text: str
    ) -> Opening | None:
        """The model's answer as something to open, if it may be opened: an app only if it is in
        the Start menu; an address only if it passes `validate_url` (`http://` made `https://`,
        unless the speaker said it)."""
        if answer.kind == "app":
            app = await self._catalog.lookup(answer.name)
            return None if app is None else Opening("app", app.app_id, "model")
        said_http = "http://" in unicodedata.normalize("NFKC", text).casefold()
        url = answer.url.strip()
        if not said_http and url[:7].lower() == "http://":
            url = "https://" + url[7:]
        checked = validate_url(url, allow_http=said_http)
        if checked is None:
            return None
        # A search is not learned: what was searched for is what was said.
        name = (target or clean_target(answer.name)) if answer.kind == "site" else None
        return Opening("url", checked, "model", name or None)

    def _known(self, name_key: str) -> str | None:
        try:
            return self._targets.get(name_key)
        except sqlite3.Error as exc:
            logger.warning("Could not read the names that opened: %s", type(exc).__name__)
            return None

    def _learn(self, name_key: str, url: str) -> None:
        try:
            self._targets.learn(name_key, url)
        except sqlite3.Error as exc:
            logger.warning("Could not keep a name that opened: %s", type(exc).__name__)

    def _what_to_copy(self, text: str, member_id: int = 0, conversation_id: int | None = None) -> tuple[str, str] | None:
        """`(what to copy, the phrase that asks about it)`, or None when there is nothing to copy:
        a text the speaker quoted, else Raphael's last answer, with its detail (`_copy_text`)."""
        for quote in _QUOTES:
            found = quote.search(text)
            if found and found.group(1).strip():
                return found.group(1).strip(), "copy_quote"
        last = next(
            (
                turn
                for turn in reversed(self._context(20, member_id, conversation_id))
                if turn.route in _ANSWER_ROUTES and (turn.detail or turn.text)
            ),
            None,
        )
        if last is None:
            return None
        return _copy_text(last), "copy_last"

    # --- confirmations --------------------------------------------------------------------------

    def _ask_to_confirm(self, ask: _Ask, action: Action, question: str, ledger: Ledger) -> dict:
        text = phrase(ask.language, question or "confirm_action")
        return self._open_question(ask, action, "command", text, ledger)

    def _open_question(
        self, ask: _Ask, action: Action, route: str, text: str, ledger: Ledger
    ) -> dict:
        """Record the turn as waiting for a yes or a no and open the question for `action`, at the
        tier it has; the reply is the question as the overlay is given it."""
        language = ask.language
        tier = max(Tier.T2, self._gate.tier(action.tool))
        prefix = PREFIXES[language].question
        turn_id = self._record(ask, route, prefix, text, "confirm", None, ledger)
        pending = self._confirmations.open(ask.session_id, turn_id, action, tier, language)
        return {
            "status": "confirm",
            "turn_id": turn_id,
            "confirm": {
                "id": pending.id,
                "tier": tier.name,
                "prefix": prefix,
                "text": text,
                "yes": phrase(language, "yes"),
                "no": phrase(language, "no"),
                "timeout_ms": CONFIRM_TIMEOUT_MS,
            },
        }

    async def _confirm(self, params: dict) -> dict:
        """`agent.confirm`: the answer to a question, said aloud when it is an answer and the
        question was Raphael's (a member's question ends in its thread, in writing)."""
        reply = await self._resolve(params)
        return await self._said(
            reply, member_id=self._member_of_turn(reply.get("turn_id")), language=self._last_language
        )

    def _member_of_turn(self, turn_id: object) -> int | None:
        """Whose thread the turn is in (0 is Raphael), or None if that cannot be told."""
        if type(turn_id) is not int:
            return None
        if turn_id <= 0:
            return 0  # a turn that could not be written is Raphael's (no member runs on the desktop)
        try:
            turn = self._history.get(turn_id)
        except sqlite3.Error:
            return None
        return None if turn is None else (turn.member_id or 0)

    async def _resolve(self, params: dict) -> dict:
        confirm_id = _str(params, "id")
        approved = params.get("approved")
        if type(approved) is not bool:
            raise invalid_params("approved must be true or false")
        always = params.get("always", False)
        if type(always) is not bool:
            raise invalid_params("always must be true or false")
        run = None if self._computer is None else self._computer.for_question(confirm_id)
        if run is not None:
            # A question of a run on the desktop: the run goes on, and says what comes next.
            run.answer(confirm_id, approved, always)
            if getattr(run, "narration", None) is not None:
                return await self._resume_computer(run)
            return await self._next_from_run(run)
        self._expire(self._confirmations.sweep())
        resolution = self._confirmations.resolve(confirm_id, approved)
        await asyncio.gather(*(loop.aclose() for loop in list(self._cli_loops) if loop.task and loop.task.cancelling()))
        pending = resolution.pending
        if resolution.outcome is Outcome.UNKNOWN or pending is None:
            return self._done(
                0, self._last_language, "negative", phrase(self._last_language, "expired"), silent=True
            )
        language = pending.language
        if resolution.outcome is Outcome.EXPIRED:
            self._drop_followup(pending, "expired")
            return self._outcome(pending, "negative", phrase(language, "expired"), "expired")
        if resolution.outcome is Outcome.DENIED:
            followup = self._followups.get(pending.id)
            if followup is not None and isinstance(followup.loop, CliLoop):
                self._followups.pop(pending.id)
                followup.loop.permission_always = False
                return await self._resume_narrated(pending, None, followup)
            self._drop_followup(pending, "denied")
            return self._outcome(pending, "understood", phrase(language, "denied"), "denied")
        followup = self._followups.pop(pending.id, None)
        if followup is not None:
            if isinstance(followup.loop, CliLoop):
                followup.loop.permission_always = bool(approved and always)
            if followup.loop is not None:
                return await self._resume_narrated(pending, resolution.approval, followup)
            return await self._confirm_plugin(pending, resolution.approval, followup)
        command = COMMANDS.get(pending.action.tool)
        if command is None:
            return self._outcome(pending, "negative", phrase(language, "failed"), "error")
        began = time.perf_counter()
        try:
            self._gate.check(pending.action, resolution.approval)
            await run_command(command, pending.action.args, self._actions)
        except Exception as exc:  # NotApproved among them: it is a failure all the same
            logger.warning("The confirmed command %s failed: %s", command.name, type(exc).__name__)
            step = self._step("command", command.name, None, "error", began, None)
            self._write(lambda: self._history.add_steps(pending.turn_id, [step]))
            return self._outcome(pending, "negative", phrase(language, "failed"), "error")
        step = self._step("command", command.name, None, "ok", began, None)
        self._write(lambda: self._history.add_steps(pending.turn_id, [step]))
        done = _DONE_PHRASE.get(command.name)
        return self._outcome(pending, "understood", phrase(language, done) if done else "", "done")

    async def _resume_computer(self, run: ComputerRun) -> dict:
        """Keep desktop confirmation and the enclosing harness in one cancellable turn."""
        ask: _Ask = run.context
        loop = run.narration

        async def resume():
            result = await self._next_from_run(run)
            if result["status"] == "confirm":
                return result
            loop.continue_with(self._computer_result(loop, result))
            self._record(ask, "answer", "", "", "running", None, run.ledger)
            try:
                return await self._narrated(ask, self._read_settings(ask.speaker), run.ledger, loop)
            except AgentFailure as error:
                return self._failed(ask, "answer", error.kind, run.ledger)
            except Exception as error:
                logger.warning("The desktop harness failed: %s", type(error).__name__)
                return self._failed(ask, "answer", "error", run.ledger)

        task = asyncio.ensure_future(resume())
        self._inflight[ask.session_id] = task
        self._inflight_members[ask.session_id] = ask.speaker.id
        try:
            return await task
        except asyncio.CancelledError:
            if not task.cancelled() or asyncio.current_task().cancelling():
                raise
            return _Silent({"status": "done", "turn_id": ask.turn_id, "prefix": "", "text": ""})
        finally:
            if task.cancelled():
                self._finish(ask, "answer", "understood", phrase(ask.language, "denied"), run.ledger, status="denied")
            if self._inflight.get(ask.session_id) is task:
                del self._inflight[ask.session_id]
                self._inflight_members.pop(ask.session_id, None)

    async def _resume_narrated(
        self, pending: Pending, approval: Approval | None, followup: _Followup
    ) -> dict:
        """Keep the approved call and all later rounds cancellable as one live request."""
        task = asyncio.ensure_future(self._confirm_plugin(pending, approval, followup))
        session_id = followup.ask.session_id
        self._inflight[session_id] = task
        self._inflight_members[session_id] = followup.ask.speaker.id
        try:
            return await task
        except asyncio.CancelledError:
            if not task.cancelled() or asyncio.current_task().cancelling():
                raise
            return _Silent({"status": "done", "turn_id": pending.turn_id, "prefix": "", "text": ""})
        finally:
            if task.cancelled():
                self._finish(followup.ask, "answer", "understood", phrase(pending.language, "denied"),
                             followup.loop.ledger, status="denied")
            if self._inflight.get(session_id) is task:
                del self._inflight[session_id]
                self._inflight_members.pop(session_id, None)

    def _outcome(
        self,
        pending: Pending,
        which: str,
        text: str,
        status: str,
        *,
        detail: str | None = None,
        cost_usd: float = 0.0,
        model: str | None = None,
    ) -> dict:
        """End the turn of a question: what Raphael says now, and how it ended."""
        turn = self._history.get(pending.turn_id)
        prefix = _prefix(pending.language, which) if turn is None or turn.member_id == 0 else ""
        self._write(
            lambda: self._history.resolve(
                pending.turn_id,
                prefix=prefix,
                text=text,
                status=status,
                detail=detail,
                cost_usd=cost_usd,
                model=model,
            )
        )
        if turn is not None and (turn.source != VOICE or turn.member_id != 0) and text:
            self._write(lambda: self._history.add_message(
                pending.turn_id, "reply", compose(prefix, text, None)
            ))
        # The owner's own "no", and a question nobody answered, are not said back.
        silent = status in _UNSPOKEN
        # An answer that will be said is announced to the app when the voice is ready (`_said`).
        self._turn_changed(
            pending.turn_id,
            hold=not silent and self._voiced(None, self._member_of_turn(pending.turn_id)),
        )
        result = {"status": "done", "turn_id": pending.turn_id, "prefix": prefix, "text": text}
        return _Silent(result) if silent else result

    def _deny(self, pendings: list[Pending]) -> None:
        for pending in pendings:
            self._drop_followup(pending, "denied")
            self._outcome(pending, "understood", phrase(pending.language, "denied"), "denied")

    def _expire(self, pendings: list[Pending]) -> None:
        for pending in pendings:
            self._drop_followup(pending, "expired")
            self._outcome(pending, "negative", phrase(pending.language, "expired"), "expired")

    # --- agent.cancel ---------------------------------------------------------------------------

    async def _cancel(self, params: dict) -> dict:
        session_id = _str(params, "session_id")
        self._stop_speaking()
        self._forget_old_cancels()
        self._cancelled[session_id] = self._clock()
        task = self._inflight.get(session_id)
        if task is not None:
            task.cancel()
            if self._cli_loops:
                await asyncio.gather(task, return_exceptions=True)
        if self._computer is not None:
            self._computer.stop_session(session_id)
        self._expire(self._confirmations.sweep())
        self._deny(self._confirmations.cancel_session(session_id))
        await asyncio.gather(*(loop.aclose() for loop in list(self._cli_loops) if loop.task and loop.task.cancelling()))
        return {}

    def stop_session(self, session_id: str) -> bool:
        """End the request of a session that still thinks and deny its question, like Esc but
        without the memory that would cancel its next request. Whether anything was stopped."""
        task = self._inflight.get(session_id)
        stopped = task is not None and not task.done()
        if stopped:
            task.cancel()
        self._expire(self._confirmations.sweep())
        denied = self._confirmations.cancel_session(session_id)
        self._deny(denied)
        return stopped or bool(denied)

    def _forget_old_cancels(self) -> None:
        now = self._clock()
        for session_id, when in list(self._cancelled.items()):
            if not 0 <= now - when < CANCELLED_MEMORY_S:
                del self._cancelled[session_id]

    def _was_cancelled(self, session_id: str) -> bool:
        self._forget_old_cancels()
        return session_id in self._cancelled

    # --- the history ----------------------------------------------------------------------------

    async def _quota_list(self, params: dict) -> dict:
        return {"quotas": self.quotas.list()}

    async def _speech_status(self, params: dict) -> dict:
        return {"ready": bool(self._speaking and self._speaking.speaks())}

    async def _speak_turn(self, params: dict) -> dict:
        member = self._member_param(params)
        conversation_id = self.conversations.resolve(member, _opt_int(params, "conversation_id"))
        turn = self._history.get(_opt_int(params, "turn_id"))
        if turn is None or turn.member_id != member or turn.conversation_id != conversation_id:
            raise invalid_params("there is no such turn in this conversation")
        if not self._speaking or not self._speaking.speaks():
            return {"ok": False, "kind": "not_configured"}
        seq = _opt_int(params, "message_seq")
        text = turn.text
        if seq is not None:
            message = next((m for m in self._history.messages([turn.id]).get(turn.id, []) if m["seq"] == seq and m["kind"] == "reply"), None)
            if message is None:
                raise invalid_params("message_seq must name an answer")
            text = message["text"]
        await self._speaking.say("", text, turn_id=turn.id)
        return {"ok": True}

    async def _messages_search(self, params: dict) -> dict:
        query = _str(params, "query").strip()
        if not query or len(query) > HISTORY_QUERY_MAX_CHARS:
            raise invalid_params("query must be 1 to 200 characters")
        turns = self._history.search(query, limit=50, most=50, member_id=None)
        views = self._turn_views(turns)
        for view in views:
            conversation = self.conversations.get(view["conversation_id"])
            view["conversation_title"] = conversation["title"]
        return {"turns": views}

    def _conversations_changed(self, member_id: int) -> None:
        if self._notify:
            self._notify("conversations.changed", {"member_id": member_id})

    async def _conversations_list(self, params: dict) -> dict:
        member = self._member_param(params)
        query = params.get("query", "")
        if not isinstance(query, str) or len(query) > 200:
            raise invalid_params("query must be at most 200 characters")
        return {"conversations": self.conversations.list(member, query.strip())}

    async def _conversations_create(self, params: dict) -> dict:
        member = self._member_param(params)
        self.conversations.main(member)
        conversation = self.conversations.create(member)
        self._conversations_changed(member)
        return {"conversation": conversation}

    async def _conversations_rename(self, params: dict) -> dict:
        conversation = self.conversations.rename(_opt_int(params, "id"), params.get("title"))
        self._conversations_changed(conversation["member_id"])
        return {"conversation": conversation}

    async def _delete_turn(self, turn: Turn) -> bool:
        if turn.status in ("running", "confirm"):
            inflight = self._inflight.get(turn.session_id)
            await self._cancel({"session_id": turn.session_id})
            if inflight:
                await asyncio.gather(inflight, return_exceptions=True)
        await self._background.cancel_session(turn.session_id)
        self._conversation_of(turn.member_id, turn.conversation_id).clear_summary()
        with self._conn:
            self._conn.execute("DELETE FROM agent_conversation_cli_sessions WHERE conversation_id = ?", (turn.conversation_id,))
        deleted = self._history.delete(turn.id)
        if deleted:
            self._turn_changed(turn.id, turn.member_id)
        return deleted

    async def _history_delete(self, params: dict) -> dict:
        member = self._member_param(params)
        conversation_id = self.conversations.resolve(member, _opt_int(params, "conversation_id"))
        turn = self._history.get(_opt_int(params, "turn_id"))
        if turn is None or turn.member_id != member or turn.conversation_id != conversation_id:
            raise invalid_params("there is no such turn in this conversation")
        return {"deleted": int(await self._delete_turn(turn))}

    async def _conversations_delete(self, params: dict) -> dict:
        conversation = self.conversations.get(_opt_int(params, "id"))
        member = conversation["member_id"]
        conversation_id = conversation["id"]
        # Stop writers before removing their rows, including background reports and pending yeses.
        while turns := self._history.list(limit=200, member_id=member, conversation_id=conversation_id):
            for turn in turns:
                await self._delete_turn(turn)
        cached = self._named_conversations.pop(conversation_id, None)
        if cached:
            cached.clear_summary()
            await cached.aclose()
        with self._conn:
            self._conn.execute("DELETE FROM conversation_context_summaries WHERE conversation_id = ?", (conversation_id,))
            self._conn.execute("DELETE FROM agent_conversation_cli_sessions WHERE conversation_id = ?", (conversation_id,))
            self._conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        self.conversations.main(member)
        self._conversations_changed(member)
        return {"deleted": 1}

    async def _history_retract(self, params: dict) -> dict:
        """Stop one owner's request and permanently remove its turn, including partial lines."""
        member = self._member_param(params)
        session = _str(params, "session_id")
        turn_id = _opt_int(params, "turn_id")
        turns = self._history.for_session(session)
        own = [turn for turn in turns if turn.member_id == member and turn.source not in ("member", "routine")]
        if (turn_id is not None and not any(turn.id == turn_id for turn in own)) or (turns and not own):
            raise invalid_params("the message does not belong to this session and thread")
        inflight = self._inflight.get(session)
        await self._cancel({"session_id": session})
        if inflight is not None:
            await asyncio.gather(inflight, return_exceptions=True)
        await self._background.cancel_session(session)
        # Read again after stopping: a request may have written its first row before cancellation.
        own = [turn for turn in self._history.for_session(session)
               if turn.member_id == member and turn.source not in ("member", "routine")]
        deleted = sum(self._history.delete(turn.id) for turn in own if turn.id is not None)
        if deleted:
            for conversation_id in {turn.conversation_id for turn in own}:
                self._conversation_of(member, conversation_id).clear_summary()
                with self._conn:
                    self._conn.execute("DELETE FROM agent_conversation_cli_sessions WHERE conversation_id = ?", (conversation_id,))
            for turn in own:
                self._turn_changed(turn.id or 0, member)
        return {"deleted": deleted}

    def _member_param(self, params: dict) -> int:
        """The thread a method of the history is about: `member_id`, 0 (Raphael's) by default. One
        that is nobody's is refused."""
        member_id = _opt_int(params, "member_id")
        if member_id is None or member_id == 0:
            return 0
        if member_id < 0 or self._speaker(member_id) is None:
            raise invalid_params("there is no such member")
        return member_id

    async def _history_list(self, params: dict) -> dict:
        """The thread of `member_id` (Raphael's by default), newest first."""
        member_id = self._member_param(params)
        limit = _opt_int(params, "limit")
        before_id = _opt_int(params, "before_id")
        around_id = _opt_int(params, "around_id")
        # A limit is clamped into 1..200: SQLite reads a negative LIMIT as "no limit".
        limit = HISTORY_DEFAULT_LIMIT if limit is None else min(max(limit, 1), HISTORY_MAX_LIMIT)
        self._expire(self._confirmations.sweep())
        turns = self._history.list(limit=limit, before_id=before_id, member_id=member_id,
                                   conversation_id=self.conversations.resolve(member_id, _opt_int(params, "conversation_id")))
        if around_id is not None:
            conversation_id = self.conversations.resolve(member_id, _opt_int(params, "conversation_id"))
            anchor = self._history.get(around_id)
            if anchor is None or anchor.member_id != member_id or anchor.conversation_id != conversation_id:
                raise invalid_params("around_id must name a turn in this conversation")
            before = self._history.list(limit=limit, before_id=around_id + 1, member_id=member_id, conversation_id=conversation_id)
            after = self._history.after(around_id, member_id=member_id, conversation_id=conversation_id)
            turns = sorted([*before, *after[:limit]], key=lambda turn: turn.id, reverse=True)
        return {"turns": self._turn_views(turns)}

    async def _history_search(self, params: dict) -> dict:
        """The answered turns of the thread of `member_id` (Raphael's by default) that hold the
        words of `query`, best first, in the shape of `agent.history.list`. `limit` is clamped into
        1..100 (50 by default)."""
        member_id = self._member_param(params)
        query = _str(params, "query").strip()
        if not query or len(query) > HISTORY_QUERY_MAX_CHARS:
            raise invalid_params(f"query must be 1 to {HISTORY_QUERY_MAX_CHARS} characters")
        limit = _opt_int(params, "limit")
        if limit is None:
            limit = HISTORY_SEARCH_DEFAULT_LIMIT
        limit = min(max(limit, 1), HISTORY_SEARCH_MAX_LIMIT)
        self._expire(self._confirmations.sweep())
        turns = self._history.search(
            query, limit=limit, most=HISTORY_SEARCH_MAX_LIMIT, member_id=member_id,
            conversation_id=self.conversations.resolve(member_id, _opt_int(params, "conversation_id")),
        )
        return {"turns": self._turn_views(turns)}

    def _turn_views(self, turns: list[Turn]) -> list[dict]:
        """The turns as the app reads them: each with its steps and, for a turn that handed a task
        to the worker, the task and the steps the worker took for it."""
        ids = [turn.id for turn in turns if turn.id is not None]
        steps = self._history.steps(ids)
        messages = self._history.messages(ids)
        tasks = self._background.tasks_for_turns(ids)
        views = []
        for turn in turns:
            task, task_steps = tasks.get(turn.id or 0, (None, []))
            views.append(
                {
                    "id": turn.id,
                    "created_at_ms": turn.created_at_ms,
                    "session_id": turn.session_id,
                    "source": turn.source,
                    "question": turn.question,
                    "route": turn.route,
                    "prefix": turn.prefix,
                    "text": turn.text,
                    "detail": turn.detail,
                    "status": turn.status,
                    "cost_usd": turn.cost_usd,
                    "model": turn.model,
                    # Whose thread it is in, and for an exchange the member on the other side.
                    "member_id": turn.member_id,
                    "conversation_id": turn.conversation_id,
                    "peer_id": turn.peer_id,
                    # The files handed with the request: their names and types, never more.
                    "attachments": list(turn.attachments or []),
                    **({"quote": turn.quote} if turn.quote else {}),
                    # What the fast tier did, then what the worker did for the task it was given.
                    "steps": [_step_view(step) for step in steps.get(turn.id or 0, [])]
                    + task_steps,
                    "task": task,
                    "messages": messages.get(turn.id or 0, []),
                }
            )
        return views

    async def _history_clear(self, params: dict) -> dict:
        """Clear the conversations: every thread, with every summary (each member's too), what
        opening taught Raphael and the background tasks."""
        if "member_id" in params:
            member_id = self._member_param(params)
            sessions = {row[0] for row in self._conn.execute("SELECT DISTINCT session_id FROM agent_turns WHERE member_id = ?", (member_id,))}
            sessions |= {session for session, owner in self._inflight_members.items() if owner == member_id}
            tasks = [self._inflight[session] for session in sessions if session in self._inflight]
            for session in sessions:
                self.stop_session(session)
                if self._computer is not None:
                    self._computer.stop_session(session)
            await asyncio.gather(*tasks, return_exceptions=True)
            loops = [loop for loop in self._cli_loops if loop.member_id == member_id]
            await asyncio.gather(*(loop.aclose() for loop in loops))
            self._cli_loops.difference_update(loops)
            self._cli_sessions.clear_member(member_id)
            self._background.forget_member(member_id)
            deleted = self._history.clear_member(member_id)
            self._conversation_of(member_id).clear_summary()
            for conversation in self._named_conversations.values():
                if conversation.member_id == member_id:
                    conversation.clear_summary()
            with self._conn:
                self._conn.execute("DELETE FROM conversation_context_summaries WHERE conversation_id IN (SELECT id FROM conversations WHERE member_id = ?)", (member_id,))
            if self._memory is not None:
                self._memory.forget_member_turns(member_id)
            if self._team is not None and hasattr(self._team, "store"):
                self._team.store.mark_read(member_id)
            if self._notify is not None:
                self._notify("agent.turn", {"turn_id": 0, "member_id": member_id})
            if self._team is not None and hasattr(self._team, "_changed"):
                self._team._changed()
            return {"deleted": deleted}
        if self._cli_loops:
            for task in self._inflight.values():
                task.cancel()
            await asyncio.gather(*self._inflight.values(), return_exceptions=True)
            self._deny(self._confirmations.cancel_all())
        await asyncio.gather(*(loop.aclose() for loop in list(self._cli_loops)))
        self._cli_loops.clear()
        self._cli_sessions.clear()
        deleted = self._history.clear()
        try:
            # The summary is made of the turns, and the reflection pass starts over with the ids.
            self._conversation.clear_summary()
            for conversation in self._named_conversations.values():
                conversation.clear_summary()
            with self._conn:
                self._conn.execute("DELETE FROM conversation_context_summaries")
            for member_id in self._member_ids():
                self._conversation_of(member_id).clear_summary()
            if self._memory is not None:
                self._memory.forget_turns()
        except sqlite3.Error as exc:
            logger.warning("Could not clear the summary: %s", type(exc).__name__)
        try:
            self._targets.clear()  # what opening taught Raphael goes with what was said
        except sqlite3.Error as exc:
            logger.warning("Could not clear the names that opened: %s", type(exc).__name__)
        self._background.clear()
        return {"deleted": deleted}

    def _member_ids(self) -> list[int]:
        """The members whose conversation has a summary, or a fold under way."""
        rows = self._conn.execute(
            "SELECT member_id FROM conversation_summaries WHERE member_id != 0"
        ).fetchall()
        known = set(self._conversations)
        if self._memory is not None:
            known |= set(self._memory.member_conversations())
        return sorted({int(row[0]) for row in rows} | known)

    def _prune_if_due(self) -> None:
        """Forget the names that opened a page and were not used within the history retention,
        at most once a day. The conversation itself is not pruned: there is one, and it stays
        until the user clears it (the retention is the dictations'), with its background tasks."""
        now = self._now_ms()
        last = self._pruned_at_ms
        if last is not None and 0 <= now - last < _DAY_MS:
            return
        self._pruned_at_ms = now
        try:
            days = self._settings.get("history_retention_days")
            if days >= 0:
                self._targets.prune(max(now - days * _DAY_MS, 0))
        except sqlite3.Error as exc:
            logger.warning("Could not prune the names that opened: %s", type(exc).__name__)

    # --- the parts every reply uses -------------------------------------------------------------

    def _read_settings(self, speaker: Speaker = RAPHAEL) -> _Settings:
        """Global assistant settings, including for legacy profiles with a stored model."""
        model = self._settings.get("agent_model")
        city = self._settings.get("agent_city")
        return _Settings(
            model=_text_or(model, DEFAULTS["agent_model"]),
            city=_text_or(city, DEFAULTS["agent_city"]),
            limit_usd=self._settings.get("monthly_limit_usd"),
        )

    def _files_off(self, ask: _Ask) -> str | None:
        """The phrase that refuses the files of `ask` when one is an image and reading images is
        off ("vision_off"), or a PDF and reading PDFs is off ("pdf_off"); None otherwise."""
        services = self._tools.services
        if services is None:
            return None
        if any(a.is_image for a in ask.attachments) and not services.enabled("vision"):
            return "vision_off"
        if any(a.is_pdf for a in ask.attachments) and not services.enabled("pdf"):
            return "pdf_off"
        return None

    def _blocked(self, settings: _Settings) -> str | None:
        """Why no model may be asked: "no_key", "auth" (the key store failed), or None."""
        if brain_ref(settings.model):
            return None
        try:
            if isinstance(self._client, ProviderRouter):
                return None if self._client.available(settings.model) else "no_key"
            key = self._keys.get()
        except Exception as exc:  # its message could hold the key
            logger.warning("The key store failed to read the key: %s", type(exc).__name__)
            return "auth"
        if not (key or "").strip():
            return "no_key"
        return None

    def _failed(self, ask: _Ask, route: str, kind: str, ledger: Ledger) -> dict:
        """The 否 reply for a request that could not be answered, with the reason."""
        return self._finish(
            ask, route, "negative", phrase(ask.language, kind), ledger, status="error"
        )

    def _finish(
        self,
        ask: _Ask,
        route: str,
        which: str,
        text: str,
        ledger: Ledger,
        *,
        detail: str | None = None,
        status: str = "done",
    ) -> dict:
        """Write the turn down and answer. `which` is a prefix of the language (understood,
        negative, ...) or a prefix as the model wrote it. A member of the team says it with no
        prefix: it speaks in plain sentences."""
        prefix = _prefix(ask.language, which) if ask.speaker.is_raphael else ""
        turn_id = self._record(ask, route, prefix, text, status, detail, ledger)
        if (ask.source != VOICE or not ask.speaker.is_raphael) and status != "confirm":
            for line in ask.replies or [text]:
                self._message(ask, "reply", line, prefix)
            ask.replies = []
        return {"status": "done", "turn_id": turn_id, "prefix": prefix, "text": text}

    @staticmethod
    def _done(turn_id: int, language: str, which: str, text: str, *, silent: bool = False) -> dict:
        """The reply of a request that is over. `silent`: Raphael does not say it aloud."""
        reply = {
            "status": "done",
            "turn_id": turn_id,
            "prefix": _prefix(language, which),
            "text": text,
        }
        return _Silent(reply) if silent else reply

    def _stop_speaking(self) -> None:
        if self._speaking is not None:
            self._speaking.stop()

    def _voiced(self, source: str | None, member_id: int | None) -> bool:
        """Whether Raphael will say an answer aloud, and so is waited for: there is a voice that can
        be heard (`Speaking.speaks`), the request was made at this computer (`source`; None for the
        answer to a question, which only the owner at this computer can give) and it is Raphael's
        own (`member_id` 0; a member answers in its thread, in writing). With no voice, nothing
        changes and nothing waits."""
        if self._speaking is None or member_id != 0:
            return False
        if source is not None and source not in _AT_THIS_COMPUTER:
            return False
        try:
            return self._speaking.speaks()
        except Exception as exc:  # the voice must not cost the answer
            logger.warning("Could not tell whether Raphael can speak: %s", type(exc).__name__)
            return False

    def _voice_language(self) -> str:
        """The language the voice speaks (the setting `tts_language`: Japanese for now)."""
        try:
            language = self._settings.get("tts_language")
        except sqlite3.Error as exc:
            logger.warning("Could not read the setting tts_language: %s", type(exc).__name__)
            return "ja"
        return language if language in PREFIXES else "ja"

    def _keep_japanese(self, turn_id: int, line: str) -> None:
        """Keep the Japanese rendering of a turn's line until the turn is said (`_said`). In memory
        only, a few at a time: one that was never said is forgotten when others come."""
        if not line:
            return
        self._japanese[turn_id] = line
        while len(self._japanese) > 8:
            del self._japanese[next(iter(self._japanese))]

    def _release(self, turn_id: object) -> None:
        """Announce a turn that was held back until the voice was ready (`_turn_changed`)."""
        if type(turn_id) is int and turn_id in self._held:
            self._turn_changed(turn_id, self._held.pop(turn_id))

    async def _said(
        self,
        reply: dict,
        source: str | None = None,
        member_id: int | None = 0,
        language: str = DEFAULT_LANGUAGE,
    ) -> dict:
        """`reply`, once Raphael's voice is ready to say it aloud: only a reply that is an answer
        (`status: "done"`, not a `_Silent` one) with words in it, for which `_voiced` holds. It
        starts the voice and waits for its first sound, at most a couple of seconds
        (`Speaking.say`), so that the line is shown (the reply returned, the turn announced to the
        app) when the sound begins, not before it; a voice that is slow or fails never costs the
        answer, and a voice that fails is the voice's to log. What is spoken is not the shown line
        but its rendering in the language of the voice (`persona.spoken_line`): the line itself in
        Japanese, a fixed phrase in its Japanese wording, any other line in the Japanese the model
        wrote with it; a line with none is not spoken. The longer `detail` and the `notice` are
        not spoken. A turn held back for the voice is announced at the end, whatever happened."""
        turn_id = reply.get("turn_id")
        try:
            if (
                self._voiced(source, member_id)
                and not isinstance(reply, _Silent)
                and reply.get("status") == "done"
            ):
                prefix, text = reply.get("prefix"), reply.get("text")
                if isinstance(prefix, str) and isinstance(text, str) and text.strip():
                    spoken = spoken_line(
                        language,
                        prefix,
                        text,
                        self._japanese.get(turn_id, "") if type(turn_id) is int else "",
                        voice_language=self._voice_language(),
                    )
                    if spoken is None:
                        logger.info("A line was not spoken: it has no rendering in the voice's language")
                    else:
                        assert self._speaking is not None
                        try:
                            reply["voiced"] = True
                            await self._speaking.say(
                                *spoken, turn_id=turn_id if type(turn_id) is int else None
                            )
                        except Exception as exc:  # the voice must not cost the answer
                            reply.pop("voiced", None)
                            logger.warning(
                                "Raphael could not start to speak: %s", type(exc).__name__
                            )
        finally:
            if type(turn_id) is int:
                self._japanese.pop(turn_id, None)
            self._release(turn_id)
        return reply

    def _record(
        self,
        ask: _Ask,
        route: str,
        prefix: str,
        text: str,
        status: str,
        detail: str | None,
        ledger: Ledger,
    ) -> int:
        """Write a turn to the history, in the thread of the request's speaker, with the steps of
        its request; 0 if that fails, since the answer matters more. A request one member handed
        another is written as an exchange with the asker (`peer_id`)."""
        member_id = ask.speaker.id
        question = ask.original_text if ask.original_text is not None else ask.text
        if ask.exchange_speakers is not None:
            requester, respondent = ask.exchange_speakers
            question = labelled_lines(requester, question)
            text = relayed_answer(respondent, "\n".join(ask.replies) if ask.replies else text, ask.exchange_owner_sources)
            ask.exchange_answer = text
            if detail:
                detail = relayed_answer(respondent, detail, ask.exchange_owner_sources)
        try:
            cost = max(ledger.total - ask.cost_base, 0.0)
            if ask.turn_id:
                turn_id = ask.turn_id
                self._history.resolve(
                    turn_id, prefix=prefix, text=text, detail=detail, status=status,
                    cost_usd=max(cost - ask.recorded_cost, 0.0), model=ledger.model,
                    route="exchange" if ask.nested else route,
                )
                self._history.add_steps(turn_id, ask.steps[ask.recorded_steps:])
            else:
                turn_id = self._history.add(
                    session_id=ask.session_id, question=question,
                    route="exchange" if ask.nested else route, prefix=prefix, text=text,
                    detail=detail, status=status, cost_usd=cost,
                    source=ask.source, model=ledger.model, steps=ask.steps,
                    attachments=[a.metadata() for a in ask.attachments],
                    quote=ask.quote,
                    member_id=member_id, peer_id=ask.peer,
                    conversation_id=ask.conversation_id,
                )
                ask.turn_id = turn_id
            ask.recorded_cost = cost
            from ..brain.runs import save_metrics
            save_metrics(self._metrics_conn, "turn", turn_id, ask.started_at_ms,
                         None if status in ("running", "confirm") else self._now_ms(), ledger.calls[ask.call_base:])
            ask.recorded_steps = len(ask.steps)
        except sqlite3.Error as exc:
            logger.warning("Could not write the turn to the history: %s", type(exc).__name__)
            return 0
        # A turn that will be said aloud is announced to the app when the voice is ready, with the
        # reply (`_said`), so that the conversation shows the line as the overlay does.
        hold = ask.voiced and status not in ("running", "confirm", *_UNSPOKEN)
        self._turn_changed(turn_id, member_id, hold=hold)
        if hold and turn_id:
            ask.held = turn_id
        if self._memory is not None and status not in ("running", "confirm"):
            try:
                # The fold and, for Raphael's, the reflection pass, in the background.
                if ask.conversation_id != self.conversations.main(member_id) and self._memory.may_spend():
                    self._conversation_of(member_id, ask.conversation_id).schedule_fold(self._read_settings(ask.speaker).model, self._memory.ledger)
                if member_id:
                    self._memory.after_turn(member_id)
                else:
                    self._memory.after_turn()
            except Exception as exc:  # the memory must not cost the turn
                logger.warning("The memory could not follow the turn: %s", type(exc).__name__)
        return turn_id

    def _turn_changed(self, turn_id: int, member_id: int | None = None, *, hold: bool = False) -> None:
        """Tell the app a turn was written or completed, so that a conversation on screen follows:
        its id and whose thread it is in. With `hold`, the turn is announced later, by `_release`,
        when the voice that says it is ready."""
        if self._notify is None or not turn_id:
            return
        if hold:
            self._held[turn_id] = member_id
            return
        if member_id is None:
            turn = self.turn(turn_id)
            member_id = 0 if turn is None else turn.member_id
        turn = self.turn(turn_id)
        self._notify("agent.turn", {"turn_id": turn_id, "member_id": member_id})

    @staticmethod
    def _write(action: Callable[[], Any]) -> None:
        try:
            action()
        except sqlite3.Error as exc:
            logger.warning("Could not update the history: %s", type(exc).__name__)
