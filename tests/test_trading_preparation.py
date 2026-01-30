#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unit tests for trading_preparation.py

Tests cover:
- EMA calculations
- State detection
- Intraday scoring
- Trend summary building
- Data validation
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

# Add scripts directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts" / "trading_preparation"))

from trading_preparation import (
    TrendRules,
    ValidationError,
    calculate_intraday_score,
    compute_ema,
    consecutive_holds,
    detect_state,
    build_trend_summary,
    trend_label,
    market_session,
    validate_ticker,
)
from datetime import datetime
from zoneinfo import ZoneInfo

NY_TZ = ZoneInfo("America/New_York")


# =============================================================================
# Test EMA Calculations
# =============================================================================

class TestComputeEMA:
    """Tests for compute_ema function."""

    def test_ema_basic(self):
        """Test basic EMA calculation."""
        series = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        ema = compute_ema(series, period=3)
        
        assert len(ema) == 5
        assert ema.iloc[-1] > ema.iloc[0]  # EMA should increase with rising prices

    def test_ema_period_1(self):
        """EMA with period 1 should equal the original series."""
        series = pd.Series([10.0, 20.0, 30.0, 40.0])
        ema = compute_ema(series, period=1)
        
        pd.testing.assert_series_equal(ema, series)

    def test_ema_constant_series(self):
        """EMA of constant series should be constant."""
        series = pd.Series([5.0, 5.0, 5.0, 5.0, 5.0])
        ema = compute_ema(series, period=3)
        
        assert all(abs(v - 5.0) < 0.001 for v in ema)


# =============================================================================
# Test Consecutive Holds
# =============================================================================

class TestConsecutiveHolds:
    """Tests for consecutive_holds function."""

    def test_all_true(self):
        """All True values should give increasing counts."""
        cond = pd.Series([True, True, True, True])
        holds = consecutive_holds(cond)
        
        assert list(holds) == [1, 2, 3, 4]

    def test_all_false(self):
        """All False values should give zeros."""
        cond = pd.Series([False, False, False])
        holds = consecutive_holds(cond)
        
        assert list(holds) == [0, 0, 0]

    def test_mixed(self):
        """Mixed values should reset count on False."""
        cond = pd.Series([True, True, False, True, True, True])
        holds = consecutive_holds(cond)
        
        assert list(holds) == [1, 2, 0, 1, 2, 3]

    def test_with_na(self):
        """NA values should be treated as False."""
        cond = pd.Series([True, None, True, True])
        holds = consecutive_holds(cond)
        
        assert list(holds) == [1, 0, 1, 2]


# =============================================================================
# Test Trend Label
# =============================================================================

class TestTrendLabel:
    """Tests for trend_label function."""

    def test_up_trend(self):
        """Positive differences should be labeled UP."""
        diff = pd.Series([0.5, 1.0, 2.0])
        labels = trend_label(diff)
        
        assert all(l == "UP" for l in labels)

    def test_down_trend(self):
        """Negative differences should be labeled DOWN."""
        diff = pd.Series([-0.5, -1.0, -2.0])
        labels = trend_label(diff)
        
        assert all(l == "DOWN" for l in labels)

    def test_flat(self):
        """Zero differences should be labeled FLAT."""
        diff = pd.Series([0.0, 0.0])
        labels = trend_label(diff)
        
        assert all(l == "FLAT" for l in labels)


# =============================================================================
# Test State Detection
# =============================================================================

