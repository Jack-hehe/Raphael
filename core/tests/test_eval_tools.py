"""Tests for the evaluation command-line tools: recording, speech-to-text and cleanup.

Nothing here needs a microphone, a speech model, a network or an OpenRouter key: the tools take
their recorder, engines, key store and client as arguments, and the tests pass fakes.
"""

import csv
import json
import wave
from pathlib import Path

import httpx
import numpy as np
import pytest

from raphael_core.brain import cleanup as brain_cleanup
from raphael_core.brain.openrouter import Completion, OpenRouterClient, OpenRouterError
from raphael_core.brain.secrets import MemoryKeyStore
from raphael_core.brain.textnorm import finish_cleaned_text
from raphael_core.device.stt import SAMPLE_RATE, read_wav
from raphael_core.evaluate import cleanup as eval_cleanup
from raphael_core.evaluate import record as eval_record
from raphael_core.evaluate import stt as eval_stt
from raphael_core.languages import chinese_variant

REPO = Path(__file__).resolve().parents[2]
HELD_OUT = REPO / "eval" / "cleanup_cases.jsonl"
REGRESSION = REPO / "eval" / "regression_cases.jsonl"


def all_cases():
    """The held-out cases and the regression cases, which the same tool runs."""
    return eval_cleanup.load_cases(HELD_OUT) + eval_cleanup.load_cases(REGRESSION)


def case_named(case_id):
    return next(case for case in all_cases() if case.id == case_id)


# --- the files that come with the tools


def test_dictation_prompts_are_thirty_lines_in_all_three_languages():
    lines = (REPO / "eval" / "dictation_prompts.txt").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 30 and all(line.strip() for line in lines)
    text = "".join(lines)
    assert any("぀" <= ch <= "ゟ" for ch in text), "no Japanese (hiragana)"
    assert any("一" <= ch <= "鿿" for ch in text), "no Chinese"
    assert any(ch.isascii() and ch.isalpha() for ch in text), "no English"
    assert min(map(len, lines)) < 15 and max(map(len, lines)) > 60, "short and long lines both"
    assert eval_record.load_prompts(REPO / "eval" / "dictation_prompts.txt") == lines


def test_cleanup_cases_file_has_the_required_cases():
    cases = eval_cleanup.load_cases(REPO / "eval" / "cleanup_cases.jsonl")
    ids = [case.id for case in cases]
    assert len(cases) >= 15 and len(set(ids)) == len(ids)
    required = {
        "dont-answer",
        "self-correction",
        "fillers-zh",
        "fillers-ja",
        "keep-english",
        "numbered-list",
        "command-stays-text",
        "dictionary-homophone",
    }
    assert required <= set(ids)
    assert sum(case.id.startswith("question-") for case in cases) >= 3, "short questions"
    for case in cases:
        assert case.input.strip()
        checks = case.must_contain, case.must_contain_ci, case.must_contain_any
        assert any(checks) or case.must_not_contain, f"{case.id} checks nothing"
    by_id = {case.id: case for case in cases}
    assert by_id["dictionary-homophone"].dictionary == ["拉斐爾"]
    assert by_id["dont-answer"].must_not_contain == ["步驟", "1."]


# --- record


def test_load_prompts_skips_blank_and_comment_lines(tmp_path):
    path = tmp_path / "p.txt"
    path.write_text("# a comment\n\n 第一句 \n\nsecond line\n#another\n", encoding="utf-8")
    assert eval_record.load_prompts(path) == ["第一句", "second line"]


def test_write_wav_is_16k_mono_pcm16(tmp_path):
    pcm = (np.sin(np.linspace(0, 100, 8000)) * 20000).astype("<i2")
    path = tmp_path / "01.wav"
    eval_record.write_wav(path, pcm)
    with wave.open(str(path), "rb") as reader:
        assert (reader.getnchannels(), reader.getsampwidth(), reader.getframerate()) == (1, 2, 16000)
        assert reader.getnframes() == 8000
    assert np.allclose(read_wav(path), pcm / 32768.0, atol=1e-4)
    assert not list(tmp_path.glob("*.part")), "the temporary file is gone"


class FakeRecorder:
    """Gives each recording the next array of `lengths` seconds of a quiet tone."""

    def __init__(self, lengths):
        self.lengths = list(lengths)
        self.started = 0
        self.stopped = 0

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1
        seconds = self.lengths.pop(0)
        return np.full(int(seconds * 16000), 1000, dtype="<i2")


