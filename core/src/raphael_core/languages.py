"""The dictation languages the user can choose, with their regional variants.

The `languages` setting is a list of the ids below, in the user's order of priority. One language
means speech-to-text is told which it is; several mean it detects the language itself. Each group
holds variants of one language (its Chinese written forms, its kinds of English); at most one
variant of a group may be chosen, since they would contradict each other in how the text is
written.

This is plain data shared by the brain and the device side; it calls nothing.
"""

from collections.abc import Sequence
from typing import Any

MAX_LANGUAGES = 5
DEFAULT_LANGUAGES: tuple[str, ...] = ("zh-TW",)

# (group id, group name, ((variant id, name, note), ...)). Names are in the language's own form;
# the Chinese ones are in Traditional characters, as the rest of the app's copy is. A note says how
# a variant is written when variants of one language differ in spelling, script or usage.
_GROUPS: tuple[tuple[str, str, tuple[tuple[str, str, str], ...]], ...] = (
    (
        "zh",
        "中文",
        (
            ("zh-TW", "繁體中文（台灣）", "台灣用語，例如 軟體、視窗"),
            ("zh-HK", "繁體中文（香港）", "香港用語，例如 軟件、數據"),
            ("zh-CN", "簡體中文（中國）", "簡體字、大陸用語，例如 软件、数据"),
            ("yue-HK", "粵語（香港）", "書面粵語，保留 嘅、咗、唔"),
        ),
    ),
    (
        "en",
        "English",
        (
            ("en-US", "English (United States)", "color, center"),
            ("en-GB", "English (United Kingdom)", "colour, centre"),
            ("en-AU", "English (Australia)", "colour, centre"),
            ("en-IN", "English (India)", "colour, centre"),
        ),
    ),
    ("ja", "日本語", (("ja-JP", "日本語", ""),)),
    ("ko", "한국어", (("ko-KR", "한국어", ""),)),
    ("fr", "Français", (("fr-FR", "Français", ""),)),
    ("de", "Deutsch", (("de-DE", "Deutsch", ""),)),
    (
        "es",
        "Español",
        (
            ("es-ES", "Español (España)", "ordenador, vosotros"),
            ("es-MX", "Español (México)", "computadora, ustedes"),
        ),
    ),
    ("it", "Italiano", (("it-IT", "Italiano", ""),)),
    ("pt", "Português", (("pt-BR", "Português (Brasil)", ""),)),
    ("vi", "Tiếng Việt", (("vi-VN", "Tiếng Việt", ""),)),
    ("th", "ไทย", (("th-TH", "ไทย", ""),)),
    ("id", "Bahasa Indonesia", (("id-ID", "Bahasa Indonesia", ""),)),
)

LANGUAGE_IDS: tuple[str, ...] = tuple(v[0] for _, _, variants in _GROUPS for v in variants)
_GROUP_OF = {v[0]: group for group, _, variants in _GROUPS for v in variants}
_NAME_OF = {v[0]: v[1] for _, _, variants in _GROUPS for v in variants}

# The Chinese written forms: the text is converted to the script of the one chosen.
CHINESE_VARIANTS = ("zh-TW", "zh-HK", "zh-CN", "yue-HK")

# The code speech-to-text is told for each id: ISO 639-1, except Cantonese, which has none.
_ISO_CODE = {language_id: language_id.split("-")[0] for language_id in LANGUAGE_IDS}
STT_CODES: tuple[str, ...] = tuple(dict.fromkeys(_ISO_CODE.values()))

# The values of the old `language` setting, and the selection each one becomes.
_FROM_LEGACY = {
    "auto": ["zh-TW", "en-US", "ja-JP"],  # the user mixes these three
    "zh": ["zh-TW"],
    "ja": ["ja-JP"],
    "en": ["en-US"],
}
LEGACY_LANGUAGES = tuple(_FROM_LEGACY)


def checked_languages(key: str, value: Any) -> list[str]:
    """`value` as a new list, if it is a valid selection.

    TypeError when it is not a list of strings; ValueError, naming `key`, when it is empty, holds
    more than MAX_LANGUAGES ids, an id that is not one of LANGUAGE_IDS, an id twice, or two
    variants of one group (two Chinese written forms, two kinds of English or Spanish).
    """
    if type(value) is not list or not all(type(item) is str for item in value):
        raise TypeError(f"Setting {key!r} must be a list of language ids")
    if not value:
        raise ValueError(f"Setting {key!r} needs at least one language")
    if len(value) > MAX_LANGUAGES:
        raise ValueError(f"Setting {key!r} holds at most {MAX_LANGUAGES} languages")
    unknown = [item for item in value if item not in _GROUP_OF]
    if unknown:
        raise ValueError(
            f"Setting {key!r}: unknown language {unknown[0]!r}; use {', '.join(LANGUAGE_IDS)}"
        )
    groups = [_GROUP_OF[item] for item in value]
    if len(set(groups)) != len(groups):
        raise ValueError(f"Setting {key!r} may hold only one variant of each language")
    return list(value)


def language_groups() -> dict[str, Any]:
    """What `languages.list` answers: the groups and their variants, in the order shown."""
    return {
        "groups": [
            {
                "id": group,
                "name": name,
                "variants": [
                    {"id": variant, "name": variant_name, "note": note}
                    for variant, variant_name, note in variants
                ],
            }
            for group, name, variants in _GROUPS
        ]
    }


def display_name(language_id: str) -> str:
    """The name of a language as the picker and the cleanup prompt write it."""
    return _NAME_OF[language_id]


def iso_code(language_id: str) -> str:
    """The code speech-to-text knows a language by: "zh" for every Chinese written form, "yue"
    for Cantonese, "en" for every kind of English, and so on."""
    return _ISO_CODE[language_id]


def stt_language(languages: Sequence[str]) -> str:
    """The language hint for speech-to-text: the code of the one language chosen, or "auto"
    when there are several (speech-to-text then detects it)."""
    return iso_code(languages[0]) if len(languages) == 1 else "auto"


def stt_languages(languages: Sequence[str]) -> list[str]:
    """The codes of all the chosen languages, in order, each once."""
    return list(dict.fromkeys(iso_code(language_id) for language_id in languages))


def chinese_variant(languages: Sequence[str]) -> str | None:
    """The Chinese written form chosen (at most one can be), or None."""
    return next((item for item in languages if item in CHINESE_VARIANTS), None)


def from_legacy(language: Any) -> list[str]:
    """The selection that the old `language` setting stands for; the default for anything else."""
    selection = _FROM_LEGACY.get(language) if isinstance(language, str) else None
    return list(selection or DEFAULT_LANGUAGES)


def legacy_language(languages: Sequence[str]) -> str:
    """The old `language` setting as derived from a selection: "zh", "ja" or "en" for one
    Chinese, Japanese or English language, else "auto"."""
    if len(languages) != 1:
        return "auto"
    code = iso_code(languages[0])
    if code == "yue":
        return "zh"
    return code if code in _FROM_LEGACY else "auto"
