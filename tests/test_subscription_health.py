"""
Tests for the subscription-freshness report in GET /status.

EODHD accepts a subscription silently and never streams a symbol it does not
carry, so a ticker can be subscribed, the connection healthy, and no tick ever
arrive. In production two tickers went quiet for two days and nothing noticed:
the connection was up, the other 49 were fine, and no log line said otherwise.
This reports freshness per symbol rather than per connection.
"""

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.api.routes import APIRoutes
from src.candle_engine import CandleEngine
from src.storage import Storage


def ticker(symbol, minutes_ago=None, last_tick_at=...):
    """A stand-in for a TrackedTicker row.

    minutes_ago builds last_tick_at relative to now; last_tick_at sets it
    directly (pass None for a ticker that has never produced one).
    """
    if last_tick_at is ...:
        if minutes_ago is None:
            last_tick_at = None
        else:
            moment = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
            last_tick_at = moment.isoformat()
    return SimpleNamespace(symbol=symbol, last_tick_at=last_tick_at)


class TestSubscriptionHealth(unittest.TestCase):
    """APIRoutes._subscription_health"""

    def health(self, tickers, threshold=15):
        return APIRoutes._subscription_health(tickers, threshold)

    def test_empty_watchlist(self):
        result = self.health([])
        self.assertEqual(result['subscribed'], 0)
        self.assertEqual(result['ticking'], 0)
        self.assertEqual(result['silent'], 0)
        self.assertEqual(result['never_seen'], [])
        self.assertEqual(result['silent_tickers'], [])

    def test_a_ticking_watchlist_reports_no_silence(self):
        result = self.health([
            ticker('NVDA', minutes_ago=0.1),
            ticker('AVGO', minutes_ago=0.5),
            ticker('MU', minutes_ago=2),
        ])
        self.assertEqual(result['subscribed'], 3)
        self.assertEqual(result['ticking'], 3)
        self.assertEqual(result['silent'], 0)

    def test_the_production_case_two_tickers_gone_quiet(self):
        """SPCX and DASH: subscribed, connection healthy, silent for two days."""
        result = self.health([
            ticker('NVDA', minutes_ago=0.2),
            ticker('AVGO', minutes_ago=0.3),
            ticker('SPCX', minutes_ago=2880),   # two days
            ticker('DASH', minutes_ago=2880),
        ])

        self.assertEqual(result['ticking'], 2)
        self.assertEqual(result['silent'], 2)
        self.assertEqual(
            [row['ticker'] for row in result['silent_tickers']],
            ['SPCX', 'DASH']
        )
        self.assertAlmostEqual(
            result['silent_tickers'][0]['silent_minutes'], 2880, delta=1
        )

    def test_longest_silence_comes_first(self):
        result = self.health([
            ticker('A', minutes_ago=20),
            ticker('B', minutes_ago=600),
            ticker('C', minutes_ago=90),
        ])
        self.assertEqual(
            [row['ticker'] for row in result['silent_tickers']], ['B', 'C', 'A']
        )

    def test_threshold_is_the_boundary(self):
        tickers = [ticker('A', minutes_ago=14.9), ticker('B', minutes_ago=15.1)]
        result = self.health(tickers, threshold=15)
        self.assertEqual(result['ticking'], 1)
        self.assertEqual(result['silent'], 1)
        self.assertEqual(result['silent_tickers'][0]['ticker'], 'B')
        self.assertEqual(result['silence_threshold_minutes'], 15)

    def test_threshold_is_configurable(self):
        tickers = [ticker('A', minutes_ago=30)]
        self.assertEqual(self.health(tickers, threshold=60)['silent'], 0)
        self.assertEqual(self.health(tickers, threshold=15)['silent'], 1)

    def test_a_ticker_that_never_ticked_is_listed_separately(self):
        """No last_tick_at at all: a subscription that never produced anything."""
        result = self.health([
            ticker('NVDA', minutes_ago=1),
            ticker('NEWLY_ADDED', minutes_ago=None),
        ])
        self.assertEqual(result['never_seen'], ['NEWLY_ADDED'])
        self.assertEqual(result['ticking'], 1)
        self.assertEqual(result['silent'], 0)
        self.assertEqual(result['silent_tickers'], [])

    def test_never_seen_is_sorted(self):
        result = self.health([
            ticker('ZZZ', minutes_ago=None),
            ticker('AAA', minutes_ago=None),
        ])
        self.assertEqual(result['never_seen'], ['AAA', 'ZZZ'])

    def test_an_unparsable_timestamp_counts_as_never_seen(self):
        """A bad value must not take the endpoint down."""
        result = self.health([ticker('ODD', last_tick_at='not a timestamp')])
        self.assertEqual(result['never_seen'], ['ODD'])
        self.assertEqual(result['silent'], 0)

    def test_a_naive_timestamp_is_read_as_utc(self):
        """Older rows may carry no timezone; they must still be comparable."""
        naive = (datetime.now(timezone.utc) - timedelta(minutes=60)) \
            .replace(tzinfo=None).isoformat()
        result = self.health([ticker('OLD', last_tick_at=naive)])
        self.assertEqual(result['silent'], 1)
        self.assertAlmostEqual(
            result['silent_tickers'][0]['silent_minutes'], 60, delta=1
        )

    def test_counts_always_add_up(self):
        tickers = [
            ticker('A', minutes_ago=1), ticker('B', minutes_ago=100),
            ticker('C', minutes_ago=None), ticker('D', minutes_ago=0.2),
        ]
        result = self.health(tickers)
        self.assertEqual(
            result['ticking'] + result['silent'] + len(result['never_seen']),
            result['subscribed']
        )


