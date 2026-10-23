"""Tests for the cleanup of a long dictation in pieces: where it is cut, how the pieces are cleaned
side by side and put back in order, and what becomes of a piece whose cleanup falls back.

As in test_cleanup.py, the OpenRouter client is a fake and the stores are real ones on an
in-memory database. The fake "cleans" a piece by writing it in capitals, which the hallucination
guard takes for a cleanup (it compares letters in lower case), so a cleaned piece is easy to tell
from one that kept its raw text.
"""

import asyncio
from datetime import UTC, datetime

import pytest

from raphael_core.brain import cleanup
from raphael_core.brain.cleanup import (
    CLEANUP_CHUNK_CHARS,
    CLEANUP_CONCURRENCY,
    CleanedPart,
    CleanupService,
    split_for_cleanup,
)
from raphael_core.brain.dictionary import DictionaryStore
from raphael_core.brain.openrouter import Completion, OpenRouterError
from raphael_core.brain.secrets import MemoryKeyStore
from raphael_core.brain.service import Brain
from raphael_core.brain.settings import SettingsStore
from raphael_core.brain.spend import SpendTracker
from raphael_core.brain.textnorm import remove_stutters
from raphael_core.router import Router
from raphael_core.textjoin import join_segments

NOW_MS = int(datetime(2026, 10, 22, 12, tzinfo=UTC).timestamp() * 1000)


def transcript_of(user: str) -> str:
    """The dictated text of a cleanup call's user message (cleanup.build_user_message)."""
    assert user.startswith("<transcript>\n") and user.endswith("\n</transcript>")
    return user[len("<transcript>\n") : -len("\n</transcript>")]


class PieceClient:
    """Cleans each piece by writing it in capitals. A piece that holds a word of `fail` fails with
    that error; one that holds a word of `answer` gets a reply that is no cleanup of it. `delay`
    gives the seconds a piece takes, by its first word."""

    def __init__(self):
        self.fail: dict[str, Exception] = {}
        self.answer: set[str] = set()
        self.delay: dict[str, float] = {}
        self.calls: list[dict] = []
        self.running = 0
        self.most_running = 0

    async def complete_json(self, **kwargs):
        self.calls.append(kwargs)
        text = transcript_of(kwargs["user"])
        self.running += 1
        self.most_running = max(self.most_running, self.running)
        try:
            await asyncio.sleep(self.delay.get(text.split()[0], 0.0))
            for word, error in self.fail.items():
                if word in text:
                    raise error
            if any(word in text for word in self.answer):
                reply = "Paris is the capital of France, as everybody knows."
            else:
                reply = text.upper()
            return Completion(
                data={"text": reply},
                model="openai/gpt-6-luna",
                cost_usd=0.001,
                prompt_tokens=100,
                completion_tokens=50,
                latency_ms=100,
            )
        finally:
            self.running -= 1


@pytest.fixture
def client():
    return PieceClient()


@pytest.fixture
def svc(conn, client):
    return CleanupService(
        client,
        SettingsStore(conn),
        DictionaryStore(conn),
        SpendTracker(conn, now_ms=lambda: NOW_MS, tz=UTC),
        has_key=lambda: True,
    )


def sentences(count: int, words: int = 30) -> list[str]:
    """`count` English sentences, each with its number as its first word."""
    return [f"s{n} " + " ".join(f"word{n}x{w}" for w in range(words)) + "." for n in range(count)]


def with_spaces(pieces: list[str]) -> list[str]:
    """`pieces` as `split_for_cleanup` gives them when they were sentences with a space between
    them: each but the last keeps the space that followed it."""
    return [piece + " " for piece in pieces[:-1]] + pieces[-1:]


def as_chunks(pieces: list[str]) -> list[tuple[str, str]]:
    """The (raw, heard) of each piece of `pieces`, sentences with a space between them, as the
    brain gives them (see `with_spaces`); raw and heard alike: these tests have no OpenCC to tell
    them apart."""
    return [(piece, piece) for piece in with_spaces(pieces)]


