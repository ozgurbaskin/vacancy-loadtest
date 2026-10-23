"""The parts of the fake server's contract the load test relies on."""

import time

import grpc
import pytest

from vacancy_loadtest.pb import (
    auth_service_pb2_grpc,
    rpc_create_vacancy_pb2,
    rpc_signin_user_pb2,
    vacancy_service_pb2,
    vacancy_service_pb2_grpc,
)


@pytest.fixture
def client(server_factory):
    fake = server_factory(require_auth=True, token_ttl=0.3, default_limit=2)
    channel = grpc.insecure_channel(fake.address)
    token = (
        auth_service_pb2_grpc.AuthServiceStub(channel)
        .SignInUser(
            rpc_signin_user_pb2.SignInUserInput(email="tester1@example.com", password="pw-one")
        )
        .access_token
    )
    stub = vacancy_service_pb2_grpc.VacancyServiceStub(channel)
    yield stub, (("authorization", f"Bearer {token}"),)
    channel.close()


def _create(stub, meta, title):
    return stub.CreateVacancy(
        rpc_create_vacancy_pb2.CreateVacancyRequest(Title=title), metadata=meta
    )


def test_titles_are_unique(client):
    stub, meta = client
    _create(stub, meta, "t0")
    with pytest.raises(grpc.RpcError) as info:
        _create(stub, meta, "t0")
    assert info.value.code() == grpc.StatusCode.ALREADY_EXISTS


def test_default_page_size_applies_when_no_limit_is_given(client):
    stub, meta = client
    for index in range(3):
        _create(stub, meta, f"t{index}")
    page = list(stub.GetVacancies(vacancy_service_pb2.GetVacanciesRequest(), metadata=meta))
    assert len(page) == 2  # default_limit, like the real server's default of 10


def test_unknown_id_is_not_found(client):
    stub, meta = client
    with pytest.raises(grpc.RpcError) as info:
        stub.GetVacancy(vacancy_service_pb2.VacancyRequest(Id="nope"), metadata=meta)
    assert info.value.code() == grpc.StatusCode.NOT_FOUND


def test_expired_token_is_unauthenticated(client):
    stub, meta = client
    time.sleep(0.4)
    with pytest.raises(grpc.RpcError) as info:
        stub.GetVacancy(vacancy_service_pb2.VacancyRequest(Id="nope"), metadata=meta)
    assert info.value.code() == grpc.StatusCode.UNAUTHENTICATED
