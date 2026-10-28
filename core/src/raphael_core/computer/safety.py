"""What a step on the owner's desktop may do by itself, what needs a yes, and what is the owner's.

Four rules, each a pure function of what the model asked for and of what the latest snapshot said
about the element it named (`snapshot.Element`):

- **Refuse** (`REFUSE`): a button that closes a window, a tab or the app (its title bar's Close, a
  tab's ×, File > Exit), clicked or pressed with Enter or Space. Raphael closes nothing, whatever
  the model declares: the step is not taken and the model is told why. The keys that close are
  refused with the others (`check_keys`).

- **Hand-off** (`HANDOFF`): a password field, a field of a payment form (a card number, a security
  code), a one-time code, a sign-in button, a payment button. Raphael never touches those, with a
  click, typed text or keys (which reach the element named, or else the one with the focus): the
  run stops and the owner is told the step is theirs.
- **Confirm** (`CONFIRM`, T2): a step that sends, submits, publishes, deletes, buys or orders. The
  model declares it (`final` on `computer_act`); the element's name (clicked, or pressed with Enter
  or Space, with any modifiers: Shift+Enter presses a button too), a key chord that sends (Ctrl+Enter) or deletes for good (Shift+Delete), a key that
  deletes what is selected away from a text field (`deletes_items`: Delete in File Explorer or a
  mail list), Enter in a message box, Enter or Space where the look knows no focus, and a line
  break in typed text (which the shell presses as Enter) say it too. Any one of them is enough: the
  owner is asked every time.
- **Keys** (`check_keys`): the chords a step may press, from an allowlist. No Windows key at all
  (Win+L, Win+R...), nothing that switches or closes windows, tabs or apps (Alt+Tab, Alt+F4,
  Ctrl+W, Ctrl+Q), nothing of the system's (Ctrl+Alt+Del, Ctrl+Shift+Esc, Ctrl+Esc), and no bare
  letters: typing is `type`. The shell checks the same list again before it presses anything.

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
REFUSE = "refuse"

# The words that make an element a step of the owner's: signing in, paying, a payment form's
# fields, one-time codes. (Password fields are told by UI Automation itself, not by their names.)
# The shell checks the live element with the focus against the same words, word for word
# (`app/src-tauri/src/computer/policy.rs`; a test holds the two together).
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
# deletes, buys or orders. The shell judges the live element with the focus by the same words, and
# by those of a message box below, before it presses Enter or Space there
# (`app/src-tauri/src/computer/policy.rs`; a test holds them together).
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

# What a button that closes a window, a tab or the app is called: a title bar's Close, a tab's
# "Close tab", File > Exit or Quit. The shell judges the live element by the same words, and by
# what UI Automation says of it besides (an automation id of "Close", a button of a title bar)
# (`app/src-tauri/src/computer/policy.rs`; a test holds them together).
_CLOSE_EN = ("close", "exit", "quit")
_CLOSE_CJK = ("關閉", "关闭", "閉じる")


def _normal(text: str) -> str:
    """`text` the way it is matched: compatibility forms folded (full-width letters), lower case,
    each run of spaces, underscores and hyphens one space."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    return " ".join(re.split(r"[\s_\-]+", folded)).strip()


def _english(words: Iterable[str]) -> re.Pattern[str]:
    # The words are matched the way the names are written out (`_normal`): "one-time code" is
    # "one time code" by then.
    normal = sorted({_normal(word) for word in words}, key=len, reverse=True)
    alternatives = "|".join(re.escape(word) for word in normal)
    return re.compile(rf"(?<![a-z0-9])(?:{alternatives})(?![a-z0-9])")


_HANDOFF_WORDS = _english(_HANDOFF_EN)
_CONFIRM_WORDS = _english(_CONFIRM_EN)
_MESSAGE_WORDS = _english(_MESSAGE_EN)
_CLOSE_WORDS = _english(_CLOSE_EN)


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


# The part of the hand-off words that is about paying: the reason a hand-over gives for a card field
# or a payment button, where the rest of the words are about signing in. Only words of the lists above.
_PAYMENT_EN = (
    "card number", "credit card", "debit card", "cvv", "cvc", "security code", "expiry",
    "expiration", "pay", "pay now", "payment", "checkout", "check out",
)  # fmt: skip
_PAYMENT_CJK = (
    "卡號", "信用卡", "金融卡", "安全碼", "有效期限", "カード番号", "セキュリティコード", "有効期限",
    "付款", "支付", "結帳", "结账", "支払", "決済", "お支払い",
)  # fmt: skip
_PAYMENT_WORDS = _english(_PAYMENT_EN)


