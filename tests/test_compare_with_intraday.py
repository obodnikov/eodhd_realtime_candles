"""
Tests for the Intraday totals in analysis/compare_with_intraday.py.

Intraday can have nothing yet for one ticker's day while the other tickers'
days are there: on 2026-11-04 six of 50 tickers had no data for 2026-11-03.
Such a ticker must not enter the totals, or its volume is counted against
nothing and its empty minutes read as minutes nobody traded.
"""

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / 'analysis' / 'compare_with_intraday.py'
spec = importlib.util.spec_from_file_location('compare_with_intraday', SCRIPT)
compare_tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compare_tool)

MINUTE = 1793642400  # a minute boundary
GRID = compare_tool.minute_grid(MINUTE, MINUTE + 240)


def row(ticker: str, ours: dict, theirs: dict) -> dict:
    """One ticker's comparison over GRID, as the script builds it."""
    result = compare_tool.compare(ours, theirs, GRID)
    result['ticker'] = ticker
    return result


class TestSummarize:

    def test_a_ticker_without_reference_is_left_out_of_the_totals(self):
        compared = row('NVDA', {MINUTE: 30, MINUTE + 60: 10},
                       {MINUTE: 1000, MINUTE + 60: 500, MINUTE + 120: 200})
        no_reference = row('CRWV', {MINUTE: 400, MINUTE + 180: 600}, {})

        summary = compare_tool.summarize([compared, no_reference])

        assert summary['our_volume'] == 40
        assert summary['their_volume'] == 1700
        assert summary['our_empty'] == 2
        assert summary['traded_elsewhere'] == 1
        assert summary['no_reference'] == ['CRWV']

    def test_a_ticker_with_reference_and_no_candle_is_still_reported_silent(self):
        silent = row('KLAC', {}, {MINUTE: 300, MINUTE + 60: 200})

        summary = compare_tool.summarize([silent])

        assert summary['silent'] == ['KLAC']
        assert summary['their_volume'] == 500
        assert summary['no_reference'] == []

    def test_shares_come_only_from_tickers_with_reference(self):
        compared = row('AMD', {MINUTE: 40}, {MINUTE: 1000})
        no_reference = row('PINS', {MINUTE: 50}, {})

        summary = compare_tool.summarize([compared, no_reference])

        assert summary['shares'] == [4.0]

    def test_has_reference_needs_a_bar_in_the_window(self):
        assert compare_tool.has_reference(row('MU', {}, {MINUTE: 1}))
        assert not compare_tool.has_reference(row('MU', {MINUTE: 5}, {}))
        assert not compare_tool.has_reference(row('MU', {}, {MINUTE - 60: 9})), \
            "a bar outside the window is not a reference for it"
