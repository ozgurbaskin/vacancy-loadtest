"""Pseudo-random vacancy data for the create and update steps."""

from __future__ import annotations

import random
import uuid
from typing import Any

from .pb import rpc_create_vacancy_pb2, rpc_update_vacancy_pb2, vacancy_pb2

DIVISIONS: tuple[int, ...] = tuple(vacancy_pb2.Vacancy.DIVISION.values())

LEVELS = ("Junior", "Mid-level", "Senior", "Lead", "Principal")
ROLES = (
    "Python Developer",
    "Backend Engineer",
    "Site Reliability Engineer",
    "Security Analyst",
    "Penetration Tester",
    "Sales Executive",
    "Account Manager",
    "Data Engineer",
    "QA Engineer",
    "Product Manager",
)
COUNTRIES = (
    "Netherlands",
    "Belgium",
    "Germany",
    "Spain",
    "Portugal",
    "Poland",
    "Türkiye",
    "United Kingdom",
    "Ireland",
    "Sweden",
)
_DESCRIPTION_PARTS = (
    "You will join a small, senior team building high-throughput gRPC services.",
    "We care about observability, load testing and boring, reliable deployments.",
    "Experience with Python, Go or Rust is a plus; curiosity is a must.",
    "Hybrid work, four days a week remote, quarterly team weeks.",
    "You own features end to end: design, implementation, rollout and measurement.",
    "Expect code review, pairing and a yearly learning budget.",
    "Competitive salary, equity and a generous hardware policy.",
    "Interview process: intro call, practical test, technical interview, offer.",
)

#: fields UpdateVacancy changes. `Views` is deliberately left out: the real server accepts it in
#: UpdateVacancyRequest but never stores it (observed: Views stays 0 after an update), so sending
#: it would only produce false consistency-check failures.
UPDATABLE_FIELDS: tuple[str, ...] = ("Title", "Description", "Division", "Country")


class VacancyFactory:
    """Generates request messages. ``seed`` makes the *content* reproducible.

    Titles always carry a random suffix taken from ``uuid4`` (not from the seeded RNG) because
    the server keeps titles unique: concurrent users, workers or repeated seeded runs must not
    collide and fail with ALREADY_EXISTS.
    """

    def __init__(self, seed: int | None = None) -> None:
        self._rng = random.Random(seed)

    # -- single values ---------------------------------------------------------------------
    def title(self) -> str:
        return f"{self._rng.choice(LEVELS)} {self._rng.choice(ROLES)} [{uuid.uuid4().hex[:8]}]"

    def description(self) -> str:
        return " ".join(self._rng.sample(_DESCRIPTION_PARTS, 3))

    def division(self, *, for_update: bool = False) -> int:
        # Updates never send the proto3 zero value (DEVELOPMENT = 0): the server drops zero
        # values on update ("omitempty" semantics, observed), which would make the consistency
        # check after GetVacancy report a false mismatch.
        choices = [d for d in DIVISIONS if d != 0] if for_update else list(DIVISIONS)
        return self._rng.choice(choices)

    def country(self) -> str:
        return self._rng.choice(COUNTRIES)

    # -- request messages ------------------------------------------------------------------
    def create_request(self) -> rpc_create_vacancy_pb2.CreateVacancyRequest:
        return rpc_create_vacancy_pb2.CreateVacancyRequest(
            Title=self.title(),
            Description=self.description(),
            Division=self.division(),
            Country=self.country(),
        )

    def update_request(
        self, vacancy_id: str, field_count: int | None = None
    ) -> tuple[rpc_update_vacancy_pb2.UpdateVacancyRequest, dict[str, Any]]:
        """Return an UpdateVacancyRequest that changes 1-3 random fields, plus the expected
        values so the caller can verify them after GetVacancy."""
        count = field_count if field_count is not None else self._rng.randint(1, 3)
        count = max(1, min(count, len(UPDATABLE_FIELDS)))
        chosen = self._rng.sample(UPDATABLE_FIELDS, count)
        request = rpc_update_vacancy_pb2.UpdateVacancyRequest(Id=vacancy_id)
        expected: dict[str, Any] = {}
        for field in chosen:
            if field == "Division":
                value: Any = self.division(for_update=True)
            else:
                value = getattr(self, field.lower())()
            setattr(request, field, value)
            expected[field] = value
        return request, expected
