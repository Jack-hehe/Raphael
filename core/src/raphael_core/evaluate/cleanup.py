"""Compare cleanup models with the real cleanup prompt.

    uv run python -m raphael_core.evaluate.cleanup --cases ../eval/cleanup_cases.jsonl --models a,b

Each case of the cases file is a line of speech-to-text output. It goes through the same steps as in
the app: given to the model as it was heard (without OpenCC, which the app applies only to the
raw text it pastes when there is no cleanup), cleaned by the model with the system prompt (the
case's target app included), user message and token limit the app uses, with no fallback model
so that what is measured is the model that was named, and the reply made final by
`finish_cleaned_text`. The checks judge what the user would get: `must_contain` and
`must_not_contain` are looked for in the final text, and the rule of `CleanupService` for a reply
that is no cleanup (`is_suspicious_reply`: empty, longer than the dictation allows, or with too
little in common with it) is applied to the reply as the model wrote it. These are the functions
the app itself uses, imported, not copied. Those rules do not catch every answer to a question (an
answer that repeats the question first passes them), so the cases hold such questions and the
prompt is what must keep the model from answering them.

The report (`cleanup_report.md` in the data folder) has, for every call, the final text, the
model's own reply when it differs, the latency, the cost and the checks that failed. The key is
read from Windows Credential Manager, where `--set-key` stores it; it is never printed or written
to a file.

Cases file: one JSON object per line with `id`, `input`, and optionally `dictionary` (a list of
words), `must_contain` and `must_not_contain` (lists of text), `must_contain_ci` (like
`must_contain`, in any letter case: for a name whose capitalisation is not the point) and
`must_contain_any` (alternatives: at least one of them must be there, in any letter case),
`misheard` (an object from a dictionary word to the forms speech-to-text gives for it: the prompt
lists them as 常被聽成 hints, as the app does for corrections it has seen, and they are never applied
as replacement rules), and
`target_app` (the process name of the window the text goes to, such as LINE.exe) and `languages`
(the `languages` setting the case is cleaned for; DEFAULT_LANGUAGES, the user's own three, when it
is left out). Spaces are ignored when a text is looked for, so 打開 YouTube and 打開YouTube are the
same. What the model leaves in the wrong Chinese script is converted as the app does for the
case's Chinese variant (for Taiwan, 打开 into 開啟), so a text to look for must be one that
conversion leaves alone: Chinese in the variant's own script is.

There are two cases files. `cleanup_cases.jsonl` holds the held-out cases: none of them, nor
anything like its wording, is in the prompt, so that what is measured is the model's judgment, not
its memory of the examples. `regression_cases.jsonl` holds real dictations that went
wrong once, and is run the same way (`--cases ../eval/regression_cases.jsonl`).
"""

import argparse
import asyncio
import getpass
import json
import logging
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from raphael_core.brain.cleanup import (
    CLEANUP_SCHEMA,
    MIN_OVERLAP,
    build_system_prompt,
    build_user_message,
    content_overlap,
    is_suspicious_reply,
    max_tokens_for,
    reply_length,
    reply_length_limit,
    suspicion,
)
from raphael_core.brain.openrouter import (
    CLEANUP_FALLBACKS,
    DEFAULT_CLEANUP_MODEL,
    OpenRouterClient,
    OpenRouterError,
)
from raphael_core.brain.secrets import CredentialKeyStore
from raphael_core.brain.textnorm import apply_replacements, finish_cleaned_text
from raphael_core.evaluate import use_utf8_output
from raphael_core.languages import checked_languages, chinese_variant

# The languages of a case that names none: the user speaks these three, often mixed.
DEFAULT_LANGUAGES = ("zh-TW", "en-US", "ja-JP")

# Generous: the app gives a cleanup 10 s and a second for every 3 of audio (cleanup.timeout_for),
# and a case is a line of speech. This measures the models, not that timeout.
CALL_TIMEOUT_S = 30.0
# A failure of these kinds will be the same for every call that follows.
_ABORT_KINDS = ("auth", "credits")

NO_KEY_MESSAGE = (
    "找不到 OpenRouter 金鑰，無法評估清理模型。\n"
    "請先把金鑰存進 Windows 認證管理員（只存在那裡，不會寫進任何檔案）：\n"
    "    uv run python -m raphael_core.evaluate.cleanup --set-key\n"
    "存好之後再執行一次原本的指令。"
)


