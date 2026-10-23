"""Create and verify the test accounts (the task's pre-requirements) over gRPC.

The task asks for this to be done with BloomRPC or Evans (the README shows the Evans session).
This module automates the same three calls so the step is reproducible and testable::

    SignUpUser  →  (verification code arrives by e-mail)  →  VerifyEmail  →  SignInUser (check)

and writes the credentials file the load test reads. Existing accounts are kept (sign-up
answers ALREADY_EXISTS), so the script can be re-run safely.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import grpc

from .config import DEFAULT_HOST
from .pb import auth_service_pb2, auth_service_pb2_grpc, rpc_signin_user_pb2, rpc_signup_user_pb2


@dataclass
class Outcome:
    email: str
    password: str
    signed_up: str = "-"
    verified: str = "-"
    signed_in: str = "-"

    @property
    def ok(self) -> bool:
        return self.signed_in == "ok"


def _code_from_input(text: str) -> str:
    """Accept the bare code or the whole link from the e-mail (…/verifyemail/<code>)."""
    text = text.strip().strip("'\"")
    if "/" in text:
        text = text.rstrip("/").rsplit("/", 1)[-1]
    return text


class Provisioner:
    def __init__(self, host: str, *, timeout: float = 15.0) -> None:
        self.channel = grpc.insecure_channel(host)
        self.auth = auth_service_pb2_grpc.AuthServiceStub(self.channel)
        self.timeout = timeout

    def close(self) -> None:
        self.channel.close()

    def sign_up(self, name: str, email: str, password: str) -> tuple[str, str]:
        request = rpc_signup_user_pb2.SignUpUserInput(
            name=name, email=email, password=password, passwordConfirm=password
        )
        try:
            response = self.auth.SignUpUser(request, timeout=self.timeout)
        except grpc.RpcError as exc:
            if exc.code() == grpc.StatusCode.ALREADY_EXISTS:
                return "exists", f"{exc.code().name}: {exc.details()}"
            return "error", f"{exc.code().name}: {exc.details()}"
        return "ok", f"{response.status}: {response.message}"

    def verify(self, code: str) -> tuple[str, str]:
        try:
            response = self.auth.VerifyEmail(
                auth_service_pb2.VerifyEmailRequest(verificationCode=code), timeout=self.timeout
            )
        except grpc.RpcError as exc:
            return "error", f"{exc.code().name}: {exc.details()}"
        return "ok", f"{response.status}: {response.message}"

    def sign_in(self, email: str, password: str) -> tuple[str, str]:
        try:
            response = self.auth.SignInUser(
                rpc_signin_user_pb2.SignInUserInput(email=email, password=password),
                timeout=self.timeout,
            )
        except grpc.RpcError as exc:
            return "error", f"{exc.code().name}: {exc.details()}"
        return "ok", f"{response.status}: access token {response.access_token[:12]}…"


def _load_existing(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    try:
        return {e["email"]: e["password"] for e in json.loads(path.read_text(encoding="utf-8"))}
    except (ValueError, KeyError, TypeError):
        return {}


def _write_credentials(path: Path, outcomes: list[Outcome]) -> None:
    payload = [{"email": o.email, "password": o.password} for o in outcomes]
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="create_users.py",
        description="Sign up, verify and sanity-check the accounts used by the load test.",
        epilog=(
            "Example: create_users.py --email 'you+cyrex{n}@gmail.com' --count 3\n"
            "The verification codes arrive by e-mail; paste each one (or the whole link) "
            "when asked, "
            "or pass them with --codes."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--host", default=DEFAULT_HOST, help=f"gRPC server (default: {DEFAULT_HOST})"
    )
    parser.add_argument(
        "--out", type=Path, default=Path("credentials.json"), help="credentials file to write"
    )
    parser.add_argument(
        "--email", default="loadtest+cyrex{n}@example.com", help="e-mail template, {n} = 1..count"
    )
    parser.add_argument(
        "--emails", help="comma-separated explicit e-mail addresses (overrides --email/--count)"
    )
    parser.add_argument("--count", type=int, default=3, help="number of accounts (task: 3)")
    parser.add_argument("--name", default="Load Tester {n}", help="display-name template")
    parser.add_argument(
        "--password", help="use this password for every account (default: random per account)"
    )
    parser.add_argument(
        "--codes", help="comma-separated verification codes, in e-mail order (non-interactive)"
    )
    parser.add_argument(
        "--skip-signup", action="store_true", help="accounts already exist; only verify/sign in"
    )
    parser.add_argument(
        "--skip-verify", action="store_true", help="accounts already verified; only sign in"
    )
    parser.add_argument("--timeout", type=float, default=15.0, help="per-RPC timeout in seconds")
    return parser


def run(
    argv: list[str] | None = None,
    *,
    input_func: Callable[[str], str] = input,
    echo: Callable[[str], None] = print,
) -> int:
    args = build_parser().parse_args(argv)
    emails = (
        [e.strip() for e in args.emails.split(",") if e.strip()]
        if args.emails
        else [args.email.format(n=n) for n in range(1, args.count + 1)]
    )
    codes = [c.strip() for c in args.codes.split(",")] if args.codes else []
    existing = _load_existing(args.out)
    outcomes: list[Outcome] = []
    client = Provisioner(args.host, timeout=args.timeout)
    echo(f"server: {args.host}")
    try:
        for index, email in enumerate(emails, start=1):
            password = args.password or existing.get(email) or secrets.token_urlsafe(12)
            outcome = Outcome(email=email, password=password)
            outcomes.append(outcome)
            echo(f"\n[{index}/{len(emails)}] {email}")
            if not args.skip_signup:
                outcome.signed_up, message = client.sign_up(
                    args.name.format(n=index), email, password
                )
                echo(f"  SignUpUser   → {message}")
            if not args.skip_verify:
                if index - 1 < len(codes):
                    code = codes[index - 1]
                else:
                    code = _code_from_input(
                        input_func(
                            f"  verification code for {email} (from the e-mail; empty = skip): "
                        )
                    )
                if code:
                    outcome.verified, message = client.verify(code)
                    echo(f"  VerifyEmail  → {message}")
                else:
                    outcome.verified = "skipped"
            outcome.signed_in, message = client.sign_in(email, password)
            echo(f"  SignInUser   → {message}")
    finally:
        client.close()

    _write_credentials(args.out, outcomes)
    echo(f"\nwrote {len(outcomes)} account(s) to {args.out} (mode 0600)")
    echo(f"{'e-mail':40} {'sign-up':8} {'verify':8} {'sign-in':8}")
    for o in outcomes:
        echo(f"{o.email:40} {o.signed_up:8} {o.verified:8} {o.signed_in:8}")
    failed = [o for o in outcomes if not o.ok]
    if failed:
        echo(
            f"\n{len(failed)} account(s) cannot sign in yet; "
            "re-run after fixing them (existing ones are kept)."
        )
        return 1
    echo("\nall accounts can sign in; the load test is ready to run.")
    return 0


def main() -> int:
    try:
        return run()
    except KeyboardInterrupt:
        print("\naborted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
