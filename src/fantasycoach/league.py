"""Assemble everything needed to give advice for one team in one league."""

from __future__ import annotations

from dataclasses import dataclass

from .lineup import NON_STARTER_SLOTS, SLOT_ELIGIBILITY
from .models import Player
from .scoring import availability_factor, score
from .sleeper import SleeperClient, SleeperError


@dataclass
class LeagueContext:
    league: dict
    season: str
    week: int
    my_roster: dict
    rostered_ids: set[str]
    positions: list[str]  # player positions this league uses
    players_db: dict[str, dict]

    @property
    def scoring(self) -> dict[str, float]:
        return self.league.get("scoring_settings") or {}

    @property
    def starter_slots(self) -> list[str]:
        return [s for s in self.league.get("roster_positions", []) if s not in NON_STARTER_SLOTS]

    @classmethod
    def load(cls, client: SleeperClient, league_id: str, user_id: str, week: int | None = None) -> "LeagueContext":
        league = client.league(league_id)
        state = client.state()
        season = league.get("season") or state["season"]
        if week is None:
            week = int(state.get("week") or 1)

        rosters = client.rosters(league_id)
        mine = next((r for r in rosters if r.get("owner_id") == user_id), None)
        if mine is None:
            mine = next((r for r in rosters if user_id in (r.get("co_owners") or [])), None)
        if mine is None:
            raise SleeperError("You don't own a roster in this league")

        rostered: set[str] = set()
        for r in rosters:
            rostered.update(r.get("players") or [])

        positions: set[str] = set()
        for slot in league.get("roster_positions", []):
            positions |= SLOT_ELIGIBILITY.get(slot, set())

        return cls(
            league=league,
            season=str(season),
            week=week,
            my_roster=mine,
            rostered_ids=rostered,
            positions=sorted(positions),
            players_db=client.players(),
        )

    # --- player construction ------------------------------------------------------

    def projections_by_id(self, client: SleeperClient, week: int) -> dict[str, dict]:
        rows = client.projections(self.season, week, self.positions)
        return {row["player_id"]: row for row in rows if row.get("player_id")}

    def build_player(self, player_id: str, projection: dict | None) -> Player:
        info = self.players_db.get(player_id) or (projection or {}).get("player") or {}
        name = info.get("full_name") or " ".join(
            p for p in (info.get("first_name"), info.get("last_name")) if p
        ) or player_id
        position = info.get("position") or "?"
        eligible = [p for p in (info.get("fantasy_positions") or [position]) if p]
        # Prefer a position this league actually uses (e.g. a DB/WR two-way player counts as WR,
        # a fullback as RB) so comparisons and lineup slots line up.
        league_eligible = [p for p in eligible if p in self.positions]
        if position not in self.positions and league_eligible:
            position = league_eligible[0]
        status = info.get("injury_status")
        projected = score(projection.get("stats", {}), self.scoring) if projection else 0.0
        return Player(
            player_id=player_id,
            name=name,
            position=position,
            team=(projection or {}).get("team") or info.get("team"),
            injury_status=status,
            opponent=(projection or {}).get("opponent"),
            projected=projected,
            adjusted=round(projected * availability_factor(status), 2),
            eligible=eligible,
        )

    def horizon_values(self, client: SleeperClient, weeks: int) -> dict[str, float]:
        """Injury-adjusted projected points per player summed over ``weeks`` weeks from ``self.week``."""
        totals: dict[str, float] = {}
        for offset in range(weeks):
            for pid, row in self.projections_by_id(client, self.week + offset).items():
                p = self.build_player(pid, row)
                factor = availability_factor(p.injury_status, weeks_ahead=offset)
                totals[pid] = round(totals.get(pid, 0.0) + p.projected * factor, 2)
        return totals

    def my_players(self, projections: dict[str, dict]) -> list[Player]:
        return [self.build_player(pid, projections.get(pid)) for pid in self.my_roster.get("players") or []]

    def free_agents(self, projections: dict[str, dict]) -> list[Player]:
        players = [
            self.build_player(pid, row)
            for pid, row in projections.items()
            if pid not in self.rostered_ids
        ]
        return [p for p in players if p.position in self.positions]