@dataclass(frozen=True)
class Case:
    id: str
    input: str
    dictionary: list[str]
    must_contain: list[str]
    must_not_contain: list[str]
    target_app: str | None = None  # the process name of the window the text goes to
    # The `languages` setting the case is cleaned for.
    languages: list[str] = field(default_factory=lambda: list(DEFAULT_LANGUAGES))
    must_contain_ci: list[str] = field(default_factory=list)  # must_contain, in any letter case
    must_contain_any: list[str] = field(default_factory=list)  # at least one, in any letter case
    # word -> the forms speech-to-text gives for it: hints in the prompt, never replacement rules
    misheard: dict[str, list[str]] = field(default_factory=dict)

    @property
    def chinese(self) -> str | None:
        """The Chinese variant of the case's languages, as the app converts for it."""
        return chinese_variant(self.languages)

    @property
    def prompt_words(self) -> list[str]:
        """The dictionary words the prompt lists: the case's words, then its misheard words."""
        return self.dictionary + [word for word in self.misheard if word not in self.dictionary]

    @property
    def prompt_hints(self) -> list[tuple[str, str]]:
        """The (misheard, word) pairs the prompt lists as 常被聽成."""
        return [(form, word) for word, forms in self.misheard.items() for form in forms]


@dataclass(frozen=True)
class Outcome:
    case_id: str
    model: str
    text: str | None  # the final text, as the app would paste it; None when the call failed
    error: str | None  # the kind of the OpenRouterError when the call failed
    latency_ms: int
    cost_usd: float
    failures: list[str]  # the checks that failed; empty when the case passed
    reply: str | None = None  # what the model wrote, before the app's processing

    @property
    def passed(self) -> bool:
        return not self.failures


class Abort(Exception):
    """The run cannot go on: the key is refused or there is no credit.

    `outcomes` are the results that were in before it.
    """

    def __init__(self, error: OpenRouterError, outcomes: list[Outcome] | None = None) -> None:
        super().__init__(error.kind)
        self.error = error
        self.outcomes = outcomes or []


