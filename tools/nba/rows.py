"""Plain-Python tables (lists of dicts) for NBA.com data sets and CSV files.

No pandas or numpy: their compiled files are unsigned, and Windows Smart App Control can refuse to
load them ("An Application Control policy has blocked this file"). Everything here is standard library.

Every file is written whole to a temporary file next to it and then swapped in, so a crash, a full disk or
a second run can never leave a half-written file for the site to read.
"""
import csv
import io
import os
import time


def from_dataset(ds):
    """An nba_api data set as (rows, columns): rows are dicts keyed by column name."""
    d = ds.get_dict() or {}
    heads = d.get("headers") or []
    if heads and not isinstance(heads[0], str):  # multi-level headers: use the last level's column names
        last = heads[-1]
        heads = [""] * last.get("columnsToSkip", 0) + [n for n in last["columnNames"] for _ in range(last.get("columnSpan", 1))]
    return [dict(zip(heads, r)) for r in (d.get("data") or [])], list(heads)


def replace_text(path, text):
    """Write text to path atomically: a temporary file in the same folder, flushed to disk, then renamed over the old one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    for attempt in range(5):  # Windows: a reader (antivirus, an editor) can hold the old file for a moment
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.5 * (attempt + 1))


def write(path, rows, columns=None):
    """Write rows to a UTF-8 CSV (atomically); None becomes an empty cell. Returns the row count."""
    cols = list(columns) if columns else (list(rows[0].keys()) if rows else [])
    buf = io.StringIO(newline="")
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in cols})
    replace_text(path, buf.getvalue())
    return len(rows)


def read(path):
    """A CSV as a list of dicts (all values are strings), or [] if the file is not there."""
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))
