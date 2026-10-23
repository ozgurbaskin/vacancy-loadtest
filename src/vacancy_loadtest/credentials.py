"""The accounts created in the pre-requirements step, handed out to users round-robin."""

from __future__ import annotations

import itertools
import json
import threading
from dataclasses import dataclass
from pathlib import Path


class CredentialsError(ValueError):
    """The credentials file is missing or malformed."""


@dataclass(frozen=True)
class Credential:
    email: str
    password: str


def load_credentials(path: str | Path) -> list[Credential]:
    """Read ``[{"email": ..., "password": ...}, ...]`` and fail with an actionable message."""
    path = Path(path)
    if not path.is_file():
        raise CredentialsError(
            f"credentials file not found: {path} "
            "(run scripts/create_users.py, or copy credentials.example.json and fill it in)"
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CredentialsError(f"{path}: invalid JSON ({exc})") from exc
    if not isinstance(raw, list) or not raw:
        raise CredentialsError(f"{path}: expected a non-empty JSON list of objects")
    credentials: list[Credential] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict) or not item.get("email") or not item.get("password"):
            raise CredentialsError(f"{path}: entry #{index} needs non-empty 'email' and 'password'")
        credentials.append(Credential(email=str(item["email"]), password=str(item["password"])))
    return credentials


class CredentialPool:
    """Round-robin: user 1 → account 1, user 2 → account 2, …, user 4 → account 1 again.

    ``start`` shifts the starting point (the worker index in a distributed run, so that N
    workers with one user each do not all pick account 1).
    """

    def __init__(self, credentials: list[Credential], start: int = 0) -> None:
        if not credentials:
            raise CredentialsError("the credential pool is empty")
        self._credentials = list(credentials)
        self._cycle = itertools.cycle(self._credentials)
        for _ in range(start % len(self._credentials)):
            next(self._cycle)
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._credentials)

    def next(self) -> Credential:
        with self._lock:
            return next(self._cycle)


_pools: dict[str, CredentialPool] = {}
_pools_lock = threading.Lock()


def get_pool(path: str | Path, start: int = 0) -> CredentialPool:
    """One shared pool per file and process (every Locust worker is its own process).

    ``start`` only matters for the call that creates the pool.
    """
    key = str(Path(path).resolve())
    with _pools_lock:
        pool = _pools.get(key)
        if pool is None:
            pool = _pools[key] = CredentialPool(load_credentials(path), start=start)
        return pool


def reset_pools() -> None:
    """Forget the cached pools: the next user re-reads the file and starts at account 1."""
    with _pools_lock:
        _pools.clear()
