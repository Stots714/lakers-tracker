"""Live game watcher: NBA.com's live feed (cdn.nba.com) to data/nba/live.json, pushed to the site during games.

What it does on a game day:
  1. Reads today's NBA.com scoreboard. No games: exits.
  2. Waits until the first tip-off (re-checking every 30 minutes).
  3. While any game is on, reads each started game's live box score, writes data/nba/live.json and
     pushes it every 7 minutes (GitHub Pages allows about 10 site updates an hour).
  4. When every game is final, pushes the finals and exits with code 0 (run_live.ps1 then runs the
     full data update so the tracker's ratings include tonight's games).

live.json, per game: status (1 scheduled, 2 live, 3 final), period and clock, score by period, team stats
so far (shooting, rebounds, turnovers, fouls, paint, second-chance, fast-break points), top scorers, and a
margin history (one point per check) for the line on the site.

Usage (run_live.ps1 calls this):
  python tools/nba/live.py            watch until every game today is final
  python tools/nba/live.py --once     one check now, write live.json, push, exit (code 3 if games are still to finish)
Exit codes: 0 every game today is final; 3 games still to finish (--once); 4 no games today.
  python tools/nba/live.py --no-push  write the file only
Plain Python plus requests (see rows.py for why there is no pandas).
"""
import argparse
import datetime as dt
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from nba_api.live.nba.endpoints import boxscore, scoreboard

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "nba" / "live.json"
LOG = Path(__file__).resolve().parent / "last_live.log"
PUSH_EVERY = 7 * 60       # seconds between pushes while games are on
CHECK_EVERY = 7 * 60      # seconds between live checks
WAIT_STEP = 30 * 60       # longest single wait before tip-off, so a schedule change is noticed
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

STATS = {  # live box score team statistics -> live.json names
    "fieldGoalsMade": "fgm", "fieldGoalsAttempted": "fga", "threePointersMade": "fg3m", "threePointersAttempted": "fg3a",
    "freeThrowsMade": "ftm", "freeThrowsAttempted": "fta", "reboundsOffensive": "oreb", "reboundsDefensive": "dreb",
    "turnoversTotal": "tov", "foulsPersonal": "pf", "assists": "ast", "steals": "stl", "blocks": "blk",
    "pointsInThePaint": "pts_paint", "pointsSecondChance": "pts_second_chance", "pointsFastBreak": "pts_fast_break",
    "pointsFromTurnovers": "pts_off_tov", "benchPoints": "pts_bench", "biggestLead": "biggest_lead", "leadChanges": "lead_changes",
}


def log(msg):
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def clock_minutes(clock):
    """'PT05M23.00S' -> 5.38 minutes left in the period; '' -> None."""
    m = re.match(r"PT(\d+)M([\d.]+)S", clock or "")
    return int(m.group(1)) + float(m.group(2)) / 60 if m else None


def elapsed(period, clock):
    """Game minutes played: 12-minute quarters, 5-minute overtimes."""
    left = clock_minutes(clock)
    if not period:
        return 0.0
    if period <= 4:
        return (period - 1) * 12 + (12 - (left if left is not None else 12))
    return 48 + (period - 5) * 5 + (5 - (left if left is not None else 5))


def clock_text(clock):
    left = clock_minutes(clock)
    if left is None:
        return ""
    s = int(round(left * 60))
    return f"{s // 60}:{s % 60:02d}"


def team_block(t, with_stats):
    out = {"team": t.get("teamTricode"), "score": t.get("score") or 0,
           "periods": [p.get("score") for p in t.get("periods", []) if p.get("score") is not None]}
    if with_stats:
        st = t.get("statistics", {}) or {}
        out["stats"] = {v: st.get(k) for k, v in STATS.items()}
        players = []
        for p in t.get("players", []) or []:
            s = p.get("statistics", {}) or {}
            if p.get("played") == "1":
                players.append({"name": p.get("name") or f'{p.get("firstName", "")} {p.get("familyName", "")}'.strip(),
                                "pts": s.get("points", 0), "reb": s.get("reboundsTotal", 0), "ast": s.get("assists", 0),
                                "min": clock_text(s.get("minutes")) or ""})
        out["leaders"] = sorted(players, key=lambda x: -(x["pts"] or 0))[:3]
    return out


def read_previous():
    try:
        return {g["game_id"]: g for g in json.loads(OUT.read_text(encoding="utf-8")).get("games", [])}
    except (OSError, ValueError):
        return {}


