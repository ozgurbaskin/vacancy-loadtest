"""VacancyUser against the fake server: the 30-second flow, the 45-second sweep, sign-in."""

from dataclasses import replace
from unittest.mock import Mock

import gevent
import grpc
import pytest
from helpers import (
    CREATE,
    DELETE,
    GET,
    LIST,
    SIGN_IN,
    UPDATE,
    events_of,
    names,
    stop_sweep,
    write_credentials,
)
from locust.exception import StopUser
from locust.runners import MasterRunner, WorkerRunner

from vacancy_loadtest import users as users_module
from vacancy_loadtest.credentials import get_pool
from vacancy_loadtest.users import (
    CHECK_NAME,
    REQUEST_TYPE_CHECK,
    REQUEST_TYPE_SWEEP,
    SWEEP_NAME,
    VacancyUser,
    worker_offset,
)


class _FakeRpcError(grpc.RpcError):
    def __init__(self, code: grpc.StatusCode, details: str = "injected") -> None:
        self._code, self._details = code, details

    def code(self) -> grpc.StatusCode:
        return self._code

    def details(self) -> str:
        return self._details


class _StopLoop(Exception):
    """Raised from a patched gevent.sleep to leave `_sweep_forever` after one iteration."""


@pytest.fixture
def user(environment):
    instance = VacancyUser(environment)
    yield instance
    instance.on_stop()


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr("vacancy_loadtest.users.gevent.sleep", lambda _seconds: None)


# -- sign-in -------------------------------------------------------------------------------


def test_on_start_signs_in_and_starts_the_sweep(environment, user):
    user.on_start()

    assert user.access_token
    assert names(environment)[0] == SIGN_IN
    assert user._sweeper is not None
    user.on_stop()
    assert user._sweeper is None


def test_accounts_are_handed_out_round_robin(environment):
    users = [VacancyUser(environment) for _ in range(4)]
    for instance in users:
        instance.on_start()
    try:
        emails = [instance.credential.email for instance in users]
        assert emails == [
            "tester1@example.com",
            "tester2@example.com",
            "tester3@example.com",
            "tester1@example.com",
        ]
    finally:
        for instance in users:
            instance.on_stop()


def test_worker_index_offsets_the_round_robin(environment):
    assert worker_offset(environment) == 0  # no runner at all
    environment.runner = Mock(spec=WorkerRunner)
    environment.runner.worker_index = 2
    assert worker_offset(environment) == 2

    user = VacancyUser(environment)
    user.on_start()
    assert user.credential.email == "tester3@example.com"
    user.on_stop()


def test_sign_in_failure_stops_the_user(env_factory, tmp_path):
    bad = write_credentials(tmp_path / "bad.json", [("tester1@example.com", "nope")])
    environment = env_factory("--credentials-file", str(bad))
    user = VacancyUser(environment)

    with pytest.raises(StopUser):
        user.on_start()
    assert names(environment) == [SIGN_IN]
    assert environment.recorded[0]["exception"].code == grpc.StatusCode.INVALID_ARGUMENT
    user.on_stop()


def test_transient_sign_in_failures_are_retried_at_start(environment, monkeypatch, no_sleep):
    user = VacancyUser(environment)
    real_sign_in = user.auth.SignInUser
    calls = []

    def flaky_sign_in(request, **kwargs):
        calls.append(request.email)
        if len(calls) < 3:
            raise _FakeRpcError(grpc.StatusCode.UNAVAILABLE, "dns hiccup")
        return real_sign_in(request, **kwargs)

    monkeypatch.setattr(user.auth, "SignInUser", flaky_sign_in)
    user.on_start()

    assert len(calls) == 3
    assert user.access_token
    assert user._sweeper is not None
    user.on_stop()


def test_user_survives_when_every_start_attempt_is_transient(environment, monkeypatch, no_sleep):
    user = VacancyUser(environment)
    monkeypatch.setattr(
        user.auth,
        "SignInUser",
        lambda request, **kw: (_ for _ in ()).throw(_FakeRpcError(grpc.StatusCode.UNAVAILABLE)),
    )

    user.on_start()  # no StopUser: the cycle keeps trying to sign in

    assert user._needs_sign_in
    assert user._sweeper is not None
    user.on_stop()


