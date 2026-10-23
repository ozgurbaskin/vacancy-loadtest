"""The channel interceptor reports every RPC, unary and streaming, success and failure."""

import grpc
import pytest
from locust.env import Environment
from locust.exception import LocustError

from vacancy_loadtest.grpc_user import REQUEST_TYPE_STREAM, REQUEST_TYPE_UNARY, GrpcUser
from vacancy_loadtest.pb import (
    auth_service_pb2_grpc,
    rpc_signin_user_pb2,
    vacancy_service_pb2,
    vacancy_service_pb2_grpc,
)


class RawUser(GrpcUser):
    """A GrpcUser without any behaviour: tests talk to the stubs directly."""


@pytest.fixture
def raw(server):
    environment = Environment(user_classes=[RawUser], host=server.address)
    environment.recorded = []
    environment.events.request.add_listener(lambda **kw: environment.recorded.append(kw))
    user = RawUser(environment)
    yield user, environment
    user.on_stop()


def _sign_in(user) -> str:
    auth = auth_service_pb2_grpc.AuthServiceStub(user.channel)
    request = rpc_signin_user_pb2.SignInUserInput(email="tester1@example.com", password="pw-one")
    return auth.SignInUser(request).access_token


def test_unary_success_is_reported(raw):
    user, environment = raw
    auth = auth_service_pb2_grpc.AuthServiceStub(user.channel)
    request = rpc_signin_user_pb2.SignInUserInput(email="tester1@example.com", password="pw-one")
    response = auth.SignInUser(request)

    assert response.access_token
    (event,) = environment.recorded
    assert event["request_type"] == REQUEST_TYPE_UNARY
    assert event["name"] == "pb.AuthService/SignInUser"
    assert event["exception"] is None
    assert event["response_time"] > 0
    assert event["response_length"] == response.ByteSize()


def test_unary_failure_is_reported_and_raised(raw):
    user, environment = raw
    auth = auth_service_pb2_grpc.AuthServiceStub(user.channel)
    request = rpc_signin_user_pb2.SignInUserInput(email="tester1@example.com", password="wrong")

    with pytest.raises(grpc.RpcError) as info:
        auth.SignInUser(request)

    (event,) = environment.recorded
    assert event["exception"].error is info.value
    assert event["exception"].code == grpc.StatusCode.INVALID_ARGUMENT
    assert str(event["exception"]) == "INVALID_ARGUMENT: Invalid email or password"
    assert repr(event["exception"]) == str(event["exception"])  # what Locust's failure table shows
    assert event["response_length"] == 0


def test_stream_is_reported_once_when_fully_consumed(raw, server):
    user, environment = raw
    for index in range(3):
        server.state.add_vacancy(f"vacancy {index}")
    token = _sign_in(user)
    environment.recorded.clear()
    vacancies = vacancy_service_pb2_grpc.VacancyServiceStub(user.channel)

    stream = vacancies.GetVacancies(
        vacancy_service_pb2.GetVacanciesRequest(page=1, limit=10),
        metadata=(("authorization", f"Bearer {token}"),),
    )
    messages = list(stream)

    assert len(messages) == 3
    (event,) = environment.recorded
    assert event["request_type"] == REQUEST_TYPE_STREAM
    assert event["name"] == "pb.VacancyService/GetVacancies"
    assert event["exception"] is None
    assert event["context"] == {"items": 3}
    assert event["response_length"] == sum(m.ByteSize() for m in messages)
    assert stream.code() == grpc.StatusCode.OK  # grpc.Call API is still available


def test_stream_failure_is_reported(raw):
    user, environment = raw
    vacancies = vacancy_service_pb2_grpc.VacancyServiceStub(user.channel)

    with pytest.raises(grpc.RpcError) as info:
        list(vacancies.GetVacancies(vacancy_service_pb2.GetVacanciesRequest()))  # no token

    (event,) = environment.recorded
    assert event["request_type"] == REQUEST_TYPE_STREAM
    assert event["exception"].error is info.value
    assert event["exception"].code == grpc.StatusCode.UNAUTHENTICATED


def test_abandoned_stream_is_not_reported(raw, server):
    user, environment = raw
    for index in range(3):
        server.state.add_vacancy(f"vacancy {index}")
    token = _sign_in(user)
    environment.recorded.clear()
    vacancies = vacancy_service_pb2_grpc.VacancyServiceStub(user.channel)

    stream = vacancies.GetVacancies(
        vacancy_service_pb2.GetVacanciesRequest(page=1, limit=10),
        metadata=(("authorization", f"Bearer {token}"),),
    )
    next(iter(stream))
    stream.cancel()

    assert environment.recorded == []


def test_client_side_failure_is_reported_and_raised(raw):
    """A request that cannot be serialised never reaches the server; gRPC surfaces it as an
    INTERNAL RpcError, which is reported like any other failure."""
    user, environment = raw
    auth = auth_service_pb2_grpc.AuthServiceStub(user.channel)

    with pytest.raises(grpc.RpcError) as info:
        auth.SignInUser("not a protobuf message")

    (event,) = environment.recorded
    assert event["exception"].error is info.value
    assert event["exception"].code == grpc.StatusCode.INTERNAL
    assert "serializing" in event["exception"].details


def test_user_without_host_is_rejected():
    environment = Environment(user_classes=[RawUser], host=None)
    with pytest.raises(LocustError):
        RawUser(environment)
