"""Client for the Sleeper API, with caching.

Covers users, leagues, rosters, weekly matchups, the full player list, weekly projections
and weekly actual stats. Docs: https://docs.sleeper.com/ (read-only, no auth). Projections
and stats come from undocumented endpoints that the Sleeper app itself uses.

Responses are cached in memory and on disk (``~/.fantasycoach/cache``) with per-endpoint
lifetimes, so repeated page loads don't hit the network. ``current_season_week`` is the one
place the app asks which season and week it is.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import httpx

API = "https://api.sleeper.app"

# Cache lifetimes in seconds.
TTL_PLAYERS = 24 * 3600
TTL_PROJECTIONS = 3600
TTL_DEFAULT = 300


class SleeperError(RuntimeError):
    """Raised when Sleeper returns an error or a user or league doesn't exist."""
    pass


def current_season_week(client: "SleeperClient") -> tuple[str, int]:
    """The NFL season (e.g. "2026") and week Sleeper considers current."""
    s = client.state()
    return str(s.get("league_season") or s.get("season")), int(s.get("week") or 1)


class SleeperClient:
    """Read-only Sleeper API client. ``cache_dir=None`` turns off the disk cache."""
    def __init__(self, cache_dir: Path | None = None, http: httpx.Client | None = None):
        self.cache_dir = cache_dir
        self.http = http or httpx.Client(base_url=API, timeout=30.0)
        # In-process memo so long-running servers don't re-read large cache files.
        self._mem: dict[str, tuple[float, Any]] = {}

    # --- low level -----------------------------------------------------------------

    def _get(
        self,
        path: str,
        params: list[tuple[str, str]] | None = None,
        ttl: int = TTL_DEFAULT,
        persist: bool = True,
        memo: bool = True,
    ) -> Any:
        """GET JSON with an in-memory memo and on-disk cache.

        ``path`` may be relative to the Sleeper API or an absolute URL. Set ``persist``/``memo``
        to False for large one-off downloads that callers condense and cache themselves.
        """
        key = path + "?" + "&".join(f"{k}={v}" for k, v in (params or []))
        hit = self._mem.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]

        cache_file = self._cache_file(path, params) if persist else None
        if cache_file and cache_file.exists() and time.time() - cache_file.stat().st_mtime < ttl:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            if memo:
                self._mem[key] = (cache_file.stat().st_mtime, data)
            return data

        resp = self.http.get(path, params=params)
        if resp.status_code != 200:
            raise SleeperError(f"GET {path} failed: HTTP {resp.status_code}")
        data = resp.json()

        if cache_file:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(data), encoding="utf-8")
        if memo:
            self._mem[key] = (time.time(), data)
        return data

    def _cache_file(self, path: str, params: list[tuple[str, str]] | None) -> Path | None:
        if self.cache_dir is None:
            return None
        key = path + "?" + "&".join(f"{k}={v}" for k, v in (params or []))
        return self.cache_dir / (hashlib.sha1(key.encode()).hexdigest() + ".json")

    # --- endpoints -----------------------------------------------------------------

    def state(self) -> dict:
        """Sleeper's view of the NFL calendar (use ``current_season_week`` instead)."""
        return self._get("/v1/state/nfl")

    def user(self, username_or_id: str) -> dict:
        """Look up a Sleeper user by username or user id."""
        data = self._get(f"/v1/user/{username_or_id}")
        if not data:
            raise SleeperError(f"Sleeper user '{username_or_id}' not found")
        return data

    def user_leagues(self, user_id: str, season: str) -> list[dict]:
        """All of a user's NFL leagues for a season."""
        return self._get(f"/v1/user/{user_id}/leagues/nfl/{season}") or []

    def league(self, league_id: str) -> dict:
        """A league's settings: name, roster positions and scoring settings."""
        data = self._get(f"/v1/league/{league_id}")
        if not data:
            raise SleeperError(f"League '{league_id}' not found")
        return data

    def rosters(self, league_id: str) -> list[dict]:
        """Every roster in a league: owner, players, starters, IR (reserve) and taxi."""
        return self._get(f"/v1/league/{league_id}/rosters", ttl=60)

    def league_users(self, league_id: str) -> list[dict]:
        """League members with display names, team names and avatars."""
        return self._get(f"/v1/league/{league_id}/users")

    def matchups(self, league_id: str, week: int) -> list[dict]:
        """Each roster's matchup for a week: roster_id, matchup_id (shared by opponents), starters."""
        return self._get(f"/v1/league/{league_id}/matchups/{week}", ttl=60) or []

    def players(self) -> dict[str, dict]:
        """All NFL players keyed by player_id (~5MB, cached for a day)."""
        return self._get("/v1/players/nfl", ttl=TTL_PLAYERS)

    def fetch_json(self, url: str, params: list[tuple[str, str]] | None = None, ttl: int = TTL_DEFAULT) -> Any:
        """Cached GET of any absolute JSON URL (used for ESPN odds)."""
        return self._get(url, params=params, ttl=ttl)

    def stats(
        self, season: str, week: int, positions: list[str] | None = None, persist: bool = True, memo: bool = True
    ) -> list[dict]:
        """Actual weekly stat lines (same stat keys as projections), optionally filtered by position."""
        params = [("season_type", "regular")] + [("position[]", p) for p in sorted(positions or [])]
        return self._get(f"/stats/nfl/{season}/{week}", params=params, ttl=TTL_PROJECTIONS,
                         persist=persist, memo=memo) or []

    def projections(
        self, season: str, week: int, positions: list[str], persist: bool = True, memo: bool = True
    ) -> list[dict]:
        """Projected stat lines for a week, one row per player (cached for an hour)."""
        params = [("season_type", "regular")] + [("position[]", p) for p in sorted(positions)]
        return self._get(f"/projections/nfl/{season}/{week}", params=params, ttl=TTL_PROJECTIONS,
                         persist=persist, memo=memo) or []
