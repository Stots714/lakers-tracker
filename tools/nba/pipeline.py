"""Who writes what: data/pipeline.json, shared by the PC's tasks and the GitHub ticks (tools/cloud/tick.py).

  data_writer  "pc": the PC's tasks write data/nba/ (the GitHub ticks write data/nba_cloud/ for the side-by-side check)
               "cloud": the GitHub ticks write data/nba/, and the PC's update and live watcher stand down
  odds_pretip  "pc": the live watcher starts the pre-tip betting-line read; "tick": the GitHub ticks do it

One file, so the switch (and switching back) is one change. A missing or unreadable file means the PC writes.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULTS = {"data_writer": "pc", "odds_pretip": "pc"}


def settings(root=ROOT):
    try:
        got = json.loads((Path(root) / "data" / "pipeline.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        got = {}
    return {k: got.get(k, v) if got.get(k) in ("pc", "cloud", "tick") else v for k, v in DEFAULTS.items()}


def pc_writes(root=ROOT):
    return settings(root)["data_writer"] == "pc"
