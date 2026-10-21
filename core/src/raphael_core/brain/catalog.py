"""`models.catalog`: every OpenRouter model Raphael could use, by kind, with its price and what
each of Raphael's roles can make of it.

The list is OpenRouter's public one (GET /models?output_modalities=all), which pricing.py fetches
and keeps for a day. This module reads it. Two kinds of model are kept:

- `llm`: text in, text out. A model that also writes images or audio is not one.
- `stt`: speech to text, the models whose output is a transcription.

Every other kind (embeddings, images, speech, video, reranking, decisions) is left out: no role can
use one. So is a `:batch` variant, which runs on OpenRouter's batch API, whose answers come within a
day: never in time for anything Raphael does. Jev, which sorts the assistant's requests, is a fixed
model, not a choice.

The roles (`ROLES`), and what each needs of a model, from the calls it makes:

- `cleanup` (the `cleanup_model` setting) and `answer` (`agent_model`: Raphael's answers, his
  routing when Jev fails or is unsure, the app he picks to open, and his web search) call
  `complete_json`. It asks for JSON that fits a schema (`response_format` with a strict
  `json_schema`), with no reasoning (`reasoning.effort: "none"`) and a `max_tokens`, under
  `provider.require_parameters`, which leaves out every provider that does not take all of those.
  So the model must list `response_format`, `structured_outputs`, `reasoning` and `max_tokens`
  among its supported parameters, and must be able to answer without reasoning first: not
  `reasoning.mandatory`, and "none" among its efforts when it lists them. Tool calling is not
  needed: Raphael runs his tools himself and hands the model their results, and the web search
  (`chat_text` with OpenRouter's `web` plugin, which requires no parameter) takes any text model.
  The one thing that wants it is the call that picks a plugin's tools (agent/plugins.py), which
  asks for `tools` and `tool_choice` under the same `provider.require_parameters`: the catalog
  records it as the capability `tool_calling`, so that a request whose answer model lacks it goes
  on without the plugins. No role needs it to choose a model, and no model is refused for lacking
  it.
- `stt` (`stt_cloud_model`) calls `transcribe`, which sends the audio and the language to
  POST /audio/transcriptions: every transcription model takes that. Two of them are also given
  the dictionary (`STT_MODELS_WITH_HINTS`).

A model that a role cannot use is listed all the same, with the reason as a word that the app puts
in plain words ("no_structured_output").

Prices, in US dollars, are each model's headline price, the cheapest endpoint's. What a call costs
depends on the provider OpenRouter picks for it, so they are a guide, not a bill. Tiers (a higher
price for a very long prompt, or a cheaper one for batch use) are ignored: a dictation is short.

- A language model is priced per million tokens: `input_per_mtok` and `output_per_mtok`.
- A speech-to-text model is priced per second of audio, which is shown per minute (`per_minute`),
  or per token like a language model. The list does not say which: a model with a price for the
  tokens it writes (`completion`) is priced per token, any other per second. A per-second price
  over US$1 a minute cannot be right: Microsoft's MAI models are listed by the hour of audio
  instead (MAI-Transcribe 2 at "0.1", its published US$0.10 an hour; MAI-Transcribe 1.5 at
  "0.36"), so such a price is read per hour. One that is too dear even so is left unknown (None)
  instead of shown.
- A router, which picks a model for each request, has no price of its own: the list gives it -1,
  which is `variable_price`. A model whose prices are all 0 is `free`.
"""

import math
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .openrouter import CLEANUP_MODEL_CHOICES, STT_MODELS_WITH_HINTS
from .settings import AGENT_TEXT_MAX_CHARS, DEFAULTS, STT_CLOUD_MODELS

LLM = "llm"
STT = "stt"
KINDS = (LLM, STT)
# The capability of a language model that takes `tools` and `tool_choice` (see the docstring).
TOOL_CALLING = "tool_calling"