def spaced_parts(texts: list[str], cleaned: list[bool]) -> tuple[CleanedPart, ...]:
    """The parts of a dictation of sentences with a space between them."""
    afters = [" "] * (len(texts) - 1) + [""]
    return tuple(CleanedPart(t, c, a) for t, c, a in zip(texts, cleaned, afters, strict=True))


def spend_rows(conn):
    return [tuple(r) for r in conn.execute("SELECT purpose, model, cost_usd FROM spend")]


# --- where a dictation is cut --------------------------------------------------------------------


def test_the_size_of_a_piece():
    assert CLEANUP_CHUNK_CHARS == 1500 and CLEANUP_CONCURRENCY == 4
    # One piece takes at most twice its characters in tokens: far within a reply of 4096.
    assert cleanup.max_tokens_for("字" * CLEANUP_CHUNK_CHARS) == 3064


@pytest.mark.parametrize("text", ["", "你好。", "  hello world  ", "字" * CLEANUP_CHUNK_CHARS])
def test_a_text_within_the_limit_is_one_piece_as_it_is(text):
    assert split_for_cleanup(text) == [text]


def test_a_long_english_text_is_cut_after_sentences():
    text = " ".join(sentences(30))
    pieces = split_for_cleanup(text)
    assert len(pieces) > 1
    assert all(len(piece) <= CLEANUP_CHUNK_CHARS for piece in pieces)
    assert all(piece.endswith(". ") for piece in pieces[:-1])  # never inside a sentence
    assert pieces[-1].endswith(".")
    assert all(piece.split()[0].startswith("s") for piece in pieces)  # each starts one
    assert "".join(pieces) == text


def test_a_long_chinese_text_is_cut_after_sentences_and_joined_without_spaces():
    sentence = "今天下午我們要討論新產品的上市計畫以及預算的分配問題。"
    text = sentence * 120
    pieces = split_for_cleanup(text)
    assert len(pieces) > 1
    assert all(len(piece) <= CLEANUP_CHUNK_CHARS for piece in pieces)
    assert all(piece.endswith("。") for piece in pieces)
    assert "".join(pieces) == text


def test_a_sentence_too_long_for_a_piece_is_cut_after_a_clause():
    clause = "我們先看第一季的數字然後再看第二季的數字，"
    text = clause * 150 + "最後結束。"
    pieces = split_for_cleanup(text)
    assert len(pieces) > 1
    assert all(len(piece) <= CLEANUP_CHUNK_CHARS for piece in pieces)
    assert all(piece.endswith(("，", "。")) for piece in pieces)
    assert "".join(pieces) == text


def test_text_with_no_place_to_cut_is_cut_at_the_limit():
    text = "字" * (2 * CLEANUP_CHUNK_CHARS + 7)
    pieces = split_for_cleanup(text)
    assert [len(piece) for piece in pieces] == [CLEANUP_CHUNK_CHARS, CLEANUP_CHUNK_CHARS, 7]
    assert "".join(pieces) == text


def test_a_decimal_point_is_no_end_of_sentence():
    text = "pi is 3.14159 and e is 2.71828 " * 100
    pieces = split_for_cleanup(text.strip())
    assert not any(piece.rstrip().endswith(("3.", "2.")) for piece in pieces)
    assert "".join(pieces) == text.strip()