class Script:
    """Stands in for input(): hands out the answers in order, and notes what it was asked."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.asked = []

    def __call__(self, question):
        self.asked.append(question)
        if not self.answers:
            raise EOFError
        return self.answers.pop(0)


def run_session(tmp_path, prompts, recorder, *answers, overwrite=False):
    messages = []
    script = Script(*answers)
    saved = eval_record.record_session(
        prompts, tmp_path, recorder=recorder, ask=script, say=messages.append, overwrite=overwrite
    )
    return saved, script, messages


def test_record_session_saves_wav_and_reference_text(tmp_path):
    recorder = FakeRecorder([1.0, 2.0])
    saved, _, messages = run_session(
        tmp_path, ["第一句", "second"], recorder, "", "", "", "", "", ""
    )
    assert saved == 2
    assert (tmp_path / "01.txt").read_text(encoding="utf-8") == "第一句\n"
    assert (tmp_path / "02.txt").read_text(encoding="utf-8") == "second\n"
    assert read_wav(tmp_path / "01.wav").size == 16000
    assert read_wav(tmp_path / "02.wav").size == 32000
    assert any("第一句" in message for message in messages), "each prompt is shown"
    assert recorder.started == recorder.stopped == 2


def test_record_session_redo_replaces_the_take(tmp_path):
    recorder = FakeRecorder([1.0, 2.0])
    # start, stop, "r" = again; start, stop, Enter = save
    saved, _, _ = run_session(tmp_path, ["a"], recorder, "", "", "r", "", "", "")
    assert saved == 1
    assert read_wav(tmp_path / "01.wav").size == 32000


def test_record_session_refuses_a_take_that_is_too_short(tmp_path):
    recorder = FakeRecorder([0.05, 1.0])
    saved, _, messages = run_session(tmp_path, ["a"], recorder, "", "", "", "", "")
    assert saved == 1
    assert read_wav(tmp_path / "01.wav").size == 16000
    assert any("太短" in message for message in messages)


def test_record_session_skip_and_quit(tmp_path):
    recorder = FakeRecorder([1.0])
    saved, _, _ = run_session(tmp_path, ["a", "b", "c"], recorder, "s", "", "", "", "q")
    assert saved == 1
    assert not (tmp_path / "01.wav").exists()
    assert (tmp_path / "02.wav").exists() and not (tmp_path / "03.wav").exists()


def test_record_session_stops_at_the_end_of_input(tmp_path):
    recorder = FakeRecorder([1.0])
    saved, _, _ = run_session(tmp_path, ["a"], recorder, "")  # the ask for "stop" raises EOFError
    assert saved == 0 and recorder.stopped == 1, "the recorder is stopped"
    assert not list(tmp_path.iterdir())


def test_record_session_keeps_existing_clips_unless_told_to_overwrite(tmp_path):
    (tmp_path / "01.wav").write_bytes(b"old")
    (tmp_path / "01.txt").write_text("edited reference", encoding="utf-8")
    saved, script, _ = run_session(tmp_path, ["a"], FakeRecorder([]))
    assert saved == 0 and script.asked == []
    assert (tmp_path / "01.wav").read_bytes() == b"old"

    saved, _, _ = run_session(
        tmp_path, ["a"], FakeRecorder([1.0]), "", "", "", overwrite=True
    )
    assert saved == 1 and read_wav(tmp_path / "01.wav").size == 16000
    # The user may have edited the reference text: recording again must not undo that.
    assert (tmp_path / "01.txt").read_text(encoding="utf-8") == "edited reference"


def test_record_main_says_what_is_wrong_without_a_prompts_file(tmp_path, capsys):
    assert eval_record.main(["--prompts", str(tmp_path / "nope.txt"), "--out", str(tmp_path)]) == 1
    assert "nope.txt" in capsys.readouterr().err


def test_record_main_help_exits_cleanly(capsys):
    with pytest.raises(SystemExit) as raised:
        eval_record.main(["--help"])
    assert raised.value.code == 0
    assert "--prompts" in capsys.readouterr().out


# --- stt


def test_find_clips_pairs_each_wav_with_its_text(tmp_path):
    for name in ("02", "01", "03"):
        eval_record.write_wav(tmp_path / f"{name}.wav", np.zeros(1600, dtype="<i2"))
    (tmp_path / "01.txt").write_text("甲\n", encoding="utf-8")
    (tmp_path / "02.txt").write_text("﻿乙", encoding="utf-8")  # an editor's byte order mark
    clips, missing = eval_stt.find_clips(tmp_path)
    assert [(clip.name, clip.reference) for clip in clips] == [("01", "甲"), ("02", "乙")]
    assert missing == ["03"]


def test_split_long_keeps_short_audio_whole():
    samples = np.ones(15 * SAMPLE_RATE, dtype=np.float32)
    assert [chunk.size for chunk in eval_stt.split_long(samples)] == [samples.size]
    assert eval_stt.split_long(np.zeros(0, dtype=np.float32))[0].size == 0


def test_split_long_cuts_at_a_quiet_moment_and_loses_nothing():
    rate = SAMPLE_RATE
    samples = np.full(50 * rate, 0.5, dtype=np.float32)
    samples[18 * rate : 18 * rate + rate // 5] = 0.0  # a pause at 18.0 to 18.2 s
    chunks = eval_stt.split_long(samples)
    assert sum(chunk.size for chunk in chunks) == samples.size
    assert all(chunk.size <= 20 * rate for chunk in chunks)
    assert 18 * rate <= chunks[0].size <= 18 * rate + rate // 5, "cut inside the pause"
    assert np.array_equal(np.concatenate(chunks), samples)


class FakeEngine:
    def __init__(self, name, answers):
        self.name = name
        self.answers = answers  # clip length in samples -> text
        self.heard = []

    def transcribe(self, samples, *, hints=(), language="auto"):
        self.heard.append(samples.size)
        return self.answers[samples.size]


def ticking_clock(step):
    now = -step

    def clock():
        nonlocal now
        now += step
        return now

    return clock


def make_clips(tmp_path, *clips):
    for name, seconds, reference in clips:
        eval_record.write_wav(tmp_path / f"{name}.wav", np.full(int(seconds * 16000), 900, "<i2"))
        (tmp_path / f"{name}.txt").write_text(reference, encoding="utf-8")
    return eval_stt.find_clips(tmp_path)[0]


def test_evaluate_engine_measures_cer_and_time(tmp_path):
    clips = make_clips(tmp_path, ("01", 2.0, "今天天氣很好"), ("02", 4.0, "OK 好的"))
    engine = FakeEngine("fake", {32000: "今天天汽很好", 64000: "ok好的"})
    results = eval_stt.evaluate_engine(engine, clips, clock=ticking_clock(0.5))
    assert [r.clip for r in results] == ["01", "02"]
    assert results[0].cer == pytest.approx(1 / 6) and results[1].cer == 0.0
    assert [r.audio_seconds for r in results] == [2.0, 4.0]
    assert [r.seconds for r in results] == [0.5, 0.5]  # the clock moves 0.5 per reading
    assert results[0].hypothesis == "今天天汽很好" and results[0].reference == "今天天氣很好"
    assert engine.heard[:2] == [32000, 32000], "the first clip is decoded once untimed to warm up"

    summary = eval_stt.summarize("fake", results)
    assert summary.clips == 2
    assert summary.mean_cer == pytest.approx(1 / 12)
    assert summary.rtf == pytest.approx(1.0 / 6.0)  # 1.0 s of work for 6.0 s of audio
    assert summary.mean_seconds == pytest.approx(0.5)


def test_evaluate_engine_joins_the_text_of_a_long_clip(tmp_path):
    rate = 16000
    clips = make_clips(tmp_path, ("01", 30.0, "前半後半"))
    sizes = [c.size for c in eval_stt.split_long(np.full(30 * rate, 900 / 32768, np.float32))]
    engine = FakeEngine("fake", {sizes[0]: "前半", sizes[1]: "後半"})
    (result,) = eval_stt.evaluate_engine(engine, clips, clock=ticking_clock(1.0))
    assert result.hypothesis == "前半 後半" and result.cer == 0.0
    assert result.seconds == 1.0, "one reading before and one after all the pieces"


def test_write_csvs(tmp_path):
    clips = make_clips(tmp_path, ("01", 1.0, "你好，世界"))
    results = eval_stt.evaluate_engine(
        FakeEngine("fake", {16000: "你好世界"}), clips, clock=ticking_clock(0.25)
    )
    summary_path, clips_path = tmp_path / "stt_results.csv", tmp_path / "stt_clips.csv"
    eval_stt.write_summary_csv(summary_path, [eval_stt.summarize("fake", results)])
    eval_stt.write_clips_csv(clips_path, results)
    with summary_path.open(encoding="utf-8-sig", newline="") as file:
        (row,) = list(csv.DictReader(file))
    assert row == {
        "engine": "fake",
        "clips": "1",
        "mean_cer": "0.0000",
        "rtf": "0.2500",
        "mean_seconds": "0.250",
    }
    with clips_path.open(encoding="utf-8-sig", newline="") as file:
        (row,) = list(csv.DictReader(file))
    assert row["reference"] == "你好，世界" and row["hypothesis"] == "你好世界"
    assert row["engine"] == "fake" and row["clip"] == "01" and row["cer"] == "0.0000"


class FakeModels:
    def __init__(self):
        self.ensured = []

    def ensure(self, name, progress=None):
        self.ensured.append(name)


def test_stt_main_runs_every_engine_and_writes_the_results(tmp_path, capsys):
    make_clips(tmp_path, ("01", 1.0, "你好世界"))
    engines = {
        "good": FakeEngine("good", {16000: "你好世界"}),
        "bad": FakeEngine("bad", {16000: "你好"}),
    }
    models = FakeModels()
    code = eval_stt.main(
        ["--data", str(tmp_path), "--engines", "good,bad"],
        models=models,
        engine_factory=lambda name, models, threads: engines[name],
    )
    assert code == 0 and models.ensured == ["good", "bad"]
    out = capsys.readouterr().out
    assert "good" in out and "bad" in out
    with (tmp_path / "stt_results.csv").open(encoding="utf-8-sig", newline="") as file:
        rows = {row["engine"]: row for row in csv.DictReader(file)}
    assert rows["good"]["mean_cer"] == "0.0000" and rows["bad"]["mean_cer"] == "0.5000"


def test_stt_main_goes_on_after_an_engine_that_cannot_load(tmp_path, capsys):
    make_clips(tmp_path, ("01", 1.0, "你好"))

    def factory(name, models, threads):
        if name == "broken":
            raise FileNotFoundError("The speech model broken is not downloaded")
        return FakeEngine(name, {16000: "你好"})

    code = eval_stt.main(
        ["--data", str(tmp_path), "--engines", "broken,fine"], models=FakeModels(), engine_factory=factory
    )
    captured = capsys.readouterr()
    assert code == 1, "an engine failed"
    assert "broken" in captured.err
    with (tmp_path / "stt_results.csv").open(encoding="utf-8-sig", newline="") as file:
        assert [row["engine"] for row in csv.DictReader(file)] == ["fine"]


def test_stt_main_without_clips_says_so(tmp_path, capsys):
    code = eval_stt.main(["--data", str(tmp_path)], models=FakeModels(), engine_factory=None)
    assert code == 1
    assert "record" in capsys.readouterr().err


def test_stt_main_help_exits_cleanly(capsys):
    with pytest.raises(SystemExit) as raised:
        eval_stt.main(["--help"])
    assert raised.value.code == 0
    assert "--engines" in capsys.readouterr().out


# --- cleanup


def case_file(tmp_path, *lines):
    path = tmp_path / "cases.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_load_cases_reads_optional_fields(tmp_path):
    path = case_file(
        tmp_path,
        '{"id": "a", "input": "你好", "must_contain": ["你"]}',
        "",
        '{"id": "b", "input": "x", "dictionary": ["W"], "must_not_contain": ["y"],'
        ' "target_app": "LINE.exe"}',
    )
    first, second = eval_cleanup.load_cases(path)
    assert (first.id, first.input, first.dictionary) == ("a", "你好", [])
    assert (first.must_contain, first.must_not_contain, first.target_app) == (["你"], [], None)
    assert (second.dictionary, second.must_not_contain) == (["W"], ["y"])
    assert second.target_app == "LINE.exe"


def test_optional_name_and_alternative_checks_reject_missing_content(tmp_path):
    path = case_file(
        tmp_path,
        '{"id": "a", "input": "Ask Gemini using a permission agent",'
        ' "must_contain_ci": ["Gemini"],'
        ' "must_contain_any": ["permission agent", "personal agent"]}',
    )
    case = eval_cleanup.load_cases(path)[0]
    assert len(eval_cleanup.failed_checks(case, "Ask using an agent.")) == 2
    assert eval_cleanup.failed_checks(case, "Ask GEMINI using a Permission Agent.") == []
    assert eval_cleanup.failed_checks(case, "Ask gemini using a personal agent.") == []


@pytest.mark.parametrize("field", ["must_contain_ci", "must_contain_any"])
@pytest.mark.parametrize("value", ['"Gemini"', '[1]'])
def test_load_cases_validates_optional_name_checks(tmp_path, field, value):
    line = f'{{"id": "a", "input": "x", "{field}": {value}}}'
    with pytest.raises(ValueError, match=field):
        eval_cleanup.load_cases(case_file(tmp_path, line))


@pytest.mark.parametrize(
    "line, fragment",
    [
        ("{not json", "第 1 行"),
        ('["a"]', "第 1 行"),
        ('{"input": "x"}', "id"),
        ('{"id": "a"}', "input"),
        ('{"id": "a", "input": "x", "must_contain": "y"}', "must_contain"),
        ('{"id": "a", "input": "x", "dictionary": [1]}', "dictionary"),
        ('{"id": "a", "input": "x", "target_app": 1}', "target_app"),
        ('{"id": "a", "input": "x", "target_app": ["LINE.exe"]}', "target_app"),
    ],
)
def test_load_cases_names_the_problem(tmp_path, line, fragment):
    with pytest.raises(ValueError, match=fragment):
        eval_cleanup.load_cases(case_file(tmp_path, line))


def test_load_cases_refuses_a_repeated_id(tmp_path):
    line = '{"id": "a", "input": "x"}'
    with pytest.raises(ValueError, match="重複"):
        eval_cleanup.load_cases(case_file(tmp_path, line, line))


def make_case(**fields):
    return eval_cleanup.Case(
        id=fields.pop("id", "c"),
        input=fields.pop("input", "明天 不對 後天下午三點開會"),
        dictionary=fields.pop("dictionary", []),
        must_contain=fields.pop("must_contain", []),
        must_not_contain=fields.pop("must_not_contain", []),
        target_app=fields.pop("target_app", None),
        **fields,
    )


def test_a_case_is_for_the_users_three_languages_unless_it_says_otherwise(tmp_path):
    path = case_file(
        tmp_path,
        '{"id": "a", "input": "x", "must_contain": ["x"]}',
        '{"id": "b", "input": "x", "must_contain": ["x"], "languages": ["zh-CN", "en-GB"]}',
    )
    first, second = eval_cleanup.load_cases(path)
    assert first.languages == ["zh-TW", "en-US", "ja-JP"] == list(eval_cleanup.DEFAULT_LANGUAGES)
    assert second.languages == ["zh-CN", "en-GB"]
    assert make_case().languages == ["zh-TW", "en-US", "ja-JP"]


@pytest.mark.parametrize("languages", ['"zh-TW"', "[]", '["zh-TW", "zh-CN"]', '["xx"]', "[1]"])
def test_a_case_with_languages_that_are_no_valid_selection_is_refused(tmp_path, languages):
    line = f'{{"id": "a", "input": "x", "languages": {languages}}}'
    with pytest.raises(ValueError, match="第 1 行.*languages"):
        eval_cleanup.load_cases(case_file(tmp_path, line))


async def test_run_case_sends_the_prompt_of_the_cases_languages():
    case = make_case(languages=["zh-CN", "en-GB"], must_contain=["软件"])
    client = FakeClient(["这个軟件。"])
    outcome = await eval_cleanup.run_case(client, "m", case)
    system = client.calls[0]["system"]
    assert "The speaker's languages: 簡體中文（中國）, English (United Kingdom)." in system
    assert "Simplified characters with Mainland usage" in system
    # What the app does with the reply for zh-CN: what is left in Traditional is made Simplified.
    assert outcome.text == "这个软件。" and outcome.failures == []


def test_failed_checks_follow_the_cases_chinese_variant():
    hong_kong = make_case(languages=["zh-HK"], must_contain=["軟件"], must_not_contain=["軟體"])
    assert eval_cleanup.failed_checks(hong_kong, "这个软件。") == []
    mainland = make_case(languages=["zh-CN"], must_contain=["这个"])
    assert eval_cleanup.failed_checks(mainland, "這個。") == []


def test_failed_checks_pass_for_a_good_output():
    case = make_case(must_contain=["後天下午三點"], must_not_contain=["明天"])
    assert eval_cleanup.failed_checks(case, "後天下午三點開會。") == []


def test_failed_checks_name_what_is_missing_and_what_must_not_be_there():
    case = make_case(must_contain=["後天"], must_not_contain=["明天"])
    failures = eval_cleanup.failed_checks(case, "明天開會。")
    assert len(failures) == 2
    assert any("後天" in f and "缺少" in f for f in failures)
    assert any("明天" in f and "不該" in f for f in failures)


def test_failed_checks_ignore_spacing_between_scripts():
    case = make_case(must_contain=["打開 YouTube", "race condition"], must_not_contain=["了。"])
    assert eval_cleanup.failed_checks(case, "打開YouTube，race  condition。") == []


def test_failed_checks_judge_the_text_the_user_gets_after_the_apps_processing():
    # After the cleanup the app converts what the model left in Simplified Chinese (打开 into
    # 開啟 with OpenCC s2twp), so that is the text the checks look at.
    reply = "幫我打开 YouTube。"
    assert eval_cleanup.failed_checks(make_case(must_contain=["開啟 YouTube"]), reply) == []
    assert eval_cleanup.failed_checks(make_case(must_contain=["打开"]), reply) != []
    assert eval_cleanup.failed_checks(make_case(must_not_contain=["打开"]), reply) == []
    assert eval_cleanup.failed_checks(make_case(must_contain=["今天天氣"]), "今天天气很好。") == []


def test_failed_checks_see_the_traditional_chinese_of_the_model_as_it_wrote_it():
    # s2twp alone would turn this 只要 into 隻要 and 打開 into 開啟; the app leaves both alone.
    case = make_case(must_contain=["只要", "打開"], must_not_contain=["隻要", "開啟"])
    assert eval_cleanup.failed_checks(case, "就是只要打開就好。") == []


def test_failed_checks_leave_the_kanji_of_a_text_with_hiragana_alone():
    case = make_case(input="学校で会います", must_contain=["学校で会います"])
    assert eval_cleanup.failed_checks(case, "学校で会います。") == []


def test_failed_checks_apply_the_length_rule_to_the_reply_as_it_came():
    case = make_case(input="你好嗎")  # 3 characters: the limit is 1.5 * 3 + 20 = 24.5
    assert eval_cleanup.failed_checks(case, "你" * 24) == []
    assert eval_cleanup.failed_checks(case, "你" * 12 + "\n\n" + "你" * 12) == []
    assert any("過長" in f for f in eval_cleanup.failed_checks(case, "你" * 25))
    assert any("空" in f for f in eval_cleanup.failed_checks(case, "  "))
    # Converting 开 into 開 does not change the length, but the rule is on the reply in any case.
    assert any("過長" in f for f in eval_cleanup.failed_checks(case, "开" * 25))


def test_the_length_rule_and_the_prompt_helpers_are_the_ones_of_the_service():
    assert eval_cleanup.is_suspicious_reply is brain_cleanup.is_suspicious_reply
    assert eval_cleanup.build_user_message is brain_cleanup.build_user_message
    assert eval_cleanup.max_tokens_for is brain_cleanup.max_tokens_for
    assert not hasattr(eval_cleanup, "is_suspicious") and not hasattr(eval_cleanup, "length_limit")


def test_cases_survive_the_apps_text_processing():
    """A text to look for that the app's conversion would change (打開 into 開啟) is never found."""
    cases = all_cases()
    for case in cases:
        chinese = chinese_variant(case.languages)
        for needle in [*case.must_contain, *case.must_contain_ci, *case.must_contain_any]:
            kept = finish_cleaned_text(needle, [], chinese=chinese)
            assert kept == needle, f"{case.id}: {needle}"
        for needle in case.must_not_contain:
            # It may change when the changed form is a text to avoid as well (台北 and 臺北).
            converted = finish_cleaned_text(needle, [], chinese=chinese)
            assert converted in (needle, *case.must_not_contain), f"{case.id}: {needle}"
    by_id = {case.id: case for case in cases}
    assert by_id["command-stays-text"].must_contain == ["YouTube"]
    assert by_id["command-stays-text"].must_not_contain == ["了。"]
    assert by_id["question-capital"].must_contain == ["首都是哪裡", "？"]
    # The cases built from the user's own dictations, and the tone of a chat app.
    assert "Alt" in by_id["mishear-alt-key"].must_contain
    assert "隻要" in by_id["opencc-zhi"].must_not_contain
    assert by_id["chat-tone-line"].target_app == "LINE.exe"
    # The lists: the user's own long dictation into a terminal, and one that is not a list.
    assert by_id["numbered-list-long-terminal"].target_app == "WindowsTerminal.exe"
    assert by_id["numbered-list-varied-ordinals"].target_app == "OUTLOOK.EXE"
    assert "1." in by_id["first-time-not-a-list"].must_not_contain
    terminal_cases = {
        "real-hermes-personal-agent", "real-claude-pretraining", "mishear-ai-product-names",
        "mishear-dev-product-names", "literal-common-words-stay",
    }
    assert all(by_id[case_id].target_app == "WindowsTerminal.exe" for case_id in terminal_cases)
    with_app = {
        "chat-tone-line", "numbered-list-long-terminal", "numbered-list-varied-ordinals",
        *terminal_cases,
    }
    assert all(case.target_app is None for case in cases if case.id not in with_app)
    # The variants: Mainland Simplified, Hong Kong usage and British spelling.
    assert by_id["variant-zh-cn-simplified"].languages[0] == "zh-CN"
    assert "软件" in by_id["variant-zh-cn-simplified"].must_contain
    assert by_id["variant-zh-hk-usage"].languages[0] == "zh-HK"
    assert "軟件" in by_id["variant-zh-hk-usage"].must_contain
    assert by_id["variant-en-gb-spelling"].languages == ["en-GB"]
    assert "colour" in by_id["variant-en-gb-spelling"].must_contain_ci
    variants = {"variant-zh-cn-simplified", "variant-zh-hk-usage", "variant-en-gb-spelling"}
    language_overrides = {
        "false-start-en-fuller-phrase": ["en-US"],
        "false-start-ja-fuller-phrase": ["ja-JP"],
        "literal-common-words-stay": ["en-US"],
        "variant-zh-cn-documents": ["zh-CN", "en-US"],
        "variant-zh-hk-documents": ["zh-HK", "en-GB"],
        **dict.fromkeys(
            [
                "heldout-stutter-person-en",
                "heldout-restart-fuller-en-question",
                "heldout-canonical-claude-asked",
                "heldout-literal-doctor-stays",
                "heldout-literal-cloud-weather",
                "heldout-literal-cursor-text-en",
            ],
            ["en-US"],
        ),
    }
    assert all(by_id[case_id].languages == value for case_id, value in language_overrides.items())
    others = [case for case in cases if case.id not in variants | language_overrides.keys()]
    assert all(case.languages == ["zh-TW", "en-US", "ja-JP"] for case in others)


