"""Web app backend (FastAPI).

Serves the single-page UI (``static/index.html``) and the small JSON API it calls:

- ``/api/state``: current season and week
- ``/api/user/{username}/leagues``: sign-in step, lists your leagues
- ``/api/lineup``, ``/api/matchup``, ``/api/waivers``: league features
- ``/api/players/search``, ``/api/players/compare``: player lookup (no sign-in)

The ``*_json`` helpers turn internal objects into the shapes the page expects. On startup
a background thread pre-loads history and betting lines so the first page is fast.
Interactive API docs are at ``/api/docs``.
"""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse

from fantasycoach.players import lookup
from fantasycoach.league.advice import lineup_advice, waiver_picks
from fantasycoach.league.matchup import matchup as build_matchup
from fantasycoach.players.compare import compare_players
from fantasycoach.core.config import cache_dir
from fantasycoach.core.models import Player, display_name, fantasy_position
from fantasycoach.sources.espn import STAT_LABELS
from fantasycoach.projections.engine import engine_for
from fantasycoach.sources.sleeper import SleeperClient, SleeperError, current_season_week

STATIC = Path(__file__).parent / "static"
client = SleeperClient(cache_dir=cache_dir())


def _warm() -> None:
    try:
        engine_for(client).warm(*current_season_week(client))
    except Exception:
        pass  # warm-up is best effort; requests load the same data on demand


@asynccontextmanager
async def _lifespan(_: FastAPI):
    # Load history and betting lines in the background so the first page isn't slow.
    threading.Thread(target=_warm, daemon=True).start()
    yield


app = FastAPI(title="FantasyCoach", docs_url="/api/docs", redoc_url=None, lifespan=_lifespan)


@app.exception_handler(SleeperError)
async def _sleeper_error(_: Request, exc: SleeperError) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=400)


def player_image(player_id: str, position: str | None, team: str | None) -> str:
    """Sleeper CDN headshot, or the team logo for a defense."""
    if position == "DEF":
        return f"https://sleepercdn.com/images/team_logos/nfl/{(team or player_id).lower()}.png"
    return f"https://sleepercdn.com/content/nfl/players/thumb/{player_id}.jpg"


def player_json(p: Player | None) -> dict | None:
    """JSON for a player card: identity, matchup, projection, photo and range."""
    if p is None:
        return None
    return {
        "id": p.player_id,
        "name": p.name,
        "position": p.position,
        "team": p.team,
        "opponent": p.opponent,
        "injury_status": p.injury_status,
        "projected": p.projected,
        "image": player_image(p.player_id, p.position, p.team),
        "range": range_json(p.range),
    }


def range_json(r) -> dict | None:
    """JSON for a player's outcome range, naming the stats that came from betting lines."""
    if r is None:
        return None
    return {
        "expected": r.expected,
        "floor": r.floor,
        "ceiling": r.ceiling,
        "lines": [STAT_LABELS.get(s, s) for s in r.market_stats],
        "team_points": r.team_points,
        "market": r.uses_market,
    }


def team_range_json(r) -> dict | None:
    """JSON for a team's expected score, floor and ceiling."""
    return None if r is None else {"expected": r.expected, "floor": r.floor, "ceiling": r.ceiling}


def market_json(week: int) -> dict | None:
    """Coverage of live betting lines for the current week (None for other weeks)."""
    season, current_week = current_season_week(client)
    if week != current_week:
        return None
    m = engine_for(client).current_market(season, week)
    return {"games": m.games, "players": len(m.lines), "error": m.error}


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    """Serve the web app."""
    return FileResponse(STATIC / "index.html")


@app.get("/api/state")
def state() -> dict:
    """Current NFL season and week."""
    season, week = current_season_week(client)
    return {"season": season, "week": week}


@app.get("/api/user/{username}/leagues")
def user_leagues(username: str) -> dict:
    """Look up a Sleeper user and list their leagues for the current season."""
    user = client.user(username)
    season, _ = current_season_week(client)
    leagues = client.user_leagues(user["user_id"], season)
    return {
        "user_id": user["user_id"],
        "display_name": user.get("display_name") or username,
        "season": season,
        "leagues": [
            {"league_id": lg["league_id"], "name": lg.get("name"), "teams": lg.get("total_rosters")}
            for lg in leagues
        ],
    }


