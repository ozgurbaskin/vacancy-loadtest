"""Shared fixtures: in-process fake servers and Locust environments wired to them.

``locust`` is imported first on purpose: it applies gevent's monkey-patching, so the fake
server's thread pool runs on greenlets exactly as in Locust's own gRPC example.
"""

import locust  # noqa: F401  # isort: skip

from collections.abc import Callable
from pathlib import Path

import pytest
from helpers import ACCOUNTS, write_credentials
from locust.argument_parser import get_parser
from locust.env import Environment

from vacancy_loadtest.fake_server import FakeServer
from vacancy_loadtest.users import VacancyUser


@pytest.fixture
def server_factory() -> Callable[..., FakeServer]:
    """Start fake servers (with the test accounts pre-verified); all are stopped at teardown."""
    servers: list[FakeServer] = []

    def make(**options) -> FakeServer:
        fake = FakeServer(**options)
        for email, password in ACCOUNTS:
            fake.state.add_verified_user(email, password)
        fake.start(port=0)
        servers.append(fake)
        return fake

    yield make
    for fake in servers:
        fake.stop()


@pytest.fixture
def server(server_factory) -> FakeServer:
    return server_factory(require_auth=True)


@pytest.fixture
def credentials_file(tmp_path: Path) -> Path:
    return write_credentials(tmp_path / "credentials.json")


@pytest.fixture
def env_factory(server, credentials_file) -> Callable[..., Environment]:
    """Build a Locust Environment that targets a fake server and records request events."""

    def make(*extra_args: str, target: FakeServer | None = None) -> Environment:
        options = get_parser().parse_args(
            ["--credentials-file", str(credentials_file), *extra_args]
        )
        environment = Environment(
            user_classes=[VacancyUser], host=(target or server).address, parsed_options=options
        )
        environment.recorded = []
        environment.events.request.add_listener(
            lambda **kwargs: environment.recorded.append(kwargs)
        )
        return environment

    return make


@pytest.fixture
def environment(env_factory) -> Environment:
    return env_factory()
