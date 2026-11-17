"""Command line interface: `fantasycoach setup | leagues | lineup | waivers`."""

from __future__ import annotations

from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from . import service
from .config import Config, cache_dir
from .models import Player
from .sleeper import SleeperClient, SleeperError

app = typer.Typer(help="Fantasy football advice for your Sleeper league.", no_args_is_help=True)
console = Console()


def _client() -> SleeperClient:
    return SleeperClient(cache_dir=cache_dir())


def _require_config(league: Optional[str]) -> tuple[Config, str]:
    cfg = Config.load()
    league_id = league or cfg.league_id
    if not cfg.user_id or not league_id:
        console.print("[red]Not set up yet.[/] Run [bold]fantasycoach setup --username <sleeper name>[/] first.")
        raise typer.Exit(1)
    return cfg, league_id


def _status(p: Player) -> str:
    if not p.injury_status:
        return ""
    color = "yellow" if p.injury_status == "Questionable" else "red"
    return f"[{color}]{p.injury_status}[/]"


def _opp(p: Player) -> str:
    return f"vs {p.opponent}" if p.opponent else "[dim]BYE/—[/]"


@app.command()
def setup(
    username: str = typer.Option(..., "--username", "-u", help="Your Sleeper username"),
    league: Optional[str] = typer.Option(None, "--league", "-l", help="League ID (skip the picker)"),
):
    """Link your Sleeper account and choose a default league."""
    client = _client()
    try:
        user = client.user(username)
        season = client.state()["league_season"]
        leagues = client.user_leagues(user["user_id"], season)
    except SleeperError as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(1)

    if league is None:
        if not leagues:
            console.print(f"[red]No {season} leagues found for {username}.[/]")
            raise typer.Exit(1)
        for i, lg in enumerate(leagues, 1):
            console.print(f"  [bold]{i}[/]. {lg['name']}  [dim]({lg['league_id']})[/]")
        choice = 1 if len(leagues) == 1 else typer.prompt("Pick a league", type=int, default=1)
        league = leagues[choice - 1]["league_id"]

    path = Config(username=username, user_id=user["user_id"], league_id=league).save()
    console.print(f"[green]Saved[/] to {path}")


@app.command()
def leagues():
    """List your leagues for the current season."""
    cfg = Config.load()
    if not cfg.user_id:
        console.print("[red]Run setup first.[/]")
        raise typer.Exit(1)
    client = _client()
    season = client.state()["league_season"]
    for lg in client.user_leagues(cfg.user_id, season):
        marker = "*" if lg["league_id"] == cfg.league_id else " "
        console.print(f"{marker} {lg['name']}  [dim]({lg['league_id']})[/]")


@app.command()
def lineup(
    week: Optional[int] = typer.Option(None, "--week", "-w", help="NFL week (default: current)"),
    league: Optional[str] = typer.Option(None, "--league", "-l"),
):
    """Start/sit: recommend the best lineup for the week."""
    cfg, league_id = _require_config(league)
    ctx, advice = service.lineup_advice(_client(), league_id, cfg.user_id, week)

    table = Table(title=f"{ctx.league['name']} — Week {ctx.week} recommended lineup")
    for col in ("Slot", "Player", "Matchup", "Status", "Proj"):
        table.add_column(col, justify="right" if col == "Proj" else "left")
    for s in advice.recommended:
        p = s.player
        if p is None:
            table.add_row(s.slot, "[red]EMPTY[/]", "", "", "")
            continue
        new = " [green]▲[/]" if p in advice.to_start else ""
        table.add_row(s.slot, p.label + new, _opp(p), _status(p), f"{p.adjusted:.1f}")
    table.add_section()
    for p in sorted(advice.bench, key=lambda p: p.adjusted, reverse=True):
        table.add_row("BN", f"[dim]{p.label}[/]", _opp(p), _status(p), f"[dim]{p.adjusted:.1f}[/]")
    console.print(table)

    if advice.to_start:
        console.print("[bold]Changes:[/]")
        for p in advice.to_start:
            console.print(f"  [green]Start[/] {p.label}  {p.adjusted:.1f}")
        for p in advice.to_bench:
            console.print(f"  [red]Bench[/] {p.label}  {p.adjusted:.1f}")
        console.print(
            f"Projected: {advice.current_total:.1f} → [bold]{advice.recommended_total:.1f}[/] "
            f"([green]+{advice.gain:.1f}[/])"
        )
    else:
        console.print(f"[green]Your current lineup is optimal.[/] Projected {advice.recommended_total:.1f}")


@app.command()
def waivers(
    week: Optional[int] = typer.Option(None, "--week", "-w", help="First week to evaluate (default: current)"),
    weeks: int = typer.Option(3, "--weeks", "-n", min=1, max=8, help="How many weeks ahead to value players"),
    position: Optional[str] = typer.Option(None, "--position", "-p", help="Only this position (QB, RB, WR, ...)"),
    limit: int = typer.Option(15, "--limit"),
    league: Optional[str] = typer.Option(None, "--league", "-l"),
):
    """Waiver wire: free agents ranked by upgrade over your weakest player."""
    cfg, league_id = _require_config(league)
    ctx, picks = service.waiver_picks(_client(), league_id, cfg.user_id, week, weeks, position, limit)

    span = f"weeks {ctx.week}-{ctx.week + weeks - 1}" if weeks > 1 else f"week {ctx.week}"
    table = Table(title=f"{ctx.league['name']} — waiver targets ({span})")
    for col in ("#", "Add", "Status", f"Proj ({weeks}w)", "This wk", "Drop", "Drop proj", "Gain"):
        table.add_column(col, justify="right" if col not in ("Add", "Drop", "Status") else "left")
    for i, pk in enumerate(picks, 1):
        drop = f"{pk.drop.label} {_status(pk.drop)}" if pk.drop else "[dim](open spot)[/]"
        table.add_row(
            str(i), pk.add.label, _status(pk.add), f"{pk.add_value:.1f}", f"{pk.this_week:.1f}",
            drop, f"{pk.drop_value:.1f}", f"[green]+{pk.gain:.1f}[/]",
        )
    if picks:
        console.print(table)
    else:
        console.print("[green]No free agent projects better than your current players.[/]")


@app.command()
def web(
    host: str = typer.Option("127.0.0.1", help="Interface to bind (use 0.0.0.0 to allow other devices)"),
    port: int = typer.Option(8000, help="Port to serve on"),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="Open a browser tab"),
):
    """Start the web interface."""
    import threading
    import webbrowser

    import uvicorn

    url = f"http://{'localhost' if host in ('127.0.0.1', '0.0.0.0') else host}:{port}"
    console.print(f"FantasyCoach running at [bold]{url}[/]  (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    uvicorn.run("fantasycoach.web:app", host=host, port=port, log_level="warning")


def main() -> None:
    try:
        app()
    except SleeperError as e:
        console.print(f"[red]{e}[/]")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
