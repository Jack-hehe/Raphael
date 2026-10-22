"""What a step on the owner's desktop may do by itself, what needs a yes, and what is the owner's.

Three rules, each a pure function of what the model asked for and of what the latest snapshot said
about the element it named (`snapshot.Element`):

- **Hand-off** (`HANDOFF`): a password field, a field of a payment form (a card number, a security
  code), a one-time code, a sign-in button, a payment button. Raphael never touches those: the run
  stops and the owner is told the step is theirs.
- **Confirm** (`CONFIRM`, T2): a step that sends, submits, publishes, deletes, buys or orders. The
  model declares it (`final` on `computer_act`); the element's name, a key chord that sends (Ctrl+
  Enter) or deletes for good (Shift+Delete), and Enter in a message box say it too. Any one of them
  is enough: the owner is asked every time.
- **Keys** (`check_keys`): the chords a step may press, from an allowlist. No Windows key at all
  (Win+L, Win+R...), nothing that switches or closes windows (Alt+Tab, Alt+F4), nothing of the
  system's (Ctrl+Alt+Del, Ctrl+Shift+Esc, Ctrl+Esc), and no bare letters: typing is `type`. The
  shell checks the same list again before it presses anything.

The words are matched in English, Traditional Chinese and Japanese, as the apps label their
buttons; English as whole words, Chinese and Japanese as they are written. A guess that is too
careful costs a question; one that is too loose would cost what cannot be undone, so the lists lean
to the careful side.

Nothing here logs a name, a key or a text.
"""

import re
import unicodedata
from collections.abc import Iterable

HANDOFF = "handoff"
CONFIRM = "confirm"

# The words that make an element a step of the owner's: signing in, paying, a payment form's
# fields, one-time codes. (Password fields are told by UI Automation itself, not by their names.)
_HANDOFF_EN = (
    "sign in", "signin", "log in", "login", "log on", "logon",
    "card number", "credit card", "debit card", "cvv", "cvc", "security code", "expiry",
    "expiration", "pay", "pay now", "payment", "checkout", "check out", "one-time code",
    "verification code", "otp", "2fa", "passcode", "password", "passwd",
)  # fmt: skip
_HANDOFF_CJK = (
    "登入", "登錄", "登录", "ログイン", "サインイン",
    "卡號", "信用卡", "金融卡", "安全碼", "有效期限", "カード番号", "セキュリティコード", "有効期限",
    "付款", "支付", "結帳", "结账", "支払", "決済", "お支払い",
    "驗證碼", "验证码", "認証コード", "確認コード", "密碼", "パスワード",
)  # fmt: skip

# The words that make a step one the owner says yes to first: it sends, submits, publishes,
# deletes, buys or orders.
_CONFIRM_EN = (
    "send", "send now", "submit", "post", "publish", "share", "tweet", "reply all", "forward",
    "delete", "remove", "erase", "discard", "uninstall", "empty recycle bin", "permanently",
    "buy", "buy now", "purchase", "order", "place order", "subscribe", "confirm", "sign",
    "transfer", "accept", "approve", "invite", "upload",
)  # fmt: skip
_CONFIRM_CJK = (
    "傳送", "發送", "送出", "寄出", "提交", "發布", "發佈", "发布", "公開", "分享", "轉寄", "回覆全部",
    "刪除", "删除", "移除", "永久", "解除安裝", "清空資源回收筒",
    "購買", "购买", "下單", "訂購", "訂閱", "確認", "簽署", "轉帳", "上傳", "接受", "核准", "邀請",
    "送信", "投稿", "公開する", "削除", "アンインストール", "購入", "注文", "購読", "確定",
    "送金", "アップロード", "承認", "招待", "共有", "転送", "全員に返信",
)  # fmt: skip

# What a box for a message is called: Enter in it sends.
_MESSAGE_EN = ("message", "chat", "reply", "comment", "post", "tweet", "compose", "write a")
_MESSAGE_CJK = ("訊息", "消息", "聊天", "回覆", "留言", "貼文", "メッセージ", "チャット", "返信", "コメント")
_TEXT_ROLES = frozenset({"Edit", "Document"})


