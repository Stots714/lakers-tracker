"""Side-by-side check: the GitHub job's files (data/nba_cloud/, from cdn.nba.com) against the PC's (data/nba/,
from stats.nba.com), file by file, game by game. The site switches to the GitHub files only when they match.

For each file: rows on both sides, rows on one side only, and for every column how many shared rows differ, with
examples. Numbers are compared as numbers (14 = 14.0); minutes to within 0.02; names without accents or punctuation.
Writes data/nba_cloud/compare.md (and the run page summary on GitHub).

  python tools/cloud/compare.py [--cloud data/nba_cloud] [--pc data/nba]
Exit 0 always: differences are findings, not failures.
"""
import argparse
import csv
import os
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# file: (key columns, columns to compare)
FILES = {
    "league_results.csv": (["game_id"], ["date", "away", "home", "away_pts", "home_pts", "ot", "cup", "neutral"]),
    "league_box.csv": (["game_id", "team"], ["date", "opp", "home_away", "pts", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "oreb", "dreb",
                                             "tov", "pf", "mp", "tov_players", "tov_team"]),
    "league_player_games.csv": (["game_id", "player_id"], ["date", "team", "opp", "player", "min", "pts", "fgm", "fga", "fg3m", "fg3a", "ftm",
                                                          "fta", "oreb", "dreb", "ast", "stl", "blk", "tov", "pf"]),
    "league_quarters.csv": (["game_id", "team"], ["date", "opp", "q1", "q2", "q3", "q4", "ot"]),
    "league_inactive.csv": (["game_id", "person_id"], ["date", "team", "player"]),
    "league_extras.csv": (["game_id", "team"], ["date", "opp", "tov_team", "tov_total", "reb_team", "pts_paint", "pts_second_chance",
                                                "pts_fast_break", "pts_off_tov", "pts_bench", "biggest_lead", "lead_changes", "times_tied", "biggest_run"]),
}


def read(path):
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def plain(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    return "".join(ch for ch in s if ch.isalnum() or ch == " ").strip()


def same(col, a, b):
    a, b = (a or "").strip(), (b or "").strip()
    if a == b:
        return True
    if col == "player":
        return plain(a) == plain(b)
    try:
        x, y = float(a), float(b)
    except ValueError:
        return False
    return abs(x - y) <= (0.02 if col == "min" else 1e-9)


def compare(cloud_dir, pc_dir, games=None):
    """{file: report}. games: only these game ids (the finals on both sides), so unfinished games do not count."""
    out = {}
    for f, (key, cols) in FILES.items():
        c, p = read(cloud_dir / f), read(pc_dir / f)
        if f == "league_results.csv":  # only games final on both sides
            c = [r for r in c if r.get("home_pts")]
            p = [r for r in p if r.get("home_pts")]
        if games is not None:
            c = [r for r in c if r["game_id"] in games]
            p = [r for r in p if r["game_id"] in games]
        cm = {tuple(r.get(k, "") for k in key): r for r in c}
        pm = {tuple(r.get(k, "") for k in key): r for r in p}
        both = sorted(set(cm) & set(pm))
        diffs = {}
        for k in both:
            for col in cols:
                if col in cm[k] and col in pm[k] and not same(col, cm[k][col], pm[k][col]):
                    diffs.setdefault(col, []).append((k, cm[k][col], pm[k][col]))
        out[f] = {"cloud": len(cm), "pc": len(pm), "both": len(both), "cloud_only": sorted(set(cm) - set(pm)),
                  "pc_only": sorted(set(pm) - set(cm)), "diffs": diffs, "cols": cols}
    return out


def report(res):
    lines = ["## Side-by-side check: GitHub (cdn.nba.com) against the PC (stats.nba.com)", "",
             "| File | Rows on both | Only GitHub | Only PC | Rows with any difference | Columns that differ |", "|---|---|---|---|---|---|"]
    for f, r in res.items():
        rows_diff = len({k for d in r["diffs"].values() for k, _, _ in d})
        cols = ", ".join(f"{c} ({len(d)})" for c, d in sorted(r["diffs"].items(), key=lambda x: -len(x[1]))) or "none"
        lines.append(f"| {f} | {r['both']} | {len(r['cloud_only'])} | {len(r['pc_only'])} | {rows_diff} | {cols} |")
    lines += ["", "### Examples (GitHub value / PC value)", ""]
    for f, r in res.items():
        for c, d in sorted(r["diffs"].items(), key=lambda x: -len(x[1])):
            lines.append(f"- {f} {c}: " + "; ".join(f"{'/'.join(k)}: {a!r} / {b!r}" for k, a, b in d[:4]))
        if r["cloud_only"][:3]:
            lines.append(f"- {f} only on GitHub, e.g. " + ", ".join("/".join(k) for k in r["cloud_only"][:3]))
        if r["pc_only"][:3]:
            lines.append(f"- {f} only on the PC, e.g. " + ", ".join("/".join(k) for k in r["pc_only"][:3]))
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cloud", default="data/nba_cloud")
    ap.add_argument("--pc", default="data/nba")
    a = ap.parse_args()
    cloud, pc = ROOT / a.cloud, ROOT / a.pc
    finals = lambda d: {r["game_id"] for r in read(d / "league_results.csv") if r.get("home_pts")}
    games = finals(cloud) & finals(pc)
    text = f"Games final on both sides: {len(games)}\n\n" + report(compare(cloud, pc, games))
    (cloud / "compare.md").write_text(text, encoding="utf-8")
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(text)
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