# What a minute of speech-to-text can plausibly cost at most, in US dollars. The dearest model
# (Google Chirp 3) is 1.6 cents.
_MAX_PER_MINUTE = 1.0
_PER_MTOK = 1_000_000
_SECONDS_PER_MINUTE = 60
_MINUTES_PER_HOUR = 60
_MS_PER_SECOND = 1000
# The end of the id of a model's variant on OpenRouter's batch API, which is left out.
_BATCH = ":batch"


@dataclass(frozen=True)
class ModelPrice:
    """US dollars; None is unknown or does not apply."""

    per_minute: float | None = None  # of audio, for speech-to-text priced per second
    input_per_mtok: float | None = None  # per million tokens
    output_per_mtok: float | None = None


# --- the roles ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Role:
    """What a model is chosen for."""

    name: str  # "cleanup", "answer" or "stt"
    setting: str  # the setting that holds the chosen model
    kind: str  # the kind of model it takes
    curated: tuple[str, ...]  # what the settings page offered before it offered every model
    # What it needs of a model, in the order it is checked: a capability, and the reason given for
    # a model without it.
    needs: tuple[tuple[str, str], ...]

    @property
    def default(self) -> str:
        """The model the setting reads as when nobody chose one."""
        return DEFAULTS[self.setting]


# What `complete_json` needs (see the module's docstring).
_JSON_WITHOUT_REASONING = (
    ("structured_output", "no_structured_output"),
    ("reasoning_off", "reasoning_required"),
    ("reasoning", "no_reasoning_setting"),
    ("max_tokens", "no_max_tokens"),
)
ROLES = (
    Role("cleanup", "cleanup_model", LLM, tuple(CLEANUP_MODEL_CHOICES), _JSON_WITHOUT_REASONING),
    # The settings page offered the cleanup models for the answers too.
    Role("answer", "agent_model", LLM, tuple(CLEANUP_MODEL_CHOICES), _JSON_WITHOUT_REASONING),
    Role("stt", "stt_cloud_model", STT, STT_CLOUD_MODELS, ()),
)
ROLE_OF_SETTING = {role.setting: role for role in ROLES}

# What the catalog tells of each kind of model, as the `capabilities` of its entries.
CAPABILITIES = {
    LLM: ("structured_output", "reasoning", "reasoning_off", "max_tokens", TOOL_CALLING),
    STT: ("dictionary_hints",),
}


@dataclass(frozen=True)
class CatalogEntry:
    """One model of the list, as Raphael reads it."""

    id: str
    kind: str  # LLM or STT
    name: str  # without its maker: "GPT-6 Luna"
    provider: str  # its maker: "OpenAI"
    price: ModelPrice
    variable_price: bool  # the list gives no price: a router picks a model for each request
    context_length: int | None  # in tokens, for a language model
    created_at_ms: int | None  # when OpenRouter added it
    capabilities: frozenset[str]  # those of CAPABILITIES[kind] that it has

    @property
    def free(self) -> bool:
        """Whether it costs nothing: all of its prices are known, and 0."""
        price = self.price
        tokens = price.input_per_mtok == 0 and price.output_per_mtok == 0
        if self.variable_price:
            return False
        return tokens if self.kind == LLM else price.per_minute == 0 or tokens


def refusal(entry: CatalogEntry, role: Role) -> str | None:
    """Why `role` cannot use the model of `entry`: the reason of a need it lacks (`Role.needs`), or
    "wrong_kind". None when it can."""
    if entry.kind != role.kind:
        return "wrong_kind"
    for capability, reason in role.needs:
        if capability not in entry.capabilities:
            return reason
    return None


def find(entries: Iterable[CatalogEntry], kind: str, model_id: object) -> CatalogEntry | None:
    """The entry of the model `model_id` of `kind`, if the catalog lists it."""
    return next((e for e in entries if e.kind == kind and e.id == model_id), None)


