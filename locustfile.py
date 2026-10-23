"""Locust entry point: ``locust -f locustfile.py`` (or just ``locust``: this is the default name).

The user class lives in ``vacancy_loadtest.users``; importing it also registers the custom
command-line options (listed in the README).
"""

from vacancy_loadtest.users import VacancyUser

__all__ = ["VacancyUser"]
