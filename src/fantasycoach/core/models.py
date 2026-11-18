"""Player model and small helpers shared across the app.

``Player`` is the object passed between the lineup, waiver, matchup and web code. The
helpers normalise names for matching (Sleeper and ESPN spell names differently) and work
out which fantasy position a player counts as (a DB/WR two-way player counts as a WR).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Positions with fantasy projections (team defenses are "DEF"), sorted to keep cache keys stable.
FANTASY_POSITIONS = ["DEF", "K", "QB", "RB", "TE", "WR"]

_SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b")


def display_name(info: dict) -> str:
    """A Sleeper player's display name (team defenses have only first/last name)."""
    return info.get("full_name") or " ".join(p for p in (info.get("first_name"), info.get("last_name")) if p)


def normalize(text: str) -> str:
    """Name key for matching: lowercase, no punctuation, suffixes or spaces ("A.J. Brown Jr." -> "ajbrown")."""
    text = re.sub(r"[^a-z0-9 ]", "", text.lower())
    return re.sub(r"\s+", "", _SUFFIX.sub("", text))


def fantasy_position(info: dict) -> str | None:
    """The fantasy position a player counts as: their listed position, or for players listed
    elsewhere (e.g. a DB/WR two-way player) their first fantasy-eligible position."""
    pos = info.get("position")
    if pos in FANTASY_POSITIONS:
        return pos
    return next((p for p in info.get("fantasy_positions") or [] if p in FANTASY_POSITIONS), None)


@dataclass
class Player:
    """A player as used for one week's decisions: identity, matchup, projection and optional range."""
    player_id: str
    name: str
    position: str
    team: str | None = None
    injury_status: str | None = None
    opponent: str | None = None
    # Sleeper's projected points under league scoring for the week being analysed.
    # Injury designations are shown but never change this number (reports lag too much).
    projected: float = 0.0
    # Positions the player can fill (e.g. ["WR", "RB"] for dual-eligible players).
    eligible: list[str] = field(default_factory=list)
    # Expected / floor / ceiling (projections.simulation.Range) when computed; display only.
    range: Any = None

    @property
    def label(self) -> str:
        """Short display label, for example 'Bijan Robinson (RB, ATL)'."""
        team = self.team or "FA"
        return f"{self.name} ({self.position}, {team})"
