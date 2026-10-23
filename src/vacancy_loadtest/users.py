"""The Locust user that exercises the vacancy service the way the task describes.

Per Locust user:

1. ``on_start``: sign in with one of the pre-created accounts (handed out round-robin; in a
   distributed run the round-robin starts at the worker's index), keep the access token and
   start the background sweep.
2. Every ``--crud-interval`` seconds (30): create a vacancy with pseudo-random data → update one
   or more of its fields → fetch that vacancy → delete it. Every RPC is timed and reported by
   the channel interceptor; a consistency check compares the fetched vacancy with the update.
3. In the background, every ``--list-interval`` seconds (45): fetch the list of all vacancies
   (``GetVacancies`` is a paginated server-streaming RPC; the sweep walks all pages and is
   reported per page *and* as one "all pages" row).

Every RPC carries a deadline (``--rpc-timeout``). Errors returned by the server surface in
Locust's failure statistics with their gRPC status code and details. Access tokens expire
after 15 minutes and the API has no refresh RPC, so a user signs in again shortly before that
(``--token-ttl``) and after any ``UNAUTHENTICATED`` error.
"""

from __future__ import annotations

import itertools
import logging
import time
from collections.abc import Callable
from typing import Any

import gevent
import grpc
from locust import constant_pacing, events, task
from locust.env import Environment
from locust.exception import StopUser
from locust.runners import MasterRunner, WorkerRunner

from .config import DEFAULT_HOST, Settings
from .credentials import Credential, CredentialsError, get_pool, load_credentials, reset_pools
from .data import VacancyFactory
from .grpc_user import GrpcUser, RpcFailure
from .pb import (
    auth_service_pb2_grpc,
    rpc_signin_user_pb2,
    vacancy_pb2,
    vacancy_service_pb2,
    vacancy_service_pb2_grpc,
)

log = logging.getLogger(__name__)

#: ``request_type`` of the consistency check (only failures are reported)
REQUEST_TYPE_CHECK = "check"
CHECK_NAME = "GetVacancy reflects UpdateVacancy"
#: one row per complete "all vacancies" sweep, on top of the per-page ``gRPC-stream`` rows
REQUEST_TYPE_SWEEP = "sweep"
SWEEP_NAME = "GetVacancies (all pages)"
#: sign-in failures worth retrying at start-up (network, not credentials)
_TRANSIENT = frozenset(
    {grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED, grpc.StatusCode.UNKNOWN}
)
#: attempts at start-up before giving up on a transient failure
SIGN_IN_ATTEMPTS = 3
_user_ordinal = itertools.count()


def worker_offset(environment: Environment) -> int:
    """This worker's index in a distributed run, 0 otherwise.

    Offsets the credential round-robin so that N workers with one user each do not all pick
    the first account.
    """
    runner = getattr(environment, "runner", None)
    if isinstance(runner, WorkerRunner):
        return int(getattr(runner, "worker_index", 0) or 0)
    return 0