def choice_problem(
    role: Role, model_id: str, entries: Sequence[CatalogEntry] | None, current: object
) -> str | None:
    """Why `model_id` cannot be chosen for `role`; None when it can.

    `entries` is the catalog OpenRouter gave last, however old, or None when it never gave one.
    With it, any model it lists that the role can use may be chosen; otherwise the problem is
    "not_listed", or why the role cannot use it (`refusal`). Without it, only the models the role
    offered all along may be ("not_curated"). The model that is chosen now (`current`) may always
    be chosen again, which changes nothing, and so may the default, which is what the setting
    reads as anyway.
    """
    if model_id in (current, role.default):
        return None
    if entries is None:
        return None if model_id in role.curated else "not_curated"
    entry = find(entries, role.kind, model_id)
    return "not_listed" if entry is None else refusal(entry, role)


# --- reading the model list ---------------------------------------------------------------------


def _number(value: object) -> float | None:
    """A number of the model list (a string such as "0.0000001", or a JSON number), or None if it
    is missing, no number or not finite. A bool is no number."""
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _price(value: object) -> float | None:
    """A price of the model list as a number, or None if it is missing, no number, negative or not
    finite."""
    number = _number(value)
    return number if number is not None and number >= 0 else None


def _tidy(number: float) -> float:
    """`number` to six significant digits: 0.09999999999999999 is 0.1."""
    return float(f"{number:.6g}")


def _per_mtok(per_token: float | None) -> float | None:
    return None if per_token is None else _tidy(per_token * _PER_MTOK)


def token_price(pricing: Mapping) -> ModelPrice:
    """The price of a model that is priced per token, from the list's `pricing` of it."""
    return ModelPrice(
        input_per_mtok=_per_mtok(_price(pricing.get("prompt"))),
        output_per_mtok=_per_mtok(_price(pricing.get("completion"))),
    )


def stt_price(pricing: Mapping) -> ModelPrice:
    """The price of a speech-to-text model, from the list's `pricing` of it: per minute of audio, or
    per token for one that is priced so (see the module's docstring)."""
    prompt, completion = _price(pricing.get("prompt")), _price(pricing.get("completion"))
    if completion is not None and completion > 0:  # priced per token, like a chat model
        return ModelPrice(input_per_mtok=_per_mtok(prompt), output_per_mtok=_per_mtok(completion))
    if prompt is None:
        return ModelPrice()
    per_minute = _tidy(prompt * _SECONDS_PER_MINUTE)  # `prompt` is a price per second of audio
    if per_minute > _MAX_PER_MINUTE:  # no price per second, but per hour (see the module docstring)
        per_minute = _tidy(prompt / _MINUTES_PER_HOUR)
    return ModelPrice(per_minute=per_minute if per_minute <= _MAX_PER_MINUTE else None)


# Raphael's own models, by kind: what one of them is when the list does not say.
_CURATED_KINDS = {
    **{model_id: LLM for model_id in CLEANUP_MODEL_CHOICES},
    **{model_id: STT for model_id in STT_CLOUD_MODELS},
}


def _modalities(architecture: object, key: str) -> list[str] | None:
    values = architecture.get(key) if isinstance(architecture, dict) else None
    if isinstance(values, list) and all(isinstance(value, str) for value in values):
        return values
    return None


def kind_of(model: Mapping) -> str | None:
    """LLM, STT, or None for a kind no role uses, by what the model reads and writes. A model whose
    modalities the list does not give is of no kind, unless it is one of Raphael's own."""
    architecture = model.get("architecture")
    inputs = _modalities(architecture, "input_modalities")
    outputs = _modalities(architecture, "output_modalities")
    if inputs is None or outputs is None:
        return _CURATED_KINDS.get(str(model.get("id")))
    if "transcription" in outputs:
        return STT
    if set(outputs) == {"text"} and "text" in inputs:
        return LLM
    return None


def _author(model_id: str) -> str:
    """The author part of a model id: "openai" for "openai/gpt-6-luna" and for an alias such as
    "~openai/gpt-luna-latest"."""
    return model_id.split("/", 1)[0].lstrip("~")


