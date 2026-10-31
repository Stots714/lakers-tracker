"""Pull NBA.com stats for the tracker.

Run this on your own PC. NBA.com's stats site often refuses or times out requests from cloud
servers, so the tracker cannot fetch these itself. Plain Python plus requests: no pandas or numpy,
whose unsigned compiled files Windows Smart App Control can block.

League-wide, for all 30 teams (see league.py): league_results.csv, league_box.csv,
league_player_games.csv, league_quarters.csv, league_inactive.csv. The site reads these.

For the chosen teams it also writes these CSV files to data/nba/:
  games_<TEAM>.csv            one row per game: game id, date, matchup, result, team box totals
  player_games_<TEAM>.csv     player box score per game (basic)
  player_games_adv_<TEAM>.csv player advanced stats per game (usage, ratings)
  onoff_<TEAM>.csv            team results with each player on the court and off it (advanced)
  lineups5_<TEAM>.csv         five-man lineups (advanced: ratings, minutes)
  lineups2_<TEAM>.csv         two-man pairs (advanced)
  shots_<TEAM>.csv            every shot the team took: location, zone, distance, make or miss
  shots_against_<TEAM>.csv    every shot opponents took against the team
  hustle_<TEAM>.csv           contests, deflections, loose balls, box-outs, charges, screen assists
  matchups/<GAME_ID>.csv      who guarded whom in each game (one file per game, fetched once)
  pbp/<GAME_ID>.csv           play-by-play for each game (one file per game, fetched once)
  manifest.json               when it ran, what worked, row counts

Usage (from the repository folder):
  python tools/nba/update_nba.py               Lakers and their next opponent
  python tools/nba/update_nba.py --teams LAL   Lakers only
  python tools/nba/update_nba.py --check       one small request to test the connection
  python tools/nba/update_nba.py --refresh     re-download per-game files that already exist
  python tools/nba/update_nba.py --no-league   skip the league-wide files
"""
import argparse
import csv
import datetime as dt
import json
import sys
import time
import traceback
import unicodedata
from importlib.metadata import version
from pathlib import Path

from nba_api.stats.endpoints import (
    boxscorematchupsv3,
    leaguedashlineups,
    leaguegamefinder,
    leaguehustlestatsplayer,
    playbyplayv3,
    playergamelogs,
    shotchartdetail,
    teamplayeronoffdetails,
)
from nba_api.stats.static import teams as static_teams

sys.path.insert(0, str(Path(__file__).resolve().parent))
import league  # noqa: E402  (league-wide files in the tracker's formats)
import rows  # noqa: E402  (plain-Python tables; no pandas, see rows.py)

SEASON = "2026-27"
SEASON_TYPE = "Regular Season"
PAUSE = 1.5      # seconds between requests, to stay polite
TIMEOUT = 60     # seconds per request
RETRIES = 3      # attempts per request; waits 5, 15, 45 seconds between them

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "nba"
LOG = Path(__file__).resolve().parent / "last_run.log"
status = {}
if hasattr(sys.stdout, "reconfigure"):  # Windows consoles and scheduled runs: keep names like Dončić printable
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def plain(name):
    return unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().lower()


def log(msg):
    line = f"{dt.datetime.now():%H:%M:%S} {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def fetch(cls, **kw):
    """Call one NBA.com endpoint with retries. Returns the endpoint object."""
    last = None
    for attempt in range(RETRIES):
        try:
            ep = cls(timeout=TIMEOUT, **kw)
            time.sleep(PAUSE)
            return ep
        except Exception as e:  # timeouts, connection resets, bad JSON
            last = e
            wait = 5 * 3 ** attempt
            log(f"  {cls.__name__} attempt {attempt + 1} failed: {type(e).__name__}: {str(e)[:160]}")
            if attempt < RETRIES - 1:
                time.sleep(wait)
    raise last


def save(data, name, columns=None):
    """Write rows (a list of dicts) to data/nba/<name>; returns the row count."""
    return rows.write(OUT / name, data, columns)


def save_table(t, name):
    """Save a (rows, columns) pair from table() to data/nba/<name>, keeping NBA.com's column order."""
    data, cols = t
    return save(data, name, cols)


def table(ds):
    """An nba_api data set as a list of dicts, with its column order kept for writing."""
    r, cols = rows.from_dataset(ds)
    return r, cols


