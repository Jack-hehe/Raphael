"""Fixtures and helpers shared by the voice assistant's tests."""

import sqlite3
from collections.abc import Iterator

import pytest

from raphael_core.brain import db


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    connection = db.connect(":memory:")
    yield connection
    connection.close()