def _normal(text: str) -> str:
    """`text` the way it is matched: compatibility forms folded (full-width letters), lower case,
    each run of spaces, underscores and hyphens one space."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    return " ".join(re.split(r"[\s_\-]+", folded)).strip()


def _english(words: Iterable[str]) -> re.Pattern[str]:
    alternatives = "|".join(re.escape(word) for word in sorted(words, key=len, reverse=True))
    return re.compile(rf"(?<![a-z0-9])(?:{alternatives})(?![a-z0-9])")


_HANDOFF_WORDS = _english(_HANDOFF_EN)
_CONFIRM_WORDS = _english(_CONFIRM_EN)
_MESSAGE_WORDS = _english(_MESSAGE_EN)


def _names(name: str, words: re.Pattern[str], cjk: Iterable[str]) -> bool:
    text = _normal(name)
    return bool(words.search(text)) or any(word in text for word in cjk)


def handoff_by_name(name: str) -> bool:
    """Whether an element called `name` is a step of the owner's: a sign-in, a payment, a field of
    a payment form or a one-time code."""
    return _names(name, _HANDOFF_WORDS, _HANDOFF_CJK)


def confirm_by_name(name: str) -> bool:
    """Whether an element called `name` sends, submits, publishes, deletes, buys or orders."""
    return _names(name, _CONFIRM_WORDS, _CONFIRM_CJK)


def is_message_box(name: str, role: str) -> bool:
    """Whether an element is a box a message is written in: Enter in it sends."""
    return role in _TEXT_ROLES and _names(name, _MESSAGE_WORDS, _MESSAGE_CJK)


# --- the keys a step may press ---------------------------------------------------------------------

MODIFIERS = ("ctrl", "shift", "alt")
_NAMED = frozenset(
    {
        "enter", "tab", "escape", "backspace", "delete", "space", "insert",
        "up", "down", "left", "right", "home", "end", "pageup", "pagedown",
        *(f"f{n}" for n in range(1, 13)),
    }
)  # fmt: skip
_ALIASES = {
    "control": "ctrl", "ctl": "ctrl", "esc": "escape", "return": "enter", "del": "delete",
    "bksp": "backspace", "pgup": "pageup", "pgdn": "pagedown", "page_up": "pageup",
    "page_down": "pagedown", "arrowup": "up", "arrowdown": "down", "arrowleft": "left",
    "arrowright": "right", "option": "alt",
}  # fmt: skip
_WINDOWS_KEYS = frozenset({"win", "windows", "meta", "super", "cmd", "command", "lwin", "rwin"})
# Keys that, with Alt or Ctrl among the modifiers, switch or close windows or open the system's own
# (Alt+Tab, Alt+F4, Alt+Space, Ctrl+Esc, Ctrl+Shift+Esc, Ctrl+F4): never pressed.
_SYSTEM_WITH = {"tab": {"alt"}, "f4": {"alt", "ctrl"}, "escape": {"alt", "ctrl"}, "space": {"alt"}}
# Chords that send (Ctrl+Enter in a mail or a chat) or delete for good (Shift+Delete).
_CONFIRM_CHORDS = frozenset(
    {("ctrl", "enter"), ("ctrl", "shift", "enter"), ("alt", "s"), ("shift", "delete")}
)
MAX_CHORD_CHARS = 40


class KeysRefused(ValueError):
    """A chord that may not be pressed. `why` is one word: "bad" (not a chord), "windows" (the
    Windows key), "system" (a chord that switches or closes windows, or is the system's), "typing"
    (letters with no modifier or with Shift alone, which `type` is for)."""

    def __init__(self, why: str) -> None:
        super().__init__(why)
        self.why = why


def parse_chord(text: object) -> tuple[str, ...]:
    """The chord `text` ("ctrl+s", "Shift+Tab", "enter") as its modifiers, in the order of
    `MODIFIERS`, and its key, all in lower case: ("ctrl", "s"). KeysRefused when it is not one, or
    is one that may not be pressed (`check_keys`)."""
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_CHORD_CHARS:
        raise KeysRefused("bad")
    parts = [_ALIASES.get(part, part) for part in (p.strip().casefold() for p in text.split("+"))]
    if any(part in _WINDOWS_KEYS for part in parts):
        raise KeysRefused("windows")
    if not parts or any(not part for part in parts):
        raise KeysRefused("bad")
    *mods, key = parts
    if key in MODIFIERS or len(set(mods)) != len(mods) or any(m not in MODIFIERS for m in mods):
        raise KeysRefused("bad")
    if not (key in _NAMED or re.fullmatch(r"[a-z0-9]", key)):
        raise KeysRefused("bad")
    chord = (*sorted(mods, key=MODIFIERS.index), key)
    check_keys(chord)
    return chord


def check_keys(chord: tuple[str, ...]) -> None:
    """KeysRefused for a chord that may not be pressed (see the module). Letters and digits need
    Ctrl or Alt; Ctrl and Alt together are never pressed (they are AltGr, or the system's)."""
    *mods, key = chord
    if set(mods) & _SYSTEM_WITH.get(key, set()) or ("ctrl" in mods and "alt" in mods):
        raise KeysRefused("system")
    if len(key) == 1 and not ({"ctrl", "alt"} & set(mods)):
        raise KeysRefused("typing")


def chord_text(chord: tuple[str, ...]) -> str:
    """A chord as the shell takes it and the owner reads it: "Ctrl+Shift+Tab"."""
    return "+".join(part.upper() if len(part) == 1 else part.capitalize() for part in chord)


def confirm_by_keys(chord: tuple[str, ...]) -> bool:
    """Whether pressing `chord` sends or deletes for good."""
    return tuple(chord) in _CONFIRM_CHORDS


# --- one step, judged ------------------------------------------------------------------------------


def judge(
    *,
    action: str,
    declared_final: bool,
    name: str = "",
    role: str = "",
    password: bool = False,
    chord: tuple[str, ...] | None = None,
    focused_name: str = "",
    focused_role: str = "",
    focused_password: bool = False,
) -> str | None:
    """What a step needs before it is taken: `HANDOFF` (the owner's), `CONFIRM` (a yes first) or
    None (it runs).

    `action` is the step ("click", "type", "keys", "scroll", "focus"), `declared_final` whether the
    model said it sends, submits, deletes or buys. `name`, `role` and `password` are what the latest
    snapshot said about the element it acts on (empty for a step on no element); the `focused_*`
    ones about the element that has the keyboard focus, which keys and typing reach when no element
    is named.
    """
    if password or (action in ("type", "keys") and focused_password and not name):
        return HANDOFF
    if action in ("click", "type") and name and handoff_by_name(name):
        return HANDOFF
    if declared_final:
        return CONFIRM
    if action == "click" and name and confirm_by_name(name):
        return CONFIRM
    if action == "keys" and chord is not None:
        if confirm_by_keys(chord):
            return CONFIRM
        if chord == ("enter",):
            box_name, box_role = (name, role) if name else (focused_name, focused_role)
            if is_message_box(box_name, box_role):
                return CONFIRM
    return None