@pytest.mark.parametrize(
    "case_id, cleaned",
    [
        (
            "real-hermes-personal-agent",
            "之後會有 Hermes Agent 可以幫我看 Gmail，處理 GitHub 的東西，"
            "基本上任何事情它都可以做，它就是一個 personal agent。",
        ),
        (
            "real-claude-pretraining",
            "理論上 Claude 的 pre-training 的資料應該有辦法知道 Hermes Agent 是什麼。",
        ),
        ("false-start-zh-fuller-phrase", "我想先把這個功能做完，明天再測試。"),
        (
            "false-start-en-fuller-phrase",
            "Please update the config file before we restart the server.",
        ),
        ("false-start-ja-fuller-phrase", "明日の会議は三時からです。資料を先に送ってください。"),
        (
            "mishear-ai-product-names",
            "我用 Claude 和 ChatGPT 比較模型，再用 Gemini 跑一次，結果放在 GitHub。",
        ),
        (
            "mishear-dev-product-names",
            "我用 OpenRouter 呼叫模型，在 Cursor 裡寫 Python，再用 Tauri 做桌面介面。",
        ),
        ("opencc-zhi-only", "只有這個版本可以用，只要按一下就好。"),
    ],
)
def test_new_cleanup_cases_reject_the_reported_errors_and_accept_corrected_text(case_id, cleaned):
    case = case_named(case_id)
    assert eval_cleanup.failed_checks(case, case.input)
    assert eval_cleanup.failed_checks(case, cleaned) == []


