"""The voice assistant as RPC methods: `agent.ask`, `agent.confirm`, `agent.cancel` and the history.

A request travels like this. The app records the user with `dictation.start {purpose: "agent"}`,
gets the transcript from `dictation.stop`, and sends it with `agent.ask {session_id, text,
ui_language, target_app}`. The router (Jev, and the language model when Jev fails or is unsure;
`routing`) says what it is:

- a command runs at once if it is T0 or T1 (`commands`, `policy`) and Raphael says 《了。》; at T2 or
  T3 (copying to the clipboard) nothing runs: the answer is a question for a yes or a no
  (`status: "confirm"`), which `agent.confirm` answers within 30 seconds;
- an answer looks up what it needs, together (`tools`: the weather, a web search, the time), and one
  call to the language model words it (`answers`); a discussion and small talk are worded with the
  last turns of the conversation as context;
- when the user has connected plugins (`integrations`) with tools that have a tier, an answer, and a
  task for a connected service, may use them too: one call to the language model picks the tools
  it needs from those alone (`plugins`), the program makes the calls within their tiers, and the
  results are given to the call that words the answer as data, not instructions. A call at T0 or T1
  is made at once; at T2 or T3 it is a question for a yes or a no like a command at that tier, one
  to a turn, and the call is made when the yes comes. A tool with no tier is never offered;
- a request that is too garbled to act on, or a task for the background (not there yet), gets a
  fixed 否 reply.

Whatever goes wrong is answered, never raised: no key, the monthly limit reached, no network, a
model that fails each get a short 否 reason in the user's language, and the commands still work (a
list of words finds them, `local`). Every paid call is written to the spend with the purpose
"agent". Every turn is written to the history (`agent.history.list`, `agent.history.clear`).

`agent.cancel {session_id}` ends a request that is still being thought about and denies the
question that session is waiting on. A new request replaces any question that waits.

Threads: this runs on the event loop, which owns the database. The device actions (a key, a page, the
clipboard, PowerShell) run on worker threads. Nothing here logs what the user said, what Raphael
answered, a city or a key: only routes, kinds, latencies and costs.
"""

import asyncio
import logging
import re
import sqlite3
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, tzinfo
from typing import Any

import httpx

from ..brain.openrouter import OpenRouterClient
from ..brain.secrets import KeyStore
from ..brain.settings import DEFAULTS, SettingsStore
from ..brain.spend import SpendTracker
from ..protocol import INVALID_PARAMS, RpcError
from ..router import Router as RpcRouter
from .answers import Voice
from .cities import city_in
from .commands import COMMANDS, AppCatalog, run_command
from .device import DeviceActions, StartApp
from .failure import AgentFailure
from .history import AgentHistory, Turn
from .ledger import Ledger
from .local import match_command
from .persona import DEFAULT_LANGUAGE, PREFIXES, normalize_language, parenthesized, phrase
from .plugins import (
    PLAN_CONTEXT_TURNS,
    Offer,
    PlannedCall,
    Planner,
    PluginHub,
    PluginResult,
    make_offer,
    preview,
)
from .policy import (
    CONFIRM_TIMEOUT_MS,
    Action,
    Approval,
    Confirmations,
    NotApproved,
    Outcome,
    Pending,
    Tier,
    ToolRegistry,
    UnknownTool,
)
from .routing import RouteDecision, Router
from .tools import ToolResult, Tools

logger = logging.getLogger(__name__)

HISTORY_DEFAULT_LIMIT = 50
# A session agent.cancel named is remembered this long: its request may still be on its way (Esc
# and the request can cross on the pipe), and it is then answered as cancelled, with nothing run.
CANCELLED_MEMORY_S = 60.0
HISTORY_MAX_LIMIT = 200
# A request is cut to this many characters: a dictation holds a few thousand at the very most.
MAX_QUESTION_CHARS = 1000
# How many turns of the conversation each route is shown.
CONTEXT_TURNS = {"discuss": 10, "answer": 3, "chat": 3}
_DAY_MS = 86_400_000
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


def _wall_clock_ms() -> int:
    return time.time_ns() // 1_000_000


def _prefix(language: str, which: str) -> str:
    """The prefix of `language` that `which` names ("understood", "negative", ...); `which` itself
    when it is a prefix already, as the model wrote it."""
    return PREFIXES[language]._asdict().get(which, which)


