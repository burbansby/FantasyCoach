"""Shared advice workflows used by both the CLI and the web app."""

from __future__ import annotations

from . import lineup as lineup_mod
from . import players as players_mod
from . import waivers as waivers_mod
from .range_engine import engine_for
from .league import LeagueContext
from .lineup import LineupAdvice
from .sleeper import SleeperClient
from .waivers import WaiverPick


def _reserve_ids(ctx: LeagueContext) -> set[str]:
    return set(ctx.my_roster.get("reserve") or []) | set(ctx.my_roster.get("taxi") or [])


def lineup_advice(
    client: SleeperClient, league_id: str, user_id: str, week: int | None = None
) -> tuple[LeagueContext, LineupAdvice]:
    ctx = LeagueContext.load(client, league_id, user_id, week)
    projections = ctx.projections_by_id(client, ctx.week)
    reserve = _reserve_ids(ctx)
    players = [p for p in ctx.my_players(projections) if p.player_id not in reserve]
    advice = lineup_mod.advise(players, ctx.starter_slots, ctx.my_roster.get("starters") or [])
    current_week = int(client.state().get("week") or ctx.week)
    engine = engine_for(client)
    found = engine.week_ranges(ctx.season, ctx.week, current_week, [p.player_id for p in players], ctx.scoring)
    for p in players:
        p.range = found.get(p.player_id)
    starters = [s.player.player_id for s in advice.recommended if s.player]
    advice.recommended_range = engine.lineup_range(ctx.season, ctx.week, current_week, starters, ctx.scoring)
    current = [pid for pid in ctx.my_roster.get("starters") or [] if pid and pid != "0"]
    advice.current_range = (
        advice.recommended_range if set(current) == set(starters)
        else engine.lineup_range(ctx.season, ctx.week, current_week, current, ctx.scoring)
    )
    return ctx, advice


def waiver_picks(
    client: SleeperClient,
    league_id: str,
    user_id: str,
    week: int | None = None,
    weeks: int = 3,
    position: str | None = None,
    limit: int = 15,
) -> tuple[LeagueContext, list[WaiverPick]]:
    ctx = LeagueContext.load(client, league_id, user_id, week)
    horizon = ctx.horizon_values(client, weeks)
    first_week = ctx.projections_by_id(client, ctx.week)
    picks = waivers_mod.recommend(
        ctx.free_agents(first_week),
        ctx.my_players(first_week),
        horizon,
        _reserve_ids(ctx),
        position.upper() if position else None,
        limit,
    )
    return ctx, picks


def resolve_scoring(client: SleeperClient, scoring: str | None, league_id: str | None) -> tuple[str, dict[str, float]]:
    """Pick scoring rules: a league's own settings, or a named preset (default: Sleeper default).

    ``scoring`` is "league", one of ``players.SCORING_PRESETS``, or None (league if
    ``league_id`` is given, otherwise the Sleeper default).
    """
    if scoring in (None, "league") and league_id:
        league = client.league(league_id)
        return league.get("name") or "League scoring", league.get("scoring_settings") or {}
    label, rules = players_mod.SCORING_PRESETS.get(scoring or "default", players_mod.SCORING_PRESETS["default"])
    return label, rules


def compare_players(
    client: SleeperClient,
    player_ids: list[str],
    scoring: str | None = None,
    league_id: str | None = None,
    week: int | None = None,
    weeks: int = 4,
    with_ranges: bool = True,
) -> tuple[str, int, list[players_mod.PlayerOutlook]]:
    state = client.state()
    start = week or int(state.get("week") or 1)
    label, rules = resolve_scoring(client, scoring, league_id)
    season = str(state.get("league_season") or state.get("season"))
    outs = players_mod.outlooks(client, player_ids, rules, season, start, weeks)
    if outs and with_ranges:
        engine = engine_for(client)
        current_week = int(state.get("week") or start)
        for i, w in enumerate(outs[0].weeks):
            found = engine.week_ranges(season, w.week, current_week, player_ids, rules)
            for o in outs:
                o.weeks[i].range = found.get(o.player_id)
    return label, start, outs
