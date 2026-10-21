"""The fixed commands, found with no model.

With no OpenRouter key, with the monthly limit reached, or with no network, the router cannot
run, but the commands need none: "mute", "鎖定螢幕", "open YouTube" can still be done. So the words
of the plain ones are listed here, in Traditional Chinese, English and Japanese, and a request that
is nothing but one of them (with the polite words around it taken off) is that command.

It must miss rather than guess. A command is found only when the whole request is one: a volume
or media or lock phrase on its own, or an open verb and then what to open (any short name: no site
is listed here, the apps, the learned names, the domains and the settings pages find it or do not).
"I want to open a bakery", "play me something by Radiohead" and "open YouTube and search for cats"
are left to the models, and fail with the reason they could not run. Copying is never found here:
it changes state and asks first.
"""

import re
import unicodedata

from .opening import FILLERS, PREFIXES, SUFFIXES, clean_target
from .routing import RouteDecision

# Longer than this is not a bare command.
_MAX_CHARS = 60
# What is to be opened is named in at most this many words (English) or characters.
_MAX_TARGET_WORDS = 3
_MAX_TARGET_CHARS = 30
_MAX_FORMS = 400

# The polite words (`opening.FILLERS`, `PREFIXES`, `SUFFIXES`) are shared with the finding of the
# target.
_FILLERS = FILLERS
_PREFIXES = PREFIXES
_SUFFIXES = SUFFIXES
_OPEN_EN = ("open", "launch")
_OPEN_ZH = (
    "打開一下", "開啟一下", "開一下", "打開", "打开", "開啟", "开启", "啟動", "启动",
)  # fmt: skip
_OPEN_JA = ("開いて", "開く", "開け", "起動して", "起動", "立ち上げて", "ひらいて")
# What follows the verb in Japanese ("開いてください"), apart from the verb itself.
_JA_POLITE_ENDINGS = ("ください", "下さい", "お願いします", "お願い")
# Words that join a second request to the first: more than a name to open.
_CONNECTORS = ("然後", "然后", "並且", "并且", "接著", "接着", "之後", "之后", "再", "そして", "それから")
# Words that ask for a search: "open YouTube and search for cats" is for the models.
_SEARCH_ZH = ("搜尋", "搜索", "搜寻", "検索")
_SEARCH_EN = ("search", "find", "look")
# A "開" that opens something: followed by the name of an app or a site in Latin letters.
_OPEN_ZH_SHORT = re.compile(r"開\s*(?=[a-z0-9])")
# Punctuation that is part of a name when it has letters on both sides: "reddit.com",
# "github.com/notifications", "wi-fi", "https://example.org".
_INNER = {".": "\ue000", ":": "\ue001", "/": "\ue002", "-": "\ue003"}
_INNER_PUNCTUATION = re.compile(r"(?<=\w)[.:/-](?=\w)")
_NOT_WORD = re.compile(r"[^\w\s\ue000-\ue004]|_")


def _normalize(text: str) -> str:
    """`text` in lower case with full-width letters made plain, punctuation taken out (but not
    the dots, slashes and hyphens inside a name) and the words one space apart."""
    folded = unicodedata.normalize("NFKC", text).casefold().replace("://", "\ue004")
    folded = _INNER_PUNCTUATION.sub(lambda found: _INNER[found.group()], folded)
    kept = " ".join(_NOT_WORD.sub(" ", folded).split())
    for plain, mask in _INNER.items():
        kept = kept.replace(mask, plain)
    return kept.replace("\ue004", "://")


def _compact(text: str) -> str:
    """`text` without the English polite words and without spaces."""
    return "".join(word for word in text.split() if word not in _FILLERS)


def _forms(text: str) -> set[str]:
    """Every way `text` reads once its polite words are taken off: itself (compact) and what is
    left when any of the Chinese prefixes and Chinese or Japanese suffixes at its ends are taken off,
    one after another. A form is never empty."""
    first = _compact(text)
    forms, pending = {first}, [first]
    while pending and len(forms) < _MAX_FORMS:
        form = pending.pop()
        shorter = [form[len(p) :] for p in _PREFIXES if form.startswith(p)]
        shorter += [form[: -len(s)] for s in _SUFFIXES if form.endswith(s)]
        for candidate in shorter:
            if candidate and candidate not in forms:
                forms.add(candidate)
                pending.append(candidate)
    return forms


def _table(phrases_of: dict[str, tuple[str, ...]]) -> dict[str, str]:
    table: dict[str, str] = {}
    for command, phrases in phrases_of.items():
        for phrase in phrases:
            key = _compact(_normalize(phrase))
            if table.setdefault(key, command) != command:
                raise ValueError(f"The phrase {phrase!r} is for two commands")
    return table