def handoff_reason(element: object | None, focused: object | None = None) -> str:
    """Why a step is the owner's, as the hand-over names it (`tools.HAND_OVER_REASONS`): "password"
    for a password field, "payment" for a field or a button of a payment form, else "sign_in". The
    element is the one the step names, else the one with the focus, which keys and typing reach."""
    target = element if element is not None else focused
    if target is None:
        return "sign_in"
    if getattr(target, "password", False):
        return "password"
    return "payment" if _names(getattr(target, "name", ""), _PAYMENT_WORDS, _PAYMENT_CJK) else "sign_in"


# What a window that is done with a sign-in or a payment no longer shows: the roles of the elements the
# owner types into or presses.
_FORM_ROLES = frozenset({"Edit", "ComboBox", "Button", "Hyperlink", "Link"})


def blocks(elements: Iterable[object]) -> bool:
    """Whether a look still shows what only the owner may do: a password field, or a field or a button
    of a sign-in or a payment form (a card number, a security code, a code, a Sign in or Pay button)
    that can be used. A window that shows none is one the owner got through."""
    for element in elements:
        if getattr(element, "password", False):
            return True
        if (getattr(element, "role", "") in _FORM_ROLES and getattr(element, "enabled", True)
                and handoff_by_name(getattr(element, "name", ""))):
            return True
    return False


def closes_by_name(name: str) -> bool:
    """Whether an element called `name` closes a window, a tab or the app."""
    return _names(name, _CLOSE_WORDS, _CLOSE_CJK)


def is_message_box(name: str, role: str) -> bool:
    """Whether an element is a box a message is written in: Enter in it sends."""
    return role in _TEXT_ROLES and _names(name, _MESSAGE_WORDS, _MESSAGE_CJK)


def edits_text(role: str, actions: Iterable[str]) -> bool:
    """Whether an element is a field whose text Delete and Backspace only edit: an Edit or a
    Document whose value can be set (`value` among its actions, which the shell gives only for a
    Value pattern that is not read only).

    A Document with no value to set is no such field: a mail's reading pane or a web page, where
    Delete deletes the mail or the item the page has selected. A rich editor that only has the Text
    pattern (a Word document) is not one either, as far as this can tell: Delete there asks first,
    which costs a question and nothing else. The shell tells a text field apart the same way, on
    the live element with the focus."""
    return role in _TEXT_ROLES and "value" in actions


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
# Letters that, with Ctrl (and Shift or not), close a tab, a window or the app: Ctrl+W and
# Ctrl+Shift+W (a browser's tab and window, File Explorer), Ctrl+Q and Ctrl+Shift+Q (quitting).
# Never pressed: Raphael closes nothing. The shell's `CLOSE_LETTERS` (keys.rs), held to this by a
# test.
_CLOSE_LETTERS = ("w", "q")
# Chords that send (Ctrl+Enter in a mail or a chat) or delete for good (Shift+Delete), wherever
# they are pressed.
_CONFIRM_CHORDS = frozenset(
    {("ctrl", "enter"), ("ctrl", "shift", "enter"), ("alt", "s"), ("shift", "delete")}
)
# Ctrl+D deletes the selected file in File Explorer and the selected item in Outlook.
_DELETE_CHORDS = frozenset({("ctrl", "d")})
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
    if key in _CLOSE_LETTERS and "ctrl" in mods:
        raise KeysRefused("system")
    if len(key) == 1 and not ({"ctrl", "alt"} & set(mods)):
        raise KeysRefused("typing")


def chord_text(chord: tuple[str, ...]) -> str:
    """A chord as the shell takes it and the owner reads it: "Ctrl+Shift+Tab"."""
    return "+".join(part.upper() if len(part) == 1 else part.capitalize() for part in chord)


def confirm_by_keys(chord: tuple[str, ...]) -> bool:
    """Whether pressing `chord` sends or deletes for good."""
    return tuple(chord) in _CONFIRM_CHORDS


def deletes_items(chord: tuple[str, ...]) -> bool:
    """Whether pressing `chord` away from a text field may delete what is selected there: Delete
    or Backspace with any modifiers but Alt (a file in File Explorer, a mail in a list, a row, a
    slide; Backspace deletes in some of them too), and Ctrl+D alone (File Explorer, Outlook). In a
    field whose text can be edited they only edit it (`edits_text`). Alt with either is left out:
    it undoes, or opens an app's menu.

    The shell keeps the same rule (`Chord::deletes_items` in `app/src-tauri/src/computer/keys.rs`)
    and refuses such keys on a live focus that is no text field, unless the owner said yes."""
    *mods, key = chord
    if key in ("delete", "backspace"):
        return "alt" not in mods
    return tuple(chord) in _DELETE_CHORDS


# --- one step, judged ------------------------------------------------------------------------------

