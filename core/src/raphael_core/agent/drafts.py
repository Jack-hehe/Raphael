"""Email drafts: the mail a member writes for the owner, shown in a private chat typed at this computer as
an editable card (寄件者, 收件者, 副本, 主旨, body; 傳送 and 捨棄), and sent only by the owner, once.

A member forms a draft with the tool `email_draft` instead of sending: the draft is an artifact with a
version and the digest of that version (`draft_digest`: the app that sends it and every field the owner
sees). What happens to it after that is the owner's, through four methods the card calls:

- `integrations.draft_save {id, digest, to?, cc?, subject?, body?, via?}`: the owner edited it. Every
  change makes a new version with a new digest; a change on top of an old digest (a card that is not
  the draft any more) is refused, and nothing changes.
- `integrations.draft_send {id, digest}`: 傳送. It sends the version whose digest the card shows, through
  the one gate, with the app that sends (a tool of a connection of the owner's that sends mail, found by
  its schema: `is_mail_sender`), **once**: the draft is claimed with one conditional update, so a double
  click, a replay and a second card find it claimed. The owner's click on that very version is the yes the
  gate asks for, so no second question is asked; the owner's Never (a rule that forbids sending) still
  refuses it, and nothing is sent. An old digest never sends, and a send whose outcome is not known (a
  timeout) is not tried again by itself: the owner edits it (a new version) to send it anew.
- `integrations.draft_discard {id}`: 捨棄. It changes nothing outside: no call, no gate, no receipt.
- `integrations.draft_handover {id, choice}`: the member offered the draft to a colleague with a choice
  card (「這封回信可以交給焦媽媽寄嗎？」). On yes the same version appears, editable, in that colleague's
  private chat, and the first chat shows one centred line (「已轉交給 ◎ 焦媽媽」); on no it stays where it
  is. Handing a draft over never sends it: only 傳送 does.

A member's preference to write drafts first (`Speaker.draft_first`, on unless the owner turns it off)
never takes the owner's rules away: where it is off the member sends through the gate like any tool, and
a rule that asks first has the question asked. Rooms, an exchange between members, a quick question, a
spoken request and a message from a phone are never shown a draft, and never carry what it says.

A draft holds the owner's own words for the mail; they are shown in their chat and kept in the database
like the rest of the conversation, and never logged.
"""

import hashlib
import json
import logging
import re
import secrets
import sqlite3
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..clock import wall_clock_ms
from ..protocol import invalid_params
from .gate import ToolGate
from .history import AgentHistory
from .plugins import PluginHub, PluginTool
from .policy import Action, Approval
from .safety import DENY, Proposed, narrow_scope, summarize

if TYPE_CHECKING:
    from .ledger import Ledger

logger = logging.getLogger(__name__)

# The longest each field of a draft may be, in characters.
LIMITS = {"to": 1000, "cc": 1000, "subject": 300, "body": 50_000}
# Where a draft is in its life. `sending` is the claim of the one send.
STATES = ("draft", "sending", "sent", "failed", "discarded")
# A tool whose name says it sends (`send`, `gmail_send`, `send_email`) and does not say it writes a draft.
_SENDS = re.compile(r"(?:^|[^a-z])send(?:[^a-z]|$)", re.IGNORECASE)
_DRAFT = re.compile(r"draft", re.IGNORECASE)
# The names a mail tool may give the recipient parameter.
RECIPIENT = ("to", "recipient_email")
_ADDRESS = re.compile(r"[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_COLUMNS = ("id, author_id, holder_id, conversation_id, turn_id, origin_turn_id, language, state, version, digest,"
            " send_via, to_addr, cc_addr, subject, body, offer_to, offer_state, error, sent_digest, created_at_ms,"
            " updated_at_ms, sent_at_ms")


