"""Waiver wire: rank free agents by how much they'd upgrade your roster."""

from __future__ import annotations

from dataclasses import dataclass

from .models import Player


@dataclass
class WaiverPick:
    add: Player
    add_value: float  # projected points over the horizon
    drop: Player | None
    drop_value: float
    this_week: float

    @property
    def gain(self) -> float:
        return round(self.add_value - self.drop_value, 2)


def recommend(
    free_agents: list[Player],
    my_players: list[Player],
    horizon_value: dict[str, float],
    protected_ids: set[str] = frozenset(),
    position: str | None = None,
    limit: int = 15,
) -> list[WaiverPick]:
    """Pair each free agent with your weakest same-position player.

    ``horizon_value`` maps player_id -> projected points summed over the
    upcoming weeks. Players in ``protected_ids`` (e.g. on IR)
    are never suggested as drops. Only picks with a positive gain are returned.
    """
    weakest: dict[str, Player] = {}
    for p in my_players:
        if p.player_id in protected_ids:
            continue
        cur = weakest.get(p.position)
        if cur is None or horizon_value.get(p.player_id, 0.0) < horizon_value.get(cur.player_id, 0.0):
            weakest[p.position] = p

    picks: list[WaiverPick] = []
    for fa in free_agents:
        if position and fa.position != position:
            continue
        value = horizon_value.get(fa.player_id, 0.0)
        if value <= 0:
            continue
        drop = weakest.get(fa.position)
        drop_value = horizon_value.get(drop.player_id, 0.0) if drop else 0.0
        pick = WaiverPick(add=fa, add_value=round(value, 2), drop=drop, drop_value=round(drop_value, 2), this_week=fa.projected)
        if pick.gain > 0:
            picks.append(pick)

    picks.sort(key=lambda p: p.gain, reverse=True)
    return picks[:limit]