def _split_name(name: str) -> tuple[str, str] | None:
    """`("OpenAI", "GPT-6 Luna")` for the list's name "OpenAI: GPT-6 Luna"; None for a name that
    does not start with its maker."""
    maker, separator, rest = name.partition(": ")
    if not separator or not maker.strip() or not rest.strip():
        return None
    return maker.strip(), rest.strip()


def _makers(models: Iterable[Mapping]) -> dict[str, str]:
    """The maker the list names each author's models after, the most common one ("Mistral" for
    "mistralai"): the maker of a model whose own name does not say."""
    seen: dict[str, Counter[str]] = {}
    for model in models:
        name = model.get("name")
        split = _split_name(name) if isinstance(name, str) else None
        if split is not None:
            seen.setdefault(_author(model["id"]), Counter())[split[0]] += 1
    return {author: counts.most_common(1)[0][0] for author, counts in seen.items()}


def _positive_int(value: object) -> int | None:
    return value if type(value) is int and value > 0 else None


def _can_skip_reasoning(reasoning: object) -> bool:
    """Whether a model can be asked to answer without reasoning first, by the list's `reasoning` of
    it. A model the list says nothing of there does not reason."""
    if not isinstance(reasoning, dict):
        return True
    if reasoning.get("mandatory") is True:
        return False
    efforts = reasoning.get("supported_efforts")
    return not (isinstance(efforts, list) and efforts and "none" not in efforts)


def _capabilities(model: Mapping, kind: str) -> frozenset[str]:
    model_id = model["id"]
    found = set()
    if kind == STT:
        if model_id in STT_MODELS_WITH_HINTS:
            found.add("dictionary_hints")
        return frozenset(found)
    listed = model.get("supported_parameters")
    parameters = {p for p in listed if isinstance(p, str)} if isinstance(listed, list) else set()
    # `response_format` is what is sent; `structured_outputs` says that it may hold a JSON schema.
    if {"response_format", "structured_outputs"} <= parameters:
        found.add("structured_output")
    if "reasoning" in parameters:
        found.add("reasoning")
    if _can_skip_reasoning(model.get("reasoning")):
        found.add("reasoning_off")
    if "max_tokens" in parameters:
        found.add("max_tokens")
    # The tools, and the choice among them, which the call that picks a plugin's tools sends.
    if {"tools", "tool_choice"} <= parameters:
        found.add(TOOL_CALLING)
    return frozenset(found)


def _entry(model: Mapping, kind: str, makers: Mapping[str, str]) -> CatalogEntry:
    model_id: str = model["id"]
    listed_name = model.get("name")
    listed_name = listed_name.strip() if isinstance(listed_name, str) else ""
    split = _split_name(listed_name)
    if split is None:
        author = _author(model_id)
        # An author none of whose models is named after a maker goes by its slug: "Unbiased".
        maker = makers.get(author) or author[:1].upper() + author[1:]
        split = maker, listed_name or model_id
    pricing = model.get("pricing")
    pricing = pricing if isinstance(pricing, dict) else {}
    created = _positive_int(model.get("created"))
    return CatalogEntry(
        id=model_id,
        kind=kind,
        name=split[1],
        provider=split[0],
        price=token_price(pricing) if kind == LLM else stt_price(pricing),
        # A router has no price of its own: the list gives it -1.
        variable_price=any((_number(pricing.get(f)) or 0) < 0 for f in ("prompt", "completion")),
        context_length=_positive_int(model.get("context_length")) if kind == LLM else None,
        created_at_ms=None if created is None else created * _MS_PER_SECOND,
        capabilities=_capabilities(model, kind),
    )


def _is_model_id(value: object) -> bool:
    """Whether `value` can be the id of a model the settings could hold: text, without spaces."""
    return (
        isinstance(value, str)
        and 0 < len(value) <= AGENT_TEXT_MAX_CHARS
        and not any(char.isspace() for char in value)
    )


