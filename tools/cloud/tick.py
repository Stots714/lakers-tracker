"""One tick: whatever is due right now, on GitHub's machines. An outside scheduler (cron-job.org) starts
.github/workflows/tick.yml through GitHub's API every 5 minutes, because GitHub's own schedule has been running jobs
5 to 10 hours late (Nov 18-24, 2026), which live scores and pre-tip lines cannot wait for.

Each tick does only what is due, in this order:
  1. Live scores: one pass over today's NBA scoreboard with the PC's own live.py code. live.json is written when a
     game changed, at most every 7 minutes (GitHub Pages takes about 10 site updates an hour), and at once when a
     game ends. During the side-by-side check (data_writer "pc") only every 30 minutes and at finals.
  2. Injury report: while games are still to start, every half hour (once the cloud writes data/nba; during the
     side-by-side check the game-data read below brings it).
  3. Game data (nba_data.py): when a final game on the scoreboard has no result on file yet (at most every 15 minutes
     until NBA.com's box score checks out), every morning after 09:40 Pacific, and at least every 6 hours.
     During the side-by-side check, the comparison with the PC's files follows (compare.py).
  4. Betting lines (odds.py): the morning read after 09:47 Pacific (again after 3 hours if the books had not priced
     every game of the day yet), and a pre-tip read 2 to 25 minutes before each group of tip-offs, when
     data/pipeline.json gives the ticks that job (odds_pretip "tick").
  5. Trade reports (rumors.py): the main feed when the last read is 2 hours old; all 30 team feeds once a day after
     07:33 Pacific.

Where the NBA files go is set in data/pipeline.json: data_writer "pc" -> data/nba_cloud/ (side-by-side check; the
PC keeps writing data/nba/), "cloud" -> data/nba/ (the PC's tasks stand down).
Writes the commit message to .tick_message for the workflow's publish step. Exit 1 only when the tick itself broke.
"""
import datetime as dt
import json
import os
import sys
import traceback
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools" / "nba"))
PT = ZoneInfo("America/Los_Angeles")
ET = ZoneInfo("America/New_York")
LIVE_EVERY = {"cloud": 7 * 60, "pc": 30 * 60}  # seconds between live.json writes while games are on
DATA_RETRY = 15 * 60
DATA_MAX_AGE = 6 * 3600
MORNING_DATA = dt.time(9, 40)
MORNING_ODDS = dt.time(9, 47)
ODDS_RETRY = 3 * 3600    # a morning read that missed some of today's games tries again after this long
ODDS_RESERVE = 30        # credits kept back: no retries below this (odds.py skips pre-tip reads below it too)
PRETIP_LEAD = 25 * 60    # a pre-tip read starts when a tip-off is this close (and more than 2 minutes away)
PRETIP_GAP = 20 * 60     # at most one pre-tip read per group of tip-offs (each read records the next hour's games)
ALL_TEAM_FEEDS = dt.time(7, 33)
RUMORS_EVERY = 2 * 3600


def load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def when(s):
    """An ISO time from a status file as an aware datetime, or None."""
    try:
        t = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def read_csv(path):
    import csv
    if not Path(path).exists():
        return []
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def tip(g):
    return when(g["tip_utc"])


def live_pass(out, writer, now, log, live=None):
    """One pass over today's scoreboard. Returns (games, wrote)."""
    if live is None:
        import live
    live.OUT = out / "live.json"
    board_date, board = live.fetch_board()
    prev_doc = load_json(live.OUT)
    games = live.check(board, live.read_previous())
    old = {g["game_id"]: g for g in prev_doc.get("games") or []}
    finals_new = any(g["status"] == 3 and (old.get(g["game_id"]) or {}).get("status") != 3 for g in games)
    changed = prev_doc.get("board_date") != board_date or [g.get("status") for g in games] != [old.get(g["game_id"], {}).get("status") for g in games] \
        or any((g["home"]["score"], g["away"]["score"]) != ((old.get(g["game_id"]) or {}).get("home", {}).get("score"),
                                                            (old.get(g["game_id"]) or {}).get("away", {}).get("score")) for g in games)
    last = when(prev_doc.get("updated"))
    due = last is None or (now - last).total_seconds() >= LIVE_EVERY[writer]
    if changed and (due or finals_new or prev_doc.get("board_date") != board_date):
        live.write(board_date, games)
        return games, True
    return games, False


def data_due(out, games, now):
    """Why the game data should be read now, or None."""
    man = load_json(out / "manifest.json")
    last = when(man.get("updated"))
    if last is None:
        return "no data on file yet"
    age = (now - last).total_seconds()
    have = {r["game_id"] for r in read_csv(out / "league_results.csv") if r.get("home_pts")}
    missing = [g["game_id"] for g in games if g["status"] == 3 and g["game_id"] not in have]
    if missing and age >= DATA_RETRY:
        return f"{len(missing)} final game(s) not on file"
    local = now.astimezone(PT)
    morning = dt.datetime.combine(local.date(), MORNING_DATA, PT)
    if local >= morning and last < morning:
        return "the morning read"
    if age >= DATA_MAX_AGE:
        return "6 hours since the last read"
    return None


