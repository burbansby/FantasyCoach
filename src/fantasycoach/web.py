"""Web interface: a small FastAPI app serving JSON advice plus a single-page UI."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse

from . import service
from .config import cache_dir
from .models import Player
from .sleeper import SleeperClient, SleeperError

STATIC = Path(__file__).parent / "static"

app = FastAPI(title="FantasyCoach", docs_url="/api/docs", redoc_url=None)
client = SleeperClient(cache_dir=cache_dir())


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
        "adjusted": p.adjusted,
        "image": image,
    }


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
        "bench": [player_json(p) for p in sorted(advice.bench, key=lambda p: p.adjusted, reverse=True)],
        "to_start": [player_json(p) for p in advice.to_start],
        "to_bench": [player_json(p) for p in advice.to_bench],
        "current_total": advice.current_total,
        "recommended_total": advice.recommended_total,
        "gain": advice.gain,
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
