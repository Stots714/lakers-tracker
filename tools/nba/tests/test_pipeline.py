"""Offline tests for the who-writes-what switch (data/pipeline.json). No network."""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pipeline  # noqa: E402


class Switch(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        (self.root / "data").mkdir()

    def put(self, obj):
        (self.root / "data/pipeline.json").write_text(obj if isinstance(obj, str) else json.dumps(obj), encoding="utf-8")

    def test_missing_or_broken_file_means_the_pc_writes(self):
        self.assertTrue(pipeline.pc_writes(self.root))
        self.put("{not json")
        self.assertTrue(pipeline.pc_writes(self.root))
        self.put({"data_writer": "nobody"})
        self.assertTrue(pipeline.pc_writes(self.root))

    def test_cloud_takes_over(self):
        self.put({"data_writer": "cloud", "odds_pretip": "tick"})
        self.assertFalse(pipeline.pc_writes(self.root))
        self.assertEqual(pipeline.settings(self.root)["odds_pretip"], "tick")

    def test_live_watcher_defers_pretip_reads_to_the_tick(self):
        try:
            import live
        except Exception as e:
            self.skipTest(f"nba_api not importable here: {e}")
        self.put({"data_writer": "pc", "odds_pretip": "tick"})
        ready = self.root / "odds_status.json"; ready.write_text("{}")
        saved = live.ROOT
        live.ROOT = self.root
        self.addCleanup(setattr, live, "ROOT", saved)
        calls = []
        live._gh_missing = False
        import datetime as dt
        now = dt.datetime(2026, 11, 18, 0, 10, tzinfo=dt.timezone.utc)
        live.ask_for_odds([{"tip_utc": "2026-11-18T00:30:00Z"}], [], now=now, run=lambda *a, **k: calls.append(1), ready=ready)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