@pytest.mark.parametrize(
    "case_id, cleaned, wrong_variant",
    [
        (
            "variant-zh-cn-simplified",
            "这个软件的资料我明天再发给你。",
            "这个软体的资料我明天再发给你。",
        ),
        (
            "variant-zh-hk-usage",
            "這個軟件的資料我明天再發給你，你先看一下那個影片。",
            "這個軟體的資料我明天再發給你，你先看一下那個影片。",
        ),
    ],
)
def test_variant_cases_accept_information_without_forcing_it_into_data(
    case_id, cleaned, wrong_variant
):
    cases = eval_cleanup.load_cases(REPO / "eval" / "cleanup_cases.jsonl")
    case = next(case for case in cases if case.id == case_id)
    assert eval_cleanup.failed_checks(case, cleaned) == []
    assert eval_cleanup.failed_checks(case, wrong_variant)


@pytest.mark.parametrize(
    "case_id, cleaned, changed_meaning",
    [
        (
            "variant-zh-cn-documents",
            "这份申请资料包含推荐信，我明天再发给你。",
            "这份申请数据包含推荐信，我明天再发给你。",
        ),
        (
            "variant-zh-hk-documents",
            "這份申請資料包含推薦信，我明天再發給你。",
            "這份申請數據包含推薦信，我明天再發給你。",
        ),
        (
            "mishear-related-book-names",
            "我在做魔戒裡的甘道夫這個角色，作者是托爾金。",
            "我在做魔界裡的甘道夫這個角色，作者是托耳金。",
        ),
        (
            "literal-distance-stays",
            "這段影片裡我轉身走了十來米，再坐在地上。",
            "這段影片裡我轉生走了史萊姆，再坐在地上。",
        ),
    ],
)
def test_context_cases_distinguish_corrections_from_changes_of_meaning(
    case_id, cleaned, changed_meaning
):
    cases = eval_cleanup.load_cases(REPO / "eval" / "cleanup_cases.jsonl")
    case = next(case for case in cases if case.id == case_id)
    assert eval_cleanup.failed_checks(case, cleaned) == []
    assert eval_cleanup.failed_checks(case, changed_meaning)