def test_no_relogin_still_signs_in_when_there_is_no_token_yet(env_factory, monkeypatch, no_sleep):
    environment = env_factory("--no-relogin")
    user = VacancyUser(environment)
    real_sign_in = user.auth.SignInUser
    monkeypatch.setattr(
        user.auth,
        "SignInUser",
        lambda request, **kw: (_ for _ in ()).throw(_FakeRpcError(grpc.StatusCode.UNAVAILABLE)),
    )
    user.on_start()
    stop_sweep(user)
    monkeypatch.setattr(user.auth, "SignInUser", real_sign_in)
    environment.recorded.clear()

    user.vacancy_lifecycle()

    assert names(environment) == [SIGN_IN, CREATE, UPDATE, GET, DELETE]
    user.on_stop()


def test_missing_credentials_file_stops_the_user(env_factory, tmp_path):
    environment = env_factory("--credentials-file", str(tmp_path / "missing.json"))
    user = VacancyUser(environment)
    with pytest.raises(StopUser):
        user.on_start()
    user.on_stop()


def test_unauthenticated_triggers_a_new_sign_in(environment, server, user):
    user.on_start()
    stop_sweep(user)
    server.state.revoke_all_tokens()
    environment.recorded.clear()

    user.vacancy_lifecycle()  # CreateVacancy fails: the cycle stops there
    assert names(environment) == [CREATE]
    assert environment.recorded[0]["exception"].code == grpc.StatusCode.UNAUTHENTICATED
    assert user._needs_sign_in

    environment.recorded.clear()
    user.vacancy_lifecycle()  # signs in again first, then a full cycle
    assert names(environment) == [SIGN_IN, CREATE, UPDATE, GET, DELETE]
    assert all(event["exception"] is None for event in environment.recorded)


def test_no_relogin_flag_keeps_the_rejected_token(env_factory, server):
    environment = env_factory("--no-relogin")
    user = VacancyUser(environment)
    user.on_start()
    stop_sweep(user)
    server.state.revoke_all_tokens()
    environment.recorded.clear()

    user.vacancy_lifecycle()
    user.vacancy_lifecycle()

    assert names(environment) == [CREATE, CREATE]
    user.on_stop()


def test_signs_in_again_before_the_token_expires(env_factory):
    environment = env_factory("--token-ttl", "0.05")
    user = VacancyUser(environment)
    user.on_start()
    stop_sweep(user)
    gevent.sleep(0.06)
    environment.recorded.clear()

    user.vacancy_lifecycle()

    assert names(environment) == [SIGN_IN, CREATE, UPDATE, GET, DELETE]
    user.on_stop()


def test_token_ttl_zero_means_only_reactive_sign_in(env_factory):
    environment = env_factory("--token-ttl", "0")
    user = VacancyUser(environment)
    user.on_start()
    stop_sweep(user)
    gevent.sleep(0.02)
    environment.recorded.clear()

    user.vacancy_lifecycle()

    assert names(environment) == [CREATE, UPDATE, GET, DELETE]
    user.on_stop()


# -- the 30-second cycle -------------------------------------------------------------------


def test_lifecycle_runs_create_update_get_delete_and_cleans_up(environment, server, user):
    user.on_start()
    stop_sweep(user)
    environment.recorded.clear()

    user.vacancy_lifecycle()

    assert names(environment) == [CREATE, UPDATE, GET, DELETE]
    assert all(event["exception"] is None for event in environment.recorded)
    assert server.state.vacancies == {}


def test_every_call_carries_a_deadline(server_factory, env_factory):
    slow = server_factory(require_auth=True, latency_ms=300)
    environment = env_factory("--rpc-timeout", "1", target=slow)
    user = VacancyUser(environment)
    user.on_start()  # 300 ms < 1 s: fine
    stop_sweep(user)
    user.settings = replace(user.settings, rpc_timeout=0.1)
    environment.recorded.clear()

    user.vacancy_lifecycle()
    with pytest.raises(grpc.RpcError):
        user.fetch_all_vacancies()

    codes = {event["name"]: event["exception"].code for event in environment.recorded}
    assert codes[CREATE] == grpc.StatusCode.DEADLINE_EXCEEDED
    assert codes[LIST] == grpc.StatusCode.DEADLINE_EXCEEDED
    assert codes[SWEEP_NAME] == grpc.StatusCode.DEADLINE_EXCEEDED
    assert slow.state.vacancies == {}
    user.on_stop()


