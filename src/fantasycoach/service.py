"""Shared advice workflows used by both the CLI and the web app."""

from __future__ import annotations

from . import lineup as lineup_mod
from . import waivers as waivers_mod
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
