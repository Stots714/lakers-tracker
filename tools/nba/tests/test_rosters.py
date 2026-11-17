"""Offline tests for the roster comparison (official moves). No network.

  python -m unittest discover -s tools/nba/tests -v        (needs nba_api importable, like the other league tests)
"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def roster(team, *players):
    return [{"team": team, "player_id": str(pid), "player": name, "num": "", "position": "", "how_acquired": ""} for pid, name in players]


LAL = [(1, "Austin Reaves"), (2, "Luka Doncic"), (3, "Quentin Grimes")] + [(100 + i, f"Laker {i}") for i in range(9)]
ATL = [(4, "Trae Young"), (5, "Jalen Johnson")] + [(200 + i, f"Hawk {i}") for i in range(9)]


class FakeEndpoint:
    def __init__(self, team_rows):
        self.common_team_roster = self
        self._rows = team_rows

    def get_dict(self):
        heads = ["TeamID", "PLAYER", "PLAYER_ID", "NUM", "POSITION", "HOW_ACQUIRED"]
        return {"headers": heads, "data": [[0, r["player"], int(r["player_id"]), "", "", r["how_acquired"]] for r in self._rows]}


class Rosters(unittest.TestCase):
    def setUp(self):
        try:
            import league
            import rows
        except Exception as e:
            self.skipTest(f"nba_api not importable here: {e}")
        self.league, self.rows = league, rows
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)

    def fetch_for(self, by_team, broken=()):
        ids = {"LAL": 1, "ATL": 2}

        def fetch(cls, team_id, season):
            team = next(t for t, i in ids.items() if i == team_id)
            if team in broken:
                raise ConnectionError("timed out")
            return FakeEndpoint(by_team[team])
        return fetch, [("LAL", 1), ("ATL", 2)]

    def save(self, data, name, columns=None):
        return self.rows.write(self.dir / name, data, columns)

    def update(self, by_team, date, broken=()):
        fetch, teams = self.fetch_for(by_team, broken)
        return self.league.update_rosters(fetch, "2026-27", self.save, self.dir, date, teams)

    def moves(self):
        return [(m["player"], m["kind"], m["from_team"], m["to_team"], m["date"]) for m in self.rows.read(self.dir / "roster_moves.csv")]

    def test_first_read_records_no_moves(self):
        n, extra = self.update({"LAL": roster("LAL", *LAL), "ATL": roster("ATL", *ATL)}, "2026-11-17")
        self.assertEqual(n, 0)
        self.assertTrue(extra["first_read"])
        self.assertEqual(len(self.rows.read(self.dir / "league_rosters.csv")), len(LAL) + len(ATL))

    def test_trade_shows_as_moved_both_ways(self):
        self.update({"LAL": roster("LAL", *LAL), "ATL": roster("ATL", *ATL)}, "2026-12-14")
        lal = [p for p in LAL if p[0] != 1] + [(4, "Trae Young")]
        atl = [p for p in ATL if p[0] != 4] + [(1, "Austin Reaves")]
        n, _ = self.update({"LAL": roster("LAL", *lal), "ATL": roster("ATL", *atl)}, "2026-12-16")
        self.assertEqual(n, 2)
        self.assertEqual(sorted(self.moves()), [("Austin Reaves", "moved", "LAL", "ATL", "2026-12-16"), ("Trae Young", "moved", "ATL", "LAL", "2026-12-16")])

    def test_waived_and_signed(self):
        self.update({"LAL": roster("LAL", *LAL), "ATL": roster("ATL", *ATL)}, "2026-11-17")
        self.update({"LAL": roster("LAL", *LAL[1:], (300, "New Signee")), "ATL": roster("ATL", *ATL)}, "2026-11-18")
        self.assertEqual(sorted(self.moves()), [("Austin Reaves", "left", "LAL", "", "2026-11-18"), ("New Signee", "joined", "", "LAL", "2026-11-18")])

    def test_one_unreadable_team_changes_nothing(self):
        self.update({"LAL": roster("LAL", *LAL), "ATL": roster("ATL", *ATL)}, "2026-11-17")
        with self.assertRaises(RuntimeError):
            self.update({"LAL": roster("LAL", *LAL[1:]), "ATL": roster("ATL", *ATL)}, "2026-11-18", broken=("ATL",))
        self.assertEqual(self.moves(), [])
        self.assertEqual(len(self.rows.read(self.dir / "league_rosters.csv")), len(LAL) + len(ATL))

    def test_short_roster_answer_is_treated_as_unreadable(self):
        self.update({"LAL": roster("LAL", *LAL), "ATL": roster("ATL", *ATL)}, "2026-11-17")
        with self.assertRaises(RuntimeError):
            self.update({"LAL": roster("LAL", *LAL[:3]), "ATL": roster("ATL", *ATL)}, "2026-11-18")
        self.assertEqual(self.moves(), [])

    def test_same_day_rerun_does_not_duplicate(self):
        self.update({"LAL": roster("LAL", *LAL), "ATL": roster("ATL", *ATL)}, "2026-11-17")
        new = {"LAL": roster("LAL", *LAL[1:]), "ATL": roster("ATL", *ATL)}
        self.update(new, "2026-11-18")
        (self.dir / "league_rosters.csv").unlink()
        self.save(roster("LAL", *LAL) + roster("ATL", *ATL), "league_rosters.csv", self.league.ROSTER_COLS)
        self.update(new, "2026-11-18")
        self.assertEqual(len(self.moves()), 1)


if __name__ == "__main__":
    unittest.main()
