"""NBA game data from the NBA's public data files (cdn.nba.com), so the site can run without the PC.

stats.nba.com, which the PC's update reads, does not answer GitHub's machines; cdn.nba.com does (reach test,
Nov 24, 2026), and its per-game box scores hold every field the site uses. This writes the same files as the
PC's update, with the same columns:

  league_results.csv       every regular-season game; scores once the game is final and its box score checks out
  league_box.csv           team box score per game
  league_player_games.csv  one row per player who played
  league_quarters.csv      points by quarter, overtime together
  league_inactive.csv      players listed inactive
  league_extras.csv        paint, second-chance, fast-break, off-turnover and bench points, leads, ties, runs
  injuries.csv             the newest official injury report (tools/nba/injuries.py, as on the PC)
  manifest.json            when it ran, what it read, games held back and why

Safety, as on the PC: a final game is kept only when its box score agrees with itself (team points = final score =
the quarters added up = the players' points added up, and not tied); otherwise it is held back and read again next
run, and the game's earlier rows, if any, stay. Box scores from the last two days are read again for late stat
corrections. Every file is written to a temporary file and swapped in.

During the side-by-side check this writes data/nba_cloud/ and compare.py checks it against the PC's data/nba/.
  python tools/cloud/nba_data.py [--out data/nba_cloud] [--refresh-days 2] [--no-injuries]
"""
import argparse
import csv
import datetime as dt
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
SCHEDULE = "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json"
BOXSCORE = "https://cdn.nba.com/static/json/liveData/boxscore/boxscore_{gid}.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:158.0) Gecko/20100101 Firefox/158.0",
           "Referer": "https://www.nba.com/", "Accept": "application/json", "Accept-Language": "en-US,en;q=0.9"}
EASTERN = ZoneInfo("America/New_York")
REGULAR = "002"   # game ids: 001 preseason, 002 regular season (NBA Cup group and knockout games included)
PAUSE = 0.3       # seconds between box-score requests

BOX = ["pts", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "oreb", "dreb", "tov", "pf"]
LINE = ["pts", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "oreb", "dreb", "ast", "stl", "blk", "tov", "pf"]
STAT = {"pts": "points", "fgm": "fieldGoalsMade", "fga": "fieldGoalsAttempted", "fg3m": "threePointersMade",
        "fg3a": "threePointersAttempted", "ftm": "freeThrowsMade", "fta": "freeThrowsAttempted", "oreb": "reboundsOffensive",
        "dreb": "reboundsDefensive", "ast": "assists", "stl": "steals", "blk": "blocks", "tov": "turnovers", "pf": "foulsPersonal"}
EXTRAS = {"turnoversTeam": "tov_team", "turnoversTotal": "tov_total", "reboundsTeam": "reb_team", "pointsInThePaint": "pts_paint",
          "pointsSecondChance": "pts_second_chance", "pointsFastBreak": "pts_fast_break", "pointsFromTurnovers": "pts_off_tov",
          "benchPoints": "pts_bench", "biggestLead": "biggest_lead", "leadChanges": "lead_changes", "timesTied": "times_tied",
          "biggestScoringRun": "biggest_run"}
COLS = {
    "league_results.csv": ["date", "away", "home", "away_pts", "home_pts", "ot", "game_id", "cup", "neutral"],
    "league_box.csv": ["date", "team", "opp", "home_away"] + BOX + ["mp", "game_id", "tov_players", "tov_team"],
    "league_player_games.csv": ["date", "team", "opp", "player", "min"] + LINE + ["game_id", "player_id"],
    "league_quarters.csv": ["date", "team", "opp", "q1", "q2", "q3", "q4", "ot", "game_id"],
    "league_inactive.csv": ["date", "team", "player", "game_id", "person_id"],
    "league_extras.csv": ["date", "team", "opp"] + list(EXTRAS.values()) + ["game_id"],
}
PER_GAME = [f for f in COLS if f != "league_results.csv"]


class FetchError(Exception):
    pass


def get_json(url, opener=urllib.request.urlopen, tries=3, wait=3, sleep=time.sleep):
    for k in range(tries):
        try:
            with opener(urllib.request.Request(url, headers=HEADERS), timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                raise FetchError(f"HTTP {e.code}") from None  # not there (yet): no point asking again now
            err = f"HTTP {e.code}"
        except Exception as e:
            err = f"{type(e).__name__}: {str(e)[:80]}"
        if k < tries - 1:
            sleep(wait * (k + 1))
    raise FetchError(err)


def iso_date(v):
    s = str(v or "")
    if re.match(r"\d{4}-\d{2}-\d{2}", s):
        return s[:10]
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}" if m else ""


