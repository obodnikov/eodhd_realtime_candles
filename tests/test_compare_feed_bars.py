"""
Tests for the same-exchange comparison in analysis/compare_with_intraday.py.

Comparing against the Intraday API measures the service and the feed together,
because that reference covers every exchange. EODHD's minute bars come from the
same exchange feed the service consumes, so a minute they have and the service
does not is a loss inside the service.
"""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / 'analysis' / 'compare_with_intraday.py'
spec = importlib.util.spec_from_file_location('compare_with_intraday', SCRIPT)
compare_tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compare_tool)

MINUTE = 1793642400  # a minute boundary


class TestFeedBarsToMinutes:

    def test_milliseconds_become_minute_seconds(self):
        bars = [{'t': MINUTE * 1000, 'v': 223}, {'t': (MINUTE + 60) * 1000, 'v': 418}]

        assert compare_tool.feed_bars_to_minutes(bars) == {
            MINUTE: 223, MINUTE + 60: 418
        }

    def test_a_missing_volume_counts_as_zero(self):
        assert compare_tool.feed_bars_to_minutes([{'t': MINUTE * 1000}]) == {MINUTE: 0}

    def test_no_bars(self):
        assert compare_tool.feed_bars_to_minutes([]) == {}


class TestFeedWindow:

    def test_runs_from_the_oldest_bar_to_the_newest_one(self):
        """The provider does not return a ticker's latest bar, so nothing past it is checked."""
        bars = {MINUTE: 1, MINUTE + 120: 1}
        now = MINUTE + 600

        grid = compare_tool.feed_window(bars, now, settle_seconds=120)

        assert grid[0] == MINUTE
        assert grid[-1] == MINUTE + 120, "the newest bar is the last minute compared"

    def test_a_bar_still_settling_is_left_out(self):
        bars = {MINUTE: 1, MINUTE + 120: 1, MINUTE + 480: 1}
        now = MINUTE + 600

        grid = compare_tool.feed_window(bars, now, settle_seconds=120)

        assert grid[-1] == MINUTE + 420, "the minute ending at now - 120s is the last"

    def test_a_minute_still_closing_is_left_out(self):
        """The service closes a candle shortly after the minute; it may not be there yet."""
        bars = {MINUTE: 1}
        now = MINUTE + 61

        assert compare_tool.feed_window(bars, now, settle_seconds=120) == []

    def test_no_bars_means_nothing_to_compare(self):
        assert compare_tool.feed_window({}, MINUTE, settle_seconds=120) == []


class TestComparisonOverTheWindow:

    def test_a_minute_the_feed_has_and_we_do_not_is_counted_as_lost(self):
        theirs = {MINUTE: 100, MINUTE + 60: 200, MINUTE + 120: 300}
        ours = {MINUTE: 100, MINUTE + 120: 300}
        grid = compare_tool.minute_grid(MINUTE, MINUTE + 180)

        result = compare_tool.compare(ours, theirs, grid)

        assert result['traded_elsewhere'] == 1
        assert result['volume_share'] == pytest.approx(400 / 600 * 100)

    def test_our_candle_after_the_newest_bar_is_not_counted_as_extra(self):
        """After the close the provider withheld each ticker's last minute; ours had volume."""
        theirs = {MINUTE: 100, MINUTE + 60: 200}
        ours = {MINUTE: 100, MINUTE + 60: 200, MINUTE + 120: 1755}
        grid = compare_tool.feed_window(theirs, MINUTE + 3600, settle_seconds=120)

        result = compare_tool.compare(ours, theirs, grid)

        assert result['our_minutes'] == 2
        assert result['volume_share'] == pytest.approx(100)
