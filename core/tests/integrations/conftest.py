"""Fixtures for the plugins' tests: a vault in memory, and servers that live in the test (the
database fixture `conn` is the whole suite's)."""

from contextlib import asynccontextmanager

import pytest
from mcp import Client

from raphael_core.integrations.client import McpConnector, Secrets
from raphael_core.integrations.config import ServerConfig
from raphael_core.integrations.vault import MemoryVault

from . import fake_server


@pytest.fixture(autouse=True)
def calls():
    """What the fake servers' tools were asked to do, by tool name; empty at the start."""
    fake_server.CALLS.clear()
    yield fake_server.CALLS
    fake_server.CALLS.clear()


@pytest.fixture
def vault() -> MemoryVault:
    return MemoryVault()


class Servers:
    """The opener of the tests: a server is in-process, found by its name in the config."""

    def __init__(self) -> None:
        self.by_name = {}
        self.opened: list[tuple[str, dict[str, str], dict[str, str]]] = []
        self.active = 0  # connections that are open now

    def add(self, name: str, **kwargs):
        self.by_name[name] = fake_server.build(**kwargs)
        return self.by_name[name]

    def __call__(self, server: ServerConfig, secrets: Secrets):
        self.opened.append((server.name, dict(secrets.env), dict(secrets.headers)))
        return self._open(server)

    @asynccontextmanager
    async def _open(self, server: ServerConfig):
        async with Client(self.by_name[server.name]) as client:
            self.active += 1
            try:
                yield client
            finally:
                self.active -= 1


@pytest.fixture
def servers() -> Servers:
    return Servers()


@pytest.fixture
def connector(vault, servers) -> McpConnector:
    return McpConnector(vault, servers)