def schedule(js):
    """Regular-season games from the season schedule: id, US Eastern date, sides, status, NBA Cup label, neutral site."""
    out = []
    for day in (js.get("leagueSchedule") or {}).get("gameDates") or []:
        for g in day.get("games") or []:
            gid = str(g.get("gameId") or "")
            if not gid.startswith(REGULAR):
                continue
            label = " ".join(str(g.get(k) or "").strip() for k in ("gameLabel", "gameSubLabel")).strip()
            subtype = str(g.get("gameSubtype") or "").lower()
            cup = label if ("cup" in label.lower() or "in-season" in subtype) else ("NBA Cup" if "in-season" in subtype else "")
            out.append({"game_id": gid, "date": iso_date(g.get("gameDateEst") or g.get("gameDate") or day.get("gameDate")),
                        "home": (g.get("homeTeam") or {}).get("teamTricode") or "", "away": (g.get("awayTeam") or {}).get("teamTricode") or "",
                        "status": int(g.get("gameStatus") or 0), "cup": cup,
                        "neutral": 1 if str(g.get("isNeutral")).lower() in ("1", "true") else 0})
    return sorted(out, key=lambda r: (r["date"], r["game_id"]))


def minutes(iso):
    """'PT25M01.00S' -> 25.0167; anything else 0."""
    m = re.fullmatch(r"PT(\d+)M([\d.]+)S", str(iso or ""))
    return int(m.group(1)) + float(m.group(2)) / 60 if m else 0.0