def test_literal_common_words_case_rejects_overcorrection():
    cases = eval_cleanup.load_cases(REPO / "eval" / "cleanup_cases.jsonl")
    case = next(case for case in cases if case.id == "literal-common-words-stay")
    assert eval_cleanup.failed_checks(case, case.input) == []
    overcorrected = case.input.replace("cloud", "Claude").replace("cursor", "Cursor")
    overcorrected = overcorrected.replace("permission agent", "personal agent")
    overcorrected = overcorrected.replace("python", "Python")
    assert len(eval_cleanup.failed_checks(case, overcorrected)) == 8


# --- the two files of cases: held out, and regression
# The prompt must not hold what the cases ask for, or the cases measure memory and not judgment.


def prompt_examples(prompt):
    """The (transcript, reply) pairs under "Examples" in the prompt."""
    lines = prompt.split("Examples (transcript → reply):\n", 1)[1].splitlines()
    return [
        (first[2:], second.split("→ ", 1)[1])
        for first, second in zip(lines, lines[1:], strict=False)
        if first.startswith("- ") and second.startswith("  → ")
    ]


def test_the_held_out_file_has_new_cases_and_none_of_the_owners_anecdotes():
    cases = eval_cleanup.load_cases(HELD_OUT)
    assert len([case for case in cases if case.id.startswith("heldout-")]) >= 8
    assert not any(case.id.startswith("real-") for case in cases)


def test_the_regression_file_holds_the_anecdotal_cases_and_runs_with_the_same_tool(tmp_path):
    regression = eval_cleanup.load_cases(REGRESSION)
    ids = {case.id for case in regression}
    assert {
        "real-hermes-personal-agent",
        "real-claude-pretraining",
        "false-start-zh-fuller-phrase",
        "false-start-en-fuller-phrase",
        "mishear-alt-key",
        "mishear-window",
        "mishear-transcription",
        "opencc-zhi",
        "opencc-zhi-only",
        "numbered-list-long-terminal",
    } <= ids
    assert not ids & {case.id for case in eval_cleanup.load_cases(HELD_OUT)}
    # `--cases` takes it like any other file.
    store = MemoryKeyStore()
    store.set("sk-test")
    client = FakeClient(["好。"] * len(regression))
    report = tmp_path / "report.md"
    argv = ["--cases", str(REGRESSION), "--report", str(report), "--models", "m"]
    assert eval_cleanup.main(argv, key_store=store, client_factory=lambda key: client) == 0
    assert len(client.calls) == len(regression)
    assert all(f"### {case.id}" in report.read_text(encoding="utf-8") for case in regression)