def step(label, fn, unit="rows"):
    """Run one download step; record rows or the error and keep going."""
    try:
        out = fn()
        n, extra = out if isinstance(out, tuple) else (out, {})
        status[label] = {"ok": True, unit: n, **extra}
        wait = f", {extra['pending']} recent game(s) not on NBA.com yet (tried again next run)" if extra.get("pending") else ""
        log(f"OK   {label}: {n} {unit}{wait}")
    except Exception as e:
        status[label] = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:300]}"}
        log(f"FAIL {label}: {type(e).__name__}: {str(e)[:200]}")
        with LOG.open("a", encoding="utf-8") as f:
            f.write(traceback.format_exc() + "\n")


def team_id(abbr):
    t = static_teams.find_team_by_abbreviation(abbr)
    if not t:
        sys.exit(f"Unknown team abbreviation: {abbr}")
    return t["id"]


def next_opponent():
    """Next Lakers opponent from the tracker's own schedule (data/lakers_schedule.csv)."""
    sched, res = ROOT / "data" / "lakers_schedule.csv", ROOT / "data" / "results.csv"
    if not sched.exists():
        return None
    played = set()
    if res.exists():
        for r in csv.DictReader(res.open(encoding="utf-8")):
            if "LAL" in (r["away"], r["home"]) and r["home_pts"].strip():
                played.add(r["date"])
    today = dt.date.today().isoformat()
    upcoming = [r for r in csv.DictReader(sched.open(encoding="utf-8")) if r["date"] >= today and r["date"] not in played]
    return min(upcoming, key=lambda r: r["date"])["opp"] if upcoming else None


# ---------------- downloads ----------------
def games(abbr, tid):
    ep = fetch(leaguegamefinder.LeagueGameFinder, player_or_team_abbreviation="T", team_id_nullable=str(tid),
               season_nullable=SEASON, season_type_nullable=SEASON_TYPE, league_id_nullable="00")
    r, cols = table(ep.league_game_finder_results)
    r.sort(key=lambda x: str(x["GAME_DATE"]))
    save(r, f"games_{abbr}.csv", cols)
    return r


def player_games(abbr, tid, measure):
    ep = fetch(playergamelogs.PlayerGameLogs, season_nullable=SEASON, team_id_nullable=str(tid),
               season_type_nullable=SEASON_TYPE, measure_type_player_game_logs_nullable=measure)
    suffix = "" if measure == "Base" else "_adv"
    return save_table(table(ep.player_game_logs), f"player_games{suffix}_{abbr}.csv")


def onoff(abbr, tid):
    ep = fetch(teamplayeronoffdetails.TeamPlayerOnOffDetails, team_id=tid, season=SEASON,
               season_type_all_star=SEASON_TYPE, measure_type_detailed_defense="Advanced", per_mode_detailed="Totals")
    on, cols = table(ep.players_on_court_team_player_on_off_details)
    off, cols_off = table(ep.players_off_court_team_player_on_off_details)
    data = [dict(r, COURT="on") for r in on] + [dict(r, COURT="off") for r in off]
    return save(data, f"onoff_{abbr}.csv", (cols or cols_off) + ["COURT"])


def lineups(abbr, tid, size):
    ep = fetch(leaguedashlineups.LeagueDashLineups, team_id_nullable=str(tid), season=SEASON,
               season_type_all_star=SEASON_TYPE, group_quantity=str(size),
               measure_type_detailed_defense="Advanced", per_mode_detailed="Totals")
    return save_table(table(ep.lineups), f"lineups{size}_{abbr}.csv")


def shots(abbr, tid, against=False):
    # context_measure_simple="FGA" returns every attempt; the default ("PTS") returns made shots only.
    kw = dict(team_id=0, player_id=0, opponent_team_id=tid) if against else dict(team_id=tid, player_id=0)
    ep = fetch(shotchartdetail.ShotChartDetail, season_nullable=SEASON, season_type_all_star=SEASON_TYPE,
               context_measure_simple="FGA", **kw)
    return save_table(table(ep.shot_chart_detail), f"shots{'_against' if against else ''}_{abbr}.csv")


def hustle(abbr, tid):
    ep = fetch(leaguehustlestatsplayer.LeagueHustleStatsPlayer, season=SEASON, season_type_all_star=SEASON_TYPE,
               team_id_nullable=str(tid), per_mode_time="Totals")
    return save_table(table(ep.hustle_stats_player), f"hustle_{abbr}.csv")


RECENT_DAYS = 2  # NBA.com usually fills in per-game detail within hours; a gap older than this is a real failure


