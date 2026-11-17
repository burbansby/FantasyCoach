"""Shared advice workflows used by both the CLI and the web app."""

from __future__ import annotations

from dataclasses import dataclass

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


@dataclass
class LineupView:
    """One version of a team's lineup (as set, or the highest-projected legal lineup)."""

    slots: list[lineup_mod.LineupSlot]
    projected: float
    range: object = None  # ranges.TeamRange


@dataclass
class MatchupSide:
    roster_id: int
    team_name: str
    owner: str
    avatar: str | None
    current: LineupView
    optimal: LineupView

    # Backwards-compatible shortcuts to the lineup as currently set.
    @property
    def slots(self) -> list[lineup_mod.LineupSlot]:
        return self.current.slots

    @property
    def projected(self) -> float:
        return self.current.projected

    @property
    def range(self):
        return self.current.range

    def view(self, name: str) -> LineupView:
        return self.optimal if name == "optimal" else self.current


@dataclass
class Matchup:
    league: str
    week: int
    me: MatchupSide
    opponent: MatchupSide | None  # None on a bye / outside the regular schedule
    # Chance you outscore your opponent for each pairing, keyed (your view, their view),
    # views being "current" or "optimal".
    win_probabilities: dict[tuple[str, str], float] | None = None

    @property
    def win_probability(self) -> float | None:
        return (self.win_probabilities or {}).get(("current", "current"))


def order_by_projection(slots: list[lineup_mod.LineupSlot]) -> list[lineup_mod.LineupSlot]:
    """Re-seat the same starters so each slot type runs from highest to lowest projection.

    Slot positions are unchanged; only who sits where among interchangeable players moves
    (best RB in RB1, weakest flex-eligible player in the last FLEX). If the re-seating can't
    place every starter (unusual slot combinations), the original order is kept.
    """
    players = [s.player for s in slots if s.player]
    reordered, leftover = lineup_mod.optimize(players, [s.slot for s in slots])
    if leftover or sum(1 for s in reordered if s.player) != len(players):
        return slots
    return reordered


def matchup(client: SleeperClient, league_id: str, user_id: str, week: int | None = None) -> Matchup:
    """Both teams' lineups for the week, as set and optimal, with projections and ranges."""
    ctx = LeagueContext.load(client, league_id, user_id, week)
    my_id = ctx.my_roster.get("roster_id")
    rows = client.matchups(league_id, ctx.week)
    mine = next((r for r in rows if r.get("roster_id") == my_id), None)
    opp = None
    if mine and mine.get("matchup_id") is not None:
        opp = next((r for r in rows if r.get("matchup_id") == mine["matchup_id"] and r.get("roster_id") != my_id), None)

    rosters = {r["roster_id"]: r for r in client.rosters(league_id)}
    users = {u["user_id"]: u for u in client.league_users(league_id)}
    projections = ctx.projections_by_id(client, ctx.week)
    current_week = int(client.state().get("week") or ctx.week)
    engine = engine_for(client)
    built: dict[str, object] = {}

    def player(pid: str):
        if pid not in built:
            built[pid] = ctx.build_player(pid, projections.get(pid))
        return built[pid]

    def view(slots: list[lineup_mod.LineupSlot]) -> LineupView:
        return LineupView(slots, round(sum(s.player.projected for s in slots if s.player), 2))

    def side(roster_id: int, row: dict | None) -> MatchupSide:
        roster = rosters.get(roster_id, {})
        starters = (row or {}).get("starters") or roster.get("starters") or []
        current = []
        for i, slot in enumerate(ctx.starter_slots):
            pid = starters[i] if i < len(starters) else None
            current.append(lineup_mod.LineupSlot(slot, player(pid) if pid and pid != "0" else None))
        current = order_by_projection(current)
        # Optimal: the highest-projected legal lineup from everyone not on IR / taxi.
        unavailable = set(roster.get("reserve") or []) | set(roster.get("taxi") or [])
        pool = [player(pid) for pid in dict.fromkeys((roster.get("players") or []) + list(starters))
                if pid and pid != "0" and pid not in unavailable]
        optimal, _ = lineup_mod.optimize(pool, ctx.starter_slots)
        user = users.get(roster.get("owner_id"), {})
        owner = user.get("display_name") or f"Team {roster_id}"
        return MatchupSide(
            roster_id=roster_id,
            team_name=(user.get("metadata") or {}).get("team_name") or owner,
            owner=owner,
            avatar=f"https://sleepercdn.com/avatars/thumbs/{user['avatar']}" if user.get("avatar") else None,
            current=view(current),
            optimal=view(optimal),
        )

    me = side(my_id, mine)
    them = side(opp["roster_id"], opp) if opp else None
    sides = [me] + ([them] if them else [])
    views = ("current", "optimal")

    def ids(v: LineupView) -> list[str]:
        return [s.player.player_id for s in v.slots if s.player]

    found = engine.week_ranges(ctx.season, ctx.week, current_week,
                               list(dict.fromkeys(pid for sd in sides for v in views for pid in ids(sd.view(v)))),
                               ctx.scoring)
    for pid, p in built.items():
        p.range = found.get(pid)

    mine_ids = {v: ids(me.view(v)) for v in views}
    their_ids = {v: ids(them.view(v)) for v in views} if them else {}
    my_ranges, their_ranges, wins = engine.matchup_scenarios(
        ctx.season, ctx.week, current_week, mine_ids, their_ids, ctx.scoring
    )
    for v in views:
        me.view(v).range = my_ranges[v]
        if them:
            them.view(v).range = their_ranges[v]
    return Matchup(league=ctx.league.get("name") or "", week=ctx.week, me=me, opponent=them,
                   win_probabilities=wins or None)
