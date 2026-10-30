"""Plain-Python tables (lists of dicts) for NBA.com data sets and CSV files.

No pandas or numpy: their compiled files are unsigned, and Windows Smart App Control can refuse to
load them ("An Application Control policy has blocked this file"). Everything here is standard library.
"""
import csv


def from_dataset(ds):
    """An nba_api data set as (rows, columns): rows are dicts keyed by column name."""
    d = ds.get_dict() or {}
    heads = d.get("headers") or []
    if heads and not isinstance(heads[0], str):  # multi-level headers: use the last level's column names
        last = heads[-1]
        heads = [""] * last.get("columnsToSkip", 0) + [n for n in last["columnNames"] for _ in range(last.get("columnSpan", 1))]
    return [dict(zip(heads, r)) for r in (d.get("data") or [])], list(heads)


def write(path, rows, columns=None):
    """Write rows to a UTF-8 CSV; None becomes an empty cell. Returns the row count."""
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = list(columns) if columns else (list(rows[0].keys()) if rows else [])
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in cols})
    return len(rows)


def read(path):
    """A CSV as a list of dicts (all values are strings), or [] if the file is not there."""
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))