def test_the_prompt_holds_no_case_input_and_none_of_the_old_examples():
    prompt = brain_cleanup.build_system_prompt([], [])
    for case in all_cases():
        assert case.input not in prompt, case.id
    for example, reply in prompt_examples(prompt):
        for case in all_cases():
            assert case.input not in example and example not in case.input, case.id
    for leaked in (
        "Hermes，Hermes agents",
        "cloud 的 pre-training",
        "personal agent",
        "我想要，我想先",
        "the, the config",
        "隻要",
        "隻有",
        "開啟拉斐爾老師",
        "TypeList",
        "試床",
        "轉入",
        "轉路",
        "O S",
        "明天……不對，後天",
    ):
        assert leaked not in prompt, leaked


def test_the_prompt_still_has_its_general_rules():
    prompt = brain_cleanup.build_system_prompt([], [])
    for kept in (
        "Restore known product, model, and technical names",
        "Claude, ChatGPT, Gemini, Cursor, GitHub, OpenRouter, Hermes Agent, Tauri, Python",
        "keep only the final complete form",
        "wrong Simplified-to-Traditional conversions",
        "Keep only the final self-correction",
    ):
        assert kept in prompt, kept
    assert len(prompt) < 7_000, "the examples were swapped, not added to"


@pytest.mark.parametrize("name", ["Hermes agent", "Hermes Agent", "Hermes agents"])
@pytest.mark.parametrize("role", ["permission agent", "personal agent"])
def test_the_hermes_case_accepts_what_the_owner_has_not_ruled_out(name, role):
    case = case_named("real-hermes-personal-agent")
    cleaned = (
        f"之後會有 {name} 可以幫我看 Gmail，處理 GitHub 的東西，"
        f"基本上任何事情它都可以做，它就是一個 {role}。"
    )
    assert eval_cleanup.failed_checks(case, cleaned) == []


def test_the_hermes_case_still_rejects_the_stutter_and_a_missing_role():
    case = case_named("real-hermes-personal-agent")
    stutter = "之後會有 Hermes，Hermes agent 可以幫我看 Gmail，處理 GitHub，任何事情都是 personal agent。"
    assert any("Hermes，Hermes" in f for f in eval_cleanup.failed_checks(case, stutter))
    no_role = "之後會有 Hermes Agent 可以幫我看 Gmail，處理 GitHub 的東西，任何事情它都可以做。"
    assert any("一個都沒有" in f for f in eval_cleanup.failed_checks(case, no_role))


@pytest.mark.parametrize(
    "cleaned",
    ["我想先把這個功能做完，明天再測試。", "我想要先把這個功能做完，明天再測試。"],
)
def test_the_chinese_restart_case_accepts_the_fuller_form_with_or_without_yao(cleaned):
    assert eval_cleanup.failed_checks(case_named("false-start-zh-fuller-phrase"), cleaned) == []


@pytest.mark.parametrize(
    "cleaned",
    [
        "我想要，我想先把這個功能做完，明天再測試。",
        "我想要我想先把這個功能做完，明天再測試。",
        "我想，我想先把這個功能做完，明天再測試。",
        "我想把這個功能做完，明天再測試。",
    ],
)
def test_the_chinese_restart_case_still_rejects_the_restart_and_a_lost_idea(cleaned):
    assert eval_cleanup.failed_checks(case_named("false-start-zh-fuller-phrase"), cleaned)


def test_names_are_looked_for_in_any_letter_case_where_the_case_asks_for_it():
    case = case_named("real-claude-pretraining")
    cleaned = "理論上 Claude 的 pre-training 的資料應該有辦法知道 HERMES AGENT 是什麼。"
    assert eval_cleanup.failed_checks(case, cleaned) == []
    wrong_name = cleaned.replace("Claude", "claude")
    assert eval_cleanup.failed_checks(case, wrong_name), "the canonical spelling is the point here"
    fillers = case_named("fillers-en")
    assert eval_cleanup.failed_checks(fillers, "We should ship it on Friday.") == []


@pytest.mark.parametrize(
    ("case_id", "good", "bad"),
    [
        (
            "heldout-stutter-docker-compose",
            "我想把服務放到 Docker Compose 裡面跑。",
            "我想把服務放到 Docker，Docker Compose 裡面跑。",
        ),
        (
            "heldout-stutter-person-en",
            "Please ask Priya Raman to review the draft.",
            "Please ask Priya, Priya Raman to review the draft.",
        ),
        (
            "heldout-stutter-zh-demonstrative",
            "我覺得這個方案需要再改一下。",
            "我覺得這個，這個方案需要再改一下。",
        ),
        (
            "heldout-restart-fuller-zh-question",
            "你可以先把檔案備份一下嗎？",
            "你可以幫我，你可以先把檔案備份一下嗎？",
        ),
        (
            "heldout-restart-fuller-zh-question",
            "你可以幫我先把檔案備份一下嗎？",
            "好的，我會先把檔案備份一下。",
        ),
        (
            "heldout-restart-fuller-en-question",
            "Can you send me the latest invoice by tomorrow?",
            "Can you send, can you send me the latest invoice by tomorrow?",
        ),
        (
            "heldout-restart-name-zh",
            "請把這份文件寄給王經理，謝謝。",
            "請把這份文件寄給王，寄給王經理，謝謝。",
        ),
        (
            "heldout-canonical-docker-context",
            "把這個服務放進 Docker Compose 的 yaml 裡。",
            "把這個服務放進 doctor compose 的 yaml 裡。",
        ),
        (
            "heldout-canonical-docker-context",
            "把這個服務放進 docker compose 的 yaml 裡。",
            "把這個服務放進 doctor compose 的 yaml 裡。",
        ),
        (
            "heldout-canonical-openai-gemini",
            "我先用 Gemini 分析，再丟給 OpenAI 的模型整理。",
            "我先用 gem and I 分析，再丟給 open AI 的模型整理。",
        ),
        (
            "heldout-canonical-claude-asked",
            "I asked Claude to refactor the parser, and it wrote the tests too.",
            "I asked cloud to refactor the parser and it wrote the tests too.",
        ),
        (
            "heldout-literal-doctor-stays",
            "The doctor said I should rest, so I skipped the gym.",
            "The Docker said I should rest, so I skipped the gym.",
        ),
        (
            "heldout-literal-cloud-weather",
            "Tomorrow there will be heavy cloud over the mountains, so bring a jacket.",
            "Tomorrow there will be heavy Claude over the mountains, so bring a jacket.",
        ),
        (
            "heldout-literal-python-snake-zh",
            "動物園新來了一條 python，飼養員說牠一個月只吃一次老鼠。",
            "動物園新來了一條 Python，飼養員說牠一個月只吃一次老鼠。",
        ),
        (
            "heldout-literal-cursor-text-en",
            "Press Home to move the cursor to the start of the line.",
            "Press Home to move the Cursor to the start of the line.",
        ),
        (
            "heldout-literal-permission-agent",
            "這個 permission agent 會在每次安裝套件前向使用者要求核准。",
            "這個 personal agent 會在每次安裝套件前向使用者要求核准。",
        ),
    ],
)
def test_the_held_out_cases_accept_a_good_cleanup_and_reject_the_error(case_id, good, bad):
    case = case_named(case_id)
    assert eval_cleanup.failed_checks(case, good) == []
    assert eval_cleanup.failed_checks(case, bad)


