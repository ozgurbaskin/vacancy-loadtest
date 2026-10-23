"""Small helpers shared by the test modules (kept out of conftest.py so they can be imported)."""

from __future__ import annotations

import json
from pathlib import Path

from locust.env import Environment

from vacancy_loadtest.users import VacancyUser

ACCOUNTS = [
    ("tester1@example.com", "pw-one"),
    ("tester2@example.com", "pw-two"),
    ("tester3@example.com", "pw-three"),
]

SIGN_IN = "pb.AuthService/SignInUser"
CREATE = "pb.VacancyService/CreateVacancy"
UPDATE = "pb.VacancyService/UpdateVacancy"
GET = "pb.VacancyService/GetVacancy"
DELETE = "pb.VacancyService/DeleteVacancy"
LIST = "pb.VacancyService/GetVacancies"


def write_credentials(path: Path, accounts=ACCOUNTS) -> Path:
    path.write_text(
        json.dumps([{"email": e, "password": p} for e, p in accounts]), encoding="utf-8"
    )
    return path


def names(environment: Environment) -> list[str]:
    return [event["name"] for event in environment.recorded]


def events_of(environment: Environment, request_type: str) -> list[dict]:
    return [event for event in environment.recorded if event["request_type"] == request_type]


def stop_sweep(user: VacancyUser) -> None:
    """Kill the background GetVacancies loop so a test only sees the events it triggers."""
    if user._sweeper is not None:
        user._sweeper.kill(block=True)
        user._sweeper = None