def _string_list(entry: dict, field: str, where: str) -> list[str]:
    value = entry.get(field, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{where}：{field} 必須是文字清單")
    return value


def _misheard(entry: dict, where: str) -> dict[str, list[str]]:
    value = entry.get("misheard", {})
    if not isinstance(value, dict) or not all(
        isinstance(word, str) and isinstance(forms, list) and all(isinstance(form, str) for form in forms)
        for word, forms in value.items()
    ):
        raise ValueError(f"{where}：misheard 必須是「詞 → 文字清單」的物件")
    return value


def load_cases(path: Path) -> list[Case]:
    """The cases in a JSON Lines file. ValueError, naming the line, for a case that is not valid."""
    cases: list[Case] = []
    seen: set[str] = set()
    for number, line in enumerate(Path(path).read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        where = f"第 {number} 行"
        try:
            entry = json.loads(line)
        except ValueError as exc:
            raise ValueError(f"{where}不是有效的 JSON：{exc}") from exc
        if not isinstance(entry, dict):
            raise ValueError(f"{where}必須是一個 JSON 物件")
        case_id, text = entry.get("id"), entry.get("input")
        if not isinstance(case_id, str) or not case_id:
            raise ValueError(f"{where}：缺少 id（文字）")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"{where}：缺少 input（文字）")
        if case_id in seen:
            raise ValueError(f"{where}：id「{case_id}」重複")
        seen.add(case_id)
        target_app = entry.get("target_app")
        if target_app is not None and not isinstance(target_app, str):
            raise ValueError(f"{where}：target_app 必須是文字")
        languages = entry.get("languages", list(DEFAULT_LANGUAGES))
        try:
            languages = checked_languages("languages", languages)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{where}：languages 不是有效的語言選擇（{exc}）") from exc
        cases.append(
            Case(
                id=case_id,
                input=text,
                dictionary=_string_list(entry, "dictionary", where),
                must_contain=_string_list(entry, "must_contain", where),
                must_not_contain=_string_list(entry, "must_not_contain", where),
                target_app=target_app,
                languages=languages,
                must_contain_ci=_string_list(entry, "must_contain_ci", where),
                must_contain_any=_string_list(entry, "must_contain_any", where),
                misheard=_misheard(entry, where),
            )
        )
    return cases


# The dictionary's replacement rules (misheard form -> word) come from the user's dictionary in the
# app. A case lists only the words, so there are none to apply here.
_NO_RULES: tuple[tuple[str, str], ...] = ()


def transcript_for(case: Case) -> str:
    """The case's input as the model gets it. The app gives the model the speech-to-text result
    as it was heard, with the dictionary's replacements (a case has none) and without OpenCC."""
    return apply_replacements(case.input, _NO_RULES)


def _squash(text: str) -> str:
    return "".join(text.split())


def _quoted(texts: list[str]) -> str:
    """The texts as 「a」、「b」."""
    return "、".join(f"「{text}」" for text in texts)


def _suspicious(sent: str, reply: str) -> str:
    """The failure for a reply that the app takes for no cleanup, by the reason it gives."""
    if suspicion(sent, reply) == "unrelated":
        share = content_overlap(sent, reply) or 0.0
        return (
            f"輸出和原文幾乎沒有共同的字（重疊 {share:.0%}，至少要 {MIN_OVERLAP:.0%}），"
            "多半是回答了原文：app 會當成可疑而改貼原文"
        )
    limit = reply_length_limit(sent)
    return (
        f"輸出過長（{reply_length(reply)} 字，不算換行，上限 {limit:g} 字）："
        "app 會當成可疑而改貼原文"
    )


def failed_checks(case: Case, reply: str) -> list[str]:
    """What is wrong with the model's `reply` to the case: one message per failed check.

    This judges what the user would get. The rule for a reply that is no cleanup (too long, or
    with too little in common with the dictation) is applied to the reply as the model wrote it,
    as `CleanupService` does. The words that must or must not be there are looked for in
    the text after the app's processing of the reply (`finish_cleaned_text`: what is still
    Simplified Chinese made Taiwan Traditional, which turns 打开 into 開啟, and the dictionary's
    replacements), because that
    is what is pasted. A reply that the app refuses is pasted as the raw text there; it fails here,
    and the other checks still look at what the model wrote.
    """
    reply = reply.strip()
    if not reply:
        return ["輸出是空的（app 會改貼原文）"]
    failures = []
    sent = transcript_for(case)
    if is_suspicious_reply(sent, reply):
        failures.append(_suspicious(sent, reply))
    final = _squash(finish_cleaned_text(reply, _NO_RULES, chinese=case.chinese))
    folded = final.casefold()
    for needle in case.must_contain:
        if _squash(needle) not in final:
            failures.append(f"缺少「{needle}」")
    for needle in case.must_contain_ci:
        if _squash(needle).casefold() not in folded:
            failures.append(f"缺少「{needle}」（不分大小寫）")
    if case.must_contain_any and not any(
        _squash(needle).casefold() in folded for needle in case.must_contain_any
    ):
        failures.append(f"一個都沒有：{_quoted(case.must_contain_any)}")
    for needle in case.must_not_contain:
        if _squash(needle) in final:
            failures.append(f"不該出現「{needle}」")
    return failures


async def run_case(
    client, model: str, case: Case, *, timeout_s: float = CALL_TIMEOUT_S
) -> Outcome:
    """Have `model` clean the case, and check what the user would get.

    A call that fails gives an Outcome that says so, except when the key is refused or the credit
    is gone: that raises Abort.
    """
    sent = transcript_for(case)
    try:
        completion = await client.complete_json(
            model=model,
            fallbacks=[],
            system=build_system_prompt(
                case.prompt_words,
                case.prompt_hints,
                case.target_app,
                languages=case.languages,
                heard=sent,
            ),
            user=build_user_message(sent),
            schema=CLEANUP_SCHEMA,
            schema_name="cleaned_dictation",
            timeout_s=timeout_s,
            max_tokens=max_tokens_for(sent),
        )
    except OpenRouterError as error:
        if error.kind in _ABORT_KINDS:
            raise Abort(error) from error
        failure = f"呼叫失敗（{error.kind}）：{error}"
        return Outcome(case.id, model, None, error.kind, 0, 0.0, [failure])
    reply = completion.data.get("text")
    reply = reply.strip() if isinstance(reply, str) else ""
    return Outcome(
        case_id=case.id,
        model=model,
        text=finish_cleaned_text(reply, _NO_RULES, chinese=case.chinese),
        error=None,
        latency_ms=completion.latency_ms,
        cost_usd=completion.cost_usd,
        failures=failed_checks(case, reply),
        reply=reply,
    )


async def run_all(
    client,
    models: list[str],
    cases: list[Case],
    *,
    on_outcome: Callable[[Outcome], None] | None = None,
) -> list[Outcome]:
    """Every case with every model, one call at a time (so that the latencies are not a queue).

    Abort, when the key or the credit gives out, carries the outcomes so far.
    """
    outcomes: list[Outcome] = []
    for model in models:
        for case in cases:
            try:
                outcome = await run_case(client, model, case)
            except Abort as stop:
                raise Abort(stop.error, outcomes) from stop.error
            outcomes.append(outcome)
            if on_outcome is not None:
                on_outcome(outcome)
    return outcomes


def _fence(text: str) -> str:
    """`text` as a Markdown code block whose fence cannot be closed by the text itself."""
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    mark = "`" * max(3, longest + 1)
    return f"{mark}\n{text}\n{mark}"


def render_report(
    cases: list[Case],
    models: list[str],
    outcomes: list[Outcome],
    when: str,
    note: str | None = None,
) -> str:
    """The report as Markdown (in Traditional Chinese). `note` is a remark for the top, such as
    why the report is not complete."""
    by_key = {(o.model, o.case_id): o for o in outcomes}
    lines = [
        "# 清理模型評估報告",
        "",
        f"- 產生時間：{when}",
        f"- 案例數：{len(cases)}",
        f"- 模型：{'、'.join(f'`{m}`' for m in models)}",
    ]
    if note:
        lines.append(f"- 備註：{note}")
    lines += [
        "",
        "## 總覽",
        "",
        "| 模型 | 通過 | 平均延遲 (ms) | 最慢 (ms) | 總花費 (US$) | 未通過的案例 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for model in models:
        mine = [o for o in outcomes if o.model == model]
        answered = [o.latency_ms for o in mine if o.error is None]
        mean = f"{sum(answered) / len(answered):.0f}" if answered else "-"
        slowest = f"{max(answered)}" if answered else "-"
        failed = "、".join(o.case_id for o in mine if not o.passed) or "無"
        passed = sum(o.passed for o in mine)
        cost = sum(o.cost_usd for o in mine)
        lines.append(
            f"| `{model}` | {passed} / {len(mine)} | {mean} | {slowest} | {cost:.6f} | {failed} |"
        )
    lines += [
        "",
        (
            "通過表示沒有任何檢查失敗（必須出現、不可出現、長度與內容重疊規則）。"
            "「必須出現」與「不可出現」檢查的是 App 最後會貼上的文字（模型輸出再經過繁體轉換與字典取代），"
            "長度與內容重疊規則看的是模型原始輸出。"
            "延遲是 OpenRouter 這次呼叫本身花的時間，不含辨識與貼上。"
        ),
        "",
        "## 逐案結果",
    ]
    for case in cases:
        lines += ["", f"### {case.id}", "", "輸入：", "", _fence(case.input)]
        lines.append("")
        if case.target_app:
            lines.append(f"- 目標 App：`{case.target_app}`")
        if case.dictionary:
            lines.append(f"- 字典：{'、'.join(case.dictionary)}")
        for word, forms in case.misheard.items():
            lines.append(f"- 字典提示：{word}（常被聽成：{'、'.join(forms)}）")
        if case.must_contain:
            lines.append(f"- 必須出現：{_quoted(case.must_contain)}")
        if case.must_contain_ci:
            lines.append(f"- 必須出現（不分大小寫）：{_quoted(case.must_contain_ci)}")
        if case.must_contain_any:
            lines.append(f"- 至少出現一個（不分大小寫）：{_quoted(case.must_contain_any)}")
        if case.must_not_contain:
            lines.append(f"- 不可出現：{_quoted(case.must_not_contain)}")
        for model in models:
            outcome = by_key.get((model, case.id))
            if outcome is None:
                continue
            verdict = "通過" if outcome.passed else "未通過"
            lines += ["", f"#### `{model}`：{verdict}", ""]
            lines.append(f"延遲 {outcome.latency_ms} ms，花費 US${outcome.cost_usd:.6f}")
            if outcome.text is not None:
                lines += ["", "最終文字（App 會貼上的）：", "", _fence(outcome.text)]
                if outcome.reply is not None and outcome.reply != outcome.text:
                    lines += ["", "模型原始輸出：", "", _fence(outcome.reply)]
                elif outcome.reply is not None:
                    lines += ["", "模型原始輸出與最終文字相同。"]
            if outcome.failures:
                lines += ["", "未通過的檢查："] + [f"- {failure}" for failure in outcome.failures]
    return "\n".join(lines) + "\n"


async def _run(client, models: list[str], cases: list[Case]) -> list[Outcome]:
    try:
        return await run_all(client, models, cases, on_outcome=_show_outcome)
    finally:
        await client.aclose()


def _write_report(
    path: Path, cases: list[Case], models: list[str], outcomes: list[Outcome], note: str | None = None
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    when = datetime.now().strftime("%Y-%m-%d %H:%M")
    path.write_text(render_report(cases, models, outcomes, when, note), encoding="utf-8", newline="\n")


def _show_outcome(outcome: Outcome) -> None:
    verdict = "通過" if outcome.passed else "未通過"
    print(f"  {outcome.model}  {outcome.case_id}  {verdict}  {outcome.latency_ms} ms", flush=True)


def _set_key(store, read_secret: Callable[[str], str]) -> int:
    secret = read_secret("貼上 OpenRouter 金鑰（輸入時不會顯示）：").strip()
    if not secret:
        print("沒有輸入金鑰。", file=sys.stderr)
        return 1
    try:
        store.set(secret)
    except Exception as exc:  # the vault can fail in ways of its own; its message is not shown
        print(f"無法把金鑰存進 Windows 認證管理員（{type(exc).__name__}）。", file=sys.stderr)
        return 1
    print("金鑰已存進 Windows 認證管理員（服務名稱 Raphael）。")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m raphael_core.evaluate.cleanup",
        description="Run the cleanup prompt of Raphael on test cases with several models.",
    )
    parser.add_argument("--cases", type=Path, default=Path("../eval/cleanup_cases.jsonl"))
    parser.add_argument(
        "--models",
        default=",".join([DEFAULT_CLEANUP_MODEL, *CLEANUP_FALLBACKS]),
        help="comma-separated OpenRouter model ids (default: the cleanup model Raphael"
        " uses when nobody chose another, then its two fallbacks)",
    )
    parser.add_argument("--report", type=Path, default=Path("../eval/data/cleanup_report.md"))
    parser.add_argument(
        "--set-key",
        action="store_true",
        help="ask for the OpenRouter key and store it in Windows Credential Manager, then stop",
    )
    return parser


def main(
    argv: list[str] | None = None,
    *,
    key_store=None,
    client_factory: Callable[[Callable[[], str | None]], object] = OpenRouterClient,
    read_secret: Callable[[str], str] = getpass.getpass,
) -> int:
    args = _parser().parse_args(argv)
    store = key_store if key_store is not None else CredentialKeyStore()
    if args.set_key:
        return _set_key(store, read_secret)

    try:
        cases = load_cases(args.cases)
    except (OSError, ValueError) as exc:
        print(f"案例檔 {args.cases} 有問題：{exc}", file=sys.stderr)
        return 1
    if not cases:
        print(f"案例檔 {args.cases} 裡沒有任何案例。", file=sys.stderr)
        return 1
    models = list(dict.fromkeys(m.strip() for m in args.models.split(",") if m.strip()))
    if not models:
        print("--models 沒有指定任何模型。", file=sys.stderr)
        return 1

    try:
        key = store.get()
    except Exception as exc:  # not shown: the message of a vault error could hold anything
        print(f"無法從 Windows 認證管理員讀取 OpenRouter 金鑰（{type(exc).__name__}）。", file=sys.stderr)
        return 1
    if not key or not key.strip():
        print(NO_KEY_MESSAGE, file=sys.stderr)
        return 1

    print(f"{len(cases)} 個案例 x {len(models)} 個模型：")
    try:
        outcomes = asyncio.run(_run(client_factory(store.get), models, cases))
    except KeyboardInterrupt:
        print("\n已中止，沒有寫入報告。", file=sys.stderr)
        return 130
    except Abort as stop:
        if stop.error.kind == "auth":
            reason = f"OpenRouter 不接受這把金鑰（{stop.error}）"
            print(f"{reason}。請用 --set-key 重新存入。", file=sys.stderr)
        else:
            reason = f"OpenRouter 沒有額度或已達花費上限（{stop.error}）"
            print(f"{reason}。", file=sys.stderr)
        # Models are run one after the other, so the ones that finished have complete results.
        done = [m for m in models if sum(o.model == m for o in stop.outcomes) == len(cases)]
        if done:
            note = f"中止於 `{next(m for m in models if m not in done)}`：{reason}。"
            note += "這份報告只有已經跑完的模型。"
            kept = [o for o in stop.outcomes if o.model in done]
            _write_report(args.report, cases, done, kept, note)
            print(f"已跑完的模型的結果寫在 {args.report}", file=sys.stderr)
        return 1

    _write_report(args.report, cases, models, outcomes)
    print("\n結果：")
    for model in models:
        mine = [o for o in outcomes if o.model == model]
        print(
            f"- {model}：通過 {sum(o.passed for o in mine)} / {len(mine)}，"
            f"花費 US${sum(o.cost_usd for o in mine):.6f}"
        )
    print(f"\n完整報告：{args.report}")
    return 0


if __name__ == "__main__":
    use_utf8_output()
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    sys.exit(main())
