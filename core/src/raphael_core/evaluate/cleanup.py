"""Compare cleanup models with the real cleanup prompt.

    uv run python -m raphael_core.evaluate.cleanup --cases ../eval/cleanup_cases.jsonl --models a,b

Each case of the cases file is a line of speech-to-text output. Every model cleans every case with
the system prompt the app uses (`build_system_prompt`), asked for the same JSON, with no fallback
model, so that what is measured is the model that was named. The reply is checked against the
case's `must_contain` and `must_not_contain` and against the length rule of `CleanupService`
(a reply of more than 1.5 times the dictation plus 20 characters, or an empty one, is not used by
the app: it pastes the raw text). That rule cannot catch a short answer to a short question, so the
cases hold such questions and the prompt is what must keep the model from answering them.

The report (`cleanup_report.md` in the data folder) has the outputs, the latency and the cost of
every call and the checks that failed. The key is read from Windows Credential Manager, where
`--set-key` stores it; it is never printed or written to a file.

Cases file: one JSON object per line with `id`, `input`, and optionally `dictionary` (a list of
words), `must_contain` and `must_not_contain` (lists of text). Spaces are ignored when a text is
looked for, so 打開 YouTube and 打開YouTube are the same.
"""

import argparse
import asyncio
import getpass
import json
import logging
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from raphael_core.brain.cleanup import CLEANUP_SCHEMA, build_system_prompt
from raphael_core.brain.openrouter import (
    CLEANUP_MODEL_CHOICES,
    OpenRouterClient,
    OpenRouterError,
)
from raphael_core.brain.secrets import CredentialKeyStore
from raphael_core.evaluate import use_utf8_output

# Generous: this measures the models, and the app's own timeout (3 to 15 s) is not what is tested.
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


@dataclass(frozen=True)
class Outcome:
    case_id: str
    model: str
    text: str | None  # what the model wrote; None when the call failed
    error: str | None  # the kind of the OpenRouterError when the call failed
    latency_ms: int
    cost_usd: float
    failures: list[str]  # the checks that failed; empty when the case passed

    @property
    def passed(self) -> bool:
        return not self.failures


class Abort(Exception):
    """The run cannot go on: the key is refused or there is no credit."""

    def __init__(self, error: OpenRouterError) -> None:
        super().__init__(error.kind)
        self.error = error