class DraftError(Exception):
    """Why a draft cannot be written or sent: a kind, never the words of the mail."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


def is_mail_sender(tool: PluginTool) -> bool:
    """Whether a connection's tool sends mail: its name says it sends (and not that it writes a draft), and
    its input takes the recipient, the subject and the body as text."""
    if _DRAFT.search(tool.name) or not _SENDS.search(tool.name):
        return False
    props = tool.schema.get("properties") if isinstance(tool.schema, dict) else None
    if not isinstance(props, dict):
        return False
    text = lambda name: isinstance(props.get(name), dict) and props[name].get("type") == "string"  # noqa: E731
    return any(text(name) for name in RECIPIENT) and text("subject") and text("body")


def draft_digest(send_via: str, to: str, cc: str, subject: str, body: str) -> str:
    """The digest of a version: the app that sends it and every field the owner sees. A send is for one."""
    text = json.dumps({"via": send_via, "to": to, "cc": cc, "subject": subject, "body": body},
                      ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def mail_arguments(tool: PluginTool, to: str, cc: str, subject: str, body: str) -> dict[str, str]:
    """What the sending tool is given for a version: its recipient, subject and body, and the copy when it
    has a place for it. DraftError for a version it cannot carry whole: a recipient that is no address, or
    a copy for a tool with nowhere to put it (never dropped without a word)."""
    props = tool.schema.get("properties", {})
    recipients = [part for part in re.split(r"[,;\s]+", to.strip()) if part]
    if not recipients or not all(_ADDRESS.fullmatch(part) for part in recipients):
        raise DraftError("invalid_recipient")
    copies = [part for part in re.split(r"[,;\s]+", cc.strip()) if part]
    if copies and not all(_ADDRESS.fullmatch(part) for part in copies):
        raise DraftError("invalid_recipient")
    arguments = {next(name for name in RECIPIENT if name in props): ", ".join(recipients), "subject": subject, "body": body}
    if copies:
        if "cc" not in props:
            raise DraftError("cc_unsupported")
        arguments["cc"] = ", ".join(copies)
    if not subject.strip() or not body.strip():
        raise DraftError("empty")
    return arguments


def clean_field(name: str, value: Any) -> str:
    """One field as the owner (or the model) wrote it: text without control characters, cut to its limit."""
    if not isinstance(value, str):
        raise invalid_params(f"{name} must be text")
    text = _CONTROL.sub("", value.replace("\r\n", "\n").replace("\r", "\n"))
    if name != "body":
        text = " ".join(text.split())
    else:
        text = text.strip("\n")
    if len(text) > LIMITS[name]:
        raise invalid_params(f"{name} is too long")
    return text


@dataclass(frozen=True)
class Draft:
    id: str
    author_id: int
    holder_id: int
    conversation_id: int | None
    turn_id: int
    origin_turn_id: int
    language: str
    state: str
    version: int
    digest: str
    send_via: str
    to: str
    cc: str
    subject: str
    body: str
    offer_to: int | None
    offer_state: str
    error: str
    sent_digest: str
    created_at_ms: int
    updated_at_ms: int
    sent_at_ms: int | None


def _draft(row: sqlite3.Row) -> Draft:
    return Draft(row["id"], row["author_id"], row["holder_id"], row["conversation_id"], row["turn_id"],
                 row["origin_turn_id"], row["language"], row["state"], row["version"], row["digest"], row["send_via"],
                 row["to_addr"], row["cc_addr"], row["subject"], row["body"], row["offer_to"], row["offer_state"],
                 row["error"], row["sent_digest"], row["created_at_ms"], row["updated_at_ms"], row["sent_at_ms"])


class DraftStore:
    """The drafts, in the database: the head of each (its current version) and every version it had. A send
    that was claimed when the core last ended is of unknown outcome: it is `failed` with `interrupted`, and is
    never sent again by itself."""

    def __init__(self, conn: sqlite3.Connection, now_ms: Callable[[], int] = wall_clock_ms) -> None:
        self._conn, self._now_ms = conn, now_ms
        with conn:
            conn.execute("UPDATE email_drafts SET state='failed', error='interrupted', updated_at_ms=? WHERE state='sending'",
                         (now_ms(),))

    def create(self, *, author_id: int, turn_id: int, conversation_id: int | None, language: str, send_via: str, to: str,
               cc: str, subject: str, body: str, offer_to: int | None) -> Draft:
        """A new draft, version 1, held by its author's chat, with a choice card when it is offered to a colleague."""
        draft_id, now = secrets.token_hex(8), self._now_ms()
        digest = draft_digest(send_via, to, cc, subject, body)
        with self._conn:
            self._conn.execute(
                "INSERT INTO email_drafts (id, author_id, holder_id, conversation_id, turn_id, origin_turn_id, language,"
                " state, version, digest, send_via, to_addr, cc_addr, subject, body, offer_to, offer_state, created_at_ms,"
                " updated_at_ms) VALUES (?, ?, ?, ?, ?, ?, ?, 'draft', 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (draft_id, author_id, author_id, conversation_id, turn_id, turn_id, language, digest, send_via, to, cc,
                 subject, body, offer_to, "pending" if offer_to is not None else "", now, now))
            self._version(draft_id, 1, digest, send_via, to, cc, subject, body, by_owner=False)
        return self.get(draft_id)  # type: ignore[return-value]

    def _version(self, draft_id: str, version: int, digest: str, send_via: str, to: str, cc: str, subject: str, body: str,
                 *, by_owner: bool) -> None:
        self._conn.execute(
            "INSERT INTO email_draft_versions (draft_id, version, digest, send_via, to_addr, cc_addr, subject, body, by_owner,"
            " at_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (draft_id, version, digest, send_via, to, cc, subject, body, int(by_owner), self._now_ms()))

    def get(self, draft_id: str) -> Draft | None:
        row = self._conn.execute(f"SELECT {_COLUMNS} FROM email_drafts WHERE id=?", (draft_id,)).fetchone()
        return None if row is None else _draft(row)

    def versions(self, draft_id: str) -> list[dict]:
        """Every version of a draft, oldest first: the number, the digest and who wrote it."""
        return [{"version": row["version"], "digest": row["digest"], "by_owner": bool(row["by_owner"])}
                for row in self._conn.execute("SELECT version, digest, by_owner FROM email_draft_versions WHERE draft_id=?"
                                              " ORDER BY version", (draft_id,))]

    def shown_in(self, turn_ids: Iterable[int]) -> dict[int, list[Draft]]:
        """The drafts each turn shows: the cards the turn holds, and for the turn that wrote a draft since
        handed to a colleague, that draft (to draw its centred line)."""
        ids = sorted({int(turn_id) for turn_id in turn_ids})
        found: dict[int, list[Draft]] = {}
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            marks = ",".join("?" * len(chunk))
            rows = self._conn.execute(
                f"SELECT {_COLUMNS} FROM email_drafts WHERE turn_id IN ({marks}) OR origin_turn_id IN ({marks})"
                " ORDER BY created_at_ms, rowid", [*chunk, *chunk])
            wanted = set(chunk)
            for draft in map(_draft, rows):
                for turn_id in {draft.turn_id, draft.origin_turn_id} & wanted:
                    found.setdefault(turn_id, []).append(draft)
        return found

    def open_in_chat(self, holder_id: int, conversation_id: int | None) -> Draft | None:
        """The draft still waiting for the owner in this chat (the latest), if any: what a member's revision replaces."""
        row = self._conn.execute(
            f"SELECT {_COLUMNS} FROM email_drafts WHERE holder_id=? AND conversation_id IS ? AND state='draft'"
            " ORDER BY created_at_ms DESC, rowid DESC LIMIT 1", (holder_id, conversation_id)).fetchone()
        return None if row is None else _draft(row)

    def revise(self, draft: Draft, *, send_via: str, to: str, cc: str, subject: str, body: str, by_owner: bool,
               base_digest: str | None = None, turn_id: int | None = None) -> Draft | None:
        """A new version, with its digest, on top of the draft's current one: None when the draft is not open for it
        (sent, sending, discarded) or `base_digest` is not the current version's (a card of an older one). A member's
        revision shows the card on the turn that made it (`turn_id`)."""
        digest = draft_digest(send_via, to, cc, subject, body)
        version, now = draft.version + 1, self._now_ms()
        with self._conn:
            moved = self._conn.execute(
                "UPDATE email_drafts SET version=?, digest=?, send_via=?, to_addr=?, cc_addr=?, subject=?, body=?, state='draft',"
                " error='', updated_at_ms=?, turn_id=COALESCE(?, turn_id) WHERE id=? AND digest=? AND state IN ('draft','failed')",
                (version, digest, send_via, to, cc, subject, body, now, turn_id, draft.id,
                 draft.digest if base_digest is None else base_digest)).rowcount
            if not moved:
                return None
            self._version(draft.id, version, digest, send_via, to, cc, subject, body, by_owner=by_owner)
        return self.get(draft.id)

    def claim_send(self, draft_id: str, digest: str) -> bool:
        """True once: the draft is taken to `sending` if it is a draft of exactly this digest. A double click, a replay
        and a second card find nothing to claim."""
        with self._conn:
            return self._conn.execute(
                "UPDATE email_drafts SET state='sending', error='', updated_at_ms=? WHERE id=? AND digest=? AND state='draft'",
                (self._now_ms(), draft_id, digest)).rowcount > 0

    def release(self, draft_id: str, error: str) -> None:
        """The claim of a send that did not reach the tool (the rules forbid it, there is nowhere to send it, the version
        cannot be carried): the draft is open for the owner again, with why."""
        with self._conn:
            self._conn.execute("UPDATE email_drafts SET state='draft', error=?, updated_at_ms=? WHERE id=? AND state='sending'",
                               (error, self._now_ms(), draft_id))

    def finish(self, draft_id: str, digest: str, *, ok: bool, error: str = "") -> None:
        now = self._now_ms()
        with self._conn:
            if ok:
                self._conn.execute("UPDATE email_drafts SET state='sent', error='', sent_digest=?, sent_at_ms=?, updated_at_ms=?"
                                   " WHERE id=? AND state='sending'", (digest, now, now, draft_id))
            else:
                self._conn.execute("UPDATE email_drafts SET state='failed', error=?, updated_at_ms=? WHERE id=? AND state='sending'",
                                   (error, now, draft_id))

    def discard(self, draft_id: str) -> bool:
        """捨棄: True once. Nothing outside is touched."""
        with self._conn:
            return self._conn.execute(
                "UPDATE email_drafts SET state='discarded', updated_at_ms=? WHERE id=? AND state IN ('draft','failed')",
                (self._now_ms(), draft_id)).rowcount > 0

    def answer_offer(self, draft_id: str, accept: bool, *, turn_id: int | None, conversation_id: int | None) -> bool:
        """The owner's answer to the choice card: yes moves the draft to the colleague's chat (`turn_id` and
        `conversation_id` are where its card is now), no leaves it where it is. True once."""
        now = self._now_ms()
        with self._conn:
            if accept:
                return self._conn.execute(
                    "UPDATE email_drafts SET holder_id=offer_to, turn_id=?, conversation_id=?, offer_state='', updated_at_ms=?"
                    " WHERE id=? AND offer_state='pending' AND state='draft'", (turn_id, conversation_id, now, draft_id)).rowcount > 0
            return self._conn.execute(
                "UPDATE email_drafts SET offer_state='declined', updated_at_ms=? WHERE id=? AND offer_state='pending'",
                (now, draft_id)).rowcount > 0


class EmailDrafts:
    """The drafts' flow. `plugins` are the connections whose tools send the mail, `gate` the one gate, `history` and
    `conversations` where the card of a handed draft is shown. The agent sets `approve` (the owner's click on a version
    is the yes of the gate: it opens and answers the question there), `ledger` (what a screening of the rules is paid
    into), `names` (a member's name) and `colleague` (the member a name or id stands for)."""

    def __init__(self, conn: sqlite3.Connection, plugins: PluginHub | None, gate: ToolGate, *,
                 notify: Callable[[str, dict], None] | None = None, now_ms: Callable[[], int] = wall_clock_ms) -> None:
        from .conversations import Conversations

        self.store = DraftStore(conn, now_ms)
        self._conn, self._plugins, self._gate, self._notify, self._now_ms = conn, plugins, gate, notify, now_ms
        self._history = AgentHistory(conn, now_ms)
        self._conversations = Conversations(conn, now_ms)
        self.approve: Callable[[Action, int, str], Approval] | None = None
        self.ledger: Callable[[], "Ledger"] | None = None
        self.names: Callable[[int], str] = lambda member_id: ""
        self.colleague: Callable[[str], int | None] = lambda name: None

    def register(self, router) -> None:
        router.register("integrations.draft_save", self._save)
        router.register("integrations.draft_send", self._send)
        router.register("integrations.draft_discard", self._discard)
        router.register("integrations.draft_handover", self._handover)

    # --- the apps that send mail ---------------------------------------------------------------------------

    def senders(self) -> list[PluginTool]:
        """The tools of the owner's connections that send mail, as they are now (on, of a connection that is on)."""
        if self._plugins is None:
            return []
        try:
            return [tool for tool in self._plugins.offered() if is_mail_sender(tool)]
        except Exception as exc:  # a connection that breaks leaves its tools out
            logger.warning("Could not list the tools that send mail: %s", type(exc).__name__)
            return []

    def sender(self, key: str) -> PluginTool | None:
        return next((tool for tool in self.senders() if tool.key == key), None)

    def choose_sender(self, via: str | None) -> str:
        """The key of the tool that will send a draft: the one the model named (its connection's name or id, or the tool's key),
        else the first there is (a connection called Gmail first). "" when nothing sends mail."""
        found = self.senders()
        if via:
            wanted = via.strip().casefold()
            named = [tool for tool in found if wanted in (tool.key.casefold(), tool.server.casefold(), tool.name.casefold())
                     or tool.key.casefold().startswith(f"mcp:{wanted}/")]
            if named:
                return named[0].key
        found.sort(key=lambda tool: (tool.server.casefold() != "gmail", tool.server.casefold(), tool.name))
        return found[0].key if found else ""

    # --- what the agent does --------------------------------------------------------------------------------

    def write(self, *, author_id: int, turn_id: int, conversation_id: int | None, language: str, to: str, cc: str, subject: str,
              body: str, via: str | None, hand_to: str | None, revise: bool) -> tuple[Draft, str]:
        """The draft a member wrote, shown in its chat: a new one, or with `revise` a new version of the draft still waiting
        there. The second value says what the card asks, for the member's note: "" or the name of the colleague it is offered to.
        DraftError("unknown_member") for a colleague nobody is."""
        offer = None
        if hand_to:
            offer = self.colleague(hand_to)
            if offer is None or offer == author_id:
                raise DraftError("unknown_member")
        send_via = self.choose_sender(via)
        existing = self.store.open_in_chat(author_id, conversation_id) if revise else None
        draft = None
        if existing is not None and existing.author_id == author_id:
            draft = self.store.revise(existing, send_via=send_via or existing.send_via, to=to, cc=cc, subject=subject, body=body,
                                      by_owner=False, turn_id=turn_id)
        if draft is None:
            draft = self.store.create(author_id=author_id, turn_id=turn_id, conversation_id=conversation_id, language=language,
                                      send_via=send_via, to=to, cc=cc, subject=subject, body=body, offer_to=offer)
        self._changed(draft)
        return draft, self.names(offer) if offer is not None and draft.offer_state == "pending" else ""

    def views(self, turn_ids: Iterable[int]) -> dict[int, list[dict]]:
        """What each turn shows of the drafts: the full card, or for the first chat of a draft handed to a colleague its line."""
        found = self.store.shown_in(turn_ids)
        if not found:
            return {}  # most pages of a chat have no draft: the connections are not listed for them
        senders = [{"key": tool.key, "name": tool.server} for tool in self.senders()]
        shown: dict[int, list[dict]] = {}
        for turn_id, drafts in found.items():
            for draft in drafts:
                if draft.turn_id == turn_id:
                    shown.setdefault(turn_id, []).append(self._card(draft, senders))
                elif draft.holder_id != draft.author_id:  # handed on: the first chat keeps one line, the card is elsewhere
                    shown.setdefault(turn_id, []).append({
                        "id": draft.id, "mode": "handed", "state": draft.state, "holder_id": draft.holder_id,
                        "holder_name": self.names(draft.holder_id)})
        return shown

    def _card(self, draft: Draft, senders: list[dict]) -> dict:
        names = {sender["key"]: sender["name"] for sender in senders}
        offer = None
        if draft.offer_state == "pending" and draft.offer_to is not None:
            offer = {"member_id": draft.offer_to, "name": self.names(draft.offer_to)}
        # Who wrote it, when it was handed on: the colleague's card says whose draft it is.
        author = self.names(draft.author_id) if draft.holder_id != draft.author_id else ""
        return {"id": draft.id, "mode": "card", "state": draft.state, "version": draft.version, "digest": draft.digest,
                "from": {"key": draft.send_via, "name": names.get(draft.send_via, "")}, "senders": senders, "to": draft.to,
                "cc": draft.cc, "subject": draft.subject, "body": draft.body, "offer": offer,
                "error": draft.error or None, "holder_id": draft.holder_id, "author_name": author}

    # --- the card's methods -----------------------------------------------------------------------------------

    def _of(self, params: dict) -> Draft:
        draft_id = params.get("id")
        draft = self.store.get(draft_id) if isinstance(draft_id, str) and draft_id else None
        if draft is None or self._conn.execute("SELECT 1 FROM agent_turns WHERE id=?", (draft.turn_id,)).fetchone() is None:
            raise invalid_params("There is no such draft")
        return draft

    def _view(self, draft_id: str) -> dict | None:
        draft = self.store.get(draft_id)
        return None if draft is None else self._card(draft, [{"key": t.key, "name": t.server} for t in self.senders()])

    async def _save(self, params: dict) -> dict:
        """The owner's edit: a new version when something changed, on top of the version the card shows."""
        draft = self._of(params)
        base = params.get("digest")
        if not isinstance(base, str):
            raise invalid_params("digest must be text")
        fields = {name: clean_field(name, params[name]) if name in params else getattr(draft, name)
                  for name in ("to", "cc", "subject", "body")}
        via = params.get("via", draft.send_via)
        if not isinstance(via, str) or (via and self.sender(via) is None and via != draft.send_via):
            raise invalid_params("via must be an app that sends mail")
        if draft.state not in ("draft", "failed") or base != draft.digest:
            return {"draft": self._view(draft.id), "saved": False}
        unchanged = fields == {"to": draft.to, "cc": draft.cc, "subject": draft.subject, "body": draft.body} and via == draft.send_via
        if unchanged:
            return {"draft": self._view(draft.id), "saved": False}
        revised = self.store.revise(draft, send_via=via, by_owner=True, base_digest=base, **fields)
        if revised is not None:
            self._changed(revised)
        return {"draft": self._view(draft.id), "saved": revised is not None}

    async def _discard(self, params: dict) -> dict:
        """捨棄: the draft is dropped and nothing outside changes (no call, no gate, no receipt)."""
        draft = self._of(params)
        done = self.store.discard(draft.id)
        if done:
            self._changed(self.store.get(draft.id) or draft)
        return {"draft": self._view(draft.id), "discarded": done}

    async def _handover(self, params: dict) -> dict:
        """The owner's answer to 「這封回信可以交給…寄嗎？」. Yes shows the same version, editable, in the colleague's private chat;
        it never sends."""
        draft = self._of(params)
        choice = params.get("choice")
        if choice not in ("yes", "no"):
            raise invalid_params("choice must be yes or no")
        if draft.offer_state != "pending" or draft.offer_to is None or draft.state != "draft":
            return {"draft": self._view(draft.id), "handed": False}
        if choice == "no":
            done = self.store.answer_offer(draft.id, False, turn_id=None, conversation_id=None)
            self._changed(self.store.get(draft.id) or draft)
            return {"draft": self._view(draft.id), "handed": False, "declined": done}
        # The colleague's own chat: a turn there carries the card, written before the draft moves.
        colleague = draft.offer_to
        conversation = self._conversations.main(colleague)
        subject = draft.subject or ""
        turn_id = self._history.add(
            session_id=f"draft-{draft.id}-{draft.version}", question="", route="draft", prefix="",
            text=_RECEIVED[draft.language if draft.language in _RECEIVED else "en"].format(
                name=self.names(draft.author_id), subject=subject),
            status="done", source="draft", member_id=colleague, conversation_id=conversation)
        if not self.store.answer_offer(draft.id, True, turn_id=turn_id, conversation_id=conversation):
            self._history.delete(turn_id)  # another click got there first: the card it made is not needed
            return {"draft": self._view(draft.id), "handed": False}
        moved = self.store.get(draft.id)
        assert moved is not None
        self._changed(moved)
        self._notify_turn(draft.origin_turn_id, draft.author_id)
        return {"draft": self._view(draft.id), "handed": True}

    async def _send(self, params: dict) -> dict:
        """傳送: the version whose digest the card shows, once, through the one gate. An old digest, a draft that is not
        open (sent, being sent, discarded) and a second click send nothing."""
        draft = self._of(params)
        digest = params.get("digest")
        if not isinstance(digest, str):
            raise invalid_params("digest must be text")
        if digest != draft.digest or draft.state != "draft":
            return {"draft": self._view(draft.id), "sent": False}
        tool = self.sender(draft.send_via)
        if tool is None:
            return await self._refused(draft, "no_sender")
        try:
            arguments = mail_arguments(tool, draft.to, draft.cc, draft.subject, draft.body)
        except DraftError as error:
            return await self._refused(draft, error.kind)
        if not self.store.claim_send(draft.id, digest):
            return {"draft": self._view(draft.id), "sent": False}  # claimed already: nobody sends it twice
        self._changed(self.store.get(draft.id) or draft)
        language = draft.language
        # The owner's own rules: a Never refuses it. (An Ask first is answered by the click: this is the owner, at the
        # card of that very version, which is what the question would have asked.)
        ledger = self.ledger() if self.ledger is not None else None
        screening = await self._gate.screen(
            Proposed(f"{tool.server} / {tool.name}", tool.description, summarize(arguments), narrow_scope(tool.key)),
            language=language, ledger=ledger)
        if screening.verdict == DENY:
            self._gate.record_unrun(tool.key, outcome="blocked")
            self.store.release(draft.id, "forbidden")
            self._changed(self.store.get(draft.id) or draft)
            return {"draft": self._view(draft.id), "sent": False}
        action = Action(tool.key, arguments)
        assert self.approve is not None, "the agent gives the drafts the door of the gate"
        approval = self.approve(action, draft.turn_id, language)
        result = await self._gate.call_plugin(tool.key, arguments, approval=approval, action=action)
        self.store.finish(draft.id, digest, ok=result.ok, error="" if result.ok else (result.kind or "error"))
        after = self.store.get(draft.id) or draft
        self._changed(after)
        return {"draft": self._view(draft.id), "sent": result.ok}

    async def _refused(self, draft: Draft, kind: str) -> dict:
        """A send that cannot be made as the card stands (no app sends it, a recipient is no address...): the draft stays
        open, and says why."""
        with self._conn:
            self._conn.execute("UPDATE email_drafts SET error=?, updated_at_ms=? WHERE id=? AND state='draft'",
                               (kind, self._now_ms(), draft.id))
        self._changed(self.store.get(draft.id) or draft)
        return {"draft": self._view(draft.id), "sent": False}

    # --- telling the window --------------------------------------------------------------------------------------

    def _changed(self, draft: Draft) -> None:
        self._notify_turn(draft.turn_id, draft.holder_id)
        if draft.origin_turn_id != draft.turn_id:
            self._notify_turn(draft.origin_turn_id, draft.author_id)

    def _notify_turn(self, turn_id: int, member_id: int) -> None:
        if self._notify is not None:
            self._notify("agent.turn", {"turn_id": turn_id, "member_id": member_id})


# What a colleague's chat says (to the model, never as a bubble) when a draft was handed to it.
_RECEIVED: Mapping[str, str] = {
    "zh-TW": "{name}把一封郵件草稿「{subject}」交給你，等主人編輯後寄出。",
    "en": "{name} handed you an email draft, \"{subject}\", for the owner to edit and send.",
    "ja": "{name}からメールの下書き「{subject}」が届きました。オーナーが編集して送信します。",
}
