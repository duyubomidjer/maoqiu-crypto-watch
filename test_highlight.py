"""Exercise the real Tk image lifecycle and the exact >10% pulse rule offline."""
import gc
from pathlib import Path
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import patch

from app import WatchWindow
from market import Feed


class HighlightRenderingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.feed = Feed(self.temp.name)
        self.now = int(time.time())//2*2
        self.feed.market = {c["id"]: dict(price=100, cap=1000000, rank=i+1, volume=1000,
            d1=12, d7=-12, ts=self.now) for i, c in enumerate(self.feed.coins)}
        self.feed.prices = {c["pair"]: dict(price=100, ts=self.now) for c in self.feed.coins}
        self.root = tk.Tk()
        with patch("app.time.time", return_value=self.now):
            self.window = WatchWindow(self.root, self.feed, orb_mode=False)
            self.root.geometry("1180x800+80+80")
            self.root.update_idletasks()
        self.cells = [self.window.rows["bitcoin"][column][0].master for column in (2, 3)]

    def tearDown(self):
        self.window.close()
        self.temp.cleanup()

    def tick_at(self, phase):
        with patch("app.time.time", return_value=self.now+phase):
            self.window.tick()
            self.root.update_idletasks()

    def assert_healthy(self):
        self.assertEqual(self.window.ui_error_until, 0)
        self.assertFalse((Path(self.temp.name)/"runtime-errors.log").exists())
        for row in self.window.rows.values():
            self.assertFalse(any(detail.cget("text") == "显示异常" for _, detail in row))

    def test_exact_threshold_both_columns_and_stale_or_missing(self):
        for value, stale, should_light in (
            (9.99, False, False), (-9.99, False, False), (10, False, False), (-10, False, False),
            (10.01, False, True), (-10.01, False, True), (12, False, True), (-12, False, True),
            (None, False, False), ("NaN", False, False), (12, True, False), (-12, True, False)):
            with self.subTest(value=value, stale=stale):
                self.feed.market["bitcoin"].update(d1=value, d7=value, ts=self.now-2000 if stale else self.now)
                self.tick_at(0)
                for cell in self.cells:
                    self.assertEqual(cell.itemcget(cell.alert, "state"), "normal" if should_light else "hidden")
                    if should_light:
                        self.assertIs(cell.active_alert_photo, cell.alert_photos[value > 0])
                    else:
                        self.assertEqual(cell.itemcget(cell.alert, "image"), "")
                self.tick_at(1)
                for cell in self.cells:
                    self.assertEqual(cell.itemcget(cell.alert, "state"), "hidden")
                self.assert_healthy()

    def test_only_the_exceeding_column_pulses_and_stops_after_recovery(self):
        row = self.feed.market["bitcoin"]
        for d1, d7, expected in ((12, 2, (True, False)), (2, -12, (False, True)), (10, -10, (False, False))):
            row.update(d1=d1, d7=d7)
            self.tick_at(0)
            for cell, active in zip(self.cells, expected):
                self.assertEqual(cell.itemcget(cell.alert, "state"), "normal" if active else "hidden")
            self.assert_healthy()

    def test_lit_images_survive_relayout_gc_and_hidden_phase_resize(self):
        for phase in (0, 1, 0):
            self.tick_at(phase)
            for geometry in ("1260x820+80+80", "1100x740+80+80", "1180x800+80+80"):
                with self.subTest(phase=phase, geometry=geometry):
                    self.root.geometry(geometry)
                    self.root.update_idletasks()
                    gc.collect()
                    self.root.update_idletasks()
                    for cell in self.cells:
                        name = cell.itemcget(cell.alert, "image")
                        if phase == 0:
                            self.assertTrue(name)
                            self.assertIn(name, self.root.tk.call("image", "names"))
                            self.assertEqual(cell.itemcget(cell.alert, "state"), "normal")
                        else:
                            self.assertEqual(name, "")
                            self.assertIsNone(cell.active_alert_photo)
                    self.assert_healthy()


if __name__ == "__main__":
    unittest.main(verbosity=2)