def test_delete_still_runs_when_update_fails(environment, server, user, monkeypatch):
    user.on_start()
    stop_sweep(user)
    environment.recorded.clear()

    def failing_update(request, **kwargs):
        raise _FakeRpcError(grpc.StatusCode.INTERNAL)

    monkeypatch.setattr(user.vacancies, "UpdateVacancy", failing_update)
    user.vacancy_lifecycle()

    assert names(environment) == [CREATE, GET, DELETE]
    assert server.state.vacancies == {}


def test_consistency_check_reports_a_server_that_ignores_updates(environment, server, user):
    server.state.drop_updates = True
    user.on_start()
    stop_sweep(user)
    environment.recorded.clear()

    user.vacancy_lifecycle()

    checks = events_of(environment, REQUEST_TYPE_CHECK)
    assert len(checks) == 1
    assert checks[0]["name"] == CHECK_NAME
    assert isinstance(checks[0]["exception"], AssertionError)
    assert "update set" in str(checks[0]["exception"])


# -- the 45-second sweep -------------------------------------------------------------------


def _sweeping_user(env_factory, *args, target=None):
    environment = env_factory(*args, target=target)
    user = VacancyUser(environment)
    user.on_start()
    stop_sweep(user)
    environment.recorded.clear()
    return environment, user


def test_sweep_walks_every_page_and_reports_one_aggregate_row(env_factory, server):
    for index in range(25):
        server.state.add_vacancy(f"vacancy {index}")
    environment, user = _sweeping_user(env_factory, "--list-page-limit", "10")

    found = user.fetch_all_vacancies()

    assert len(found) == 25
    pages = events_of(environment, "gRPC-stream")
    assert [event["name"] for event in pages] == [LIST, LIST, LIST]
    assert [event["context"]["items"] for event in pages] == [10, 10, 5]
    (sweep,) = events_of(environment, REQUEST_TYPE_SWEEP)
    assert sweep["name"] == SWEEP_NAME
    assert sweep["exception"] is None
    assert sweep["context"] == {"pages": 3, "items": 25}
    assert sweep["response_length"] == sum(event["response_length"] for event in pages)
    assert sweep["response_time"] >= max(event["response_time"] for event in pages)
    user.on_stop()


def test_sweep_stops_when_the_server_ignores_pagination(env_factory, server):
    server.state.ignore_pagination = True
    for index in range(25):
        server.state.add_vacancy(f"vacancy {index}")
    environment, user = _sweeping_user(env_factory, "--list-page-limit", "10")

    found = user.fetch_all_vacancies()

    assert len(found) == 25  # the repeated page is not counted twice
    assert [e["name"] for e in events_of(environment, "gRPC-stream")] == [LIST, LIST]
    user.on_stop()


def test_sweep_respects_the_page_cap(env_factory, server):
    for index in range(25):
        server.state.add_vacancy(f"vacancy {index}")
    _, user = _sweeping_user(env_factory, "--list-page-limit", "10", "--list-max-pages", "2")

    assert len(user.fetch_all_vacancies()) == 20
    user.on_stop()


def test_sweep_detects_a_server_that_caps_the_page_size(server_factory, env_factory):
    capped = server_factory(require_auth=True, max_limit=10)
    for index in range(25):
        capped.state.add_vacancy(f"vacancy {index}")
    environment, user = _sweeping_user(env_factory, "--list-page-limit", "500", target=capped)

    assert len(user.fetch_all_vacancies()) == 25
    assert user._page_limit == 10
    assert events_of(environment, REQUEST_TYPE_SWEEP)[0]["context"] == {"pages": 3, "items": 25}

    environment.recorded.clear()
    assert len(user.fetch_all_vacancies()) == 25  # the cap is remembered: no second probe
    assert events_of(environment, REQUEST_TYPE_SWEEP)[0]["context"]["pages"] == 3
    user.on_stop()