def parse_catalog(models: object) -> list[CatalogEntry]:
    """The models of the list (the `data` of GET /models) that are of a kind Raphael uses, in the
    list's order, the batch variants aside. Entries that are no models are skipped, and a model
    listed twice is kept once."""
    listed = [
        model
        for model in (models if isinstance(models, list) else [])
        if isinstance(model, dict)
        and _is_model_id(model.get("id"))
        and not model["id"].endswith(_BATCH)
    ]
    makers = _makers(listed)
    entries: list[CatalogEntry] = []
    seen: set[str] = set()
    for model in listed:
        kind = kind_of(model)
        if kind is None or model["id"] in seen:
            continue
        seen.add(model["id"])
        entries.append(_entry(model, kind, makers))
    return entries


# --- the cache ----------------------------------------------------------------------------------


def entry_to_json(entry: CatalogEntry) -> dict[str, Any]:
    """`entry` as pricing.py keeps it."""
    return {
        "id": entry.id,
        "kind": entry.kind,
        "name": entry.name,
        "provider": entry.provider,
        "per_minute": entry.price.per_minute,
        "input_per_mtok": entry.price.input_per_mtok,
        "output_per_mtok": entry.price.output_per_mtok,
        "variable_price": entry.variable_price,
        "context_length": entry.context_length,
        "created_at_ms": entry.created_at_ms,
        "capabilities": sorted(entry.capabilities),
    }


def entry_from_json(data: object) -> CatalogEntry | None:
    """The entry kept as `data`, or None if it is not one (the cache is only a cache)."""
    if not isinstance(data, dict) or data.get("kind") not in KINDS:
        return None
    kind = data["kind"]
    texts = data.get("name"), data.get("provider")
    capabilities = data.get("capabilities")
    if (
        not _is_model_id(data.get("id"))
        or not all(isinstance(text, str) and text for text in texts)
        or type(data.get("variable_price")) is not bool
        or not isinstance(capabilities, list)
    ):
        return None
    return CatalogEntry(
        id=data["id"],
        kind=kind,
        name=data["name"],
        provider=data["provider"],
        price=ModelPrice(
            per_minute=_price(data.get("per_minute")),
            input_per_mtok=_price(data.get("input_per_mtok")),
            output_per_mtok=_price(data.get("output_per_mtok")),
        ),
        variable_price=data["variable_price"],
        context_length=_positive_int(data.get("context_length")),
        created_at_ms=_positive_int(data.get("created_at_ms")),
        capabilities=frozenset(c for c in capabilities if c in CAPABILITIES[kind]),
    )


# --- the answer ---------------------------------------------------------------------------------


def entry_view(entry: CatalogEntry) -> dict[str, Any]:
    """One model of the answer of `models.catalog`, with what each role of its kind can make of
    it."""
    roles = [role for role in ROLES if role.kind == entry.kind]
    return {
        "id": entry.id,
        "name": entry.name,
        "provider": entry.provider,
        "input_per_mtok": entry.price.input_per_mtok,
        "output_per_mtok": entry.price.output_per_mtok,
        "per_minute": entry.price.per_minute,
        "free": entry.free,
        "variable_price": entry.variable_price,
        "context_length": entry.context_length,
        "created_at_ms": entry.created_at_ms,
        "capabilities": {name: name in entry.capabilities for name in CAPABILITIES[entry.kind]},
        "roles": {
            role.name: {"usable": reason is None, "reason": reason}
            for role in roles
            for reason in [refusal(entry, role)]
        },
    }


def catalog_view(
    entries: Iterable[CatalogEntry],
    fetched_at_ms: int | None,
    source: str,
    replaced: Sequence[Mapping[str, str]] = (),
) -> dict[str, Any]:
    """The answer of `models.catalog`: the models by kind, in the list's order, when the list was
    fetched (None for the builtin table), where it came from ("live", "cached" or "builtin") and
    the choices of model that went back to their defaults (`replaced`)."""
    views: dict[str, list[dict[str, Any]]] = {kind: [] for kind in KINDS}
    for entry in entries:
        views[entry.kind].append(entry_view(entry))
    return {
        LLM: views[LLM],
        STT: views[STT],
        "fetched_at_ms": fetched_at_ms,
        "source": source,
        "replaced": [dict(item) for item in replaced],
    }
