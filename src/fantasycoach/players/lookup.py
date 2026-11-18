"""Player lookup: find players by name and build week-by-week projection outlooks.

``search`` matches loosely (first, last or full name, team defenses by city or nickname)
and ranks results by Sleeper's popularity order. ``outlooks`` scores each player's
projections over several weeks with any scoring rules and pulls out key stat lines.
No league or sign-in needed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fantasycoach.core.models import FANTASY_POSITIONS, display_name, fantasy_position, normalize
from fantasycoach.core.scoring import score
from fantasycoach.sources.sleeper import SleeperClient


# Stats worth showing in a quick per-week line, by position.
KEY_STATS: dict[str, list[tuple[str, str]]] = {
    "QB": [("pass_yd", "Pass Yds"), ("pass_td", "Pass TD"), ("pass_int", "INT"), ("rush_yd", "Rush Yds"), ("rush_td", "Rush TD")],
    "RB": [("rush_att", "Att"), ("rush_yd", "Rush Yds"), ("rush_td", "Rush TD"), ("rec", "Rec"), ("rec_yd", "Rec Yds")],
    "WR": [("rec_tgt", "Tgt"), ("rec", "Rec"), ("rec_yd", "Rec Yds"), ("rec_td", "Rec TD"), ("rush_yd", "Rush Yds")],
    "TE": [("rec_tgt", "Tgt"), ("rec", "Rec"), ("rec_yd", "Rec Yds"), ("rec_td", "Rec TD")],
    "K": [("fgm", "FG"), ("fga", "FGA"), ("xpm", "XP")],
    "DEF": [("sack", "Sacks"), ("int", "INT"), ("fum_rec", "Fum Rec"), ("pts_allow", "Pts Allowed")],
}

def search(players_db: dict[str, dict], query: str, limit: int = 8) -> list[tuple[str, dict]]:
    """Fantasy-relevant players matching ``query``, best matches first.

    Exact name > full/first/last name starts with query > name contains query;
    ties broken by Sleeper's ``search_rank`` (lower = more relevant), then active team.
    """
    q = normalize(query)
    if not q:
        return []
    hits: list[tuple[tuple, str, dict]] = []
    for pid, info in players_db.items():
        position = fantasy_position(info)
        if position is None:
            continue
        name = normalize(display_name(info))
        parts = [normalize(info.get("first_name") or ""), normalize(info.get("last_name") or "")]
        if q == name or q == pid.lower():
            tier = 0
        elif name.startswith(q) or any(part.startswith(q) for part in parts if part):
            tier = 1
        elif q in name:
            tier = 2
        else:
            continue
        rank = info.get("search_rank") or 10**7
        hits.append(((tier, not info.get("team"), rank), pid, info))
    hits.sort(key=lambda h: h[0])
    return [(pid, info) for _, pid, info in hits[:limit]]


@dataclass
class WeekLine:
    """One player's projection for one week."""
    week: int
    opponent: str | None
    points: float
    stats: dict[str, float]
    projected: bool = True  # False when Sleeper has no projection row (e.g. free agent)
    range: Any = None  # projections.simulation.Range: expected / floor / ceiling, when available

    @property
    def bye(self) -> bool:
        """True when the player's team has no game that week."""
        return self.projected and not self.opponent


@dataclass
class PlayerOutlook:
    """A player's projections across several weeks, with totals."""
    player_id: str
    name: str
    position: str
    team: str | None
    injury_status: str | None
    weeks: list[WeekLine] = field(default_factory=list)

    @property
    def total(self) -> float:
        """Projected points summed over the weeks shown."""
        return round(sum(w.points for w in self.weeks), 2)

    @property
    def average(self) -> float:
        """Average projection over the weeks with a game."""
        played = [w for w in self.weeks if w.opponent and w.projected]
        return round(sum(w.points for w in played) / len(played), 2) if played else 0.0

    @property
    def this_week(self) -> float:
        """Projection for the first week shown."""
        return self.weeks[0].points if self.weeks else 0.0


def outlooks(
    client: SleeperClient,
    player_ids: list[str],
    scoring: dict[str, float],
    season: str,
    start_week: int,
    weeks: int = 4,
) -> list[PlayerOutlook]:
    """Week-by-week projections for each player over ``weeks`` weeks from ``start_week``."""
    db = client.players()
    results = []
    for pid in player_ids:
        info = db.get(pid) or {}
        results.append(PlayerOutlook(pid, display_name(info) or pid, fantasy_position(info) or "?",
                                     info.get("team"), info.get("injury_status")))
    for offset in range(weeks):
        week = start_week + offset
        if week > 18:
            break
        rows = {r.get("player_id"): r for r in client.projections(season, week, FANTASY_POSITIONS)}
        for out in results:
            row = rows.get(out.player_id)
            stats = (row or {}).get("stats") or {}
            keys = [k for k, _ in KEY_STATS.get(out.position, [])]
            out.weeks.append(WeekLine(
                week=week,
                opponent=(row or {}).get("opponent"),
                points=score(stats, scoring) if row else 0.0,
                stats={k: round(stats.get(k) or 0.0, 1) for k in keys},
                projected=row is not None,
            ))
    return results
