"""
Tests for the subscription-freshness report in GET /status.

EODHD accepts a subscription silently and never streams a symbol it does not
carry, so a ticker can be subscribed, the connection healthy, and no tick ever
arrive. In production two tickers went quiet for two days and nothing noticed:
the connection was up, the other 49 were fine, and no log line said otherwise.
This reports freshness per symbol rather than per connection.
"""

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.api.routes import APIRoutes


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


if __name__ == '__main__':
    unittest.main()
