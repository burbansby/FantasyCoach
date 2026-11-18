"""Head-to-head matchup: your lineup vs your opponent's, as set and optimal.

Builds both teams' starting lineups for a week (from Sleeper's matchups), re-seats each
team's starters from best to worst within interchangeable slots, works out each team's
optimal lineup from its roster, and attaches projections, team floor/ceiling and the
win probability for every current/optimal pairing. Projections only, no advice.
"""

from __future__ import annotations

from dataclasses import dataclass

from fantasycoach.league import lineup as lineup_mod
from fantasycoach.league.context import LeagueContext
from fantasycoach.projections.engine import engine_for
from fantasycoach.sources.sleeper import SleeperClient, current_season_week


@dataclass
class LineupView:
    """One version of a team's lineup (as set, or the highest-projected legal lineup)."""

    slots: list[lineup_mod.LineupSlot]
    projected: float
    range: object = None  # projections.simulation.TeamRange


@dataclass
class MatchupSide:
    """One team in the matchup: who they are plus their current and optimal lineups."""
    roster_id: int
    team_name: str
    owner: str
    avatar: str | None
    current: LineupView
    optimal: LineupView

    def view(self, name: str) -> LineupView:
        """The 'current' or 'optimal' lineup."""
        return self.optimal if name == "optimal" else self.current


@dataclass
class Matchup:
    """Both sides of a week's matchup and the win probability for each lineup pairing."""
    league: str
    week: int
    me: MatchupSide
    opponent: MatchupSide | None  # None on a bye / outside the regular schedule
    # Chance you outscore your opponent for each pairing, keyed (your view, their view),
    # views being "current" or "optimal".
    win_probabilities: dict[tuple[str, str], float] | None = None


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
    _, current_week = current_season_week(client)
    engine = engine_for(client)
    built: dict[str, object] = {}

    def player(pid: str):
        """Build each Player once so current and optimal lineups share objects (and ranges)."""
        if pid not in built:
            built[pid] = ctx.build_player(pid, projections.get(pid))
        return built[pid]

    def view(slots: list[lineup_mod.LineupSlot]) -> LineupView:
        """Wrap slots with their projected total."""
        return LineupView(slots, round(sum(s.player.projected for s in slots if s.player), 2))

    def side(roster_id: int, row: dict | None) -> MatchupSide:
        """One team's identity plus its current (as set, re-seated) and optimal lineups."""
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
        """Player ids of a lineup's filled slots."""
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
