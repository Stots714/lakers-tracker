"""Watchdog: did the PC's jobs publish? Runs daily on GitHub's machines (.github/workflows/watchdog.yml).

It checks results, not processes: the site is current if yesterday's scores, a recent update and the live
watcher's game-night updates are in the repo, whichever PC run delivered them.

FAIL (the run fails, so GitHub emails the repo owner):
  - a game from yesterday (US Eastern date, as NBA.com dates games) has no final score in league_results.csv
  - two or more games from the last three days have no final score (one alone may be a postponement)
  - no update has published for 26 hours (data/nba/manifest.json "updated")
  - yesterday had games but the live watcher published nothing during them (no change to live.json)
  - the odds job (once set up) did not read today's lines, or reported an error
WARN (shown on the run page, no email): one game missing a score, or an update step that reported a problem.
Off-season (no games within 7 days either side of today): nothing to check.

Standard library only. Run by hand: python tools/cloud/watchdog.py   (exit 1 when something FAILs)
"""
import csv
import datetime as dt
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
ET = ZoneInfo("America/New_York")
PT = ZoneInfo("America/Los_Angeles")
MANIFEST_MAX_AGE_H = 26      # the morning update runs daily, the after-the-games update on game nights
GAP_WINDOW_DAYS = 3          # how far back missing scores are counted
OFFSEASON_DAYS = 7           # no games this many days either side of today: nothing to check
ODDS_READ_BY = dt.time(10, 30)  # Pacific: the morning odds read runs at 09:47

FAIL, WARN, OK = "FAIL", "WARN", "OK"


def read_csv(path):
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def scored(row):
    return (row.get("away_pts") or "").strip() != "" and (row.get("home_pts") or "").strip() != ""


def check_results(rows, today_et):
    """Yesterday's games all scored; at most one missing score in the last three days."""
    yday = (today_et - dt.timedelta(days=1)).isoformat()
    start = (today_et - dt.timedelta(days=GAP_WINDOW_DAYS)).isoformat()
    y_games = [r for r in rows if r["date"] == yday]
    y_missing = [r for r in y_games if not scored(r)]
    window_missing = [r for r in rows if start <= r["date"] <= yday and not scored(r)]
    out = []
    if not y_games:
        out.append((OK, "Yesterday's scores", f"no games on {yday}"))
    elif y_missing:
        out.append((FAIL, "Yesterday's scores", f"{len(y_missing)} of {len(y_games)} games on {yday} have no final score: "
                    + ", ".join(f"{r['away']}@{r['home']}" for r in y_missing[:6])))
    else:
        out.append((OK, "Yesterday's scores", f"all {len(y_games)} games on {yday} have final scores"))
    older = [r for r in window_missing if r["date"] != yday]
    if len(window_missing) >= 2 and older:
        out.append((FAIL, f"Scores, last {GAP_WINDOW_DAYS} days", f"{len(window_missing)} games without a final score: "
                    + ", ".join(f"{r['date']} {r['away']}@{r['home']}" for r in window_missing[:6])))
    elif older:
        r = older[0]
        out.append((WARN, f"Scores, last {GAP_WINDOW_DAYS} days", f"one game without a final score ({r['date']} {r['away']}@{r['home']}); "
                    "a postponed game looks like this"))
    return out


def check_manifest(manifest, now):
    if not manifest or not manifest.get("updated"):
        return [(FAIL, "Last update", "data/nba/manifest.json has no update time")]
    when = dt.datetime.fromisoformat(manifest["updated"])
    age_h = (now - when).total_seconds() / 3600
    local = when.astimezone(PT).strftime("%b %d %H:%M")
    out = [((FAIL if age_h > MANIFEST_MAX_AGE_H else OK), "Last update",
            f"published {local} Pacific, {age_h:.0f} hours ago" + (f" (limit {MANIFEST_MAX_AGE_H})" if age_h > MANIFEST_MAX_AGE_H else ""))]
    for name, step in (manifest.get("steps") or {}).items():
        if isinstance(step, dict) and step.get("ok") is False:
            out.append((WARN, "Update step", f"{name}: {(step.get('error') or 'reported a problem')[:160]}"))
    return out


