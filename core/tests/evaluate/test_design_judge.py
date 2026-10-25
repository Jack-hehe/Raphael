import json
import struct
from types import SimpleNamespace

import pytest

from raphael_core.evaluate.design_judge import build_prompt, cache_key, candidates, judge_image, parse_review, run_judge

RULES = "| R-UI-01 | 2026-10-25 | No filler | judge | hard | images |\n| R-UI-02 | 2026-10-25 | Polish | judge | normal | images |\n| R-WF-01 | 2026-10-25 | Native | human | hard | review |"


class FakeVision:
    def __init__(self, content=None, cost=.01):
        self.content = content or '{"score":4,"findings":[]}'
        self.calls, self.cost = [], cost

    async def chat_tools(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(content=self.content, calls=[], model=kwargs["model"], cost_usd=self.cost, usage_known=True)


def test_prompt_contains_judge_rules_feature_and_untrusted_boundary():
    prompt = build_prompt(RULES, "Home feature entry", "home light 720")
    assert "R-UI-01" in prompt and "R-WF-01" not in prompt
    assert "Home feature entry" in prompt and "untrusted" in prompt
    assert "FIRST image" in prompt


def test_parser_fails_low_scores_and_hard_rule_downgrades():
    assert parse_review('{"score":3,"findings":[]}', RULES)["failed"]
    result = parse_review('{"score":5,"findings":[{"rule_id":"R-UI-01","hard":false,"detail":"Remove filler below the heading"}]}', RULES)
    assert result["failed"] and result["findings"][0]["hard"]
    for text in ['{}', '{"score":true,"findings":[]}', '{"score":6,"findings":[]}', '{"score":4,"findings":[{"rule_id":"R-UI-99","hard":false,"detail":"Unknown"}]}', '```json\n{}\n```']:
        with pytest.raises(ValueError):
            parse_review(text, RULES)


async def test_cache_reuses_only_identical_image_model_rules_feature_and_references(tmp_path):
    client = FakeVision()
    kwargs = dict(model="fake/vision", prompt="prompt", references=[b"reference"], rules=RULES, cache=tmp_path, client=client)
    first = await judge_image(b"image", **kwargs)
    second = await judge_image(b"image", **kwargs)
    assert not first["cached"] and second["cached"] and second["cost_usd"] == 0
    assert len(client.calls) == 1 and client.calls[0]["fallbacks"] == []
    assert len(client.calls[0]["messages"][0]["content"]) == 3
    for change in ({"model": "other"}, {"prompt": "changed rules"}, {"references": [b"changed"]}):
        await judge_image(b"image", **{**kwargs, **change})
    await judge_image(b"changed image", **kwargs)
    assert len(client.calls) == 5


async def test_invalid_review_never_enters_cache(tmp_path):
    client = FakeVision("not JSON")
    for _ in range(2):
        with pytest.raises(ValueError):
            await judge_image(b"image", model="fake", prompt="p", references=[], rules=RULES, cache=tmp_path, client=client)
    assert len(client.calls) == 2 and not list(tmp_path.glob("*.json"))


def make_run(root):
    run = root / "run"
    run.mkdir()
    rows = []
    for theme in ("light", "dark"):
        for width in (720, 1120, 1920):
            image = f"home-{theme}-{width}.png"
            (run / image).write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 8 + struct.pack("!II", width, 1080 if width == 1920 else 900))
            rows.append(dict(scene="home", theme=theme, width=width, image=image, failed=False))
    (run / "report.json").write_text(json.dumps(dict(status="passed", errors=[], options={"scenes": ["home"]}, results=rows)))
    return run


def test_candidates_require_complete_three_size_two_theme_evidence(tmp_path):
    run = make_run(tmp_path)
    assert len(candidates(run)) == 6
    report = json.loads((run / "report.json").read_text())
    report["results"].pop()
    (run / "report.json").write_text(json.dumps(report))
    with pytest.raises(ValueError, match="both_themes"):
        candidates(run)


async def test_budget_stops_and_writes_incomplete_report(tmp_path):
    run = make_run(tmp_path)
    spec = tmp_path / ".agents/product"
    spec.mkdir(parents=True)
    (spec / "RULES.md").write_text(RULES)
    features = tmp_path / ".agents/skills/verify-raphael/features"
    features.mkdir(parents=True)
    (features / "home.md").write_text("npm run look -- home --theme both --width 1120,720")
    reference = tmp_path / "reference.png"
    reference.write_bytes(b"\x89PNGreference")
    client = FakeVision(cost=.10)
    assert await run_judge(run, root=tmp_path, references=[reference], client=client, max_cost_usd=.10, request_ceiling=.10) == 1
    assert len(client.calls) == 1
    report = json.loads(next(tmp_path.glob("app/.look/judge-*/report.json")).read_text())
    assert report["status"] == "failed" and report["errors"]
    assert report["results"][-1]["error"] == "judge_budget_exhausted"
