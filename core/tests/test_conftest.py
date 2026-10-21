"""The suite's own skips: a test that needs the speech models is skipped unless they are asked for."""

import inspect
from pathlib import Path

import conftest
import pytest


class Item:
    """Just enough of a collected test for the collection hook."""

    def __init__(self, *, models: bool) -> None:
        self.path = Path(__file__)
        self.function = test_the_collection_hook_is_defined_once
        self.models = models
        self.added: list[pytest.MarkDecorator] = []

    def get_closest_marker(self, name: str):
        return pytest.mark.models.mark if name == "models" and self.models else None

    def add_marker(self, marker: pytest.MarkDecorator) -> None:
        self.added.append(marker)

    def skip_reasons(self) -> list[str]:
        return [m.mark.kwargs.get("reason", "") for m in self.added if m.mark.name == "skip"]


def test_the_collection_hook_is_defined_once():
    # A second definition would replace the first, and the skips of the first would be lost.
    source = inspect.getsource(conftest)
    assert source.count("def pytest_collection_modifyitems(") == 1


@pytest.mark.parametrize("value", [None, "0", ""])
def test_a_models_test_is_skipped_unless_the_models_are_asked_for(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("RAPHAEL_TEST_MODELS", raising=False)
    else:
        monkeypatch.setenv("RAPHAEL_TEST_MODELS", value)
    models, other = Item(models=True), Item(models=False)
    conftest.pytest_collection_modifyitems(None, [models, other])
    assert any("RAPHAEL_TEST_MODELS=1" in reason for reason in models.skip_reasons())
    assert other.skip_reasons() == []


def test_a_models_test_runs_when_the_models_are_asked_for(monkeypatch):
    monkeypatch.setenv("RAPHAEL_TEST_MODELS", "1")
    models = Item(models=True)
    conftest.pytest_collection_modifyitems(None, [models])
    assert models.skip_reasons() == []
