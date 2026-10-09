"""Regression coverage for the four audited reliability fixes. No external API."""
import copy
import json
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

from app import WatchWindow, money
from market import Feed, DEFAULT_COINS, atomic_json
from state_guard import clean_coins, normalize_cache, normalize_settings


class StateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)

    def test_backup_recovers_watchlist_when_primary_json_is_broken(self):
        feed = Feed(self.path)
        chosen = list(reversed(copy.deepcopy(DEFAULT_COINS)))[:3]
        feed.set_coins(chosen)
        (self.path/"settings.json").write_text("{broken", encoding="utf-8")
        restored = Feed(self.path)
        self.assertEqual(restored.coins, chosen)
        self.assertIn("恢复", restored.startup_notice)
        restored.persist()
        self.assertEqual(Feed(self.path).coins, chosen)

    def test_bad_fields_restore_individually_and_keep_good_coin(self):
        raw = {"coins": [DEFAULT_COINS[0], None, {"id": []}], "orb_position": ["bad"],
               "geometry": {}, "opacity": "NaN", "topmost": "false", "refresh_minutes": True,
               "unexpected_credential": "do-not-persist"}
        atomic_json(self.path/"settings.json", raw)
        feed = Feed(self.path)
        self.assertEqual(feed.coins, [DEFAULT_COINS[0]])
        self.assertEqual(feed.settings["opacity"], 100)
        self.assertEqual(feed.settings["geometry"], "1180x800+80+80")
        self.assertNotIn("orb_position", feed.settings)
        self.assertEqual(feed.interval, 900)
        feed.persist()
        self.assertNotIn("do-not-persist", (self.path/"settings.last-good.json").read_text())

    def test_explicit_empty_watchlist_is_preserved(self):
        feed = Feed(self.path)
        feed.set_coins([])
        self.assertEqual(Feed(self.path).coins, [])

    def test_invalid_or_future_schema_uses_backup(self):
        feed = Feed(self.path)
        feed.set_coins([DEFAULT_COINS[1]])
        for raw in ([], None, {"schema_version": 99}, {"coins": [None]}):
            with self.subTest(raw=raw):
                atomic_json(self.path/"settings.json", raw)
                self.assertEqual(Feed(self.path).coins, [DEFAULT_COINS[1]])

    def test_invalid_cache_indices_and_rows_do_not_block_startup(self):
        now = time.time()
        atomic_json(self.path/"market-cache.json", {"ids": None, "received": now,
            "market": {"bitcoin": [], "ethereum": {"price": 2000, "ts": now}}})
        feed = Feed(self.path)
        self.assertNotIn("bitcoin", feed.market)
        self.assertEqual(feed.market["ethereum"]["price"], 2000)
        self.assertLess(feed.next_market_at, now)
        self.assertTrue(feed.startup_notice)

    def test_cache_sanitizes_numbers_but_keeps_negative_changes(self):
        now = time.time()
        cache, _ = normalize_cache({"ids": ["bitcoin"], "received": now-1, "market": {
            "bitcoin": {"ts": now-1, "price": "NaN", "cap": -10, "volume": True,
                        "rank": 1.5, "d1": -12, "d7": None}}}, {"bitcoin"}, now)
        row = cache["market"]["bitcoin"]
        self.assertTrue(all(row[k] is None for k in ("price", "cap", "volume", "rank", "d7")))
        self.assertEqual(row["d1"], -12)
        self.assertEqual(cache["received"], 0)

    def test_wrong_asset_with_same_symbol_cannot_get_btc_pair(self):
        instruments = [{"instId": "BTC-USDT", "baseCcy": "BTC"}]
        self.assertEqual(Feed.verified_pairs("bitcoin", instruments), ["BTC-USDT"])
        self.assertEqual(Feed.verified_pairs("unrelated-bitcoin", instruments), [])
        self.assertEqual(Feed.verified_pairs("bitcoin", []), [])
        spoof = {"id": "unrelated-bitcoin", "symbol": "BTC", "name": "Different asset", "pair": "BTC-USDT"}
        feed = Feed(self.path)
        feed.set_coins([spoof])
        self.assertEqual(feed.coins[0]["pair"], "")
        self.assertEqual(Feed(self.path).coins[0]["pair"], "")

    def test_existing_unverified_pair_is_migrated_to_global_price(self):
        atomic_json(self.path/"settings.json", {"coins": [{"id": "other-token", "symbol": "BTC",
                    "name": "Other", "pair": "BTC-USDT"}]})
        feed = Feed(self.path)
        self.assertEqual(feed.coins[0]["pair"], "")
        self.assertIn("未核验", feed.startup_notice)

    def test_current_six_verified_pairs_are_unchanged(self):
        self.assertEqual(clean_coins(DEFAULT_COINS), DEFAULT_COINS)

    def test_tiny_prices_are_nonzero_and_keep_sign(self):
        for value in (1e-12, 1e-9, 1.23456e-8, 1e-6, -1e-9):
            with self.subTest(value=value):
                shown = money(value)
                self.assertNotEqual(float(shown), 0)
                self.assertAlmostEqual(float(shown)/value, 1, places=5)
        self.assertEqual(money(0), "0")
        self.assertEqual(money(None), "—")
        self.assertEqual(money(84823.6), "84,823.60")
        self.assertEqual(money(.4867), "0.4867")

    def test_bad_search_rows_do_not_reach_ui(self):
        with patch("market.get_json", return_value={"coins": [None, {"id": []},
                {"id": "bitcoin", "symbol": "BTC", "name": "Bitcoin"}]}):
            result = Feed(self.path).search("btc")
        self.assertEqual([r["id"] for r in result], ["bitcoin"])


class UiRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.feed = Feed(self.temp.name)
        now = time.time()
        self.feed.prices = {c["pair"]: {"price": 100+i, "ts": now} for i, c in enumerate(self.feed.coins)}
        self.feed.market = {c["id"]: {"price": 100+i, "cap": 1000000, "rank": i+1,
            "volume": 1000, "d1": 1, "d7": -2, "ts": now} for i, c in enumerate(self.feed.coins)}
        self.root = tk.Tk()
        self.window = WatchWindow(self.root, self.feed, orb_mode=False)
        self.root.withdraw()

    def tearDown(self):
        self.window.close()
        self.temp.cleanup()

    def assert_one_tick(self):
        jobs = self.root.tk.call("after", "info")
        self.assertIn(self.window._tick_job, jobs)
        self.assertEqual(len(jobs), 1)

    def test_bad_callback_does_not_block_next_callback_or_price_update(self):
        done = []
        def bad():
            raise RuntimeError("do-not-log-payload")
        self.window.events.put(bad)
        self.window.events.put(lambda: done.append(True))
        self.feed.prices["ETH-USDT"]["price"] = 4321
        self.window.tick()
        self.assertEqual(done, [True])
        self.assertEqual(self.window.rows["ethereum"][1][0].cget("text"), "4,321.00")
        self.assertIn("异常", self.window.status.cget("text"))
        self.assert_one_tick()
        log = (Path(self.temp.name)/"runtime-errors.log").read_text(encoding="utf-8")
        self.assertIn("RuntimeError", log)
        self.assertNotIn("do-not-log-payload", log)

    def test_bad_row_is_hidden_other_rows_update_then_bad_row_recovers(self):
        original = self.feed.market["bitcoin"]
        self.feed.market["bitcoin"] = []
        self.feed.prices["ETH-USDT"]["price"] = 4321
        self.window.tick()
        self.assertEqual(self.window.rows["bitcoin"][1][0].cget("text"), "—")
        self.assertEqual(self.window.rows["bitcoin"][1][1].cget("text"), "显示异常")
        self.assertEqual(self.window.rows["ethereum"][1][0].cget("text"), "4,321.00")
        self.feed.market["bitcoin"] = original
        self.window.tick()
        self.assertEqual(self.window.rows["bitcoin"][1][0].cget("text"), "100.00")
        self.assert_one_tick()

    def test_snapshot_failure_retries_and_recovers_with_one_timer(self):
        with patch.object(self.feed, "snapshot", side_effect=ValueError("bad snapshot")):
            self.window.tick()
        self.assertEqual(self.window.rows["ethereum"][1][0].cget("text"), "—")
        self.assert_one_tick()
        self.window.tick()
        self.assertEqual(self.window.rows["ethereum"][1][0].cget("text"), "101.00")
        self.assert_one_tick()

    def test_identical_errors_are_log_rate_limited(self):
        for _ in range(5):
            self.window.report_ui_error("tk-callback", ValueError("private payload"))
        text = (Path(self.temp.name)/"runtime-errors.log").read_text(encoding="utf-8")
        self.assertEqual(text.count("ValueError"), 1)
        self.assertNotIn("private payload", text)

    def test_real_timer_continues_after_callback_failure(self):
        def bad():
            raise RuntimeError("synthetic callback failure")
        self.window.events.put(bad)
        self.root.after(50, lambda: self.feed.prices["ETH-USDT"].update(price=5432))
        self.root.after(1150, self.root.quit)
        self.root.mainloop()
        self.assertEqual(self.window.rows["ethereum"][1][0].cget("text"), "5,432.00")
        self.assert_one_tick()


if __name__ == "__main__":
    unittest.main(verbosity=2)