def check(board, prev):
    """One pass over today's games. Returns the games list for live.json."""
    now = dt.datetime.now(dt.timezone.utc)
    games = []
    for g in board:
        gid = g["gameId"]
        tip = dt.datetime.fromisoformat(g["gameTimeUTC"].replace("Z", "+00:00"))
        rec = {"game_id": gid, "tip_utc": g["gameTimeUTC"], "status": g.get("gameStatus", 1), "status_text": g.get("gameStatusText", ""),
               "period": g.get("period", 0), "clock": clock_text(g.get("gameClock")),
               "home": team_block(g["homeTeam"], False), "away": team_block(g["awayTeam"], False),
               "history": (prev.get(gid) or {}).get("history", [])}
        old = prev.get(gid)
        if old and old.get("status") == 3 and old.get("home", {}).get("stats"):
            games.append(old)  # final and already complete: nothing more to fetch
            continue
        if now >= tip - dt.timedelta(minutes=5):
            try:
                b = boxscore.BoxScore(game_id=gid, timeout=30).get_dict()["game"]
                rec.update({"status": b.get("gameStatus", rec["status"]), "status_text": b.get("gameStatusText", rec["status_text"]),
                            "period": b.get("period", 0), "clock": clock_text(b.get("gameClock")),
                            "home": team_block(b["homeTeam"], True), "away": team_block(b["awayTeam"], True)})
                if rec["status"] >= 2:
                    t = round(elapsed(rec["period"], b.get("gameClock")), 1)
                    m = rec["home"]["score"] - rec["away"]["score"]
                    if not rec["history"] or rec["history"][-1] != {"t": t, "m": m}:
                        rec["history"] = rec["history"] + [{"t": t, "m": m}]
            except Exception as e:  # box score not published yet, or a network hiccup: keep the scoreboard line
                log(f"  box score {gid} not available: {type(e).__name__}: {str(e)[:120]}")
        games.append(rec)
    return games


def write(board_date, games):
    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc = {"updated": dt.datetime.now().astimezone().isoformat(timespec="seconds"), "board_date": board_date, "games": games,
           "source": "NBA.com live data via nba_api (cdn.nba.com)"}
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")


def git(*args):
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True)


def push(message):
    git("add", "data/nba/live.json")
    if git("diff", "--cached", "--quiet").returncode == 0:
        return False
    for args in (("commit", "-q", "-m", message), ("pull", "-q", "--rebase"), ("push", "-q")):
        r = git(*args)
        if r.returncode != 0:
            log(f"  git {args[0]} failed: {(r.stderr or r.stdout).strip()[:200]}")
            return False
    return True


def fetch_board():
    sb = scoreboard.ScoreBoard(timeout=30).get_dict()["scoreboard"]
    return sb.get("gameDate", ""), [g for g in sb.get("games", []) if str(g.get("gameId", "")).startswith("002")]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true", help="one check now, then exit")
    ap.add_argument("--no-push", action="store_true", help="write live.json only")
    a = ap.parse_args()
    log("--- live watcher started")
    board_date, board = fetch_board()
    if not board:
        log(f"No regular-season games on NBA.com's scoreboard for {board_date or 'today'}.")
        return 4
    ids = [g["gameId"] for g in board]
    log(f"{len(board)} games on {board_date}: " + ", ".join(f'{g["awayTeam"]["teamTricode"]}@{g["homeTeam"]["teamTricode"]}' for g in board))
    last_push = 0.0
    while True:
        try:
            date_now, now_board = fetch_board()
            known = {g["gameId"]: g for g in now_board}
            board = [known.get(gid, next(g for g in board if g["gameId"] == gid)) for gid in ids]  # keep tonight's games even after the board rolls over
        except Exception as e:
            log(f"  scoreboard not available: {type(e).__name__}: {str(e)[:120]}")
        prev = read_previous()
        games = check(board, prev)
        live = [g for g in games if g["status"] == 2]
        now = dt.datetime.now(dt.timezone.utc)
        waiting = [g for g in games if g["status"] == 1
                   and now < dt.datetime.fromisoformat(g["tip_utc"].replace("Z", "+00:00")) + dt.timedelta(hours=4)]  # later than that: postponed
        done = not live and not waiting
        write(board_date, games)
        summary = "; ".join(f'{g["away"]["team"]} {g["away"]["score"]}-{g["home"]["score"]} {g["home"]["team"]} ({g["status_text"]})' for g in games if g["status"] >= 2)
        log(f"check: {len(live)} live, {sum(g['status'] == 3 for g in games)} final of {len(games)}. {summary}")
        if not a.no_push and (a.once or done or (live and time.time() - last_push >= PUSH_EVERY)):
            if push(f"Live: {summary[:120] or 'tonight'}"):
                last_push = time.time()
                log("  pushed")
        if done:
            log("--- every game is final")
            return 0
        if a.once:
            return 3
        if live:
            time.sleep(CHECK_EVERY)
        else:  # nothing on right now: sleep until the next tip-off, in steps
            nxt = min(dt.datetime.fromisoformat(g["tip_utc"].replace("Z", "+00:00")) for g in waiting)
            wait = (nxt - dt.datetime.now(dt.timezone.utc)).total_seconds() - 120
            time.sleep(max(60, min(WAIT_STEP, wait)))


if __name__ == "__main__":
    sys.exit(main())