class TestTickingWithoutCandles(unittest.TestCase):
    """Ticks arriving with no candle to show for them.

    On 2 November 2026 tickers looked alive for seven hours, because bad ticks
    still moved last_tick_at, while not one candle was written. Nothing in
    /status could show that: it compared tick times with the clock and never
    with the candles.
    """

    @staticmethod
    def _row(symbol, tick_minutes_ago, candle_minutes_ago=None):
        now = datetime.now(timezone.utc)
        row = ticker(symbol, minutes_ago=tick_minutes_ago)
        row.last_candle_at = (
            None if candle_minutes_ago is None
            else (now - timedelta(minutes=candle_minutes_ago)).isoformat()
        )
        return row

    def health(self, tickers, threshold=15, interval=1):
        return APIRoutes._subscription_health(tickers, threshold, interval)

    def test_a_healthy_ticker_is_not_listed(self):
        result = self.health([self._row('NVDA', 0.1, candle_minutes_ago=0.5)])
        self.assertEqual(result['ticking_without_candles'], [])

    def test_the_production_case_ticks_but_no_candle_for_hours(self):
        result = self.health([
            self._row('NVDA', 0.1, candle_minutes_ago=2 * 24 * 60),
            self._row('AMD', 0.1, candle_minutes_ago=1),
        ])

        rows = result['ticking_without_candles']
        self.assertEqual([row['ticker'] for row in rows], ['NVDA'])
        self.assertAlmostEqual(rows[0]['candle_lag_minutes'], 2 * 24 * 60, delta=1)
        self.assertEqual(result['ticking'], 2, "it is a subset of ticking, not a new state")

    def test_a_ticker_with_no_candle_at_all_is_listed(self):
        rows = self.health([self._row('NVDA', 0.1)])['ticking_without_candles']
        self.assertEqual(rows[0]['ticker'], 'NVDA')
        self.assertIsNone(rows[0]['candle_lag_minutes'])
        self.assertIsNone(rows[0]['last_candle_at'])

    def test_a_silent_ticker_is_not_listed_here(self):
        result = self.health([self._row('NVDA', 60, candle_minutes_ago=60)])
        self.assertEqual(result['ticking_without_candles'], [])
        self.assertEqual(result['silent'], 1)

    def test_a_silent_ticker_shows_its_last_candle(self):
        row = self._row('KLAC', 60, candle_minutes_ago=90)
        result = self.health([row])
        self.assertEqual(
            result['silent_tickers'][0]['last_candle_at'], row.last_candle_at
        )

    def test_a_long_candle_interval_is_not_mistaken_for_a_gap(self):
        """A 60-minute candle's timestamp is its start, up to an hour before a tick."""
        tickers = [self._row('NVDA', 0.1, candle_minutes_ago=50)]
        self.assertEqual(
            self.health(tickers, interval=60)['ticking_without_candles'], []
        )
        self.assertEqual(
            len(self.health(tickers, interval=1)['ticking_without_candles']), 1
        )

    def test_longest_gap_comes_first(self):
        rows = self.health([
            self._row('A', 0.1, candle_minutes_ago=30),
            self._row('B', 0.1, candle_minutes_ago=300),
            self._row('C', 0.1),
        ])['ticking_without_candles']
        self.assertEqual([row['ticker'] for row in rows], ['C', 'B', 'A'])

    def test_an_unparsable_candle_time_does_not_take_the_endpoint_down(self):
        row = ticker('ODD', minutes_ago=0.1)
        row.last_candle_at = 'not a timestamp'
        rows = self.health([row])['ticking_without_candles']
        self.assertIsNone(rows[0]['candle_lag_minutes'])