def settle(label, n, missed):
    """Finish a per-game step. missed: [(game_id, date, error)] for games that could not be read.

    Games from the last RECENT_DAYS days count as pending: nothing is written for them, so the next run
    asks again. An older miss fails the step, so a real problem still shows as a failure."""
    today = dt.date.today()

    def age(d):
        try:
            return (today - dt.date.fromisoformat(str(d)[:10])).days
        except ValueError:
            return None
    old = [m for m in missed if age(m[1]) is None or age(m[1]) > RECENT_DAYS]
    for gid, d, err in missed:
        log(f"  {label} {gid} ({d or 'date unknown'}): not readable yet ({err})")
    if old:
        gid, d, err = old[0]
        raise RuntimeError(f"{len(old)} game(s) older than {RECENT_DAYS} days could not be read, e.g. {gid} ({d}): {err}")
    return (n, {"pending": len(missed)}) if missed else n


def per_game(game_ids, folder, cls, attr, refresh, dates=None):
    """One file per game. A game that fails is skipped (no file), so the next run tries it again."""
    new, missed = 0, []
    for gid in game_ids:
        path = OUT / folder / f"{gid}.csv"
        if path.exists() and not refresh:
            continue
        try:
            ep = fetch(cls, game_id=gid)
            save_table(table(getattr(ep, attr)), f"{folder}/{gid}.csv")
            new += 1
        except Exception as e:  # e.g. matchups not published yet: nba_api's parser then fails on an empty list
            missed.append((gid, (dates or {}).get(gid, ""), f"{type(e).__name__}: {str(e)[:120]}"))
    return settle(folder, new, missed)


