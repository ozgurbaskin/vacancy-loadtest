"""An in-memory stand-in for the vacancy server, for tests, CI and offline demos.

It implements the two services the load test uses (AuthService, VacancyService) with the
behaviour of the real server that matters: e-mail verification before
sign-in, unique titles, NOT_FOUND for unknown ids, paginated ``GetVacancies`` with a default
page size. Knobs add things the real server may or may not do (bearer-token checks, token
expiry, latency) so the client code can be tested against them.

    python -m vacancy_loadtest.fake_server --port 50051 --credentials credentials.example.json
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import secrets
import threading
import time
from collections.abc import Callable, Iterator
from concurrent import futures
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import grpc
from google.protobuf.timestamp_pb2 import Timestamp

from .pb import (
    auth_service_pb2,
    auth_service_pb2_grpc,
    rpc_create_vacancy_pb2,
    rpc_signin_user_pb2,
    rpc_signup_user_pb2,
    rpc_update_vacancy_pb2,
    user_pb2,
    vacancy_pb2,
    vacancy_service_pb2,
    vacancy_service_pb2_grpc,
)

log = logging.getLogger(__name__)


def _now() -> Timestamp:
    stamp = Timestamp()
    stamp.GetCurrentTime()
    return stamp


def _object_id() -> str:
    """24 hex characters, like a MongoDB ObjectId."""
    return secrets.token_hex(12)


@dataclass
class StoredUser:
    id: str
    name: str
    email: str
    password: str
    verified: bool = False
    verification_code: str | None = None
    created_at: Timestamp = field(default_factory=_now)


@dataclass
class StoredVacancy:
    id: str
    title: str
    description: str
    division: int
    country: str
    views: int = 0
    created_at: Timestamp = field(default_factory=_now)
    updated_at: Timestamp = field(default_factory=_now)

    def to_pb(self) -> vacancy_pb2.Vacancy:
        return vacancy_pb2.Vacancy(
            Id=self.id,
            Title=self.title,
            Description=self.description,
            Views=self.views,
            Division=self.division,
            Country=self.country,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


class FakeState:
    """All server state, behind one lock (handlers run in a thread/greenlet pool)."""

    def __init__(
        self,
        *,
        require_auth: bool = True,
        token_ttl: float | None = None,
        default_limit: int = 10,
        ignore_pagination: bool = False,
        latency_ms: float = 0.0,
        on_signup: Callable[[str, str], None] | None = None,
        drop_updates: bool = False,
        max_limit: int | None = None,
    ) -> None:
        self.require_auth = require_auth
        self.token_ttl = token_ttl
        self.default_limit = default_limit
        self.ignore_pagination = ignore_pagination
        self.latency_ms = latency_ms
        self.on_signup = on_signup
        #: pretend to update but keep the old values (a server quirk the client must detect)
        self.drop_updates = drop_updates
        #: cap on the page size, like a server that silently clamps `limit`
        self.max_limit = max_limit
        self.lock = threading.RLock()
        self.users: dict[str, StoredUser] = {}  # by e-mail
        self.vacancies: dict[str, StoredVacancy] = {}  # by id, insertion ordered
        self.tokens: dict[str, tuple[str, float]] = {}  # token -> (user id, expiry)
        #: the "e-mails": verification code per address, so tests/demos can read them
        self.verification_codes: dict[str, str] = {}

    # -- helpers used by the servicers ---------------------------------------------------
    def delay(self) -> None:
        if self.latency_ms > 0:
            time.sleep(self.latency_ms / 1000.0)

    def issue_token(self, user_id: str) -> str:
        token = secrets.token_urlsafe(24)
        expiry = math.inf if self.token_ttl is None else time.monotonic() + self.token_ttl
        with self.lock:
            self.tokens[token] = (user_id, expiry)
        return token

    def token_valid(self, token: str) -> bool:
        with self.lock:
            entry = self.tokens.get(token)
        return entry is not None and time.monotonic() < entry[1]

    # -- knobs for tests and demos -------------------------------------------------------
    def add_verified_user(self, email: str, password: str, name: str = "Load Tester") -> StoredUser:
        with self.lock:
            user = StoredUser(
                id=_object_id(), name=name, email=email, password=password, verified=True
            )
            self.users[email] = user
            return user

    def add_vacancy(
        self, title: str, description: str = "", division: int = 0, country: str = "NL"
    ) -> StoredVacancy:
        with self.lock:
            vacancy = StoredVacancy(
                id=_object_id(),
                title=title,
                description=description,
                division=division,
                country=country,
            )
            self.vacancies[vacancy.id] = vacancy
            return vacancy

    def revoke_all_tokens(self) -> None:
        with self.lock:
            self.tokens.clear()


class _AuthService(auth_service_pb2_grpc.AuthServiceServicer):
    def __init__(self, state: FakeState) -> None:
        self.state = state

    def SignUpUser(
        self, request: rpc_signup_user_pb2.SignUpUserInput, context: grpc.ServicerContext
    ) -> user_pb2.GenericResponse:
        self.state.delay()
        if request.password != request.passwordConfirm:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "passwords do not match")
        if not request.email or not request.password:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "email and password are required")
        with self.state.lock:
            if request.email in self.state.users:
                context.abort(grpc.StatusCode.ALREADY_EXISTS, "user with that email already exist")
            code = secrets.token_hex(10)  # 20 characters, like the real server's e-mail code
            self.state.users[request.email] = StoredUser(
                id=_object_id(),
                name=request.name,
                email=request.email,
                password=request.password,
                verification_code=code,
            )
            self.state.verification_codes[request.email] = code
        if self.state.on_signup is not None:
            self.state.on_signup(request.email, code)
        return user_pb2.GenericResponse(
            status="success",
            message=f"We sent an email with a verification code to {request.email}",
        )

    def VerifyEmail(
        self, request: auth_service_pb2.VerifyEmailRequest, context: grpc.ServicerContext
    ) -> user_pb2.GenericResponse:
        self.state.delay()
        with self.state.lock:
            user = next(
                (
                    u
                    for u in self.state.users.values()
                    if u.verification_code == request.verificationCode
                ),
                None,
            )
            if user is None or not request.verificationCode:
                context.abort(grpc.StatusCode.PERMISSION_DENIED, "Could not verify email address")
            user.verified = True
            user.verification_code = None
        return user_pb2.GenericResponse(status="success", message="Email verified successfully")

    def SignInUser(
        self, request: rpc_signin_user_pb2.SignInUserInput, context: grpc.ServicerContext
    ) -> rpc_signin_user_pb2.SignInUserResponse:
        self.state.delay()
        with self.state.lock:
            user = self.state.users.get(request.email)
        if user is None or user.password != request.password:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Invalid email or password")
        if not user.verified:
            context.abort(
                grpc.StatusCode.PERMISSION_DENIED,
                "You are not verified, please verify your email to login",
            )
        return rpc_signin_user_pb2.SignInUserResponse(
            status="success",
            access_token=self.state.issue_token(user.id),
            refresh_token=self.state.issue_token(user.id),
        )


class _VacancyService(vacancy_service_pb2_grpc.VacancyServiceServicer):
    def __init__(self, state: FakeState) -> None:
        self.state = state

    def _authorize(self, context: grpc.ServicerContext) -> None:
        if not self.state.require_auth:
            return
        header = dict(context.invocation_metadata()).get("authorization", "")
        token = header[len("Bearer ") :] if header.startswith("Bearer ") else ""
        if not self.state.token_valid(token):
            context.abort(
                grpc.StatusCode.UNAUTHENTICATED, "missing, invalid or expired access token"
            )

    def _get(self, vacancy_id: str, context: grpc.ServicerContext) -> StoredVacancy:
        with self.state.lock:
            vacancy = self.state.vacancies.get(vacancy_id)
        if vacancy is None:
            context.abort(grpc.StatusCode.NOT_FOUND, "no vacancy with that Id exists")
        return vacancy

    def CreateVacancy(
        self, request: rpc_create_vacancy_pb2.CreateVacancyRequest, context: grpc.ServicerContext
    ) -> vacancy_pb2.VacancyResponse:
        self._authorize(context)
        self.state.delay()
        if not request.Title:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Title is required")
        with self.state.lock:
            if any(v.title == request.Title for v in self.state.vacancies.values()):
                context.abort(
                    grpc.StatusCode.ALREADY_EXISTS, "vacancy with that title already exists"
                )
            vacancy = StoredVacancy(
                id=_object_id(),
                title=request.Title,
                description=request.Description,
                division=request.Division,
                country=request.Country,
            )
            self.state.vacancies[vacancy.id] = vacancy
        return vacancy_pb2.VacancyResponse(vacancy=vacancy.to_pb())

    def GetVacancy(
        self, request: vacancy_service_pb2.VacancyRequest, context: grpc.ServicerContext
    ) -> vacancy_pb2.VacancyResponse:
        self._authorize(context)
        self.state.delay()
        return vacancy_pb2.VacancyResponse(vacancy=self._get(request.Id, context).to_pb())

    def GetVacancies(
        self, request: vacancy_service_pb2.GetVacanciesRequest, context: grpc.ServicerContext
    ) -> Iterator[vacancy_pb2.Vacancy]:
        self._authorize(context)
        self.state.delay()
        page = request.page if request.HasField("page") and request.page > 0 else 1
        limit = (
            request.limit
            if request.HasField("limit") and request.limit > 0
            else self.state.default_limit
        )
        if self.state.max_limit is not None:
            limit = min(limit, self.state.max_limit)
        with self.state.lock:
            items = list(self.state.vacancies.values())
        if not self.state.ignore_pagination:
            items = items[(page - 1) * limit : page * limit]
        for vacancy in items:
            yield vacancy.to_pb()

    def UpdateVacancy(
        self, request: rpc_update_vacancy_pb2.UpdateVacancyRequest, context: grpc.ServicerContext
    ) -> vacancy_pb2.VacancyResponse:
        self._authorize(context)
        self.state.delay()
        with self.state.lock:
            vacancy = self._get(request.Id, context)
            if self.state.drop_updates:
                return vacancy_pb2.VacancyResponse(vacancy=vacancy.to_pb())
            if request.HasField("Title"):
                vacancy.title = request.Title
            if request.HasField("Description"):
                vacancy.description = request.Description
            if request.HasField("Views"):
                vacancy.views = request.Views
            if request.HasField("Division"):
                vacancy.division = request.Division
            if request.HasField("Country"):
                vacancy.country = request.Country
            vacancy.updated_at = _now()
            return vacancy_pb2.VacancyResponse(vacancy=vacancy.to_pb())

    def DeleteVacancy(
        self, request: vacancy_service_pb2.VacancyRequest, context: grpc.ServicerContext
    ) -> vacancy_service_pb2.DeleteVacancyResponse:
        self._authorize(context)
        self.state.delay()
        with self.state.lock:
            self._get(request.Id, context)
            del self.state.vacancies[request.Id]
        return vacancy_service_pb2.DeleteVacancyResponse(success=True)


class FakeServer:
    """Owns a ``grpc.server`` serving :class:`FakeState` on a local port."""

    def __init__(self, **state_options: Any) -> None:
        self.state = FakeState(**state_options)
        self.port: int | None = None
        self.bind_host = "127.0.0.1"
        self._server: grpc.Server | None = None

    @property
    def address(self) -> str:
        return f"{self.bind_host}:{self.port}"

    def start(self, port: int = 0, bind_host: str = "127.0.0.1", max_workers: int = 16) -> int:
        self.bind_host = bind_host
        server = grpc.server(futures.ThreadPoolExecutor(max_workers=max_workers))
        auth_service_pb2_grpc.add_AuthServiceServicer_to_server(_AuthService(self.state), server)
        vacancy_service_pb2_grpc.add_VacancyServiceServicer_to_server(
            _VacancyService(self.state), server
        )
        self.port = server.add_insecure_port(f"{bind_host}:{port}")
        if self.port == 0:
            raise RuntimeError(f"could not bind {bind_host}:{port}")
        server.start()
        self._server = server
        return self.port

    def stop(self, grace: float = 0.5) -> None:
        if self._server is not None:
            self._server.stop(grace).wait()
            self._server = None

    def wait(self) -> None:
        if self._server is not None:
            self._server.wait_for_termination()


def _seed_from_credentials(state: FakeState, path: Path) -> int:
    entries = json.loads(path.read_text(encoding="utf-8"))
    for index, entry in enumerate(entries, start=1):
        state.add_verified_user(entry["email"], entry["password"], name=f"Load Tester {index}")
    return len(entries)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the in-memory fake vacancy server.")
    parser.add_argument("--port", type=int, default=50051)
    parser.add_argument("--bind", default="127.0.0.1", help="bind address (0.0.0.0 inside Docker)")
    parser.add_argument(
        "--credentials", type=Path, help="JSON file whose accounts are created pre-verified"
    )
    parser.add_argument(
        "--no-auth", action="store_true", help="do not require a bearer token on VacancyService"
    )
    parser.add_argument(
        "--token-ttl", type=float, default=None, help="access-token lifetime in seconds"
    )
    parser.add_argument("--latency-ms", type=float, default=0.0, help="artificial latency per RPC")
    parser.add_argument(
        "--default-limit", type=int, default=10, help="GetVacancies page size when none is given"
    )
    parser.add_argument(
        "--seed-vacancies", type=int, default=0, help="number of vacancies to create at start"
    )
    parser.add_argument(
        "--print-codes",
        action="store_true",
        help="print verification codes (the 'e-mail') on sign-up",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    on_signup = (
        (lambda email, code: print(f"[e-mail to {email}] verification code: {code}", flush=True))
        if args.print_codes
        else None
    )
    server = FakeServer(
        require_auth=not args.no_auth,
        token_ttl=args.token_ttl,
        default_limit=args.default_limit,
        latency_ms=args.latency_ms,
        on_signup=on_signup,
    )
    if args.credentials:
        log.info(
            "seeded %d verified account(s) from %s",
            _seed_from_credentials(server.state, args.credentials),
            args.credentials,
        )
    for index in range(args.seed_vacancies):
        server.state.add_vacancy(
            f"Seeded vacancy {index + 1}", "seeded at start-up", index % 4, "Netherlands"
        )
    port = server.start(port=args.port, bind_host=args.bind)
    log.info(
        "fake vacancy server listening on %s:%d (auth %s)",
        args.bind,
        port,
        "off" if args.no_auth else "on",
    )
    try:
        server.wait()
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