def _string_list(entry: dict, field: str, where: str) -> list[str]:
    value = entry.get(field, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{where}：{field} 必須是文字清單")
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
        cases.append(
            Case(
                id=case_id,
                input=text,
                dictionary=_string_list(entry, "dictionary", where),
                must_contain=_string_list(entry, "must_contain", where),
                must_not_contain=_string_list(entry, "must_not_contain", where),
            )
        )
    return cases


def length_limit(raw: str) -> float:
    return 1.5 * len(raw) + 20


def is_suspicious(raw: str, text: str) -> bool:
    """Whether the app would refuse `text` as the cleanup of `raw` and paste `raw` instead.

    The same rule as CleanupService: an empty reply, or one longer than 1.5 times the dictation
    plus 20 characters (it probably answered instead of cleaning). Keep the two in step.
    """
    text = text.strip()
    return not text or len(text) > length_limit(raw)


def _squash(text: str) -> str:
    return "".join(text.split())


def failed_checks(case: Case, text: str) -> list[str]:
    """What is wrong with `text` as the cleanup of the case: one message per failed check.

    `text` is what the model wrote, as it wrote it. The app turns the Chinese in it into Traditional
    Chinese (Taiwan) afterwards, which changes some words (打開 into 開啟), so the checks must not
    be run on that: they are about the model, and a model that writes Simplified characters, which
    its prompt forbids, fails them.
    """
    text = text.strip()
    if not text:
        return ["輸出是空的（app 會改貼原文）"]
    failures = []
    if is_suspicious(case.input, text):
        failures.append(
            f"輸出過長（{len(text)} 字，上限 {length_limit(case.input):g} 字）：app 會當成可疑而改貼原文"
        )
    squashed = _squash(text)
    for needle in case.must_contain:
        if _squash(needle) not in squashed:
            failures.append(f"缺少「{needle}」")
    for needle in case.must_not_contain:
        if _squash(needle) in squashed:
            failures.append(f"不該出現「{needle}」")
    return failures


async def run_case(
    client, model: str, case: Case, *, timeout_s: float = CALL_TIMEOUT_S
) -> Outcome:
    """Have `model` clean the case, and check what it wrote.

    A call that fails gives an Outcome that says so, except when the key is refused or the credit
    is gone: that raises Abort.
    """
    try:
        completion = await client.complete_json(
            model=model,
            fallbacks=[],
            system=build_system_prompt(case.dictionary, []),
            user=f"<transcript>\n{case.input}\n</transcript>",
            schema=CLEANUP_SCHEMA,
            schema_name="cleaned_dictation",
            timeout_s=timeout_s,
            max_tokens=max(256, 2 * len(case.input) + 64),
        )
    except OpenRouterError as error:
        if error.kind in _ABORT_KINDS:
            raise Abort(error) from error
        return Outcome(case.id, model, None, error.kind, 0, 0.0, [f"呼叫失敗（{error.kind}）：{error}"])
    reply = completion.data.get("text")
    text = reply if isinstance(reply, str) else ""
    return Outcome(
        case_id=case.id,
        model=model,
        text=text,
        error=None,
        latency_ms=completion.latency_ms,
        cost_usd=completion.cost_usd,
        failures=failed_checks(case, text),
    )


async def run_all(
    client,
    models: list[str],
    cases: list[Case],
    *,
    on_outcome: Callable[[Outcome], None] | None = None,
) -> list[Outcome]:
    """Every case with every model, one call at a time (so that the latencies are not a queue)."""
    outcomes = []
    for model in models:
        for case in cases:
            outcome = await run_case(client, model, case)
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
    cases: list[Case], models: list[str], outcomes: list[Outcome], when: str
) -> str:
    """The report as Markdown (in Traditional Chinese)."""
    by_key = {(o.model, o.case_id): o for o in outcomes}
    lines = [
        "# 清理模型評估報告",
        "",
        f"- 產生時間：{when}",
        f"- 案例數：{len(cases)}",
        f"- 模型：{'、'.join(f'`{m}`' for m in models)}",
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
        "通過表示沒有任何檢查失敗（必須出現、不可出現、長度規則）。"
        "延遲是 OpenRouter 這次呼叫本身花的時間，不含辨識與貼上。",
        "",
        "## 逐案結果",
    ]
    for case in cases:
        lines += ["", f"### {case.id}", "", "輸入：", "", _fence(case.input), ""]
        if case.dictionary:
            lines.append(f"- 字典：{'、'.join(case.dictionary)}")
        if case.must_contain:
            lines.append(f"- 必須出現：{'、'.join(f'「{t}」' for t in case.must_contain)}")
        if case.must_not_contain:
            lines.append(f"- 不可出現：{'、'.join(f'「{t}」' for t in case.must_not_contain)}")
        for model in models:
            outcome = by_key.get((model, case.id))
            if outcome is None:
                continue
            verdict = "通過" if outcome.passed else "未通過"
            lines += ["", f"#### `{model}`：{verdict}", ""]
            lines.append(f"延遲 {outcome.latency_ms} ms，花費 US${outcome.cost_usd:.6f}")
            if outcome.text is not None:
                lines += ["", _fence(outcome.text)]
            if outcome.failures:
                lines += ["", "未通過的檢查："] + [f"- {failure}" for failure in outcome.failures]
    return "\n".join(lines) + "\n"


async def _run(client, models: list[str], cases: list[Case]) -> list[Outcome]:
    try:
        return await run_all(client, models, cases, on_outcome=_show_outcome)
    finally:
        await client.aclose()


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
        default=",".join(CLEANUP_MODEL_CHOICES),
        help="comma-separated OpenRouter model ids (default: the models the app offers)",
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
            print(f"OpenRouter 不接受這把金鑰（{stop.error}）。請用 --set-key 重新存入。", file=sys.stderr)
        else:
            print(f"OpenRouter 沒有額度或已達花費上限：{stop.error}", file=sys.stderr)
        return 1

    args.report.parent.mkdir(parents=True, exist_ok=True)
    report = render_report(cases, models, outcomes, datetime.now().strftime("%Y-%m-%d %H:%M"))
    args.report.write_text(report, encoding="utf-8", newline="\n")
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