class VacancyUser(GrpcUser):
    """One simulated client of the vacancy service."""

    host = DEFAULT_HOST

    def __init__(self, environment: Environment) -> None:
        super().__init__(environment)
        self.settings = Settings.from_environment(environment)
        # constant_pacing: a cycle starts every N seconds regardless of how long it takes
        # (plain `constant` would add N seconds *after* each cycle).
        self._pacing = constant_pacing(self.settings.crud_interval)
        self.auth = auth_service_pb2_grpc.AuthServiceStub(self.channel)
        self.vacancies = vacancy_service_pb2_grpc.VacancyServiceStub(self.channel)
        seed = None if self.settings.seed is None else self.settings.seed + next(_user_ordinal)
        self.factory = VacancyFactory(seed)
        self.credential: Credential | None = None
        self.access_token = ""
        self._signed_in_at = 0.0
        self._needs_sign_in = True
        self._last_sign_in_code: grpc.StatusCode | None = None
        self._sweeper: gevent.Greenlet | None = None
        # "last page" threshold of the sweep; lowered once if the server caps the page size
        self._page_limit = self.settings.list_page_limit
        self._cap_checked = False

    def wait_time(self) -> float:
        return self._pacing(self)

    # -- lifecycle -------------------------------------------------------------------------
    def on_start(self) -> None:
        try:
            pool = get_pool(self.settings.credentials_file, start=worker_offset(self.environment))
        except CredentialsError as exc:
            log.error("%s", exc)
            raise StopUser() from exc
        self.credential = pool.next()
        for attempt in range(1, SIGN_IN_ATTEMPTS + 1):
            if self.sign_in():
                break
            if self._last_sign_in_code not in _TRANSIENT:
                # wrong password, unverified account, …: retrying cannot help
                log.error("could not sign in as %s; stopping this user", self.credential.email)
                raise StopUser()
            if attempt < SIGN_IN_ATTEMPTS:
                gevent.sleep(2.0 * attempt)
        else:
            # every attempt failed on a network error: keep the user, the cycle retries
            log.warning("%s: sign-in keeps failing, will retry every cycle", self.credential.email)
        self._sweeper = gevent.spawn(self._sweep_forever)

    def on_stop(self) -> None:
        if self._sweeper is not None:
            # let the sweep die before the channel is closed underneath it
            self._sweeper.kill(block=True, timeout=5)
            self._sweeper = None
        super().on_stop()

    # -- authentication --------------------------------------------------------------------
    def sign_in(self) -> bool:
        """SignInUser with this user's account; False if the server rejected it."""
        assert self.credential is not None
        request = rpc_signin_user_pb2.SignInUserInput(
            email=self.credential.email, password=self.credential.password
        )
        try:
            response = self.auth.SignInUser(request, timeout=self.settings.rpc_timeout)
        except grpc.RpcError as exc:  # reported by the interceptor
            self._last_sign_in_code = exc.code()
            log.warning(
                "SignInUser failed for %s: %s (%s)",
                self.credential.email,
                exc.code().name,
                exc.details(),
            )
            return False
        self.access_token = response.access_token
        self._signed_in_at = time.monotonic()
        self._needs_sign_in = False
        self._last_sign_in_code = None
        return True

    def _token_expiring(self) -> bool:
        ttl = self.settings.token_ttl
        return ttl > 0 and time.monotonic() - self._signed_in_at >= ttl

    def _ensure_signed_in(self) -> bool:
        """Sign in again when needed. False means the cycle must be skipped."""
        if self._needs_sign_in:
            if self.settings.relogin or not self.access_token:
                return self.sign_in()
            return True  # --no-relogin with a rejected token: carry on, the server keeps saying so
        if self.settings.relogin and self._token_expiring():
            self.sign_in()  # a failure is reported; the old token may still work for a while
        return True

    def _metadata(self) -> tuple[tuple[str, str], ...]:
        return (("authorization", f"Bearer {self.access_token}"),)

    # -- the recurring flow (every --crud-interval seconds) --------------------------------
    @task
    def vacancy_lifecycle(self) -> None:
        if not self._ensure_signed_in():
            return  # the failure is in the statistics; try again next cycle
        created = self._unary(self.vacancies.CreateVacancy, self.factory.create_request())
        if created is None:
            return  # nothing to update/fetch/delete; the error is in the statistics
        vacancy_id = created.vacancy.Id
        try:
            update, expected = self.factory.update_request(vacancy_id)
            updated = self._unary(self.vacancies.UpdateVacancy, update)
            fetched = self._unary(
                self.vacancies.GetVacancy, vacancy_service_pb2.VacancyRequest(Id=vacancy_id)
            )
            if updated is not None and fetched is not None:
                self._check_fetched(fetched.vacancy, vacancy_id, expected)
        finally:
            # never leave test data behind on the shared server
            self._unary(
                self.vacancies.DeleteVacancy, vacancy_service_pb2.VacancyRequest(Id=vacancy_id)
            )

    def _unary(self, rpc: Callable[..., Any], request: Any) -> Any | None:
        """Call a unary RPC; return None on error (the interceptor has already reported it)."""
        try:
            return rpc(request, metadata=self._metadata(), timeout=self.settings.rpc_timeout)
        except grpc.RpcError as exc:
            if exc.code() == grpc.StatusCode.UNAUTHENTICATED:
                self._needs_sign_in = True
            log.debug(
                "%s failed: %s (%s)", getattr(rpc, "_method", rpc), exc.code().name, exc.details()
            )
            return None

    def _check_fetched(
        self, vacancy: vacancy_pb2.Vacancy, vacancy_id: str, expected: dict[str, Any]
    ) -> None:
        """Report a failure if GetVacancy does not show what UpdateVacancy wrote."""
        problems = []
        if vacancy.Id != vacancy_id:
            problems.append(f"Id: fetched {vacancy.Id!r}, requested {vacancy_id!r}")
        for field, value in expected.items():
            actual = getattr(vacancy, field)
            if actual != value:
                problems.append(f"{field}: fetched {actual!r} but update set {value!r}")
        if problems:
            self.environment.events.request.fire(
                request_type=REQUEST_TYPE_CHECK,
                name=CHECK_NAME,
                response_time=0,
                response_length=0,
                exception=AssertionError("; ".join(problems)),
                context={},
            )

    # -- the background sweep (every --list-interval seconds) ------------------------------
    def _sweep_forever(self) -> None:
        interval = self.settings.list_interval
        while True:
            started = time.monotonic()
            try:
                self.fetch_all_vacancies()
            except grpc.RpcError as exc:  # reported by the interceptor and as a failed sweep
                if exc.code() == grpc.StatusCode.UNAUTHENTICATED:
                    self._needs_sign_in = True
            except Exception as exc:  # the sweep must survive anything unexpected
                log.exception("unexpected error while fetching the vacancy list")
                self._report_sweep(time.perf_counter(), 0, 0, 0, exc)
            gevent.sleep(max(0.0, interval - (time.monotonic() - started)))

    def _fetch_page(self, page: int) -> list[vacancy_pb2.Vacancy]:
        request = vacancy_service_pb2.GetVacanciesRequest(
            page=page, limit=self.settings.list_page_limit
        )
        stream = self.vacancies.GetVacancies(
            request, metadata=self._metadata(), timeout=self.settings.rpc_timeout
        )
        return list(stream)

    def _report_sweep(
        self,
        started: float,
        pages: int,
        items: int,
        response_length: int,
        exc: BaseException | None,
    ) -> None:
        self.environment.events.request.fire(
            request_type=REQUEST_TYPE_SWEEP,
            name=SWEEP_NAME,
            response_time=(time.perf_counter() - started) * 1000.0,
            response_length=response_length,
            response=None,
            context={"pages": pages, "items": items},
            exception=exc,
        )

    def fetch_all_vacancies(self) -> list[vacancy_pb2.Vacancy]:
        """Fetch every vacancy on the server, page by page.

        Each page is one GetVacancies stream and one ``gRPC-stream`` row in the statistics; the
        whole sweep is one ``sweep`` row. The sweep stops at the first short page, when the
        server repeats a page (it ignores pagination) or at ``--list-max-pages``. A server that
        caps the page size below ``--list-page-limit`` would make every page "short", so the
        first time page 1 is short the sweep looks at page 2 once: empty means the list really
        ended, anything else means a cap, which becomes the "last page" threshold from then on.
        """
        started = time.perf_counter()
        found: list[vacancy_pb2.Vacancy] = []
        pages = 0
        response_length = 0
        try:
            previous_first_id: str | None = None
            probing = False  # looking at page 2 to tell "end of list" from "capped page size"
            page = 1
            while page <= self.settings.list_max_pages:
                batch = self._fetch_page(page)
                pages += 1
                if probing:
                    probing = False
                    if not batch:  # page 1 really was the whole list; no cap
                        self._page_limit = self.settings.list_page_limit
                        break
                    log.warning(
                        "GetVacancies returns at most %d items per page although %d were "
                        "requested; using %d as the page size from now on",
                        self._page_limit,
                        self.settings.list_page_limit,
                        self._page_limit,
                    )
                if batch and batch[0].Id == previous_first_id:
                    log.warning(
                        "GetVacancies repeated page %d; the server seems to ignore pagination",
                        page - 1,
                    )
                    break
                found.extend(batch)
                response_length += sum(item.ByteSize() for item in batch)
                if not batch:
                    break
                if len(batch) < self._page_limit:
                    if page == 1 and not self._cap_checked:
                        self._cap_checked = True
                        self._page_limit = len(batch)  # tentative: confirmed or reverted by page 2
                        probing = True
                    else:
                        break
                previous_first_id = batch[0].Id
                page += 1
            else:
                log.warning(
                    "GetVacancies: stopped after --list-max-pages=%d pages",
                    self.settings.list_max_pages,
                )
        except grpc.RpcError as exc:
            self._report_sweep(started, pages, len(found), response_length, RpcFailure(exc))
            raise
        self._report_sweep(started, pages, len(found), response_length, None)
        return found


@events.init.add_listener
def _fail_fast_without_credentials(
    environment: Environment, runner: Any = None, **_kwargs: Any
) -> None:
    """Exit with one clear message instead of one traceback per spawned user."""
    if isinstance(runner, MasterRunner):
        return  # the master does not run users
    settings = Settings.from_environment(environment)
    try:
        accounts = load_credentials(settings.credentials_file)
    except CredentialsError as exc:
        log.error("%s", exc)
        raise SystemExit(1) from exc
    if isinstance(runner, WorkerRunner):
        # intervals, page size etc. arrive from the master when the test starts
        log.info("%d account(s) from %s", len(accounts), settings.credentials_file)
        return
    log.info(
        "%d account(s) from %s; create/update/get/delete every %gs, list every %gs "
        "(page size %d, deadline %gs)",
        len(accounts),
        settings.credentials_file,
        settings.crud_interval,
        settings.list_interval,
        settings.list_page_limit,
        settings.rpc_timeout,
    )


@events.test_start.add_listener
def _restart_round_robin(environment: Environment, **_kwargs: Any) -> None:
    """A new test (web UI "Start" again) re-reads the credentials file and starts at account 1."""
    reset_pools()
