"""Thin client for the Sleeper API with on-disk caching.

Docs: https://docs.sleeper.com/  (read-only, no auth).
Projections come from the undocumented ``/projections`` endpoint the Sleeper app uses.
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
    pass


class SleeperClient:
    def __init__(self, cache_dir: Path | None = None, http: httpx.Client | None = None):
        self.cache_dir = cache_dir
        self.http = http or httpx.Client(base_url=API, timeout=30.0)
        # In-process memo so long-running servers don't re-read large cache files.
        self._mem: dict[str, tuple[float, Any]] = {}

    # --- low level -----------------------------------------------------------------

    def _get(self, path: str, params: list[tuple[str, str]] | None = None, ttl: int = TTL_DEFAULT) -> Any:
        key = path + "?" + "&".join(f"{k}={v}" for k, v in (params or []))
        hit = self._mem.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]

        cache_file = self._cache_file(path, params)
        if cache_file and cache_file.exists() and time.time() - cache_file.stat().st_mtime < ttl:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            self._mem[key] = (cache_file.stat().st_mtime, data)
            return data

        resp = self.http.get(path, params=params)
        if resp.status_code != 200:
            raise SleeperError(f"GET {path} failed: HTTP {resp.status_code}")
        data = resp.json()

        if cache_file:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(data), encoding="utf-8")
        self._mem[key] = (time.time(), data)
        return data

    def _cache_file(self, path: str, params: list[tuple[str, str]] | None) -> Path | None:
        if self.cache_dir is None:
            return None
        key = path + "?" + "&".join(f"{k}={v}" for k, v in (params or []))
        return self.cache_dir / (hashlib.sha1(key.encode()).hexdigest() + ".json")

    # --- endpoints -----------------------------------------------------------------

    def state(self) -> dict:
        return self._get("/v1/state/nfl")

    def user(self, username_or_id: str) -> dict:
        data = self._get(f"/v1/user/{username_or_id}")
        if not data:
            raise SleeperError(f"Sleeper user '{username_or_id}' not found")
        return data

    def user_leagues(self, user_id: str, season: str) -> list[dict]:
        return self._get(f"/v1/user/{user_id}/leagues/nfl/{season}") or []

    def league(self, league_id: str) -> dict:
        data = self._get(f"/v1/league/{league_id}")
        if not data:
            raise SleeperError(f"League '{league_id}' not found")
        return data

    def rosters(self, league_id: str) -> list[dict]:
        return self._get(f"/v1/league/{league_id}/rosters", ttl=60)

    def league_users(self, league_id: str) -> list[dict]:
        return self._get(f"/v1/league/{league_id}/users")

    def players(self) -> dict[str, dict]:
        """All NFL players keyed by player_id (~5MB, cached for a day)."""
        return self._get("/v1/players/nfl", ttl=TTL_PLAYERS)

    def projections(self, season: str, week: int, positions: list[str]) -> list[dict]:
        params = [("season_type", "regular")] + [("position[]", p) for p in sorted(positions)]
        return self._get(f"/projections/nfl/{season}/{week}", params=params, ttl=TTL_PROJECTIONS) or []
