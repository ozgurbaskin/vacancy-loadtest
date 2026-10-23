"""scripts/create_users.py end to end against the fake server."""

import json
import re
import stat

from vacancy_loadtest.provision import run


def _code_reader(server):
    """Play the person reading the verification e-mails: answer with the link from the mail."""

    def read(prompt: str) -> str:
        email = re.search(r"code for (\S+) \(", prompt).group(1)
        return f"https://vacancies.example/verifyemail/{server.state.verification_codes[email]}"

    return read


def test_sign_up_verify_sign_in_and_write_file(server, tmp_path):
    out = tmp_path / "credentials.json"
    args = [
        "--host",
        server.address,
        "--out",
        str(out),
        "--email",
        "qa+{n}@example.com",
        "--count",
        "2",
    ]

    assert run(args, input_func=_code_reader(server), echo=lambda _: None) == 0

    data = json.loads(out.read_text())
    assert [entry["email"] for entry in data] == ["qa+1@example.com", "qa+2@example.com"]
    assert all(len(entry["password"]) >= 12 for entry in data)
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    assert all(server.state.users[entry["email"]].verified for entry in data)

    # Re-running keeps the passwords and tolerates the existing, verified accounts.
    assert run([*args, "--skip-verify"], echo=lambda _: None) == 0
    assert json.loads(out.read_text()) == data


def test_wrong_code_is_reported_and_exit_code_is_one(server, tmp_path):
    out = tmp_path / "credentials.json"
    lines: list[str] = []
    args = ["--host", server.address, "--out", str(out), "--emails", "solo@example.com"]

    assert run([*args, "--codes", "not-the-code"], echo=lines.append) == 1

    assert any("PERMISSION_DENIED" in line for line in lines)
    assert json.loads(out.read_text())[0]["email"] == "solo@example.com"
    assert not server.state.users["solo@example.com"].verified
