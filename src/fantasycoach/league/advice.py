"""Start/sit and waiver-wire workflows for your team.

Each function loads your league context, runs the pure logic from ``league.lineup`` / ``league.waivers``
and attaches outcome ranges from the projection engine. Used by both the CLI and web app.
"""

from __future__ import annotations

from fantasycoach.league import lineup as lineup_mod
from fantasycoach.league import waivers as waivers_mod
from fantasycoach.league.context import LeagueContext
from fantasycoach.league.lineup import LineupAdvice
from fantasycoach.league.waivers import WaiverPick
from fantasycoach.projections.engine import engine_for
from fantasycoach.sources.sleeper import SleeperClient, current_season_week


def _reserve_ids(ctx: LeagueContext) -> set[str]:
    return set(ctx.my_roster.get("reserve") or []) | set(ctx.my_roster.get("taxi") or [])


def lineup_advice(
    client: SleeperClient, league_id: str, user_id: str, week: int | None = None
) -> tuple[LeagueContext, LineupAdvice]:
    """Recommended lineup for your team with player ranges and team floor/ceiling (recommended and current)."""
    ctx = LeagueContext.load(client, league_id, user_id, week)
    projections = ctx.projections_by_id(client, ctx.week)
    reserve = _reserve_ids(ctx)
    players = [p for p in ctx.my_players(projections) if p.player_id not in reserve]
    advice = lineup_mod.advise(players, ctx.starter_slots, ctx.my_roster.get("starters") or [])
    _, current_week = current_season_week(client)
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
    """Top waiver adds over the next ``weeks`` weeks, each paired with a suggested drop."""
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
