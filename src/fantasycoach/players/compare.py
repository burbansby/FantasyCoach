"""Player comparison workflow: pick scoring rules, build week-by-week outlooks, add ranges.

Works without a league: if no league is given, Sleeper's default scoring is used.
"""

from __future__ import annotations

from fantasycoach.core.scoring import SCORING_PRESETS
from fantasycoach.players import lookup
from fantasycoach.projections.engine import engine_for
from fantasycoach.sources.sleeper import SleeperClient, current_season_week


def resolve_scoring(client: SleeperClient, scoring: str | None, league_id: str | None) -> tuple[str, dict[str, float]]:
    """Pick scoring rules: a league's own settings, or a named preset (default: Sleeper default).

    ``scoring`` is "league", one of ``core.scoring.SCORING_PRESETS``, or None (league if
    ``league_id`` is given, otherwise the Sleeper default).
    """
    if scoring in (None, "league") and league_id:
        league = client.league(league_id)
        return league.get("name") or "League scoring", league.get("scoring_settings") or {}
    label, rules = SCORING_PRESETS.get(scoring or "default", SCORING_PRESETS["default"])
    return label, rules


def compare_players(
    client: SleeperClient,
    player_ids: list[str],
    scoring: str | None = None,
    league_id: str | None = None,
    week: int | None = None,
    weeks: int = 4,
) -> tuple[str, int, list[lookup.PlayerOutlook]]:
    """Week-by-week outlooks with ranges for ``player_ids``; returns (scoring label, first week, outlooks)."""
    season, current_week = current_season_week(client)
    start = week or current_week
    label, rules = resolve_scoring(client, scoring, league_id)
    outs = lookup.outlooks(client, player_ids, rules, season, start, weeks)
    if outs:
        engine = engine_for(client)
        for i, w in enumerate(outs[0].weeks):
            found = engine.week_ranges(season, w.week, current_week, player_ids, rules)
            for o in outs:
                o.weeks[i].range = found.get(o.player_id)
    return label, start, outs
