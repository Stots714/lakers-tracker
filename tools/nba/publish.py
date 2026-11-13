"""Commit and push data files to GitHub: one at a time, always as the GitHub handle, failures logged.

Used by the live watcher (live.json and the injury report during games) and by run_update.ps1 (the full
update). Before, each had its own git commands: a failed "pull --rebase" left the folder half-rebased, so
every later run failed too, and failures were only on screen.

  python tools/nba/publish.py --message "NBA.com data update 2026-11-13" data/nba
    exit 0: pushed, or nothing new to push; exit 1: not pushed (see tools/nba/git_failures.log)
"""
import argparse
import datetime as dt
import subprocess
import sys
import time
from pathlib import Path

from lock import RunLock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
FAILURES = HERE / "git_failures.log"
IDENTITY = ("-c", "user.name=Stots714", "-c", "user.email=Stots714@users.noreply.github.com")
PUSH_TRIES = 3          # a push can be refused when the other job pushed a moment earlier
RETRY_WAIT = 20         # seconds between push attempts
LOCK_WAIT = 5 * 60      # how long to wait for the other job's publish to finish


def git(*args, root=ROOT):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)


def record(what, r, log):
    """Log one failed git command to the screen log and to git_failures.log (kept, not overwritten)."""
    text = (r.stderr or r.stdout or "").strip()
    log(f"  git {what} failed (code {r.returncode}): {text[:200]}")
    try:
        with FAILURES.open("a", encoding="utf-8") as f:
            f.write(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} git {what} (code {r.returncode})\n{text}\n\n")
    except OSError:
        pass


def recover(root=ROOT, log=print):
    """Leave the folder usable after an interrupted run: finish nothing, undo a half-done rebase or merge."""
    gd = Path(git("rev-parse", "--git-dir", root=root).stdout.strip() or ".git")
    gd = gd if gd.is_absolute() else Path(root) / gd
    if (gd / "rebase-merge").exists() or (gd / "rebase-apply").exists():
        log("  undoing a rebase left half-done by an earlier run")
        git("rebase", "--abort", root=root)
    if (gd / "MERGE_HEAD").exists():
        log("  undoing a merge left half-done by an earlier run")
        git("merge", "--abort", root=root)


def publish(paths, message, log=print, root=ROOT, lock_folder=HERE, retry_wait=RETRY_WAIT):
    """Stage paths, commit, bring in GitHub's changes, push. Returns 'pushed', 'nothing' or 'failed'."""
    lock = RunLock("publish", stale_after=15 * 60, folder=lock_folder)
    if not lock.acquire(wait=LOCK_WAIT):
        log(f"  not published: another publish is still running ({lock.describe()})")
        return "failed"
    try:
        recover(root, log)
        r = git("add", "--", *paths, root=root)
        if r.returncode != 0:
            record("add", r, log)
            return "failed"
        if git("diff", "--cached", "--quiet", root=root).returncode == 0:
            if git("status", "-sb", root=root).stdout.splitlines()[0].find("ahead") < 0:
                return "nothing"
        else:
            r = git(*IDENTITY, "commit", "-q", "-m", message, root=root)
            if r.returncode != 0:
                record("commit", r, log)
                return "failed"
        for attempt in range(PUSH_TRIES):
            # Our commit only touches data files, so where GitHub has a different version of one of them, ours is newer
            r = git(*IDENTITY, "pull", "-q", "--rebase", "-X", "theirs", root=root)
            if r.returncode != 0:
                record("pull --rebase", r, log)
                recover(root, log)
                return "failed"
            r = git("push", "-q", root=root)
            if r.returncode == 0:
                return "pushed"
            record(f"push (attempt {attempt + 1} of {PUSH_TRIES})", r, log)
            if attempt < PUSH_TRIES - 1:
                time.sleep(retry_wait)
        return "failed"  # the commit stays on this PC and goes up with the next successful push
    finally:
        lock.release()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--message", required=True)
    ap.add_argument("paths", nargs="+")
    a = ap.parse_args()
    out = publish(a.paths, a.message)
    print({"pushed": "Pushed. The site updates in a minute or two.", "nothing": "No new data to push.",
           "failed": f"Not pushed; details in {FAILURES}. The next run tries again."}[out])
    return 1 if out == "failed" else 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
