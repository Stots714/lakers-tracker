"""Betting-market lines for NBA games, from The Odds API, saved as one consensus number per game.

Runs on GitHub's machines (.github/workflows/odds.yml): every morning at 09:47 Pacific ("morning", read at the
same time the app makes its forecast), and about 10 minutes before each group of tip-offs ("pretip", started by
the live watcher on the PC: the closing line, the toughest bar).

What it keeps (data/markets/odds_lines.csv), per game and read: the market's expected home margin (the median
of US sportsbooks' spreads) and the home team's win chance (the median of their moneylines, with each book's
margin removed). Only games not yet started. Never book names, prices or links: The Odds API's terms allow
showing odds in an app but not redistributing their data as files, and this repo is public.

Credits (free plan: 500 a month): each read costs 2 (2 markets, 1 region). A pre-tip read is skipped when fewer
than 30 credits are left, so the morning reads last the month.

  ODDS_API_KEY=... python tools/cloud/odds.py --kind morning
No key set: prints a note and exits 0 without writing anything (the watchdog then reports "not set up").
Exit 1 when The Odds API refuses the read; the reason is saved in data/markets/odds_status.json.
"""
import argparse
import csv
import datetime as dt
import io
import json
import os
import statistics
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "data/nba/league_results.csv"
LINES = ROOT / "data/markets/odds_lines.csv"
STATUS = ROOT / "data/markets/odds_status.json"
URL = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds/"
ET = ZoneInfo("America/New_York")
COLS = ["game_id", "date", "away", "home", "kind", "read_at", "tip_utc", "home_margin", "p_home", "books"]
RESERVE = 30            # credits kept for morning reads
PRETIP_WINDOW = 60      # minutes: a pre-tip read records games starting within this long
MIN_BOOKS = 2

TEAMS = {
    "Atlanta Hawks": "ATL", "Boston Celtics": "BOS", "Brooklyn Nets": "BKN", "Charlotte Hornets": "CHA",
    "Chicago Bulls": "CHI", "Cleveland Cavaliers": "CLE", "Dallas Mavericks": "DAL", "Denver Nuggets": "DEN",
    "Detroit Pistons": "DET", "Golden State Warriors": "GSW", "Houston Rockets": "HOU", "Indiana Pacers": "IND",
    "Los Angeles Clippers": "LAC", "LA Clippers": "LAC", "Los Angeles Lakers": "LAL", "Memphis Grizzlies": "MEM",
    "Miami Heat": "MIA", "Milwaukee Bucks": "MIL", "Minnesota Timberwolves": "MIN", "New Orleans Pelicans": "NOP",
    "New York Knicks": "NYK", "Oklahoma City Thunder": "OKC", "Orlando Magic": "ORL", "Philadelphia 76ers": "PHI",
    "Phoenix Suns": "PHX", "Portland Trail Blazers": "POR", "Sacramento Kings": "SAC", "San Antonio Spurs": "SAS",
    "Toronto Raptors": "TOR", "Utah Jazz": "UTA", "Washington Wizards": "WAS",
}


class OddsError(Exception):
    pass


def fetch(key, opener=urllib.request.urlopen):
    """(events, quota headers). Raises OddsError with a plain reason."""
    q = urllib.parse.urlencode({"apiKey": key, "regions": "us", "markets": "h2h,spreads", "oddsFormat": "decimal", "dateFormat": "iso"})
    req = urllib.request.Request(f"{URL}?{q}", headers={"User-Agent": "lakers-tracker-odds", "Accept": "application/json"})
    try:
        with opener(req, timeout=30) as r:
            quota = {k: r.headers.get(f"x-requests-{k}") for k in ("remaining", "used", "last")}
            return json.load(r), quota
    except urllib.error.HTTPError as e:
        reason = {401: "the key was refused (401): check the ODDS_API_KEY secret",
                  429: "the monthly credits or the rate limit ran out (429)"}.get(e.code, f"The Odds API answered HTTP {e.code}")
        raise OddsError(reason) from None
    except Exception as e:
        raise OddsError(f"no answer from The Odds API ({type(e).__name__})") from None


def median(xs):
    return statistics.median(xs) if xs else None


def consensus(event):
    """(home margin, home win chance, books) from every book that offers both markets' numbers."""
    home, away = event["home_team"], event["away_team"]
    margins, chances = [], []
    for book in event.get("bookmakers") or []:
        for m in book.get("markets") or []:
            out = {o.get("name"): o for o in m.get("outcomes") or []}
            if home not in out or away not in out:
                continue
            if m.get("key") == "h2h":
                ph, pa = out[home].get("price"), out[away].get("price")
                if ph and pa and ph > 1 and pa > 1:
                    chances.append((1 / ph) / (1 / ph + 1 / pa))  # remove the book's margin
            elif m.get("key") == "spreads":
                h, a = out[home].get("point"), out[away].get("point")
                if h is not None and a is not None and abs(h + a) < 1e-9:
                    margins.append(-h)  # home -6.5 means the market expects the home team by 6.5
    books = max(len(margins), len(chances))
    m, p = median(margins), median(chances)
    return (None if m is None else round(m * 2) / 2), (None if p is None else round(p, 3)), books


