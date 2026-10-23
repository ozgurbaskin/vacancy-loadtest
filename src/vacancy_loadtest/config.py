"""Runtime settings of the load test.

Everything is exposed as a Locust command-line option, can also be given through environment
variables and shows up in the web UI under "Custom parameters" (the README lists them). The
defaults are the numbers required by the task description.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields
from typing import Any

from locust import events
from locust.argument_parser import LocustArgumentParser
from locust.env import Environment

#: The vacancy server given in the task. Locust's own ``--host`` (or ``LOCUST_HOST``) wins.
DEFAULT_HOST = os.environ.get("LOADTEST_HOST", "vacancies.cyrextech.net:7823")


def _optional_int(value: Any) -> int | None:
    if value is None or value in ("", "None"):
        return None
    return int(value)


def _number(value: Any, default: float, cast: type) -> Any:
    if value is None or value == "":
        return default
    return cast(value)


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "off"}
    return bool(value)


@dataclass(frozen=True)
class Settings:
    """Values the users read at runtime (normalised, since the web UI posts strings)."""

    credentials_file: str = "credentials.json"
    #: seconds between two create→update→get→delete cycles of one user (task: 30)
    crud_interval: float = 30.0
    #: seconds between two background "fetch all vacancies" sweeps of one user (task: 45)
    list_interval: float = 45.0
    #: page size requested while sweeping all vacancies (GetVacancies is paginated; the server
    #: defaults to 10 per page and currently holds ~5.5k vacancies)
    list_page_limit: int = 500
    #: safety cap on the number of pages per sweep (500 x 100 = 50k vacancies)
    list_max_pages: int = 100
    #: deadline of every RPC in seconds; a page of the GetVacancies stream counts as one RPC
    rpc_timeout: float = 10.0
    #: sign in again this many seconds after the last sign-in (access tokens expire after 15
    #: minutes and the API has no refresh RPC); 0 = only after an UNAUTHENTICATED error
    token_ttl: float = 840.0
    #: seed for the pseudo-random vacancy data (None = different data on every run)
    seed: int | None = None
    #: sign in again at all (proactively and after UNAUTHENTICATED); ``--no-relogin`` turns it off
    relogin: bool = True

    @classmethod
    def from_environment(cls, environment: Environment | None) -> Settings:
        """Build the settings from ``environment.parsed_options`` (defaults when absent)."""
        opts = getattr(environment, "parsed_options", None)
        if opts is None:
            return cls()
        values: dict[str, Any] = {}
        for field in fields(cls):
            if hasattr(opts, field.name):
                values[field.name] = getattr(opts, field.name)
        d = cls()
        return cls(
            credentials_file=str(values.get("credentials_file") or d.credentials_file),
            crud_interval=_number(values.get("crud_interval"), d.crud_interval, float),
            list_interval=_number(values.get("list_interval"), d.list_interval, float),
            list_page_limit=_number(values.get("list_page_limit"), d.list_page_limit, int),
            list_max_pages=_number(values.get("list_max_pages"), d.list_max_pages, int),
            rpc_timeout=_number(values.get("rpc_timeout"), d.rpc_timeout, float),
            token_ttl=_number(values.get("token_ttl"), d.token_ttl, float),
            seed=_optional_int(values.get("seed", d.seed)),
            relogin=_as_bool(values.get("relogin", d.relogin)),
        )


@events.init_command_line_parser.add_listener
def _add_arguments(parser: LocustArgumentParser) -> None:
    d = Settings()
    parser.add_argument(
        "--credentials-file",
        env_var="LOADTEST_CREDENTIALS_FILE",
        default=d.credentials_file,
        help='JSON file with the pre-created users: [{"email": ..., "password": ...}, ...]',
    )
    parser.add_argument(
        "--crud-interval",
        type=float,
        env_var="LOADTEST_CRUD_INTERVAL",
        default=d.crud_interval,
        help="Seconds between two create→update→get→delete cycles of one user (task: 30)",
    )
    parser.add_argument(
        "--list-interval",
        type=float,
        env_var="LOADTEST_LIST_INTERVAL",
        default=d.list_interval,
        help="Seconds between two background GetVacancies sweeps of one user (task: 45)",
    )
    parser.add_argument(
        "--list-page-limit",
        type=int,
        env_var="LOADTEST_LIST_PAGE_LIMIT",
        default=d.list_page_limit,
        help="Page size (GetVacanciesRequest.limit) used while fetching all vacancies",
    )
    parser.add_argument(
        "--list-max-pages",
        type=int,
        env_var="LOADTEST_LIST_MAX_PAGES",
        default=d.list_max_pages,
        help="Safety cap on the number of pages fetched per sweep",
    )
    parser.add_argument(
        "--rpc-timeout",
        type=float,
        env_var="LOADTEST_RPC_TIMEOUT",
        default=d.rpc_timeout,
        help="Deadline of every RPC in seconds (reported as DEADLINE_EXCEEDED when hit)",
    )
    parser.add_argument(
        "--token-ttl",
        type=float,
        env_var="LOADTEST_TOKEN_TTL",
        default=d.token_ttl,
        help="Sign in again this many seconds after the last sign-in (server tokens last 15 min); "
        "0 = only after an UNAUTHENTICATED error",
    )
    parser.add_argument(
        "--seed",
        type=int,
        env_var="LOADTEST_SEED",
        default=None,
        help="Seed for the pseudo-random vacancy data (reproducible content)",
    )
    parser.add_argument(
        "--no-relogin",
        dest="relogin",
        action="store_false",
        include_in_web_ui=False,
        help="Never sign in again (neither before the token expires nor after UNAUTHENTICATED)",
    )
