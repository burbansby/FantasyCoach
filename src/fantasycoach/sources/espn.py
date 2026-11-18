"""Live betting-market context from ESPN's public odds feed (DraftKings lines, no key, no quota).

Two things come from the market:

* **Player prop lines** (passing/rushing/receiving yards, receptions, passing TDs, INTs):
  the line is the book's median outcome, so it anchors the centre of each stat.
* **Game spread and total**: converted to each team's implied points, which scales
  touchdown expectations (the biggest driver of fantasy variance).

ESPN only exposes prices for game lines, not player props, so prop lines are treated as
medians. Everything is cached (30 min for odds, a day for rosters) and failures degrade to
"no market data" rather than breaking the app.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from fantasycoach.core.models import normalize
from fantasycoach.sources.sleeper import SleeperClient

log = logging.getLogger(__name__)

SITE = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
CORE = "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl"
DRAFTKINGS = "100"
TTL_ODDS = 30 * 60
TTL_ROSTER = 24 * 3600

# ESPN prop market name -> Sleeper stat key
PROP_STATS = {
    "Total Passing Yards (incl. overtime)": "pass_yd",
    "Total Passing Touchdowns (incl. overtime)": "pass_td",
    "Total Passing Interceptions (incl. overtime)": "pass_int",
    "Total Rushing Yards (incl. overtime)": "rush_yd",
    "Total Receptions (incl. overtime)": "rec",
    "Total Receiving Yards (incl. overtime)": "rec_yd",
}

STAT_LABELS = {
    "pass_yd": "pass yds", "pass_td": "pass TDs", "pass_int": "INTs",
    "rush_yd": "rush yds", "rec": "receptions", "rec_yd": "rec yds",
}

# ESPN team abbreviations that differ from Sleeper's.
TEAM_FIX = {"WSH": "WAS"}

_ATHLETE_ID = re.compile(r"/athletes/(\d+)")


def _team(abbr: str | None) -> str | None:
    return TEAM_FIX.get(abbr, abbr) if abbr else None


@dataclass
class Market:
    """Betting context for one week: implied team points and player prop lines."""
    week: int
    team_points: dict[str, float] = field(default_factory=dict)  # Sleeper team abbr -> implied points
    lines: dict[str, dict[str, float]] = field(default_factory=dict)  # Sleeper player_id -> {stat: line}
    games: int = 0
    error: str | None = None


def implied_points(details: str | None, total: float | None, home: str, away: str) -> dict[str, float]:
    """Split a game total into team totals using the spread, e.g. ("HOU -12.5", 44.5)."""
    if not total:
        return {}
    fav, spread = None, 0.0
    if details and details.upper() != "EVEN":
        parts = details.split()
        if len(parts) == 2:
            fav = _team(parts[0])
            try:
                spread = abs(float(parts[1]))
            except ValueError:
                spread = 0.0
    if fav not in (home, away):
        return {home: total / 2, away: total / 2}
    dog = away if fav == home else home
    return {fav: (total + spread) / 2, dog: (total - spread) / 2}


class MarketFetcher:
    """Pulls a week's scoreboard, game odds, team rosters and prop lines from ESPN."""
    def __init__(self, client: SleeperClient):
        self.client = client

    def week_market(self, season: str, week: int) -> Market:
        """Market data for every unfinished game in ``week``; empty (with ``error``) on failure."""
        market = Market(week=week)
        try:
            board = self.client.fetch_json(
                f"{SITE}/scoreboard", [("week", str(week)), ("seasontype", "2"), ("dates", season)], ttl=TTL_ODDS
            )
        except Exception as exc:  # network/API change: run without market data
            log.warning("ESPN scoreboard unavailable: %s", exc)
            market.error = "Betting lines unavailable right now"
            return market

        name_index = self._sleeper_name_index()
        espn_index = self._sleeper_espn_index()
        for event in board.get("events", []):
            try:
                self._add_event(market, event, name_index, espn_index)
            except Exception as exc:
                log.warning("Skipping ESPN event %s: %s", event.get("id"), exc)
        return market

    # --- internals -------------------------------------------------------------------

    def _add_event(self, market: Market, event: dict, name_index: dict, espn_index: dict) -> None:
        comp = event["competitions"][0]
        if comp.get("status", {}).get("type", {}).get("completed"):
            return
        teams = {c["homeAway"]: c["team"] for c in comp.get("competitors", [])}
        home, away = _team(teams["home"]["abbreviation"]), _team(teams["away"]["abbreviation"])
        odds = (comp.get("odds") or [{}])[0]
        market.team_points.update(implied_points(odds.get("details"), odds.get("overUnder"), home, away))
        market.games += 1

        roster = {}
        for side in ("home", "away"):
            roster.update(self._espn_roster(teams[side]["id"], _team(teams[side]["abbreviation"])))

        eid = event["id"]
        page, pages = 1, 1
        while page <= pages:
            data = self.client.fetch_json(
                f"{CORE}/events/{eid}/competitions/{eid}/odds/{DRAFTKINGS}/propBets",
                [("limit", "1000"), ("page", str(page))], ttl=TTL_ODDS,
            )
            pages = data.get("pageCount") or 1
            for item in data.get("items", []):
                stat = PROP_STATS.get(item.get("type", {}).get("name"))
                value = (item.get("current") or {}).get("target", {}).get("value")
                ref = (item.get("athlete") or {}).get("$ref", "")
                m = _ATHLETE_ID.search(ref)
                if not stat or value is None or not m:
                    continue
                sleeper_id = espn_index.get(m.group(1))
                if sleeper_id is None and m.group(1) in roster:
                    name, team = roster[m.group(1)]
                    sleeper_id = name_index.get((name, team))
                if sleeper_id:
                    market.lines.setdefault(sleeper_id, {})[stat] = float(value)
            page += 1

    def _espn_roster(self, team_id: str, team_abbr: str) -> dict[str, tuple[str, str]]:
        try:
            data = self.client.fetch_json(f"{SITE}/teams/{team_id}/roster", ttl=TTL_ROSTER)
        except Exception:
            return {}
        out = {}
        for group in data.get("athletes", []):
            for a in group.get("items", []):
                out[str(a["id"])] = (normalize(a.get("fullName") or a.get("displayName") or ""), team_abbr)
        return out

    def _sleeper_espn_index(self) -> dict[str, str]:
        return {str(v["espn_id"]): pid for pid, v in self.client.players().items() if v.get("espn_id")}

    def _sleeper_name_index(self) -> dict[tuple[str, str], str]:
        index = {}
        for pid, v in self.client.players().items():
            if v.get("team") and set(v.get("fantasy_positions") or [v.get("position")]) & {"QB", "RB", "WR", "TE", "K"}:
                name = v.get("full_name") or f"{v.get('first_name', '')} {v.get('last_name', '')}"
                index.setdefault((normalize(name), v["team"]), pid)
        return index