def schedule_index(results):
    """(ET date, team, team) -> (game_id, away, home), both orders, so a neutral-site game whose home side
    The Odds API lists the other way round still matches."""
    idx = {}
    for r in results:
        for pair in ((r["away"], r["home"]), (r["home"], r["away"])):
            idx[(r["date"], *pair)] = (r["game_id"], r["away"], r["home"])
    return idx


def collect(events, results, kind, now):
    """Rows for games not yet started: morning = today's (US Eastern) games; pretip = games within the hour."""
    idx, rows, skipped = schedule_index(results), [], []
    today = now.astimezone(ET).date().isoformat()
    for ev in events:
        tip = dt.datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
        if tip <= now + dt.timedelta(minutes=1):
            continue  # started: in-play lines are not pre-game lines
        if kind == "pretip" and tip - now > dt.timedelta(minutes=PRETIP_WINDOW):
            continue
        date = tip.astimezone(ET).date().isoformat()
        if kind == "morning" and date != today:
            continue
        h, a = TEAMS.get(ev.get("home_team")), TEAMS.get(ev.get("away_team"))
        hit = idx.get((date, a, h)) if h and a else None
        if not hit:
            skipped.append(f'{ev.get("away_team")} at {ev.get("home_team")} {date}')
            continue
        game_id, away, home = hit
        margin, p_home, books = consensus(ev)
        if books < MIN_BOOKS or margin is None or p_home is None:
            skipped.append(f"{away}@{home}: {books} books")
            continue
        if home != h:  # The Odds API has the sides the other way round: turn the numbers to our home team
            margin, p_home = -margin, round(1 - p_home, 3)
        rows.append({"game_id": game_id, "date": date, "away": away, "home": home, "kind": kind,
                     "read_at": now.strftime("%Y-%m-%dT%H:%MZ"), "tip_utc": tip.strftime("%Y-%m-%dT%H:%MZ"),
                     "home_margin": f"{margin:g}", "p_home": f"{p_home:.3f}", "books": books})
    return rows, skipped


def merge(old, new):
    """One row per game and kind: a newer read replaces an older one."""
    by = {(r["game_id"], r["kind"]): r for r in old}
    for r in new:
        by[(r["game_id"], r["kind"])] = r
    return sorted(by.values(), key=lambda r: (r["date"], r["tip_utc"], r["game_id"], r["kind"]))


def read_csv(path):
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_atomic(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="")
    os.replace(tmp, path)


def write_lines(rows):
    buf = io.StringIO(newline="")
    w = csv.DictWriter(buf, fieldnames=COLS, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    write_atomic(LINES, buf.getvalue())


def write_status(**status):
    write_atomic(STATUS, json.dumps(status, indent=1) + "\n")


def read_status():
    try:
        return json.loads(STATUS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def last_remaining():
    try:
        return int(read_status().get("remaining"))
    except (ValueError, TypeError):
        return None


def run(kind, key, now=None, opener=urllib.request.urlopen, log=print):
    now = now or dt.datetime.now(dt.timezone.utc)
    if not key:
        log("No ODDS_API_KEY secret is set, so there is nothing to read yet (see tools/cloud/README.md).")
        return 0
    left = last_remaining()
    if kind == "pretip" and left is not None and left < RESERVE:
        log(f"Skipped: {left} credits left, kept for the morning reads.")
        return 0
    stamp = now.astimezone(dt.timezone.utc).isoformat(timespec="seconds")
    # The last try of each kind, kept across reads, so the 5-minute ticks never repeat a read that found nothing
    attempts = {**(read_status().get("attempts") or {}), kind: stamp}
    try:
        events, quota = fetch(key, opener)
    except OddsError as e:
        log(f"Not read: {e}")
        write_status(updated=stamp, ok=False, kind=kind, message=str(e), remaining=left, attempts=attempts)
        return 1
    rows, skipped = collect(events, read_csv(RESULTS), kind, now)
    write_lines(merge(read_csv(LINES), rows))
    remaining = int(quota["remaining"]) if (quota.get("remaining") or "").strip().isdigit() else None
    note = f"{len(rows)} games read" + (f"; not matched: {'; '.join(skipped[:5])}" if skipped else "")
    write_status(updated=stamp, ok=True, kind=kind, message=note, games=len(rows), remaining=remaining,
                 used=quota.get("used"), attempts=attempts)
    log(f"{kind}: {note}. Credits left: {remaining}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kind", choices=["morning", "pretip"], default="morning")
    a = ap.parse_args()
    return run(a.kind, (os.environ.get("ODDS_API_KEY") or "").strip())


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