# ---------------- checks against the tracker's own data ----------------
def checks(lal_games):
    """Compare NBA.com Lakers scores with data/results.csv, and print the Oct 21 Doncic-Lendeborg matchup."""
    notes = []
    res = ROOT / "data" / "results.csv"
    if res.exists() and lal_games is not None and len(lal_games):
        ours = {}
        for r in csv.DictReader(res.open(encoding="utf-8")):
            lal_pts = r["home_pts"] if r["home"] == "LAL" else r["away_pts"]
            if "LAL" in (r["away"], r["home"]) and lal_pts.strip():
                ours[r["date"]] = int(lal_pts)
        mine = [(str(g["GAME_DATE"])[:10], int(g["PTS"])) for g in lal_games]
        bad = [(d, ours[d], p) for d, p in mine if d in ours and ours[d] != p]
        notes.append(f"Lakers scores checked against results.csv: {sum(d in ours for d, _ in mine)} games, {len(bad)} mismatches {bad if bad else ''}")
    oct21 = None
    if lal_games is not None and len(lal_games):
        hit = [g for g in lal_games if str(g["GAME_DATE"]).startswith("2026-10-21")]
        oct21 = hit[0]["GAME_ID"] if hit else None
    path = OUT / "matchups" / f"{oct21}.csv" if oct21 else None
    if path and path.exists():
        m = rows.read(path)
        name_cols = [c for c in (m[0].keys() if m else []) if c.startswith("familyName")]
        if len(name_cols) == 2:
            a, b = name_cols
            pair = {("doncic", "lendeborg"), ("lendeborg", "doncic")}
            sel = [r for r in m if (plain(r[a]), plain(r[b])) in pair]
            keep = [c for c in [a, b, "matchupMinutes", "partialPossessions", "playerPoints", "matchupFieldGoalsMade", "matchupFieldGoalsAttempted"] if c in m[0]]
            lines = ["  " + " | ".join(keep)] + ["  " + " | ".join(str(r[c]) for c in keep) for r in sel]
            notes.append("Oct 21 Doncic-Lendeborg rows (ABC7 reported Doncic 1 of 6 against Lendeborg):\n" + ("\n".join(lines) if sel else "  none found"))
    for n in notes:
        log("CHECK " + n)
    return notes


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--teams", nargs="+", help="team abbreviations, e.g. LAL GSW (default: LAL and next opponent)")
    ap.add_argument("--check", action="store_true", help="one small request to test the connection, then stop")
    ap.add_argument("--refresh", action="store_true", help="re-download per-game files that already exist")
    ap.add_argument("--skip-games", action="store_true", help="skip per-game matchups and play-by-play")
    ap.add_argument("--no-league", action="store_true", help="skip the league-wide files for all 30 teams")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    log(f"--- run started; nba_api {version('nba_api')}; season {SEASON}")

    if a.check:
        df = games("LAL", team_id("LAL"))
        log(f"Connection OK: {len(df)} Lakers games found on NBA.com (latest {max(str(g['GAME_DATE'])[:10] for g in df) if df else 'none'})")
        return 0

    league_notes = []
    if not a.no_league:
        L = {}

        def sched():
            L["sched"] = league.schedule(fetch, SEASON)
            return len(L["sched"])

        def finals(rows, key):
            """Keep finished games only, so a run during a game never stores partial box scores."""
            if "sched" not in L:
                return rows
            done = {str(g["gameId"]) for g in L["sched"] if league._int(g.get("gameStatus")) == 3}
            return [r for r in rows if str(r[key]) in done]

        def box():
            L["tg"] = finals(league.team_games(fetch, SEASON), "GAME_ID")
            n, L["box"] = league.write_box(L["tg"], save)
            return n

        def results():
            n, L["results"] = league.write_results(L["sched"], L["box"], save)
            return n

        def pgames():
            n, L["pg"] = league.write_player_games(finals(league.player_games(fetch, SEASON), "GAME_ID"), save)
            return n

        step("league schedule", sched, "games")
        step("league team box scores", box, "team-games")
        if "sched" in L and "box" in L:
            step("league results", results, "games")
        step("league player games", pgames, "player lines")
        if "results" in L:
            step("league quarter scores, inactive lists and game extras",
                 lambda: settle("summary", *league.summaries(fetch, save, OUT, L["results"], a.refresh)), "new games")
        if "box" in L:
            step("league team turnovers", lambda: league.add_team_turnovers(L["box"], save, OUT), "team-games")
        if all(k in L for k in ("results", "box", "pg")):
            league_notes = league.checks(ROOT, L["results"], L["box"], L["pg"], OUT)
            for n in league_notes:
                log("CHECK " + n)

    teams = [t.upper() for t in a.teams] if a.teams else ["LAL"] + ([next_opponent()] if next_opponent() else [])
    teams = list(dict.fromkeys(teams))
    log("Teams: " + ", ".join(teams))
    game_ids, game_dates, lal_games = set(), {}, None
    for abbr in teams:
        tid = team_id(abbr)
        holder = {}

        def g(abbr=abbr, tid=tid):
            holder["df"] = games(abbr, tid)
            return len(holder["df"])
        step(f"{abbr} games", g)
        if "df" in holder:
            game_ids |= {str(g["GAME_ID"]) for g in holder["df"]}
            game_dates.update({str(g["GAME_ID"]): str(g.get("GAME_DATE") or "")[:10] for g in holder["df"]})
            if abbr == "LAL":
                lal_games = holder["df"]
        step(f"{abbr} player games", lambda: player_games(abbr, tid, "Base"))
        step(f"{abbr} player games advanced", lambda: player_games(abbr, tid, "Advanced"))
        step(f"{abbr} on/off", lambda: onoff(abbr, tid))
        step(f"{abbr} 5-man lineups", lambda: lineups(abbr, tid, 5))
        step(f"{abbr} 2-man lineups", lambda: lineups(abbr, tid, 2))
        step(f"{abbr} shots", lambda: shots(abbr, tid))
        step(f"{abbr} shots against", lambda: shots(abbr, tid, against=True))
        step(f"{abbr} hustle", lambda: hustle(abbr, tid))

    if not a.skip_games and game_ids:
        ids = sorted(game_ids)
        step("matchups", lambda: per_game(ids, "matchups", boxscorematchupsv3.BoxScoreMatchupsV3, "player_stats", a.refresh, game_dates), "new games")
        step("play-by-play", lambda: per_game(ids, "pbp", playbyplayv3.PlayByPlayV3, "play_by_play", a.refresh, game_dates), "new games")

    notes = league_notes + checks(lal_games)
    ok = sum(s["ok"] for s in status.values())
    files = {"nba_results": "league_results.csv", "nba_box": "league_box.csv", "nba_player_games": "league_player_games.csv",
             "nba_quarters": "league_quarters.csv", "nba_inactive": "league_inactive.csv", "nba_extras": "league_extras.csv"}
    manifest = {
        "updated": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "season": SEASON, "teams": teams, "nba_api": version("nba_api"),
        "league": {k: f for k, f in files.items() if (OUT / f).exists()},
        "steps": status, "checks": notes,
        "source": "NBA.com stats via the nba_api Python package (github.com/swar/nba_api)",
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
    log(f"--- done: {ok} of {len(status)} steps worked")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
