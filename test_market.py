"""Meaningful feed-boundary tests; no external requests or real credentials."""
import copy
import tempfile
import time
import unittest
from unittest.mock import patch

import market
from app import money, percent, flash_background, PANEL


class MarketTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.feed = market.Feed(self.temp.name)

    def test_volume_is_aggregator_volume_never_okx_volume(self):
        self.feed.apply_tickers([{"instId": "BTC-USDT", "last": "80000", "ts": str(time.time() * 1000),
                                 "vol24h": "888888888", "volCcy24h": "999999999"}])
        row = {"id": "bitcoin", "current_price": 79900, "market_cap": 123456,
               "market_cap_rank": 1, "total_volume": 234567, "last_updated": "2026-10-03T00:00:00Z",
               "price_change_percentage_24h_in_currency": 0,
               "price_change_percentage_7d_in_currency": -5}
        with patch.object(market, "get_json", return_value=[row]):
            self.feed.fetch_market()
        snap = self.feed.snapshot()
        self.assertEqual(snap["market"]["bitcoin"]["volume"], 234567)
        self.assertEqual(snap["prices"]["BTC-USDT"]["price"], 80000)
        self.assertEqual(snap["market"]["bitcoin"]["price"], 79900)
        self.assertEqual(snap["market"]["bitcoin"]["d1"], 0)
        self.assertIn("部分", snap["market_status"])

    def test_watchlist_add_reorder_remove_and_restart(self):
        coins = copy.deepcopy(market.DEFAULT_COINS)
        coins.append({"id": "solana", "symbol": "SOL", "name": "Solana", "pair": ""})
        self.feed.set_coins(coins)
        self.assertEqual(len(market.Feed(self.temp.name).coins), 7)
        self.feed.set_coins(list(reversed(coins))[1:])
        loaded = market.Feed(self.temp.name)
        self.assertEqual(loaded.coins, list(reversed(coins))[1:])
        self.feed.set_coins([])
        self.assertEqual(market.Feed(self.temp.name).coins, [])

    def test_missing_data_is_not_zero_and_invalid_prices_are_rejected(self):
        self.feed.apply_tickers([{"instId": "BTC-USDT", "last": "NaN", "ts": "1000"},
                                 {"instId": "BTC-USDT", "last": "-1", "ts": "1000"},
                                 {"instId": "UNKNOWN-USDT", "last": "1", "ts": "1000"}])
        self.assertEqual(self.feed.prices, {})
        self.assertEqual(money(None), "—")
        self.assertEqual(percent(None), "—")
        self.assertEqual(percent(0), "+0.00%")
        self.assertIsNone(market.number("inf"))

    def test_provider_timestamp_controls_staleness(self):
        self.assertTrue(market.age_is_stale(None, 1000, 20))
        self.assertTrue(market.age_is_stale(970, 1000, 20))
        self.assertFalse(market.age_is_stale(995, 1000, 20))
        self.assertTrue(market.age_is_stale(1100, 1000, 20))

    def test_failed_market_request_keeps_last_values_and_timestamp(self):
        self.feed.market = {"bitcoin": {"volume": 42, "ts": 100}}
        self.feed.market_received = 123
        with patch.object(market, "get_json", side_effect=TimeoutError):
            with self.assertRaises(TimeoutError):
                self.feed.fetch_market()
        self.assertEqual(self.feed.market["bitcoin"]["ts"], 100)
        self.assertEqual(self.feed.market_received, 123)

    def test_secret_is_encrypted_and_reloadable(self):
        fake_key = "test-only-not-a-real-secret"
        self.feed.set_key(fake_key)
        blob = (self.feed.directory / "coingecko-key.dpapi").read_bytes()
        self.assertNotIn(fake_key.encode(), blob)
        self.assertEqual(market.Feed(self.temp.name).key, fake_key)
        self.feed.persist()
        self.assertNotIn(fake_key, (self.feed.directory / "settings.json").read_text(encoding="utf-8"))

    def test_duplicate_ids_rejected(self):
        with self.assertRaises(ValueError):
            self.feed.set_coins([market.DEFAULT_COINS[0], market.DEFAULT_COINS[0]])

    def test_refresh_interval_and_manual_cooldown(self):
        self.assertEqual(self.feed.interval, 900)
        self.assertEqual(31 * 24 * 60 // 15, 2976)
        self.feed.market_received = time.time()
        self.feed.set_interval(30)
        self.assertEqual(market.Feed(self.temp.name).interval, 1800)
        self.assertGreater(self.feed.next_market_at, time.time() + 1700)
        self.assertTrue(self.feed.request_refresh())
        self.assertEqual(self.feed.next_market_at, 0)
        self.assertFalse(self.feed.request_refresh())
        self.feed.manual_after = 0
        self.feed.market_busy = True
        self.assertFalse(self.feed.request_refresh())
        self.feed.market_busy = False
        self.feed.blocked_until = time.time() + 300
        self.assertFalse(self.feed.request_refresh())

    def test_flash_threshold_both_directions_and_stale_suppression(self):
        self.assertEqual(flash_background(10, False, 100), PANEL)
        self.assertEqual(flash_background(-10, False, 100), PANEL)
        self.assertNotEqual(flash_background(10.01, False, 100), PANEL)
        self.assertNotEqual(flash_background(-10.01, False, 100), PANEL)
        self.assertNotEqual(flash_background(11, False, 100), flash_background(-11, False, 100))
        self.assertEqual(flash_background(11, False, 101), PANEL)
        self.assertEqual(flash_background(11, True, 100), PANEL)
        self.assertEqual(flash_background(None, False, 100), PANEL)


if __name__ == "__main__":
    unittest.main(verbosity=2)
