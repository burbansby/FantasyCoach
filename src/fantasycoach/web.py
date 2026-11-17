"""Web interface: a small FastAPI app serving JSON advice plus a single-page UI."""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse

from . import players as players_mod
from . import service
from .config import cache_dir
from .models import Player
from .market import STAT_LABELS
from .range_engine import engine_for
from .sleeper import SleeperClient, SleeperError

STATIC = Path(__file__).parent / "static"
client = SleeperClient(cache_dir=cache_dir())


def _warm() -> None:
    try:
        s = client.state()
        engine_for(client).warm(str(s.get("league_season") or s.get("season")), int(s.get("week") or 1))
    except Exception:
        pass


@asynccontextmanager
async def _lifespan(_: FastAPI):
    # Load history and betting lines in the background so the first page isn't slow.
    threading.Thread(target=_warm, daemon=True).start()
    yield


app = FastAPI(title="FantasyCoach", docs_url="/api/docs", redoc_url=None, lifespan=_lifespan)


@app.exception_handler(SleeperError)
async def _sleeper_error(_: Request, exc: SleeperError) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=400)


def player_json(p: Player | None) -> dict | None:
    if p is None:
        return None
    if p.position == "DEF":
        image = f"https://sleepercdn.com/images/team_logos/nfl/{(p.team or p.player_id).lower()}.png"
    else:
        image = f"https://sleepercdn.com/content/nfl/players/thumb/{p.player_id}.jpg"
    return {
        "id": p.player_id,
        "name": p.name,
        "position": p.position,
        "team": p.team,
        "opponent": p.opponent,
        "injury_status": p.injury_status,
        "projected": p.projected,
        "image": image,
        "range": range_json(p.range),
    }


def range_json(r) -> dict | None:
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
    return None if r is None else {"expected": r.expected, "floor": r.floor, "ceiling": r.ceiling}


def market_json(week: int) -> dict | None:
    """Coverage of live betting lines for the current week (None for other weeks)."""
    s = client.state()
    if week != int(s.get("week") or 0):
        return None
    m = engine_for(client).current_market(str(s.get("league_season") or s.get("season")), week)
    return {"games": m.games, "players": len(m.lines), "error": m.error}


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/state")
def state() -> dict:
    s = client.state()
    return {"season": s.get("league_season") or s.get("season"), "week": s.get("week")}


@app.get("/api/user/{username}/leagues")
def user_leagues(username: str) -> dict:
    user = client.user(username)
    season = client.state()["league_season"]
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
    ctx, advice = service.lineup_advice(client, league_id, user_id, week)
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
    ctx, picks = service.waiver_picks(client, league_id, user_id, week, weeks, position or None, limit)
    return {
        "league": ctx.league.get("name"),
        "week": ctx.week,
        "weeks": weeks,
        "positions": [p for p in ctx.positions],
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


def _image(pid: str, position: str, team: str | None) -> str:
    return player_json(Player(pid, "", position, team))["image"]


@app.get("/api/players/search")
def player_search(q: str = Query(..., min_length=2, max_length=50), limit: int = Query(8, ge=1, le=20)) -> dict:
    results = players_mod.search(client.players(), q, limit)
    return {
        "results": [
            {
                "id": pid,
                "name": players_mod.display_name(info),
                "position": info.get("position"),
                "team": info.get("team"),
                "injury_status": info.get("injury_status"),
                "image": _image(pid, info.get("position"), info.get("team")),
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
    id_list = [i for i in dict.fromkeys(x.strip() for x in ids.split(",")) if i][:8]
    label, start, outs = service.compare_players(client, id_list, scoring, league_id, week, weeks)
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
                "image": _image(o.player_id, o.position, o.team),
                "total": o.total,
                "average": o.average,
                "this_week": o.this_week,
                "key_stats": [{"key": k, "label": lbl} for k, lbl in players_mod.KEY_STATS.get(o.position, [])],
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
        # Lineup as set (top-level, for simple clients) plus both views for the toggle.
        **_view_json(side.current),
        "views": {"current": _view_json(side.current), "optimal": _view_json(side.optimal)},
    }


@app.get("/api/matchup")
def matchup(league_id: str, user_id: str, week: int | None = Query(None, ge=1, le=18)) -> dict:
    m = service.matchup(client, league_id, user_id, week)
    return {
        "league": m.league,
        "week": m.week,
        "me": _side_json(m.me),
        "opponent": _side_json(m.opponent),
        "win_probability": m.win_probability,
        # "my_view|their_view" -> chance you win, e.g. "optimal|current".
        "win_probabilities": {f"{a}|{b}": p for (a, b), p in (m.win_probabilities or {}).items()},
        "market": market_json(m.week),
    }