def _text_or(value: Any, default: str) -> str:
    """`value` without the spaces around it, or `default` if it is not text or is blank."""
    return value.strip() if isinstance(value, str) and value.strip() else default


def _invalid(message: str) -> RpcError:
    return RpcError(INVALID_PARAMS, f"Invalid params: {message}")


def _str(params: dict, name: str, *, blank: bool = False) -> str:
    value = params.get(name)
    if not isinstance(value, str) or (not blank and not value):
        raise _invalid(f"{name} must be {'a string' if blank else 'a non-empty string'}")
    return value


def _opt_int(params: dict, name: str) -> int | None:
    value = params.get(name)
    if value is None:
        return None
    if type(value) is not int:
        raise _invalid(f"{name} must be a whole number")
    return value


@dataclass
class _Ask:
    session_id: str
    text: str
    language: str


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


class AgentService:
    """The voice assistant: it keeps the history, the questions that wait for a yes, and the
    requests that are being thought about.

    `conn` is the open database (used on the event loop only), `client` the OpenRouter client,
    `keys` where the key lives, and `actions` what is done to this computer. `now_ms` is the wall
    clock and `tz` the time zone of the months of the spend; `clock` is a monotonic clock in
    seconds (the thirty seconds of a question), `now` the local time. `http` is the httpx client for
    Open-Meteo, which the caller then owns; `decider` is the router of requests; `plugins` is the
    hub of the tools the user connected (none: the assistant has only its own).
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        client: OpenRouterClient,
        keys: KeyStore,
        *,
        actions: DeviceActions,
        now_ms: Callable[[], int] = _wall_clock_ms,
        tz: tzinfo | None = None,
        clock: Callable[[], float] = time.monotonic,
        http: httpx.AsyncClient | None = None,
        now: Callable[[], datetime] | None = None,
        decider: Router | None = None,
        plugins: PluginHub | None = None,
    ) -> None:
        self._keys = keys
        self._now_ms = now_ms
        self._actions = actions
        self._settings = SettingsStore(conn)
        self._spend = SpendTracker(conn, now_ms, tz)
        self._history = AgentHistory(conn, now_ms)
        self._confirmations = Confirmations(clock)
        self._plugins = plugins
        self._registry = ToolRegistry(
            {name: command.tier for name, command in COMMANDS.items()},
            plugin_tier=None if plugins is None else plugins.tier_of,
        )
        self._planner = Planner(client)
        # pending id -> what the call of a plugin's tool that waits for that yes needs.
        self._followups: dict[str, _Followup] = {}
        for name in ("weather", "web_search", "local_time"):
            self._registry.register(name, Tier.T0)
        self._catalog = AppCatalog(actions, clock=clock)
        self._tools = Tools(client, http=http, now=now)
        self._voice = Voice(client)
        self._decider = decider if decider is not None else Router(client)
        self._inflight: dict[str, asyncio.Task[dict]] = {}
        self._clock = clock
        # session id -> when agent.cancel named it (the monotonic clock).
        self._cancelled: dict[str, float] = {}
        self._last_language = DEFAULT_LANGUAGE
        self._pruned_at_ms: int | None = None
        self._prune_if_due()
        # A question waits in this process only: one that was left by the last is long past.
        try:
            self._history.expire_pending()
        except sqlite3.Error as exc:
            logger.warning("Could not expire the old questions: %s", type(exc).__name__)

    def register(self, router: RpcRouter) -> None:
        """Put the five methods of the assistant on `router`."""
        methods = {
            "agent.ask": self._ask,
            "agent.confirm": self._confirm,
            "agent.cancel": self._cancel,
            "agent.history.list": self._history_list,
            "agent.history.clear": self._history_clear,
        }
        for method, handler in methods.items():
            router.register(method, handler)

    async def aclose(self) -> None:
        """End the requests being thought about and close the HTTP client of the tools."""
        tasks = list(self._inflight.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._inflight.clear()
        await self._tools.aclose()

    # --- agent.ask ------------------------------------------------------------------------------

    async def _ask(self, params: dict) -> dict:
        ask = _Ask(
            session_id=_str(params, "session_id"),
            text=_str(params, "text", blank=True)[:MAX_QUESTION_CHARS],
            language=normalize_language(_str(params, "ui_language")),
        )
        target_app = params.get("target_app")  # for the apps to come; not used yet
        if target_app is not None and not isinstance(target_app, str):
            raise _invalid("target_app must be a string or null")
        self._last_language = ask.language
        if self._was_cancelled(ask.session_id):
            # Esc came first: the request is answered as cancelled and nothing runs or is kept.
            return self._done(0, ask.language, "understood", phrase(ask.language, "denied"))
        # The questions that ran out are expired, and a new request replaces those that still wait.
        self._expire(self._confirmations.sweep())
        self._deny(self._confirmations.cancel_all())
        self._prune_if_due()
        earlier = self._inflight.pop(ask.session_id, None)
        if earlier is not None:
            earlier.cancel()
        task = asyncio.ensure_future(self._answer(ask))
        self._inflight[ask.session_id] = task
        try:
            return await task
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if task.cancelled() and (current is None or not current.cancelling()):
                # Ended by agent.cancel (or by a newer request of the session), not by the caller.
                return self._done(0, ask.language, "understood", phrase(ask.language, "denied"))
            raise
        finally:
            if self._inflight.get(ask.session_id) is task:
                del self._inflight[ask.session_id]

    async def _answer(self, ask: _Ask) -> dict:
        """One request, from the router to the reply. Never raises for a failure of the world."""
        began = time.perf_counter()
        ledger = Ledger(self._spend)
        if not ask.text.strip():
            return self._finish(ask, "unclear", "negative", phrase(ask.language, "unclear"), ledger)
        settings = self._read_settings()
        blocked = self._blocked(settings)
        decision: RouteDecision | None = None
        failure = blocked
        if blocked is None:
            try:
                decision = await self._decider.classify(ask.text, settings.model, ledger)
            except AgentFailure as error:
                failure = error.kind
            except Exception as exc:  # a bug in the router must not become an RPC error
                logger.warning("Routing failed unexpectedly: %s", type(exc).__name__)
                failure = "error"
        if decision is None:
            # No model could say what it is: a plain command needs none.
            decision = match_command(ask.text)
            if decision is None:
                return self._failed(ask, "unclear", failure or "error", ledger)
            logger.info("A command was found by its words: no model could route (%s)", failure)
        try:
            result = await self._route(ask, decision, settings, ledger)
        except AgentFailure as error:
            result = self._failed(ask, decision.route, error.kind, ledger)
        except Exception as exc:  # the shell shows no raw errors: this too is a 否 reply
            logger.warning("The %s route failed unexpectedly: %s", decision.route, type(exc).__name__)
            result = self._failed(ask, decision.route, "error", ledger)
        logger.info(
            "Agent turn: route=%s source=%s status=%s cost=%.5f ms=%d",
            decision.route,
            decision.source,
            result["status"],
            ledger.total,
            round((time.perf_counter() - began) * 1000),
        )
        return result

    async def _route(
        self, ask: _Ask, decision: RouteDecision, settings: _Settings, ledger: Ledger
    ) -> dict:
        route = decision.route
        if route == "command":
            return await self._command(ask, decision, settings, ledger)
        if route == "answer":
            return await self._answer_with_tools(ask, decision, settings, ledger, self._offer())
        if route == "delegate":
            # A task for a connected service has somewhere to go once a tool is allowed.
            offer = self._offer()
            if offer is not None:
                return await self._answer_with_tools(ask, decision, settings, ledger, offer)
            return self._finish(ask, route, "negative", phrase(ask.language, "delegate"), ledger)
        if route in ("discuss", "chat"):
            reply = await self._voice.reply(
                model=settings.model,
                language=ask.language,
                route=route,
                question=ask.text,
                context=self._context(CONTEXT_TURNS[route]),
                tools=[],
                moment=self._moment(),
                ledger=ledger,
            )
            return self._finish(ask, route, reply.prefix, reply.text, ledger, detail=reply.detail)
        return self._finish(ask, "unclear", "negative", phrase(ask.language, "unclear"), ledger)

    # --- answers --------------------------------------------------------------------------------

    def _moment(self) -> Mapping[str, str]:
        return self._tools.local_time().data or {}

    async def _answer_with_tools(
        self,
        ask: _Ask,
        decision: RouteDecision,
        settings: _Settings,
        ledger: Ledger,
        offer: Offer | None = None,
    ) -> dict:
        """An answer, with what it needs looked up first. With an `offer` (the plugin tools that may
        be used), a model picks the tools the request needs while the lookups run."""
        jobs = []
        if decision.needs_weather:
            # The city the question names, else the one in the settings.
            city = city_in(ask.text) or settings.city
            jobs.append(self._tool(self._tools.weather(city, ask.language), "weather"))
        if decision.needs_search:
            jobs.append(
                self._tool(
                    self._tools.web_search(ask.text, settings.model, ledger), "web_search"
                )
            )
        picking = self._pick_tools(ask, settings, ledger, offer)
        looked_up, picked = await asyncio.gather(asyncio.gather(*jobs), picking)
        results: list[ToolResult] = list(looked_up)
        if decision.needs_time:
            results.append(self._tools.local_time())
        if offer is not None:
            if isinstance(picked, AgentFailure):
                if decision.route == "delegate":
                    raise picked  # the request was for a service, and nothing could look at it
                picked = []  # an answer goes on without the services
            if decision.route == "delegate" and not picked:
                return self._finish(
                    ask, "answer", "negative", phrase(ask.language, "plugin_none"), ledger
                )
            outcome = await self._carry_out(ask, picked, results, ledger)
            if isinstance(outcome, dict):
                return outcome  # a question for a yes
            results = outcome
        reply = await self._voice.reply(
            model=settings.model,
            language=ask.language,
            route="answer",
            question=ask.text,
            context=self._context(CONTEXT_TURNS["answer"]),
            tools=results,
            moment=self._moment(),
            ledger=ledger,
        )
        return self._finish(ask, "answer", reply.prefix, reply.text, ledger, detail=reply.detail)

    @staticmethod
    async def _tool(job: Any, name: str) -> ToolResult:
        """A tool's result, whatever happens: one that breaks is a tool that failed."""
        try:
            return await job
        except Exception as exc:
            logger.warning("The %s tool failed unexpectedly: %s", name, type(exc).__name__)
            return ToolResult(name, False, None, "error")

    def _context(self, count: int) -> list[Turn]:
        try:
            return self._history.recent(count)
        except sqlite3.Error as exc:
            logger.warning("Could not read the conversation so far: %s", type(exc).__name__)
            return []

    # --- the tools of the plugins ---------------------------------------------------------------

    def _offer(self) -> Offer | None:
        """The plugin tools a model may be offered now, or None when there are none: no plugins,
        or none of their tools has a tier (a tool with no tier is never offered)."""
        if self._plugins is None:
            return None
        try:
            tools = self._plugins.offered()
        except Exception as exc:  # a plugin that breaks must not break the answer
            logger.warning("Could not list the plugins' tools: %s", type(exc).__name__)
            return None
        return make_offer(tools) if tools else None

    async def _pick_tools(
        self, ask: _Ask, settings: _Settings, ledger: Ledger, offer: Offer | None
    ) -> list[PlannedCall] | AgentFailure:
        """The calls a model picks for the request out of the tools in `offer`; the failure itself
        when it cannot (the answer decides what that means)."""
        if offer is None:
            return []
        try:
            return await self._planner.plan(
                offer,
                ask.text,
                self._context(PLAN_CONTEXT_TURNS),
                model=settings.model,
                ledger=ledger,
            )
        except AgentFailure as failure:
            return failure
        except Exception as exc:
            logger.warning("Picking tools failed unexpectedly: %s", type(exc).__name__)
            return AgentFailure("error")

    def _tier_now(self, call: PlannedCall) -> Tier | None:
        """The tier the user has given the tool of `call` now; None if it has none any more."""
        try:
            return self._registry.tier(call.tool.key)
        except UnknownTool:
            return None

    async def _carry_out(
        self, ask: _Ask, calls: list[PlannedCall], results: list[ToolResult], ledger: Ledger
    ) -> list[ToolResult] | dict:
        """Make the picked calls that need no yes, together, and add their results to `results`.
        If a call needs a yes the answer waits for it: that is the question to return instead, for
        the first such call; any others are not made (one question to a turn)."""
        automatic: list[PlannedCall] = []
        asking: list[PlannedCall] = []
        for call in calls:
            tier = self._tier_now(call)
            (asking if tier is not None and tier >= Tier.T2 else automatic).append(call)
        results = [*results, *await asyncio.gather(*(self._plugin_call(c, None) for c in automatic))]
        if not asking:
            return results
        first, *rest = asking
        results += [_unmade(call) for call in rest]
        return self._ask_plugin(ask, first, results, ledger)

    async def _plugin_call(
        self, call: PlannedCall, approval: Approval | None, action: Action | None = None
    ) -> ToolResult:
        """Make a call of a plugin's tool through the policy's one door, and give its result as
        the answer is shown it. `approval` is the yes for it, if it had to be asked about; a call
        that the door refuses is not made, and the hub writes down that it was refused."""
        action = action or Action(call.tool.key, call.arguments)
        plugins = self._plugins
        if plugins is None:
            return _tool_result(call, PluginResult(False, "blocked"))
        try:
            self._registry.check(action, approval)
            approver = "auto" if approval is None else "user"
        except (UnknownTool, NotApproved):
            approver = "none"
        try:
            outcome = await plugins.call(call.tool.key, call.arguments, approver=approver)
        except Exception as exc:  # not the hub's own failures, which it reports in the outcome
            logger.warning("A plugin call failed unexpectedly: %s", type(exc).__name__)
            outcome = PluginResult(False, "error")
        return _tool_result(call, outcome)

    def _ask_plugin(
        self, ask: _Ask, call: PlannedCall, results: list[ToolResult], ledger: Ledger
    ) -> dict:
        """The question for a yes before the call is made: which service, which tool, and the
        start of what it is given. The call itself waits in `_followups`."""
        language = ask.language
        shown = preview(call.arguments)
        text = phrase(
            language,
            "confirm_plugin",
            server=call.tool.server,
            tool=call.tool.name,
            what=parenthesized(language, shown),
        )
        action = Action(call.tool.key, dict(call.arguments))
        reply = self._open_question(ask, action, "answer", text, ledger)
        self._followups[reply["confirm"]["id"]] = _Followup(ask, call, results)
        return reply

    def _drop_followup(self, pending: Pending, outcome: str) -> None:
        """The question of a plugin's call ended without a yes ("denied" or "expired"): its call is
        forgotten, and the audit says it was asked about and not made."""
        followup = self._followups.pop(pending.id, None)
        if followup is not None and self._plugins is not None:
            try:
                self._plugins.record_unrun(pending.action.tool, outcome=outcome)
            except Exception as exc:
                logger.warning("Could not write down a call that was not made: %s", type(exc).__name__)

    async def _confirm_plugin(
        self, pending: Pending, approval: Approval | None, followup: _Followup
    ) -> dict:
        """The yes came: make the call, then word what came of it."""
        language = pending.language
        ledger = Ledger(self._spend)
        call = followup.call
        # The yes is for the tier the question was asked at. A tool that has none now, or a higher
        # one, is not covered by it.
        now = self._tier_now(call)
        if now is None or now > pending.tier:
            approval = None
        result = await self._plugin_call(call, approval, pending.action)
        if not result.ok and result.error == "timeout":
            text = phrase(language, "plugin_unsure", server=call.tool.server)
            return self._outcome(pending, "negative", text, "error")
        if not result.ok and result.error != "tool_error":
            return self._outcome(pending, "negative", phrase(language, "failed"), "error")
        settings = self._read_settings()
        try:
            if self._blocked(settings) is not None:
                raise AgentFailure("error")
            reply = await self._voice.reply(
                model=settings.model,
                language=language,
                route="answer",
                question=followup.ask.text,
                context=self._context(CONTEXT_TURNS["answer"]),
                tools=[*followup.results, result],
                moment=self._moment(),
                ledger=ledger,
            )
        except AgentFailure:
            # It was done, and Raphael cannot say more than that.
            return self._outcome(pending, "understood", "", "done", cost_usd=ledger.total)
        return self._outcome(
            pending, reply.prefix, reply.text, "done", detail=reply.detail, cost_usd=ledger.total
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
        if command.kind == "app":
            app = await self._find_app(ask, decision, settings, ledger)
            if app is None:
                return self._finish(
                    ask, "command", "negative", phrase(language, "app_not_found"), ledger
                )
            args = {"app_id": app.app_id}
        elif command.kind == "copy":
            copy = self._what_to_copy(ask.text)
            if copy is None:
                return self._finish(
                    ask, "command", "negative", phrase(language, "nothing_to_copy"), ledger
                )
            args, question = {"text": copy[0]}, copy[1]
        action = Action(command.name, args)
        if self._registry.needs_confirmation(command.name):
            return self._ask_to_confirm(ask, action, question, ledger)
        try:
            self._registry.check(action, None)
            await run_command(command, args, self._actions)
        except Exception as exc:
            logger.warning("The command %s failed: %s", command.name, type(exc).__name__)
            return self._finish(
                ask, "command", "negative", phrase(language, "failed"), ledger, status="error"
            )
        return self._finish(ask, "command", "understood", "", ledger)

    async def _find_app(
        self, ask: _Ask, decision: RouteDecision, settings: _Settings, ledger: Ledger
    ) -> StartApp | None:
        """The app of the Start menu that the request asks for: by the words of the request, else
        by the name the router heard, else by asking the language model to choose from the list."""
        app = await self._catalog.find(ask.text)
        if app is None and decision.app:
            app = await self._catalog.lookup(decision.app)
        if app is None and decision.source != "local" and not decision.app:
            names = await self._catalog.names()
            if names:
                chosen = await self._decider.pick_app(ask.text, names, settings.model, ledger)
                if chosen:
                    app = await self._catalog.lookup(chosen)
        return app

    def _what_to_copy(self, text: str) -> tuple[str, str] | None:
        """`(what to copy, the phrase that asks about it)`, or None when there is nothing to copy:
        a text the speaker quoted, else Raphael's last answer, with its detail (`_copy_text`)."""
        for quote in _QUOTES:
            found = quote.search(text)
            if found and found.group(1).strip():
                return found.group(1).strip(), "copy_quote"
        try:
            last = next(
                (
                    turn
                    for turn in reversed(self._history.recent(20))
                    if turn.route in _ANSWER_ROUTES and (turn.detail or turn.text)
                ),
                None,
            )
        except sqlite3.Error:
            last = None
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
        tier = self._registry.tier(action.tool)
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
        confirm_id = _str(params, "id")
        approved = params.get("approved")
        if type(approved) is not bool:
            raise _invalid("approved must be true or false")
        self._expire(self._confirmations.sweep())
        resolution = self._confirmations.resolve(confirm_id, approved)
        pending = resolution.pending
        if resolution.outcome is Outcome.UNKNOWN or pending is None:
            return self._done(
                0, self._last_language, "negative", phrase(self._last_language, "expired")
            )
        language = pending.language
        if resolution.outcome is Outcome.EXPIRED:
            self._drop_followup(pending, "expired")
            return self._outcome(pending, "negative", phrase(language, "expired"), "expired")
        if resolution.outcome is Outcome.DENIED:
            self._drop_followup(pending, "denied")
            return self._outcome(pending, "understood", phrase(language, "denied"), "denied")
        followup = self._followups.pop(pending.id, None)
        if followup is not None:
            return await self._confirm_plugin(pending, resolution.approval, followup)
        command = COMMANDS.get(pending.action.tool)
        if command is None:
            return self._outcome(pending, "negative", phrase(language, "failed"), "error")
        try:
            self._registry.check(pending.action, resolution.approval)
            await run_command(command, pending.action.args, self._actions)
        except Exception as exc:  # NotApproved among them: it is a failure all the same
            logger.warning("The confirmed command %s failed: %s", command.name, type(exc).__name__)
            return self._outcome(pending, "negative", phrase(language, "failed"), "error")
        done = _DONE_PHRASE.get(command.name)
        return self._outcome(pending, "understood", phrase(language, done) if done else "", "done")

    def _outcome(
        self,
        pending: Pending,
        which: str,
        text: str,
        status: str,
        *,
        detail: str | None = None,
        cost_usd: float = 0.0,
    ) -> dict:
        """End the turn of a question: what Raphael says now, and how it ended."""
        prefix = _prefix(pending.language, which)
        self._write(
            lambda: self._history.resolve(
                pending.turn_id,
                prefix=prefix,
                text=text,
                status=status,
                detail=detail,
                cost_usd=cost_usd,
            )
        )
        return self._done(pending.turn_id, pending.language, which, text)

    def _deny(self, pendings: list[Pending]) -> None:
        for pending in pendings:
            self._drop_followup(pending, "denied")
            prefix = PREFIXES[pending.language].understood
            text = phrase(pending.language, "denied")
            self._write(
                lambda p=pending, prefix=prefix, text=text: self._history.resolve(
                    p.turn_id, prefix=prefix, text=text, status="denied"
                )
            )

    def _expire(self, pendings: list[Pending]) -> None:
        for pending in pendings:
            self._drop_followup(pending, "expired")
            prefix = PREFIXES[pending.language].negative
            text = phrase(pending.language, "expired")
            self._write(
                lambda p=pending, prefix=prefix, text=text: self._history.resolve(
                    p.turn_id, prefix=prefix, text=text, status="expired"
                )
            )

    # --- agent.cancel ---------------------------------------------------------------------------

    async def _cancel(self, params: dict) -> dict:
        session_id = _str(params, "session_id")
        self._forget_old_cancels()
        self._cancelled[session_id] = self._clock()
        task = self._inflight.get(session_id)
        if task is not None:
            task.cancel()
        self._expire(self._confirmations.sweep())
        self._deny(self._confirmations.cancel_session(session_id))
        return {}

    def _forget_old_cancels(self) -> None:
        now = self._clock()
        for session_id, when in list(self._cancelled.items()):
            if not 0 <= now - when < CANCELLED_MEMORY_S:
                del self._cancelled[session_id]

    def _was_cancelled(self, session_id: str) -> bool:
        self._forget_old_cancels()
        return session_id in self._cancelled

    # --- the history ----------------------------------------------------------------------------

    async def _history_list(self, params: dict) -> dict:
        limit = _opt_int(params, "limit")
        before_id = _opt_int(params, "before_id")
        # A limit is clamped into 1..200: SQLite reads a negative LIMIT as "no limit".
        limit = HISTORY_DEFAULT_LIMIT if limit is None else min(max(limit, 1), HISTORY_MAX_LIMIT)
        self._expire(self._confirmations.sweep())
        turns = self._history.list(limit=limit, before_id=before_id)
        return {
            "turns": [
                {
                    "id": turn.id,
                    "created_at_ms": turn.created_at_ms,
                    "question": turn.question,
                    "route": turn.route,
                    "prefix": turn.prefix,
                    "text": turn.text,
                    "detail": turn.detail,
                    "status": turn.status,
                }
                for turn in turns
            ]
        }

    async def _history_clear(self, params: dict) -> dict:
        return {"deleted": self._history.clear()}

    def _prune_if_due(self) -> None:
        """Delete the turns older than the history retention, at most once a day: they are what
        the user said, like the dictations."""
        now = self._now_ms()
        last = self._pruned_at_ms
        if last is not None and 0 <= now - last < _DAY_MS:
            return
        self._pruned_at_ms = now
        try:
            days = self._settings.get("history_retention_days")
            if days >= 0:
                self._history.prune(max(now - days * _DAY_MS, 0))
        except sqlite3.Error as exc:
            logger.warning("Could not prune the assistant's history: %s", type(exc).__name__)

    # --- the parts every reply uses -------------------------------------------------------------

    def _read_settings(self) -> _Settings:
        """The settings of the assistant; one that is not valid reads as its default."""
        model = self._settings.get("agent_model")
        city = self._settings.get("agent_city")
        return _Settings(
            model=_text_or(model, DEFAULTS["agent_model"]),
            city=_text_or(city, DEFAULTS["agent_city"]),
            limit_usd=self._settings.get("monthly_limit_usd"),
        )

    def _blocked(self, settings: _Settings) -> str | None:
        """Why no model may be asked: "no_key", "auth" (the key store failed), "budget", or None."""
        try:
            key = self._keys.get()
        except Exception as exc:  # its message could hold the key
            logger.warning("The key store failed to read the key: %s", type(exc).__name__)
            return "auth"
        if not (key or "").strip():
            return "no_key"
        if self._spend.is_over(settings.limit_usd):
            return "budget"
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
        negative, ...) or a prefix as the model wrote it."""
        prefix = _prefix(ask.language, which)
        turn_id = self._record(ask, route, prefix, text, status, detail, ledger)
        return {"status": "done", "turn_id": turn_id, "prefix": prefix, "text": text}

    @staticmethod
    def _done(turn_id: int, language: str, which: str, text: str) -> dict:
        return {
            "status": "done",
            "turn_id": turn_id,
            "prefix": _prefix(language, which),
            "text": text,
        }

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
        """Write a turn to the history; 0 if that fails, since the answer matters more."""
        try:
            return self._history.add(
                session_id=ask.session_id,
                question=ask.text,
                route=route,
                prefix=prefix,
                text=text,
                detail=detail,
                status=status,
                cost_usd=ledger.total,
            )
        except sqlite3.Error as exc:
            logger.warning("Could not write the turn to the history: %s", type(exc).__name__)
            return 0

    @staticmethod
    def _write(action: Callable[[], Any]) -> None:
        try:
            action()
        except sqlite3.Error as exc:
            logger.warning("Could not update the history: %s", type(exc).__name__)