class TestLastCandleAtFromStorage(unittest.TestCase):
    """last_candle_at as the real SQLite storage and engine produce it."""

    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
        self.temp_db.close()
        self.storage = Storage(self.temp_db.name)
        self.storage.add_ticker('ONT')
        self.engine = CandleEngine(self.storage, interval_minutes=1, max_candles=100)

    def tearDown(self):
        os.unlink(self.temp_db.name)

    def _stored(self):
        return self.storage.get_tickers()[0]

    def test_no_candle_yet(self):
        self.assertIsNone(self._stored().last_candle_at)
        self.assertEqual(self._stored().candle_count, 0)

    def test_an_unfinished_candle_counts(self):
        """A quiet stock's first trade in an hour must not look like a gap.

        Its candle stays unfinished until the minute ends, and if only finished
        candles counted, the ticker would be listed for that whole minute.
        """
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        self.engine.process_tick('ONT', 15.2, 100, now_ms)
        self.engine.flush_pending_candle_writes()

        stored = self._stored()
        moment = datetime.fromisoformat(stored.last_candle_at)
        self.assertLess(
            (datetime.now(timezone.utc) - moment).total_seconds(), 120
        )
        self.assertEqual(stored.candle_count, 0, "candle_count still means finished ones")

    def test_a_finished_candle_counts_and_the_count_stays_right(self):
        bucket = int(datetime.now(timezone.utc).timestamp()) // 60 * 60 - 60
        self.engine.process_tick('ONT', 15.2, 100, bucket * 1000)
        self.engine.close_due_candles(now_timestamp=bucket + 120, grace_seconds=2.0)
        self.engine.flush_pending_candle_writes()

        stored = self._stored()
        self.assertEqual(stored.candle_count, 1)
        self.assertEqual(
            stored.last_candle_at,
            datetime.fromtimestamp(bucket, tz=timezone.utc).isoformat()
        )

    def test_single_ticker_lookup_agrees(self):
        bucket = int(datetime.now(timezone.utc).timestamp()) // 60 * 60 - 60
        self.engine.process_tick('ONT', 15.2, 100, bucket * 1000)
        self.engine.close_due_candles(now_timestamp=bucket + 120, grace_seconds=2.0)
        self.engine.flush_pending_candle_writes()

        single = self.storage.get_ticker('ONT')
        self.assertEqual(single.last_candle_at, self._stored().last_candle_at)
        self.assertEqual(single.candle_count, 1)

    def test_it_is_part_of_the_ticker_response(self):
        self.assertIn('last_candle_at', self._stored().to_dict())


if __name__ == '__main__':
    unittest.main()
