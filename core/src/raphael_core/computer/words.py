"""The words of a run that are not lines of Raphael's: the three answers to the question about an
app, and the step the overlay shows beside the emblem while he operates.

The lines themselves (the questions, 《了。已停止。》, 《告。這一步需要主人親自操作。》...) are
`agent.persona` phrases, under the rules every fixed line follows. These are labels and fragments:
a button says 「允許一次」, and the overlay says 《告。操作中：點擊「七」》, whose 「點擊「七」」 is
made here. What an element is called comes from the app: it is cleaned and cut before it is put
in (`target`), and it is shown on the overlay only, never stored.
"""

from ..agent.persona import PREFIXES
from .snapshot import clean_text

# The three answers to 《問。是否允許操作「Excel」？》, in the order of the overlay: not, always, once.
ANSWERS = {
    "zh-TW": {"once": "允許一次", "always": "一律允許", "deny": "不要"},
    "en": {"once": "Allow once", "always": "Always allow", "deny": "Deny"},
    "ja": {"once": "今回のみ許可", "always": "常に許可", "deny": "許可しない"},
}

# 「操作中：{step}」, and each step. `{target}` is an element's name, `{app}` an app's, `{keys}` a
# chord. Typing never shows what is typed.
STEPS = {
    "zh-TW": {
        "line": "操作中：{step}",
        "apps": "查看開啟的視窗",
        "open": "開啟「{app}」",
        "look": "查看「{app}」",
        "click": "點擊「{target}」",
        "type": "輸入文字",
        "keys": "按下 {keys}",
        "scroll": "捲動",
        "focus": "移到「{target}」",
        "start": "開始",
    },
    "en": {
        "line": "Operating: {step}",
        "apps": "listing the windows",
        "open": "opening {app}",
        "look": "reading {app}",
        "click": "clicking {target}",
        "type": "typing",
        "keys": "pressing {keys}",
        "scroll": "scrolling",
        "focus": "moving to {target}",
        "start": "starting",
    },
    "ja": {
        "line": "操作中：{step}",
        "apps": "ウィンドウを確認",
        "open": "「{app}」を起動",
        "look": "「{app}」を確認",
        "click": "「{target}」をクリック",
        "type": "文字を入力",
        "keys": "{keys} を押下",
        "scroll": "スクロール",
        "focus": "「{target}」へ移動",
        "start": "開始",
    },
}
# An element's name on the overlay and in a question is cut to this many characters.
TARGET_CHARS = 24
# What an element with no name is called there.
_UNNAMED = {"zh-TW": "此項目", "en": "this item", "ja": "この項目"}
# Brackets that would end the 「」 or the 《》 around a name early.
_BRACKETS = str.maketrans({"「": "『", "」": "』", "《": "〈", "》": "〉"})


def target(language: str, name: str) -> str:
    """An element's name as the overlay and a question show it: on one line, without brackets
    that would end the line's own, cut to `TARGET_CHARS`; 「此項目」 for one with no name."""
    text = clean_text(name, TARGET_CHARS).translate(_BRACKETS)
    return text or _UNNAMED[language]


def step_line(language: str, step: str, **fields: str) -> str:
    """The overlay's line for a step, whole: 《告。操作中：點擊「七」》."""
    words = STEPS[language]
    said = words.get(step, words["start"]).format(**fields)
    return wrapped(language, words["line"].format(step=said))


def wrapped(language: str, text: str, which: str = "notice") -> str:
    """`text` as one of Raphael's lines, with the prefix `which` (a field of `persona.Prefixes`):
    《告。…》, or 《Notice. …》 in English, as the shell's own notices are written."""
    prefix = getattr(PREFIXES[language], which)
    return f"《{prefix}. {text}》" if language == "en" else f"《{prefix}。{text}》"


def answers(language: str) -> dict[str, str]:
    """The labels of the three answers to the question about an app."""
    return ANSWERS[language]