def paragraphs(first_chars: int) -> tuple[str, str]:
    """Two English paragraphs, the first of about `first_chars` characters, each of sentences."""
    first = " ".join(sentences(first_chars // 250 + 1, words=30))[:first_chars].rstrip() + "."
    second = "p2 " + " ".join(f"next{w}" for w in range(40)) + "."
    return first, second


def test_a_cut_at_a_paragraph_break_keeps_the_break_with_the_piece_before():
    first, second = paragraphs(1471)
    text = first + "\n\n" + second
    assert split_for_cleanup(text) == [first + "\n\n", second]


def test_whatever_a_piece_was_cut_at_stays_with_it():
    lines = [f"line{n} " + "word " * 20 + "end." for n in range(40)]
    text = "\n".join(lines[:20]) + "\n\n\n" + "  ".join(lines[20:])
    pieces = split_for_cleanup(text)
    assert len(pieces) > 1 and "".join(pieces) == text
    assert all(not piece[0].isspace() for piece in pieces)  # a piece starts with its words


def test_a_text_that_is_all_breaks_after_the_limit_loses_none_of_them():
    text = "字" * CLEANUP_CHUNK_CHARS + "\n" * (CLEANUP_CHUNK_CHARS + 3) + "完。"
    pieces = split_for_cleanup(text)
    assert "".join(pieces) == text
    assert all(piece.strip() for piece in pieces)  # no piece is only breaks


# --- one piece: as it always was -----------------------------------------------------------------


async def test_a_dictation_of_one_piece_is_one_call_whether_or_not_chunks_are_given(svc, client):
    text = "hello there."
    plain = await svc.clean(text, audio_seconds=2.0)
    one = await svc.clean(text, audio_seconds=2.0, chunks=as_chunks([text]))
    assert plain == one
    assert plain.text == "HELLO THERE." and plain.cleaned and plain.parts == ()
    assert [transcript_of(call["user"]) for call in client.calls] == [text, text]
    assert client.calls[0]["timeout_s"] == client.calls[1]["timeout_s"] == cleanup.timeout_for(2.0)


async def test_with_one_piece_the_whole_text_is_given_not_the_piece(svc, client):
    # One piece is the text itself: what the model reads is `heard`, as it always was.
    await svc.clean("raw words.", heard="heard words.", audio_seconds=1.0, chunks=None)
    assert transcript_of(client.calls[0]["user"]) == "heard words."


# --- many pieces ---------------------------------------------------------------------------------


async def test_the_pieces_are_cleaned_side_by_side_and_joined_in_order(svc, client, conn):
    pieces = sentences(8)
    # The first pieces take the longest, so they finish last.
    client.delay = {f"s{n}": 0.08 - 0.01 * n for n in range(8)}
    raw = join_segments(pieces)
    result = await svc.clean(raw, audio_seconds=80.0, chunks=as_chunks(pieces))
    assert result.cleaned and result.fallback_reason is None
    assert result.text == join_segments([piece.upper() for piece in pieces])
    assert result.parts == spaced_parts([piece.upper() for piece in pieces], [True] * 8)
    # The model is given each piece without the space that followed it.
    assert sorted(transcript_of(call["user"]) for call in client.calls) == sorted(pieces)
    assert len(client.calls) == 8
    assert client.most_running == CLEANUP_CONCURRENCY  # bounded, and more than one at a time
    # Every call is paid for, and the result's cost is the sum.
    assert spend_rows(conn) == [("cleanup", "openai/gpt-6-luna", 0.001)] * 8
    assert result.cost_usd == pytest.approx(0.008)
    assert result.model == "openai/gpt-6-luna"


async def test_each_piece_is_waited_for_as_its_share_of_the_speech(svc, client):
    short, long = "s0 a b.", "s1 " + "word " * 30 + "end."
    await svc.clean(
        join_segments([short, long]), audio_seconds=90.0, chunks=as_chunks([short, long])
    )
    by_text = {transcript_of(call["user"]): call for call in client.calls}
    total = len(short) + len(long)
    assert by_text[short]["timeout_s"] == pytest.approx(cleanup.timeout_for(90 * len(short) / total))
    assert by_text[long]["timeout_s"] == pytest.approx(cleanup.timeout_for(90 * len(long) / total))
    assert by_text[long]["max_tokens"] == cleanup.max_tokens_for(long)


async def test_a_piece_that_fails_keeps_its_raw_text_and_the_others_stay_cleaned(
    svc, client, conn
):
    pieces = sentences(5)
    client.fail = {"s2": OpenRouterError(503, "server", "upstream down")}
    result = await svc.clean(join_segments(pieces), audio_seconds=50.0, chunks=as_chunks(pieces))
    assert result.cleaned is True
    assert result.fallback_reason == "error:server"  # said the way a single call says it
    expected = [piece.upper() for piece in pieces]
    expected[2] = pieces[2]
    assert result.text == join_segments(expected)
    assert [part.cleaned for part in result.parts] == [True, True, False, True, True]
    assert len(spend_rows(conn)) == 4 and result.cost_usd == pytest.approx(0.004)


async def test_a_piece_whose_reply_is_no_cleanup_keeps_its_raw_text(svc, client):
    pieces = sentences(4)
    client.answer = {"s1"}
    result = await svc.clean(join_segments(pieces), audio_seconds=40.0, chunks=as_chunks(pieces))
    assert result.cleaned and result.fallback_reason == "suspicious"
    assert result.parts[1] == CleanedPart(pieces[1], False, " ")
    assert result.parts[0] == CleanedPart(pieces[0].upper(), True, " ")
    assert result.cost_usd == pytest.approx(0.004)  # the answer was paid for too


async def test_the_reason_is_that_of_the_first_piece_that_fell_back(svc, client):
    pieces = sentences(4)
    client.answer = {"s3"}
    client.fail = {"s1": OpenRouterError(0, "network", "no route")}
    result = await svc.clean(join_segments(pieces), audio_seconds=40.0, chunks=as_chunks(pieces))
    assert result.fallback_reason == "error:network"
    assert [part.cleaned for part in result.parts] == [True, False, True, False]


async def test_when_every_piece_falls_back_it_is_the_raw_text_as_for_one_call(svc, client):
    pieces = sentences(3)
    client.fail = {"s": OpenRouterError(0, "timeout", "slow")}
    raw = "the whole text, as the brain normalized it."
    result = await svc.clean(raw, audio_seconds=30.0, chunks=as_chunks(pieces))
    assert result.cleaned is False and result.fallback_reason == "error:timeout"
    assert result.text == raw and result.parts == ()


async def test_a_piece_that_times_out_keeps_its_raw_text(conn, client):
    svc = CleanupService(
        client,
        SettingsStore(conn),
        DictionaryStore(conn),
        SpendTracker(conn, now_ms=lambda: NOW_MS, tz=UTC),
        has_key=lambda: True,
        timeout_fn=lambda seconds: 0.05,
    )
    pieces = sentences(3)
    client.delay = {"s1": 1.0}
    result = await svc.clean(join_segments(pieces), audio_seconds=30.0, chunks=as_chunks(pieces))
    assert result.fallback_reason == "timeout"
    assert [part.cleaned for part in result.parts] == [True, False, True]


async def test_the_budget_is_looked_at_once_and_stops_every_piece(conn, client):
    settings = SettingsStore(conn)
    settings.set("monthly_limit_usd", 0.0)
    svc = CleanupService(
        client,
        settings,
        DictionaryStore(conn),
        SpendTracker(conn, now_ms=lambda: NOW_MS, tz=UTC),
        has_key=lambda: True,
    )
    pieces = sentences(3)
    raw = join_segments(pieces)
    result = await svc.clean(raw, audio_seconds=30.0, chunks=as_chunks(pieces))
    assert (result.text, result.fallback_reason, client.calls) == (raw, "budget", [])


async def test_a_cancelled_cleanup_cancels_its_pieces(svc, client):
    pieces = sentences(6)
    client.delay = {f"s{n}": 10.0 for n in range(6)}
    task = asyncio.create_task(
        svc.clean(join_segments(pieces), audio_seconds=60.0, chunks=as_chunks(pieces))
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)
    assert client.running == 0


# --- through the brain ---------------------------------------------------------------------------


async def test_a_long_dictation_through_the_brain_is_cleaned_in_pieces_and_saved_whole(
    conn, client
):
    keys = MemoryKeyStore()
    keys.set("sk-or-v1-test")
    brain = Brain(conn, client, keys, now_ms=lambda: NOW_MS)
    router = Router()
    brain.register(router)
    pieces = sentences(5, words=100)  # about 1000 characters each: one piece each
    pieces[3] = "s3 the, the " + pieces[3][3:]  # a stutter, in the piece whose cleanup fails
    client.fail = {"s3 ": OpenRouterError(503, "server", "upstream down")}
    raw = join_segments(pieces)
    assert split_for_cleanup(raw) == with_spaces(pieces)
    try:
        result = await router.call(
            "brain.finalize_dictation",
            {"raw_text": raw, "audio_seconds": 600.0, "stt_ms": 10, "target_app": None},
        )
        items = (await router.call("history.list", {}))["items"]
    finally:
        await brain.aclose()
    assert len(client.calls) == 5
    assert result["cleaned"] is True and result["fallback_reason"] == "error:server"
    # The cleaned pieces are as the model wrote them; the one that fell back loses its obvious
    # stutter, as a whole dictation that falls back does.
    expected = [piece.upper() for piece in pieces]
    expected[3] = remove_stutters(pieces[3])
    assert "the, the" not in expected[3]
    assert result["text"] == join_segments(expected)
    assert result["raw_text"] == raw
    assert len(items) == 1 and items[0]["text"] == result["text"]
    assert items[0]["cost_usd"] == pytest.approx(0.004)


async def test_paragraphs_cut_apart_are_joined_with_their_break(conn, client):
    # The first piece ends at a paragraph break: the break is not lost to the join, whether the
    # pieces were cleaned or one of them kept its raw text.
    keys = MemoryKeyStore()
    keys.set("sk-or-v1-test")
    brain = Brain(conn, client, keys, now_ms=lambda: NOW_MS)
    router = Router()
    brain.register(router)
    first, second = paragraphs(1471)
    raw = first + "\n\n" + second
    try:
        cleaned = await router.call(
            "brain.finalize_dictation",
            {"raw_text": raw, "audio_seconds": 300.0, "stt_ms": 10, "target_app": None},
        )
        client.fail = {"p2 ": OpenRouterError(503, "server", "upstream down")}
        half = await router.call(
            "brain.finalize_dictation",
            {"raw_text": raw, "audio_seconds": 300.0, "stt_ms": 10, "target_app": None},
        )
    finally:
        await brain.aclose()
    assert sorted(transcript_of(call["user"]) for call in client.calls[:2]) == sorted(
        [first, second]
    )
    assert cleaned["text"] == first.upper() + "\n\n" + second.upper()
    assert half["text"] == first.upper() + "\n\n" + second
    assert half["fallback_reason"] == "error:server"


async def test_chinese_paragraphs_cut_apart_keep_their_break(svc, client):
    sentence = "今天下午我們要討論新產品的上市計畫以及預算的分配問題。"
    text = sentence * 55 + "\n" + sentence * 50  # the first paragraph fills a piece
    pieces = split_for_cleanup(text)
    assert len(pieces) == 2 and pieces[0].endswith("。\n")
    result = await svc.clean(text, audio_seconds=120.0, chunks=[(p, p) for p in pieces])
    assert result.text == text  # capitals change no Chinese: the text comes back as it was


async def test_a_piece_cut_inside_a_word_is_joined_with_nothing(svc, client):
    text = "a" * (CLEANUP_CHUNK_CHARS + 10)
    pieces = split_for_cleanup(text)
    result = await svc.clean(text, audio_seconds=60.0, chunks=[(p, p) for p in pieces])
    assert result.text == text.upper()
