"""Fixtures and helpers shared by the brain's tests."""

import sqlite3
from collections.abc import Iterator

import pytest

from raphael_core.brain import db
from raphael_core.brain.dictionary import DictionaryStore, Learned
from raphael_core.brain.history import HistoryItem


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    connection = db.connect(":memory:")
    yield connection
    connection.close()


def item(**overrides) -> HistoryItem:
    """A valid HistoryItem; override any field by keyword."""
    fields = {
        "id": None,
        "created_at_ms": 1_000,
        "kind": "dictation",
        "raw_text": "嗯 你好",
        "text": "你好。",
        "cleaned": True,
        "fallback_reason": None,
        "target_app": "Code.exe",
        "audio_seconds": 1.5,
        "stt_ms": 300,
        "llm_ms": 400,
        "model": "google/gemini-3.1-flash-lite",
        "cost_usd": 0.0002,
    }
    return HistoryItem(**{**fields, **overrides})


def learn(dictionary: DictionaryStore, word: str, misheard: str) -> bool:
    """Whether learning that the user corrected `misheard` into `word` changed the dictionary or
    its evidence; `learn_pair` says how."""
    return dictionary.learn_pair(word, misheard) is not Learned.NOTHING
