"""Smoke tests for every user-facing entry point: each web endpoint and CLI command runs end to end.

They use the fake league from ``test_matchup`` (no network), so they catch wiring mistakes
such as broken imports or name clashes that unit tests of the logic would miss.
"""

import json

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from fantasycoach.interface import cli, web
from test_matchup import BenchStarClient

LEAGUE = {"league_id": "L", "user_id": "me"}


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(web, "client", BenchStarClient())
    return TestClient(web.app)


def test_lineup_endpoint(api):
    body = api.get("/api/lineup", params=LEAGUE).json()
    assert body["league"] == "Test League" and body["week"] == 11
    assert [p["id"] for p in body["to_start"]] == ["wb"]  # bench star should start
    assert body["gain"] > 0 and body["team_range"]["floor"] < body["team_range"]["ceiling"]


def test_waivers_endpoint(api):
    body = api.get("/api/waivers", params={**LEAGUE, "weeks": 2}).json()
    assert body["weeks"] == 2 and isinstance(body["picks"], list)


def test_state_and_page(api):
    assert api.get("/api/state").json() == {"season": "2026", "week": 11}
    assert "FantasyCoach" in api.get("/").text


@pytest.fixture
def run_cli(monkeypatch, tmp_path):
    """Invoke CLI commands against the fake league with an isolated config folder."""
    monkeypatch.setenv("FANTASYCOACH_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text(json.dumps({"username": "me", "user_id": "me", "league_id": "L"}))
    monkeypatch.setattr(cli, "_client", lambda: BenchStarClient())
    runner = CliRunner()

    def run(*args):
        result = runner.invoke(cli.app, list(args))
        assert result.exit_code == 0, result.output
        return result.output

    return run


def test_cli_lineup(run_cli):
    out = run_cli("lineup")
    assert "Bench Star" in out and "Team range" in out


def test_cli_matchup_views(run_cli):
    assert "Win probability" in run_cli("matchup")
    assert "(optimal)" in run_cli("matchup", "--optimal", "--opp-optimal")


def test_cli_waivers_and_players(run_cli):
    run_cli("waivers", "--weeks", "2")
    assert "Bench Star" in run_cli("players", "Bench Star", "--weeks", "1")


def test_cli_requires_setup(monkeypatch, tmp_path):
    monkeypatch.setenv("FANTASYCOACH_HOME", str(tmp_path))
    result = CliRunner().invoke(cli.app, ["lineup"])
    assert result.exit_code == 1 and "Not set up yet" in result.output