class TestDetectState:
    """Tests for detect_state function."""

    def test_down_state(self):
        """DOWN state when 10/30 and 3/10 both DOWN."""
        signals = {
            "trend_10_30": "DOWN",
            "stable_10_30": False,
            "trend_3_10": "DOWN",
            "trend_1_3": "DOWN",
        }
        assert detect_state(signals) == "DOWN"

    def test_base_state(self):
        """BASE state when 10/30 DOWN but 3/10 UP."""
        signals = {
            "trend_10_30": "DOWN",
            "stable_10_30": False,
            "trend_3_10": "UP",
            "trend_1_3": "UP",
        }
        assert detect_state(signals) == "BASE"

    def test_trend_start_state(self):
        """TREND_START when 10/30 UP but not stable."""
        signals = {
            "trend_10_30": "UP",
            "stable_10_30": False,
            "trend_3_10": "UP",
            "trend_1_3": "UP",
        }
        assert detect_state(signals) == "TREND_START"

    def test_trend_state(self):
        """TREND when all aligned UP and 10/30 stable."""
        signals = {
            "trend_10_30": "UP",
            "stable_10_30": True,
            "trend_3_10": "UP",
            "trend_1_3": "UP",
        }
        assert detect_state(signals) == "TREND"

    def test_pullback_state(self):
        """PULLBACK when 10/30 UP stable but lower timeframes DOWN."""
        signals = {
            "trend_10_30": "UP",
            "stable_10_30": True,
            "trend_3_10": "DOWN",
            "trend_1_3": "DOWN",
        }
        assert detect_state(signals) == "PULLBACK"

    def test_unknown_state(self):
        """UNKNOWN for unrecognized patterns."""
        signals = {
            "trend_10_30": "FLAT",
            "stable_10_30": False,
            "trend_3_10": "FLAT",
            "trend_1_3": "FLAT",
        }
        assert detect_state(signals) == "UNKNOWN"


# =============================================================================
# Test Intraday Score
# =============================================================================

class TestCalculateIntradayScore:
    """Tests for calculate_intraday_score function."""

    def test_max_score(self):
        """All stable crossovers should give max score (10)."""
        rules = TrendRules()
        signals = {
            "stable_10_30": True,
            "stable_3_10": True,
            "stable_1_3": True,
        }
        score = calculate_intraday_score(signals, rules)
        assert score == 10

    def test_zero_score(self):
        """No stable crossovers should give zero."""
        rules = TrendRules()
        signals = {
            "stable_10_30": False,
            "stable_3_10": False,
            "stable_1_3": False,
        }
        score = calculate_intraday_score(signals, rules)
        assert score == 0

    def test_partial_score(self):
        """Partial stability should give partial score."""
        rules = TrendRules()
        signals = {
            "stable_10_30": True,  # +4
            "stable_3_10": False,  # +0
            "stable_1_3": True,    # +3
        }
        score = calculate_intraday_score(signals, rules)
        assert score == 7

    def test_custom_weights(self):
        """Custom weights should be applied correctly."""
        rules = TrendRules(w_10_30=5, w_3_10=3, w_1_3=2)
        signals = {
            "stable_10_30": True,
            "stable_3_10": True,
            "stable_1_3": False,
        }
        score = calculate_intraday_score(signals, rules)
        assert score == 8  # 5 + 3 = 8


# =============================================================================
# Test Trend Summary
# =============================================================================

class TestBuildTrendSummary:
    """Tests for build_trend_summary function."""

    def test_all_up_stable(self):
        """All UP and stable should show [OK] markers."""
        signals = {
            "trend_30_50": "UP",
            "stable_30_50": True,
            "trend_10_30": "UP",
            "stable_10_30": True,
            "trend_3_10": "UP",
            "stable_3_10": True,
            "trend_1_3": "UP",
            "stable_1_3": True,
        }
        summary = build_trend_summary(signals)
        
        assert "30/50:UP[OK]" in summary
        assert "10/30:UP[OK]" in summary
        assert "3/10:UP[OK]" in summary
        assert "1/3:UP[OK]" in summary

    def test_mixed_signals(self):
        """Mixed signals should show correct labels."""
        signals = {
            "trend_30_50": "UP",
            "stable_30_50": True,
            "trend_10_30": "DOWN",
            "stable_10_30": False,
            "trend_3_10": "UP",
            "stable_3_10": False,
            "trend_1_3": "DOWN",
            "stable_1_3": False,
        }
        summary = build_trend_summary(signals)
        
        assert "30/50:UP[OK]" in summary
        assert "10/30:DOWN" in summary
        assert "[OK]" not in summary.split("|")[1]  # 10/30 not stable

    def test_missing_signals(self):
        """Missing signals should show ? placeholder."""
        signals = {}
        summary = build_trend_summary(signals)
        
        assert "30/50:?" in summary
        assert "10/30:?" in summary


