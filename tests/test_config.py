"""Settings come from Locust's parsed options; the web UI posts them back as strings."""

from argparse import Namespace

from locust.argument_parser import get_parser
from locust.env import Environment

from vacancy_loadtest.config import Settings


def test_defaults_match_the_task():
    settings = Settings.from_environment(None)
    assert (settings.crud_interval, settings.list_interval) == (30.0, 45.0)
    assert settings.credentials_file == "credentials.json"
    assert settings.rpc_timeout > 0
    assert settings.token_ttl < 15 * 60  # the server's access tokens last 15 minutes
    assert settings.relogin is True


def test_command_line_options_are_parsed():
    options = get_parser().parse_args(
        [
            "--crud-interval",
            "5",
            "--list-interval",
            "7",
            "--rpc-timeout",
            "3",
            "--token-ttl",
            "0",
            "--list-page-limit",
            "20",
            "--list-max-pages",
            "4",
            "--seed",
            "42",
            "--no-relogin",
        ]
    )
    settings = Settings.from_environment(Environment(parsed_options=options))
    assert settings == Settings(
        crud_interval=5.0,
        list_interval=7.0,
        rpc_timeout=3.0,
        token_ttl=0.0,
        list_page_limit=20,
        list_max_pages=4,
        seed=42,
        relogin=False,
    )


def test_web_ui_strings_are_normalised():
    posted = Namespace(
        credentials_file="creds.json",
        crud_interval="30",
        list_interval="45.5",
        list_page_limit="100",
        list_max_pages="",
        rpc_timeout="5",
        token_ttl="",
        seed="",
        relogin="false",
    )
    settings = Settings.from_environment(Environment(parsed_options=posted))
    assert settings.crud_interval == 30.0
    assert settings.list_interval == 45.5
    assert settings.list_page_limit == 100
    assert settings.list_max_pages == Settings().list_max_pages  # blank → default
    assert settings.rpc_timeout == 5.0
    assert settings.token_ttl == Settings().token_ttl
    assert settings.seed is None
    assert settings.relogin is False

    assert (
        Settings.from_environment(Environment(parsed_options=Namespace(seed="None"))).seed is None
    )
    assert Settings.from_environment(Environment(parsed_options=Namespace(seed="7"))).seed == 7