def test_first_sweep_probes_page_two_when_everything_fits_in_one_page(env_factory, server):
    for index in range(5):
        server.state.add_vacancy(f"vacancy {index}")
    environment, user = _sweeping_user(env_factory, "--list-page-limit", "10")

    assert len(user.fetch_all_vacancies()) == 5
    assert events_of(environment, REQUEST_TYPE_SWEEP)[0]["context"]["pages"] == 2  # the probe
    assert user._page_limit == 10  # no cap: threshold restored

    environment.recorded.clear()
    assert len(user.fetch_all_vacancies()) == 5
    assert events_of(environment, REQUEST_TYPE_SWEEP)[0]["context"]["pages"] == 1
    user.on_stop()


def test_failed_sweep_is_reported_as_such(env_factory, server):
    environment, user = _sweeping_user(env_factory)
    server.state.revoke_all_tokens()

    with pytest.raises(grpc.RpcError):
        user.fetch_all_vacancies()

    (page,) = events_of(environment, "gRPC-stream")
    (sweep,) = events_of(environment, REQUEST_TYPE_SWEEP)
    assert page["exception"].code == grpc.StatusCode.UNAUTHENTICATED
    assert sweep["exception"].code == grpc.StatusCode.UNAUTHENTICATED
    assert sweep["context"] == {"pages": 0, "items": 0}  # no page completed
    user.on_stop()


def test_sweep_loop_asks_for_a_new_sign_in_on_unauthenticated(env_factory, server, monkeypatch):
    _, user = _sweeping_user(env_factory)
    server.state.revoke_all_tokens()
    monkeypatch.setattr(users_module.gevent, "sleep", lambda _s: (_ for _ in ()).throw(_StopLoop()))

    with pytest.raises(_StopLoop):
        user._sweep_forever()

    assert user._needs_sign_in
    user.on_stop()


def test_sweep_loop_survives_unexpected_exceptions(env_factory, monkeypatch):
    environment, user = _sweeping_user(env_factory)
    monkeypatch.setattr(
        user, "fetch_all_vacancies", lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    monkeypatch.setattr(users_module.gevent, "sleep", lambda _s: (_ for _ in ()).throw(_StopLoop()))

    with pytest.raises(_StopLoop):  # i.e. the loop reached its sleep: it survived
        user._sweep_forever()

    (sweep,) = events_of(environment, REQUEST_TYPE_SWEEP)
    assert isinstance(sweep["exception"], RuntimeError)
    user.on_stop()


def test_background_sweep_runs_until_the_user_stops(env_factory, server):
    server.state.add_vacancy("one")
    environment = env_factory("--list-interval", "0.1")
    user = VacancyUser(environment)
    user.on_start()
    deadline = gevent.get_hub().loop.now() + 5
    while (
        len(events_of(environment, REQUEST_TYPE_SWEEP)) < 3
        and gevent.get_hub().loop.now() < deadline
    ):
        gevent.sleep(0.05)
    user.on_stop()
    sweeps = len(events_of(environment, REQUEST_TYPE_SWEEP))
    gevent.sleep(0.3)

    assert sweeps >= 3
    assert len(events_of(environment, REQUEST_TYPE_SWEEP)) == sweeps


# -- event listeners -----------------------------------------------------------------------


def test_init_listener_fails_fast_without_credentials(env_factory, tmp_path):
    environment = env_factory("--credentials-file", str(tmp_path / "missing.json"))

    users_module._fail_fast_without_credentials(
        environment, runner=Mock(spec=MasterRunner)
    )  # master: skipped
    with pytest.raises(SystemExit):
        users_module._fail_fast_without_credentials(environment, runner=None)
    with pytest.raises(SystemExit):
        users_module._fail_fast_without_credentials(environment, runner=Mock(spec=WorkerRunner))


def test_init_listener_accepts_a_valid_file(environment):
    users_module._fail_fast_without_credentials(environment, runner=None)
    users_module._fail_fast_without_credentials(environment, runner=Mock(spec=WorkerRunner))


def test_test_start_restarts_the_round_robin(environment, credentials_file):
    assert get_pool(credentials_file).next().email == "tester1@example.com"
    assert get_pool(credentials_file).next().email == "tester2@example.com"

    users_module._restart_round_robin(environment)

    assert get_pool(credentials_file).next().email == "tester1@example.com"