def live_commits(since, until, repo=None, token=None):
    """Commits that changed data/nba/live.json between two times, from the GitHub API. None if unavailable."""
    repo = repo or os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        return None
    q = urllib.parse.urlencode({"path": "data/nba/live.json", "since": since.isoformat(), "until": until.isoformat(), "per_page": 100})
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/commits?{q}",
                                 headers={"Accept": "application/vnd.github+json", "User-Agent": "lakers-tracker-watchdog"})
    token = token if token is not None else os.environ.get("GITHUB_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except Exception:
        return None


def check_live(rows, today_et, fetch=live_commits):
    yday = today_et - dt.timedelta(days=1)
    n_games = sum(1 for r in rows if r["date"] == yday.isoformat())
    if not n_games:
        return [(OK, "Live watcher", f"no games on {yday.isoformat()}")]
    since = dt.datetime.combine(yday, dt.time(6, 0), PT)
    until = since + dt.timedelta(days=1)
    commits = fetch(since, until)
    if commits is None:
        return [(WARN, "Live watcher", "could not ask GitHub for live.json changes; not checked")]
    if not commits:
        return [(FAIL, "Live watcher", f"{n_games} games on {yday.isoformat()}, but live.json was not updated during them")]
    return [(OK, "Live watcher", f"{len(commits)} live updates during {yday.isoformat()}'s games")]


def check_odds(status, rows, today_et, now):
    """Only once the odds job is set up (data/markets/odds_status.json exists)."""
    if status is None:
        return [(OK, "Odds read", "not set up yet")]
    if not any(r["date"] == today_et.isoformat() for r in rows):
        return [(OK, "Odds read", "no games today")]
    if now.astimezone(PT).time() < ODDS_READ_BY:
        return [(OK, "Odds read", "too early to check")]
    when = dt.datetime.fromisoformat(status["updated"]) if status.get("updated") else None
    if when is None or when.astimezone(PT).date() != now.astimezone(PT).date():
        return [(FAIL, "Odds read", "today's lines were not read (data/markets/odds_status.json is from an earlier day)")]
    if not status.get("ok"):
        return [(FAIL, "Odds read", f"the last read reported: {status.get('message', 'an error')}")]
    left = status.get("remaining")
    return [(OK, "Odds read", f"read {when.astimezone(PT):%H:%M} Pacific" + (f"; {left} credits left this month" if left is not None else ""))]


def run(root=ROOT, now=None, fetch=live_commits):
    now = now or dt.datetime.now(dt.timezone.utc)
    today_et = now.astimezone(ET).date()
    rows = read_csv(root / "data/nba/league_results.csv")
    lo = (today_et - dt.timedelta(days=OFFSEASON_DAYS)).isoformat()
    hi = (today_et + dt.timedelta(days=OFFSEASON_DAYS)).isoformat()
    if not any(lo <= r["date"] <= hi for r in rows):
        return [(OK, "Season", f"no games between {lo} and {hi}; nothing to check")]
    manifest_path = root / "data/nba/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else None
    odds_path = root / "data/markets/odds_status.json"
    odds = json.loads(odds_path.read_text(encoding="utf-8")) if odds_path.exists() else None
    return (check_results(rows, today_et) + check_manifest(manifest, now)
            + check_live(rows, today_et, fetch) + check_odds(odds, rows, today_et, now))


def report(results, summary=None):
    lines = ["## Watchdog: did the PC's jobs publish?", "", "| Result | Check | Detail |", "|---|---|---|"]
    lines += [f"| {lvl} | {name} | {detail.replace('|', '/')} |" for lvl, name, detail in results]
    text = "\n".join(lines) + "\n"
    print(text)
    summary = summary or os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(text)
    return 1 if any(lvl == FAIL for lvl, _, _ in results) else 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(report(run()))
