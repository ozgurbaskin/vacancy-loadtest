import json

import pytest

from vacancy_loadtest.credentials import (
    CredentialPool,
    CredentialsError,
    get_pool,
    load_credentials,
    reset_pools,
)


def test_load_and_round_robin(tmp_path):
    path = tmp_path / "c.json"
    path.write_text(
        json.dumps([{"email": "a@x", "password": "1"}, {"email": "b@x", "password": "2"}])
    )
    pool = CredentialPool(load_credentials(path))

    assert len(pool) == 2
    assert [pool.next().email for _ in range(5)] == ["a@x", "b@x", "a@x", "b@x", "a@x"]


@pytest.mark.parametrize(
    ("content", "fragment"),
    [
        (None, "not found"),
        ("{not json", "invalid JSON"),
        ("[]", "non-empty"),
        ('{"email": "a@x", "password": "1"}', "non-empty JSON list"),
        ('[{"email": "a@x"}]', "entry #0"),
        ('[{"email": "a@x", "password": ""}]', "entry #0"),
    ],
)
def test_bad_files_fail_with_actionable_message(tmp_path, content, fragment):
    path = tmp_path / "c.json"
    if content is not None:
        path.write_text(content)
    with pytest.raises(CredentialsError, match=fragment):
        load_credentials(path)


def test_start_offset_shifts_the_round_robin(tmp_path):
    path = tmp_path / "c.json"
    path.write_text(json.dumps([{"email": e, "password": "1"} for e in ("a@x", "b@x", "c@x")]))
    pool = CredentialPool(load_credentials(path), start=4)  # 4 % 3 == 1
    assert [pool.next().email for _ in range(3)] == ["b@x", "c@x", "a@x"]


def test_get_pool_is_shared_per_file_until_reset(tmp_path):
    path = tmp_path / "c.json"
    path.write_text(
        json.dumps([{"email": "a@x", "password": "1"}, {"email": "b@x", "password": "2"}])
    )
    pool = get_pool(path, start=1)
    assert pool is get_pool(str(path), start=0)  # the offset only matters for the creating call
    assert pool.next().email == "b@x"

    reset_pools()
    assert get_pool(path) is not pool
    assert get_pool(path).next().email == "a@x"