# What breaks a line, as `str.splitlines` reads it: the shell types a line break as Enter, and an
# app may take a carriage return or a separator typed as a character for one too.
_LINE_BREAK = re.compile(r"[\n\r\x0b\x0c\x1c\x1d\x1e\x85  ]")
# Keys that only move away from the element with the focus: they enter, press and send nothing.
_MOVE_AWAY = frozenset({("tab",), ("shift", "tab"), ("escape",)})


def presses(chord: tuple[str, ...]) -> bool:
    """Whether `chord` may press the button with the focus: Enter or Space, with any modifiers
    (Shift+Enter and Shift+Space press a button as Enter does). The shell keeps the same rule
    (`Chord::presses`)."""
    return chord[-1] in ("enter", "space")


def breaks_lines(text: str) -> bool:
    """Whether typing `text` would break a line, which the shell does with Enter."""
    return bool(_LINE_BREAK.search(text))


def judge(
    *,
    action: str,
    declared_final: bool,
    name: str = "",
    role: str = "",
    password: bool = False,
    actions: Iterable[str] = (),
    chord: tuple[str, ...] | None = None,
    text: str = "",
    focused_name: str = "",
    focused_role: str = "",
    focused_password: bool = False,
    focused_actions: Iterable[str] = (),
    targeted: bool | None = None,
) -> str | None:
    """What a step needs before it is taken: `HANDOFF` (the owner's), `REFUSE` (never taken: it
    closes a window, a tab or the app), `CONFIRM` (a yes first) or None (it runs).

    `action` is the step ("click", "type", "keys", "scroll", "focus"), `declared_final` whether the
    model said it sends, submits, deletes or buys, `chord` the keys it presses and `text` what it
    types. `name`, `role`, `password` and `actions` are what the latest snapshot said about the
    element it acts on (empty for a step on no element); the `focused_*` ones about the element
    that has the keyboard focus, which keys and typing reach when no element is named. `targeted`
    says whether the step names an element; left out, an element is named when it has a name or
    a role (every element of a snapshot has a role, an unnamed search box included: an element
    without a name is still the step's target, never the old focus).

    Keys and typing are judged by the element they reach: the one named (the shell gives it the
    focus first), else the one with the focus. A sign-in, a payment or a code there is the
    owner's whatever the keys, except the ones that only move away from it (Tab, Shift+Tab, Esc);
    Enter or Space (with any modifiers) on a button that sends, deletes or buys asks first, as a
    click on it would, and so does Enter in a message box; on a button that closes, it is refused
    as a click on it is. A key that deletes what is selected (`deletes_items`) asks
    first unless what it reaches is a field whose text it only edits (`edits_text`); when the
    snapshot holds no element with the focus, nothing says it is one. Enter or Space on no element
    named, when the snapshot holds no element with the focus (a window looked at in the
    background, whose focus comes back once it is in front), asks first too: it may press Send. A
    line break in typed text is pressed as Enter, so it asks first wherever it is typed: the field
    may be a chat's, or a form's that Enter submits. (The shell checks the live element with the
    focus once more before it presses anything: it must be the element named, if one is; Enter,
    Space and the chords that send go to nothing called like Send or a message box, and no
    deleting key reaches anything but a text field, without the owner's yes. It types no line
    break that was not asked about.)
    """
    if targeted is None:
        targeted = bool(name or role)
    if targeted:
        target_name, target_role, target_actions = name, role, frozenset(actions)
    else:
        target_name, target_role, target_actions = focused_name, focused_role, frozenset(focused_actions)
    if password or (action in ("type", "keys") and focused_password and not targeted):
        return HANDOFF
    if action == "click" and name and handoff_by_name(name):
        return HANDOFF
    if action == "type" and target_name and handoff_by_name(target_name):
        return HANDOFF
    if action == "keys" and chord not in _MOVE_AWAY and target_name and handoff_by_name(target_name):
        return HANDOFF
    if action == "click" and name and closes_by_name(name):
        return REFUSE
    if action == "keys" and chord is not None and presses(chord) and target_name and closes_by_name(target_name):
        return REFUSE
    if declared_final:
        return CONFIRM
    if action == "click" and name and confirm_by_name(name):
        return CONFIRM
    if action == "type" and breaks_lines(text):
        return CONFIRM
    if action == "keys" and chord is not None:
        if confirm_by_keys(chord):
            return CONFIRM
        if presses(chord) and target_name and confirm_by_name(target_name):
            return CONFIRM
        if presses(chord) and not targeted and not focused_role:
            return CONFIRM
        if chord[-1] == "enter" and is_message_box(target_name, target_role):
            return CONFIRM
        if deletes_items(chord) and not edits_text(target_role, target_actions):
            return CONFIRM
    return None