def odds_due(games, cfg, now, lines, status):
    """'morning', 'pretip' or None. Each kind's last try is kept in odds_status.json ("attempts"), so a game the
    books have not priced yet costs one retry every 3 hours at most, never a read (2 credits) every tick."""
    if not os.environ.get("ODDS_API_KEY"):
        return None
    local = now.astimezone(PT)
    today = local.date().isoformat()  # the Pacific date: after 21:00 Pacific it is already tomorrow in the East
    upcoming = [g for g in games if g["status"] == 1 and tip(g) and tip(g) > now]
    have = {(r["game_id"], r["kind"]) for r in lines}
    tried = status.get("attempts") or ({status["kind"]: status["updated"]} if status.get("kind") and status.get("updated") else {})
    try:
        left = int(status.get("remaining"))
    except (TypeError, ValueError):
        left = None
    start = dt.datetime.combine(local.date(), MORNING_ODDS, PT)
    if local >= start and any(tip(g).astimezone(ET).date().isoformat() == today and (g["game_id"], "morning") not in have for g in upcoming):
        last = when(tried.get("morning"))
        if last is None or last < start or ((now - last).total_seconds() >= ODDS_RETRY and (left is None or left >= ODDS_RESERVE)):
            return "morning"
    if cfg.get("odds_pretip") == "tick" and any(120 < (tip(g) - now).total_seconds() <= PRETIP_LEAD and (g["game_id"], "pretip") not in have for g in upcoming):
        last = when(tried.get("pretip"))
        if last is None or (now - last).total_seconds() >= PRETIP_GAP:
            return "pretip"
    return None


def rumors_due(status, now):
    """(due, all_teams)."""
    last = when(status.get("updated"))
    local = now.astimezone(PT)
    all_at = when(status.get("all_teams_updated"))
    if all_at is None and (status.get("feeds") or 0) >= 31:
        all_at = last  # a status file from before all_teams_updated was kept
    all_teams = local.time() >= ALL_TEAM_FEEDS and (all_at is None or all_at.astimezone(PT).date() != local.date())
    return (all_teams or last is None or (now - last).total_seconds() >= RUMORS_EVERY), all_teams


def run(now=None, root=ROOT, log=print, steps=None):
    import pipeline
    now = now or dt.datetime.now(dt.timezone.utc)
    cfg = pipeline.settings(root)
    writer = "cloud" if cfg["data_writer"] == "cloud" else "pc"
    out = root / ("data/nba" if writer == "cloud" else "data/nba_cloud")
    out.mkdir(parents=True, exist_ok=True)
    steps = steps or {}
    did, broke = [], []

    def step(name, fn):
        try:
            r = fn()
            if r:
                did.append(r if isinstance(r, str) else name)
        except Exception as e:  # one step's failure must not stop the others
            broke.append(f"{name}: {type(e).__name__}: {str(e)[:160]}")
            log(f"{name} failed:\n{traceback.format_exc()[-800:]}")

    games = []

    def live_step():
        nonlocal games
        games, wrote = (steps.get("live") or live_pass)(out, writer, now, log)
        n_live = sum(g["status"] == 2 for g in games)
        log(f"scoreboard: {len(games)} games, {n_live} live, {sum(g['status'] == 3 for g in games)} final" + ("; live.json written" if wrote else ""))
        return f"live ({n_live} on)" if wrote else None
    step("live scores", live_step)

    def injury_step():
        # During the side-by-side check the game-data read brings the injury report too; the half-hourly reads
        # start when the cloud takes over (fewer pushes: GitHub Pages builds about 10 site updates an hour)
        if writer != "cloud" or not any(g["status"] == 1 for g in games) or now.minute % 30 >= 5:
            return None
        import injuries
        rows, label, changed = (steps.get("injuries") or injuries.update)(out, log=log)
        return f"injury report {label}" if changed else None
    step("injury report", injury_step)

    def data_step():
        why = data_due(out, games, now)
        if not why:
            return None
        import nba_data
        log(f"game data: {why}")
        # A final just in: read it (the live scoreboard's word, before the schedule file catches up) and re-check
        # only the last day; the morning and 6-hourly reads re-check the last 7 days for late stat corrections
        finals = [g["game_id"] for g in games if g["status"] == 3]
        days = 1 if why.endswith("not on file") else 7
        code = (steps.get("nba_data") or nba_data.run)(out, days, True, log=log, final_ids=finals)
        if writer == "pc":
            import compare
            (steps.get("compare") or compare_files)(root, out, compare, log)
        return f"game data ({why})" if code == 0 else None
    step("game data", data_step)

    def odds_step():
        import odds
        kind = odds_due(games, cfg, now, read_csv(odds.LINES), odds.read_status())
        if not kind:
            return None
        (steps.get("odds") or odds.run)(kind, os.environ.get("ODDS_API_KEY", ""), log=log)
        return f"{kind} betting lines"
    step("betting lines", odds_step)

    def rumors_step():
        import rumors
        due, all_teams = rumors_due(load_json(rumors.STATUS), now)
        if not due:
            return None
        (steps.get("rumors") or rumors.run)(all_teams=all_teams, log=log)
        return "trade reports" + (" (all teams)" if all_teams else "")
    step("trade reports", rumors_step)

    msg = "Tick: " + ("; ".join(did) if did else "nothing new")
    (root / ".tick_message").write_text(msg, encoding="utf-8")
    log(msg)
    for b in broke:
        log("  broke: " + b)
    return 1 if broke and not did else 0


def compare_files(root, out, compare, log):
    finals = lambda d: {r["game_id"] for r in compare.read(d / "league_results.csv") if r.get("home_pts")}
    games = finals(out) & finals(root / "data/nba")
    text = f"Games final on both sides: {len(games)}\n\n" + compare.report(compare.compare(out, root / "data/nba", games))
    (out / "compare.md").write_text(text, encoding="utf-8")
    log(text.splitlines()[0])


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(run())