# =============================================================================
# Test Market Session
# =============================================================================

class TestMarketSession:
    """Tests for market_session function."""

    def test_premarket(self):
        """4:00-9:30 AM should be PRE."""
        dt = datetime(2026, 1, 29, 8, 0, tzinfo=NY_TZ)
        assert market_session(dt) == "PRE"

    def test_rth(self):
        """9:30 AM - 4:00 PM should be RTH."""
        dt = datetime(2026, 1, 29, 12, 0, tzinfo=NY_TZ)
        assert market_session(dt) == "RTH"

    def test_extended(self):
        """After 4:00 PM should be EXT."""
        dt = datetime(2026, 1, 29, 17, 0, tzinfo=NY_TZ)
        assert market_session(dt) == "EXT"

    def test_closed(self):
        """Before 4:00 AM should be CLOSED."""
        dt = datetime(2026, 1, 29, 3, 0, tzinfo=NY_TZ)
        assert market_session(dt) == "CLOSED"

    def test_rth_boundary_start(self):
        """Exactly 9:30 AM should be RTH."""
        dt = datetime(2026, 1, 29, 9, 30, tzinfo=NY_TZ)
        assert market_session(dt) == "RTH"

    def test_rth_boundary_end(self):
        """Exactly 4:00 PM should be EXT."""
        dt = datetime(2026, 1, 29, 16, 0, tzinfo=NY_TZ)
        assert market_session(dt) == "EXT"


# =============================================================================
# Test Edge Cases
# =============================================================================

class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_empty_signals(self):
        """Empty signals should return UNKNOWN state."""
        assert detect_state({}) == "UNKNOWN"

    def test_score_with_missing_keys(self):
        """Missing keys should be treated as False."""
        rules = TrendRules()
        signals = {"stable_10_30": True}  # Missing other keys
        score = calculate_intraday_score(signals, rules)
        assert score == 4  # Only w_10_30

    def test_ema_empty_series(self):
        """EMA of empty series should return empty series."""
        series = pd.Series([], dtype=float)
        ema = compute_ema(series, period=3)
        assert len(ema) == 0

    def test_consecutive_holds_empty(self):
        """Empty series should return empty holds."""
        cond = pd.Series([], dtype=bool)
        holds = consecutive_holds(cond)
        assert len(holds) == 0


# =============================================================================
# Test Ticker Validation
# =============================================================================

class TestValidateTicker:
    """Tests for validate_ticker function."""

    def test_valid_ticker_simple(self):
        """Simple uppercase ticker should pass."""
        assert validate_ticker("AAPL") == "AAPL"

    def test_valid_ticker_lowercase(self):
        """Lowercase ticker should be uppercased."""
        assert validate_ticker("tsla") == "TSLA"

    def test_valid_ticker_with_dot(self):
        """Ticker with dot (class shares) should pass."""
        assert validate_ticker("BRK.A") == "BRK.A"

    def test_valid_ticker_with_number(self):
        """Ticker with number should pass."""
        assert validate_ticker("3M") == "3M"

    def test_valid_ticker_whitespace(self):
        """Whitespace should be trimmed."""
        assert validate_ticker("  NVDA  ") == "NVDA"

    def test_empty_ticker(self):
        """Empty ticker should raise ValidationError."""
        with pytest.raises(ValidationError, match="cannot be empty"):
            validate_ticker("")

    def test_whitespace_only_ticker(self):
        """Whitespace-only ticker should raise ValidationError."""
        with pytest.raises(ValidationError, match="Invalid ticker length"):
            validate_ticker("   ")

    def test_invalid_characters(self):
        """Invalid characters should raise ValidationError."""
        with pytest.raises(ValidationError, match="Invalid ticker format"):
            validate_ticker("AAPL$")

    def test_invalid_special_chars(self):
        """Special characters should raise ValidationError."""
        with pytest.raises(ValidationError, match="Invalid ticker format"):
            validate_ticker("AA-PL")

    def test_too_long_ticker(self):
        """Ticker over 10 chars should raise ValidationError."""
        with pytest.raises(ValidationError, match="Invalid ticker length"):
            validate_ticker("VERYLONGTICKER")