def num(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return 0


def problem(game):
    """Why a box score cannot be trusted yet, or None."""
    if num(game.get("gameStatus")) != 3:
        return "not final"
    teams = [game.get("homeTeam") or {}, game.get("awayTeam") or {}]
    if any(not t.get("teamTricode") or not t.get("statistics") for t in teams):
        return "a team's box score is missing"
    for t in teams:
        score = num(t.get("score"))
        if num(t["statistics"].get("points")) != score:
            return f"{t['teamTricode']}: team points {t['statistics'].get('points')} but final score {score}"
        periods = t.get("periods") or []
        if len(periods) < 4 or sum(num(p.get("score")) for p in periods) != score:
            return f"{t['teamTricode']}: quarters do not add up to the final score"
        played = [p for p in t.get("players") or [] if str(p.get("played")) == "1"]
        if sum(num((p.get("statistics") or {}).get("points")) for p in played) != score:
            return f"{t['teamTricode']}: players' points do not add up to the final score"
    if num(teams[0].get("score")) == num(teams[1].get("score")):
        return "tied"
    return None


def game_rows(game, sched):
    """{file: [rows]} for one checked final game."""
    gid, date = sched["game_id"], sched["date"]
    sides = {"H": game["homeTeam"], "A": game["awayTeam"]}
    rows = {f: [] for f in PER_GAME}
    for ha, t in sides.items():
        team, opp = t["teamTricode"], sides["A" if ha == "H" else "H"]["teamTricode"]
        st = t["statistics"]
        mp = minutes(st.get("minutes"))
        tov_players, tov_team = num(st.get("turnovers")), num(st.get("turnoversTeam"))
        rows["league_box.csv"].append({
            "date": date, "team": team, "opp": opp, "home_away": ha, **{k: num(st.get(STAT[k])) for k in BOX},
            "mp": 240 + 25 * max(0, int(round((mp - 240) / 25))), "game_id": gid, "tov_players": tov_players, "tov_team": tov_team})
        rows["league_box.csv"][-1]["tov"] = tov_players + tov_team
        for p in t.get("players") or []:
            s = p.get("statistics") or {}
            name = p.get("name") or f'{p.get("firstName") or ""} {p.get("familyName") or ""}'.strip()
            if str(p.get("played")) == "1":
                rows["league_player_games.csv"].append({
                    "date": date, "team": team, "opp": opp, "player": name, "min": round(minutes(s.get("minutes")), 2),
                    **{k: num(s.get(STAT[k])) for k in LINE}, "game_id": gid, "player_id": str(p.get("personId") or "")})
            if str(p.get("status") or "").upper() == "INACTIVE":
                rows["league_inactive.csv"].append({"date": date, "team": team, "player": name, "game_id": gid, "person_id": str(p.get("personId") or "")})
        per = sorted(t.get("periods") or [], key=lambda p: num(p.get("period")))
        q = [num(p.get("score")) for p in per]
        rows["league_quarters.csv"].append({"date": date, "team": team, "opp": opp, "q1": q[0], "q2": q[1], "q3": q[2], "q4": q[3],
                                            "ot": sum(q[4:]), "game_id": gid})
        rows["league_extras.csv"].append({"date": date, "team": team, "opp": opp, **{v: num(st.get(k)) for k, v in EXTRAS.items()}, "game_id": gid})
    return rows


def read(path):
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write(path, rows, cols):
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO(newline="")
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(buf.getvalue(), encoding="utf-8", newline="")
    os.replace(tmp, path)


def run(out, refresh_days=2, injuries=True, opener=urllib.request.urlopen, sleep=time.sleep, now=None, log=print):
    now = now or dt.datetime.now(dt.timezone.utc)
    today = now.astimezone(EASTERN).date()
    steps = {}
    try:
        games = schedule(get_json(SCHEDULE, opener, sleep=sleep))
        steps["schedule"] = {"ok": True, "games": len(games)}
    except FetchError as e:
        log(f"Schedule not read: {e}. Nothing changed.")
        return 1
    if len(games) < 1000:
        log(f"Schedule has only {len(games)} regular-season games; refusing to write from it.")
        return 1
    old = {f: read(out / f) for f in COLS}
    have = {r["game_id"] for r in old["league_box.csv"]}
    finals = [g for g in games if g["status"] == 3]
    recent = (today - dt.timedelta(days=refresh_days)).isoformat()
    todo = [g for g in finals if g["game_id"] not in have or g["date"] >= recent]
    fresh, held = {}, []
    for i, g in enumerate(todo):
        if i:
            sleep(PAUSE)
        try:
            game = get_json(BOXSCORE.format(gid=g["game_id"]), opener, sleep=sleep).get("game") or {}
        except FetchError as e:
            held.append({"game_id": g["game_id"], "date": g["date"], "why": f"box score not read ({e})"})
            continue
        why = problem(game)
        if why:
            held.append({"game_id": g["game_id"], "date": g["date"], "why": why})
            continue
        fresh[g["game_id"]] = game_rows(game, g)
    # Per-game files: the old rows of every game read again are replaced; games held back keep their old rows
    for f in PER_GAME:
        rows = [r for r in old[f] if r["game_id"] not in fresh] + [r for gr in fresh.values() for r in gr[f]]
        key = (lambda r: (r["date"], r["team"], r["player"])) if f == "league_player_games.csv" else (lambda r: (r["date"], r["game_id"], r.get("team", "")))
        write(out / f, sorted(rows, key=key), COLS[f])
    # Results: the schedule, with scores for games whose box score is on file
    box = {}
    for r in read(out / "league_box.csv"):
        box.setdefault(r["game_id"], {})[r["home_away"]] = r
    res = []
    for g in games:
        b = box.get(g["game_id"], {})
        done = "H" in b and "A" in b
        res.append({"date": g["date"], "away": g["away"], "home": g["home"],
                    "away_pts": b["A"]["pts"] if done else "", "home_pts": b["H"]["pts"] if done else "",
                    "ot": max(0, int(round((int(b["H"]["mp"]) - 240) / 25))) if done else "", "game_id": g["game_id"],
                    "cup": g["cup"], "neutral": g["neutral"]})
    write(out / "league_results.csv", res, COLS["league_results.csv"])
    steps["box scores"] = {"ok": True, "read": len(fresh), "held_back": len(held)}
    if injuries:
        try:
            sys.path.insert(0, str(ROOT / "tools" / "nba"))
            import injuries as inj  # requests and pypdf, installed by the workflow
            rows, label, changed = inj.update(out, log=log)
            steps["injury report"] = {"ok": True, "report": label, "changed": changed}
        except Exception as e:
            steps["injury report"] = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:160]}"}
    finals_on_file = sum(1 for r in res if r["home_pts"] != "")
    manifest = {"updated": now.isoformat(timespec="seconds"), "source": "cdn.nba.com (GitHub job tools/cloud/nba_data.py)",
                "games": len(games), "finals": finals_on_file, "read_this_run": len(fresh), "held_back": held, "steps": steps}
    (out / "manifest.json.tmp").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    os.replace(out / "manifest.json.tmp", out / "manifest.json")
    log(f"{len(games)} games on the schedule; {len(fresh)} box scores read, {len(held)} held back; {finals_on_file} finals on file.")
    for h in held[:10]:
        log(f"  held back {h['date']} {h['game_id']}: {h['why']}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="data/nba_cloud")
    ap.add_argument("--refresh-days", type=int, default=2)
    ap.add_argument("--no-injuries", action="store_true")
    a = ap.parse_args()
    return run(ROOT / a.out, a.refresh_days, not a.no_injuries)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