def test_render_report_lists_the_case_insensitive_and_alternative_checks():
    case = make_case(
        id="c",
        must_contain_ci=["Hermes agent"],
        must_contain_any=["personal agent", "permission agent"],
    )
    outcome = eval_cleanup.Outcome("c", "m", "好。", None, 1, 0.0, ["x"], reply="好。")
    report = eval_cleanup.render_report([case], ["m"], [outcome], "now")
    assert "必須出現（不分大小寫）：「Hermes agent」" in report
    assert "至少出現一個（不分大小寫）：「personal agent」、「permission agent」" in report


def test_failed_checks_say_which_kind_of_check_failed():
    case = make_case(must_contain_ci=["Hermes agent"], must_contain_any=["a b", "c d"])
    failures = eval_cleanup.failed_checks(case, "hello")
    assert any("Hermes agent" in f and "不分大小寫" in f for f in failures)
    assert any("一個都沒有" in f and "a b" in f and "c d" in f for f in failures)
    assert eval_cleanup.failed_checks(case, "HERMES AGENT, C D") == []


class FakeClient:
    """Answers each call with the next scripted reply: a string (the cleaned text) or an error."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.closed = False

    async def complete_json(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return Completion(
            data={"text": reply},
            model=kwargs["model"],
            cost_usd=0.000125,
            prompt_tokens=300,
            completion_tokens=20,
            latency_ms=420,
        )

    async def aclose(self):
        self.closed = True


async def test_run_case_sends_the_real_prompt_and_checks_the_reply():
    case = make_case(
        input="拉菲爾老師你好",
        dictionary=["拉斐爾"],
        must_contain=["拉斐爾"],
    )
    client = FakeClient(["拉斐爾老師你好。"])
    outcome = await eval_cleanup.run_case(client, "vendor/model", case)
    (call,) = client.calls
    assert call["model"] == "vendor/model" and call["fallbacks"] == []
    assert "- 拉斐爾" in call["system"], "the dictionary is in the prompt"
    assert call["user"] == "<transcript>\n拉菲爾老師你好\n</transcript>"
    assert call["schema"] == eval_cleanup.CLEANUP_SCHEMA
    assert outcome.text == "拉斐爾老師你好。" and outcome.failures == []
    assert outcome.cost_usd == 0.000125 and outcome.latency_ms == 420 and outcome.error is None
    assert call["max_tokens"] == brain_cleanup.max_tokens_for("拉菲爾老師你好")


async def test_run_case_sends_the_transcript_as_heard_like_the_app_does():
    # The app gives the model the speech-to-text result without OpenCC's conversion.
    case = make_case(input="我们用这个软件 就是只要")
    client = FakeClient(["我們用這個軟體，只要。"])
    await eval_cleanup.run_case(client, "m", case)
    assert client.calls[0]["user"] == "<transcript>\n我们用这个软件 就是只要\n</transcript>"
    assert eval_cleanup.transcript_for(case) == case.input


async def test_run_case_names_the_target_app_of_the_case():
    client = FakeClient(["好。", "好。"])
    await eval_cleanup.run_case(client, "m", make_case(target_app="LINE.exe"))
    await eval_cleanup.run_case(client, "m", make_case())
    assert "Target app: LINE.exe (chat)" in client.calls[0]["system"]
    assert "Target app: unknown" in client.calls[1]["system"]
    assert "LINE" not in client.calls[0]["user"]


def test_render_report_names_the_target_app_of_a_case():
    case = make_case(id="c", target_app="LINE.exe")
    outcome = eval_cleanup.Outcome("c", "m", "好。", None, 1, 0.0, [], reply="好。")
    assert "LINE.exe" in eval_cleanup.render_report([case], ["m"], [outcome], "now")


async def test_run_case_keeps_the_reply_and_the_final_text_apart():
    case = make_case(input="幫我打開 YouTube", must_contain=["YouTube"], must_not_contain=["了。"])
    outcome = await eval_cleanup.run_case(FakeClient(["幫我打开 YouTube。"]), "m", case)
    assert outcome.reply == "幫我打开 YouTube。", "what the model wrote"
    assert outcome.text == "幫我開啟 YouTube。", "what the app would paste"
    assert outcome.failures == []


async def test_run_case_records_a_failed_call_as_a_failure():
    error = OpenRouterError(429, "rate_limit", "slow down")
    outcome = await eval_cleanup.run_case(FakeClient([error]), "m", make_case())
    assert outcome.text is None and outcome.error == "rate_limit"
    assert outcome.failures and "rate_limit" in outcome.failures[0]


@pytest.mark.parametrize("kind", ["auth", "credits"])
async def test_run_all_stops_when_the_key_or_the_credit_is_the_problem(kind):
    client = FakeClient([OpenRouterError(401, kind, "no")])
    with pytest.raises(eval_cleanup.Abort) as raised:
        await eval_cleanup.run_all(client, ["m1", "m2"], [make_case(), make_case(id="d")])
    assert raised.value.error.kind == kind
    assert len(client.calls) == 1


async def test_abort_carries_what_was_done_before_it():
    client = FakeClient(["你好。", OpenRouterError(402, "credits", "no credit")])
    with pytest.raises(eval_cleanup.Abort) as raised:
        await eval_cleanup.run_all(client, ["m1", "m2"], [make_case()])
    assert [(o.model, o.case_id) for o in raised.value.outcomes] == [("m1", "c")]


def test_render_report_lists_outputs_latency_cost_and_failures():
    good, bad = make_case(id="good", must_contain=["後天"]), make_case(id="bad", must_contain=["後天"])
    outcomes = [
        eval_cleanup.Outcome("good", "vendor/a", "後天開會。\n第二行", None, 400, 0.0001, []),
        eval_cleanup.Outcome("bad", "vendor/a", "明天開會。", None, 800, 0.0002, ["缺少「後天」"]),
    ]
    report = eval_cleanup.render_report([good, bad], ["vendor/a"], outcomes, "2026-10-18 12:00")
    assert report.startswith("# ")
    assert "vendor/a" in report and "2026-10-18 12:00" in report
    assert "後天開會。\n第二行" in report, "the output is shown as it came"
    assert "缺少「後天」" in report
    assert "1 / 2" in report, "pass count in the overview"
    assert "600" in report, "mean latency (400 and 800 ms)"
    assert "0.000300" in report, "total cost"
    assert "### bad" in report and "### good" in report


def test_render_report_shows_the_final_text_and_the_model_reply():
    case = make_case(id="c")
    changed = eval_cleanup.Outcome("c", "m", "開啟 YouTube。", None, 1, 0.0, [], reply="打开 YouTube。")
    report = eval_cleanup.render_report([case], ["m"], [changed], "now")
    assert "最終文字" in report and "開啟 YouTube。" in report
    assert "模型原始輸出" in report and "打开 YouTube。" in report
    same = eval_cleanup.Outcome("c", "m", "你好。", None, 1, 0.0, [], reply="你好。")
    report = eval_cleanup.render_report([case], ["m"], [same], "now")
    assert report.count("你好。") == 1, "an unchanged reply is not shown twice"
    assert "與最終文字相同" in report


def test_render_report_notes_why_it_is_partial():
    case = make_case(id="c")
    outcome = eval_cleanup.Outcome("c", "m1", "你好。", None, 1, 0.0, [], reply="你好。")
    report = eval_cleanup.render_report([case], ["m1"], [outcome], "now", note="中止了")
    assert "中止了" in report


def test_render_report_survives_backticks_in_the_output():
    case = make_case(id="c")
    outcome = eval_cleanup.Outcome("c", "m", "```code```", None, 1, 0.0, [])
    report = eval_cleanup.render_report([case], ["m"], [outcome], "now")
    assert "````\n```code```\n````" in report


def run_cleanup_main(tmp_path, argv, **kwargs):
    cases = case_file(tmp_path, '{"id": "a", "input": "嗯 你好", "must_contain": ["你好"]}')
    report = tmp_path / "report.md"
    full = ["--cases", str(cases), "--report", str(report), *argv]
    return eval_cleanup.main(full, **kwargs), report


def test_cleanup_main_without_a_key_explains_and_never_calls_the_network(tmp_path, capsys):
    def no_client(key_provider):
        raise AssertionError("must not build a client without a key")

    code, report = run_cleanup_main(
        tmp_path, ["--models", "m"], key_store=MemoryKeyStore(), client_factory=no_client
    )
    err = capsys.readouterr().err
    assert code == 1
    assert "OpenRouter" in err and "金鑰" in err and "--set-key" in err
    assert not report.exists()


def test_cleanup_main_reports_a_key_store_that_fails(tmp_path, capsys):
    class Broken:
        def get(self):
            raise RuntimeError("vault locked")

    code, _ = run_cleanup_main(tmp_path, [], key_store=Broken(), client_factory=None)
    assert code == 1 and "金鑰" in capsys.readouterr().err


def test_cleanup_main_runs_every_case_for_every_model_and_writes_the_report(tmp_path, capsys):
    store = MemoryKeyStore()
    store.set("sk-test")
    client = FakeClient(["你好。", "好。"])
    providers = []

    def factory(key_provider):
        providers.append(key_provider)
        return client

    code, report = run_cleanup_main(
        tmp_path, ["--models", "vendor/a,vendor/b"], key_store=store, client_factory=factory
    )
    assert code == 0
    assert [call["model"] for call in client.calls] == ["vendor/a", "vendor/b"]
    assert providers[0]() == "sk-test" and client.closed
    text = report.read_text(encoding="utf-8")
    assert "vendor/a" in text and "vendor/b" in text and "缺少「你好」" in text
    assert "sk-test" not in text and "sk-test" not in capsys.readouterr().out


def test_cleanup_main_reports_an_unusable_key_and_writes_nothing(tmp_path, capsys):
    store = MemoryKeyStore()
    store.set("sk-bad")
    client = FakeClient([OpenRouterError(401, "auth", "bad key")])
    code, report = run_cleanup_main(
        tmp_path, ["--models", "m"], key_store=store, client_factory=lambda provider: client
    )
    assert code == 1 and not report.exists() and client.closed
    assert "金鑰" in capsys.readouterr().err


def test_cleanup_main_writes_a_partial_report_when_a_later_model_is_refused(tmp_path, capsys):
    store = MemoryKeyStore()
    store.set("sk-test")
    client = FakeClient(["你好。", OpenRouterError(402, "credits", "no credit")])
    code, report = run_cleanup_main(
        tmp_path, ["--models", "m1,m2"], key_store=store, client_factory=lambda provider: client
    )
    assert code == 1 and client.closed
    text = report.read_text(encoding="utf-8")
    assert "`m1`" in text and "`m2`" not in text.split("## 逐案結果")[1]
    assert "m2" in text and "中止" in text, "the report says that the run stopped, and at which model"
    assert "額度" in capsys.readouterr().err


def test_cleanup_main_set_key_stores_the_key_without_echoing_it(tmp_path, capsys):
    store = MemoryKeyStore()
    code = eval_cleanup.main(["--set-key"], key_store=store, read_secret=lambda prompt: " sk-new \n")
    captured = capsys.readouterr()
    assert code == 0 and store.get() == "sk-new"
    assert "sk-new" not in captured.out + captured.err


def test_cleanup_main_set_key_refuses_an_empty_key(capsys):
    store = MemoryKeyStore()
    code = eval_cleanup.main(["--set-key"], key_store=store, read_secret=lambda prompt: "  ")
    assert code == 1 and store.get() is None


def test_cleanup_main_with_bad_cases_file_says_where(tmp_path, capsys):
    path = case_file(tmp_path, "{oops")
    code = eval_cleanup.main(["--cases", str(path)], key_store=MemoryKeyStore())
    assert code == 1 and "第 1 行" in capsys.readouterr().err


def test_cleanup_main_help_exits_cleanly(capsys):
    with pytest.raises(SystemExit) as raised:
        eval_cleanup.main(["--help"])
    assert raised.value.code == 0
    assert "--models" in capsys.readouterr().out


def test_cleanup_main_with_the_real_client_sends_what_the_app_sends(tmp_path):
    """The real OpenRouterClient, with the network replaced by a canned reply."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        reply = {"text": "拉斐爾老師你好。"}
        return httpx.Response(
            200,
            json={
                "model": "vendor/a",
                "choices": [{"message": {"content": json.dumps(reply, ensure_ascii=False)}}],
                "usage": {"cost": 0.00002, "prompt_tokens": 100, "completion_tokens": 9},
            },
        )

    store = MemoryKeyStore()
    store.set("sk-secret-key")
    cases = case_file(
        tmp_path,
        '{"id": "d", "input": "拉菲爾老師你好", "dictionary": ["拉斐爾"], "must_contain": ["拉斐爾"]}',
    )
    report = tmp_path / "report.md"

    def factory(key_provider):
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        return OpenRouterClient(key_provider, http=http)

    argv = ["--cases", str(cases), "--report", str(report), "--models", "vendor/a"]
    assert eval_cleanup.main(argv, key_store=store, client_factory=factory) == 0

    (request,) = seen
    body = json.loads(request.content)
    assert request.headers["Authorization"] == "Bearer sk-secret-key"
    assert body["model"] == "vendor/a" and "models" not in body, "no fallback models"
    assert "- 拉斐爾" in body["messages"][0]["content"]
    assert body["messages"][1]["content"] == "<transcript>\n拉菲爾老師你好\n</transcript>"
    assert body["response_format"]["json_schema"]["strict"] is True
    text = report.read_text(encoding="utf-8")
    assert "拉斐爾老師你好。" in text and "0.000020" in text and "通過" in text
    assert "sk-secret-key" not in text