@app.get("/api/lineup")
def lineup(league_id: str, user_id: str, week: int | None = Query(None, ge=1, le=18)) -> dict:
    """Start/sit advice for your team, with player and team ranges."""
    ctx, advice = lineup_advice(client, league_id, user_id, week)
    starting = {p.player_id for p in advice.to_start}
    return {
        "league": ctx.league.get("name"),
        "week": ctx.week,
        "slots": [
            {"slot": s.slot, "player": player_json(s.player), "new": bool(s.player and s.player.player_id in starting)}
            for s in advice.recommended
        ],
        "bench": [player_json(p) for p in sorted(advice.bench, key=lambda p: p.projected, reverse=True)],
        "to_start": [player_json(p) for p in advice.to_start],
        "to_bench": [player_json(p) for p in advice.to_bench],
        "current_total": advice.current_total,
        "recommended_total": advice.recommended_total,
        "gain": advice.gain,
        "team_range": team_range_json(advice.recommended_range),
        "current_range": team_range_json(advice.current_range),
        "market": market_json(ctx.week),
    }


@app.get("/api/waivers")
def waivers(
    league_id: str,
    user_id: str,
    week: int | None = Query(None, ge=1, le=18),
    weeks: int = Query(3, ge=1, le=8),
    position: str | None = None,
    limit: int = Query(15, ge=1, le=50),
) -> dict:
    """Waiver-wire targets for your team."""
    ctx, picks = waiver_picks(client, league_id, user_id, week, weeks, position or None, limit)
    return {
        "league": ctx.league.get("name"),
        "week": ctx.week,
        "weeks": weeks,
        "positions": ctx.positions,
        "picks": [
            {
                "add": player_json(pk.add),
                "add_value": pk.add_value,
                "this_week": pk.this_week,
                "drop": player_json(pk.drop),
                "drop_value": pk.drop_value,
                "gain": pk.gain,
            }
            for pk in picks
        ],
    }


@app.get("/api/players/search")
def player_search(q: str = Query(..., min_length=2, max_length=50), limit: int = Query(8, ge=1, le=20)) -> dict:
    """Search players and team defenses by name (no sign-in needed)."""
    results = lookup.search(client.players(), q, limit)
    return {
        "results": [
            {
                "id": pid,
                "name": display_name(info),
                "position": fantasy_position(info),
                "team": info.get("team"),
                "injury_status": info.get("injury_status"),
                "image": player_image(pid, fantasy_position(info), info.get("team")),
            }
            for pid, info in results
        ]
    }


@app.get("/api/players/compare")
def player_compare(
    ids: str = Query(..., description="Comma-separated Sleeper player ids"),
    scoring: str | None = Query(None, pattern="^(league|default|half|std)$"),
    league_id: str | None = None,
    week: int | None = Query(None, ge=1, le=18),
    weeks: int = Query(4, ge=1, le=8),
) -> dict:
    """Week-by-week projections and ranges for up to 8 players."""
    id_list = [i for i in dict.fromkeys(x.strip() for x in ids.split(",")) if i][:8]
    label, start, outs = compare_players(client, id_list, scoring, league_id, week, weeks)
    return {
        "scoring": label,
        "week": start,
        "market": market_json(start),
        "players": [
            {
                "id": o.player_id,
                "name": o.name,
                "position": o.position,
                "team": o.team,
                "injury_status": o.injury_status,
                "image": player_image(o.player_id, o.position, o.team),
                "total": o.total,
                "average": o.average,
                "this_week": o.this_week,
                "key_stats": [{"key": k, "label": lbl} for k, lbl in lookup.KEY_STATS.get(o.position, [])],
                "weeks": [
                    {"week": w.week, "opponent": w.opponent, "points": w.points,
                     "stats": w.stats, "bye": w.bye, "projected": w.projected, "range": range_json(w.range)}
                    for w in o.weeks
                ],
            }
            for o in outs
        ],
    }


def _view_json(view) -> dict:
    return {
        "projected": view.projected,
        "range": team_range_json(view.range),
        "slots": [{"slot": s.slot, "player": player_json(s.player)} for s in view.slots],
    }


def _side_json(side) -> dict | None:
    if side is None:
        return None
    return {
        "team_name": side.team_name,
        "owner": side.owner,
        "avatar": side.avatar,
        "views": {"current": _view_json(side.current), "optimal": _view_json(side.optimal)},
    }


@app.get("/api/matchup")
def matchup(league_id: str, user_id: str, week: int | None = Query(None, ge=1, le=18)) -> dict:
    """Both teams' lineups (current and optimal) with ranges and win probabilities."""
    m = build_matchup(client, league_id, user_id, week)
    return {
        "league": m.league,
        "week": m.week,
        "me": _side_json(m.me),
        "opponent": _side_json(m.opponent),
        # "my_view|their_view" -> chance you win, e.g. "optimal|current".
        "win_probabilities": {f"{a}|{b}": p for (a, b), p in (m.win_probabilities or {}).items()},
        "market": market_json(m.week),
    }