# A request that is one of these, whole, is the command.
_STANDALONE = _table(
    {
        "volume_up": (
            "volume up", "turn the volume up", "turn up the volume", "raise the volume",
            "increase the volume", "louder", "turn it up", "make it louder",
            "音量調大", "音量调大", "音量調大一點", "音量大一點", "音量大一点", "調高音量",
            "调高音量", "加大音量", "增加音量", "提高音量", "大聲一點", "大声一点", "大聲點",
            "聲音大一點", "声音大一点", "聲音調大", "音量を上げて", "音量アップ",
            "音量を大きくして",
        ),
        "volume_down": (
            "volume down", "turn the volume down", "turn down the volume", "lower the volume",
            "decrease the volume", "quieter", "turn it down", "make it quieter",
            "音量調小", "音量调小", "音量調小一點", "音量小一點", "音量小一点", "調低音量",
            "调低音量", "降低音量", "減小音量", "小聲一點", "小声一点", "小聲點", "聲音小一點",
            "声音小一点", "聲音調小", "音量を下げて", "音量ダウン", "音量を小さくして",
        ),
        "volume_mute": (
            "mute", "unmute", "mute the sound", "mute the volume", "mute the audio",
            "靜音", "静音", "關掉聲音", "关掉声音", "關閉聲音", "消音", "ミュート",
        ),
        "media_play_pause": (
            "pause", "resume", "play", "pause the music", "pause the video", "pause playback",
            "resume playback", "resume the music", "play the music", "play music",
            "暫停", "暂停", "暫停播放", "暂停播放", "暫停音樂", "暂停音乐", "繼續播放",
            "继续播放", "播放", "播放音樂", "播放音乐", "一時停止", "再生", "再開",
        ),
        "media_next": (
            "next track", "next song", "skip", "skip song", "skip this song", "skip the song",
            "skip track", "下一首", "下一曲", "換下一首", "换下一首", "跳過", "跳过", "次の曲",
            "次へ", "スキップ",
        ),
        "media_previous": (
            "previous", "previous track", "previous song", "last song", "上一首", "上一曲",
            "前一首", "前の曲", "前へ",
        ),
        "lock_screen": (
            "lock the screen", "lock screen", "lock my computer", "lock the computer",
            "lock my pc", "lock the pc", "lock it", "鎖定螢幕", "鎖定屏幕", "鎖屏", "锁屏",
            "鎖定電腦", "鎖定电脑", "鎖螢幕", "画面をロック", "ロック画面", "パソコンをロック",
            "pcをロック",
        ),
    }
)  # fmt: skip

def _strip_prefixes(text: str) -> str:
    changed = True
    while changed:
        changed = False
        for prefix in _PREFIXES:
            if text.startswith(prefix) and len(text) > len(prefix):
                text, changed = text[len(prefix) :].lstrip(), True
    return text


def _open_target(spoken: str) -> str | None:
    """What `spoken` (normalized) asks to open, if it starts with an open verb (English, Chinese)
    or ends with one (Japanese); None if it does not."""
    words = spoken.split()
    while words and words[0] in _FILLERS:  # "could you open ..."
        words = words[1:]
    if words and words[0] in _OPEN_EN:
        return " ".join(words[1:])
    head = _strip_prefixes(" ".join(words))
    for verb in _OPEN_ZH:
        if head.startswith(verb):
            return head[len(verb) :].strip()
    short = _OPEN_ZH_SHORT.match(head)
    if short:
        return head[short.end() :].strip()
    tail = spoken
    for ending in _JA_POLITE_ENDINGS:
        tail = tail.removesuffix(ending).rstrip()
    for verb in _OPEN_JA:
        if tail.endswith(verb):
            return tail[: -len(verb)].rstrip().removesuffix("を").removesuffix("は").strip()
    return None


def _is_a_name(target: str) -> bool:
    """Whether `target` can be the name of something to open: short, and not two requests in one
    (a second thing to do, or a search)."""
    words = target.split()
    if not 0 < len(words) <= _MAX_TARGET_WORDS or "and" in words:
        return False
    if any(word in _SEARCH_EN for word in words):
        return False
    if any(connector in target for connector in (*_CONNECTORS, *_SEARCH_ZH)):
        return False
    return len("".join(words)) <= _MAX_TARGET_CHARS


def extract_target(text: str) -> str | None:
    """What `text` asks to open, as `opening.clean_target` leaves it, when it starts with an open
    verb (English, Chinese) or ends with one (Japanese): "open Reddit" gives "reddit". None when
    it asks to open nothing."""
    target = _open_target(_normalize(text))
    return clean_target(target) or None if target else None


def match_command(text: str) -> RouteDecision | None:
    """The fixed command that `text` is, as a decision, or None when it is not one (or when
    anything else is asked besides)."""
    spoken = _normalize(text)
    if not spoken or len(spoken) > _MAX_CHARS:
        return None
    command = next((_STANDALONE[form] for form in _forms(spoken) if form in _STANDALONE), None)
    if command is not None:
        return RouteDecision(route="command", command=command, source="local")
    target = extract_target(text)
    if target is None or not _is_a_name(target):
        return None
    return RouteDecision(route="command", command="open", target=target, source="local")
