#!/usr/bin/env python3
"""
Unit tests for premarket_volume.py

Tests cover:
- Premarket time detection (including DST transitions)
- Volume calculation logic
- API error handling
- Edge cases
"""

import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
import sys
import os

# Add parent directory to path to import the module
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from premarket_volume import PremarketVolumeCalculator


class TestPremarketTimeDetection(unittest.TestCase):
    """Test the is_premarket_time() method with various scenarios."""
    
    def setUp(self):
        """Set up test fixtures."""
        os.environ['EODHD_API_KEY'] = 'test_key'
        self.calculator = PremarketVolumeCalculator()
    
    def test_premarket_start_est(self):
        """Test 4:00 AM EST (winter) is detected as premarket."""
        # 2026-01-15 09:00:00 UTC = 4:00 AM EST
        dt = datetime(2026, 1, 15, 9, 0, 0, tzinfo=timezone.utc)
        self.assertTrue(self.calculator.is_premarket_time(dt))
    
    def test_premarket_end_est(self):
        """Test 9:29 AM EST (winter) is detected as premarket."""
        # 2026-01-15 14:29:00 UTC = 9:29 AM EST
        dt = datetime(2026, 1, 15, 14, 29, 0, tzinfo=timezone.utc)
        self.assertTrue(self.calculator.is_premarket_time(dt))
    
    def test_market_open_est(self):
        """Test 9:30 AM EST (winter) is NOT premarket."""
        # 2026-01-15 14:30:00 UTC = 9:30 AM EST
        dt = datetime(2026, 1, 15, 14, 30, 0, tzinfo=timezone.utc)
        self.assertFalse(self.calculator.is_premarket_time(dt))
    
    def test_premarket_start_edt(self):
        """Test 4:00 AM EDT (summer) is detected as premarket."""
        # 2026-07-15 08:00:00 UTC = 4:00 AM EDT
        dt = datetime(2026, 7, 15, 8, 0, 0, tzinfo=timezone.utc)
        self.assertTrue(self.calculator.is_premarket_time(dt))
    
    def test_premarket_end_edt(self):
        """Test 9:29 AM EDT (summer) is detected as premarket."""
        # 2026-07-15 13:29:00 UTC = 9:29 AM EDT
        dt = datetime(2026, 7, 15, 13, 29, 0, tzinfo=timezone.utc)
        self.assertTrue(self.calculator.is_premarket_time(dt))
    
    def test_market_open_edt(self):
        """Test 9:30 AM EDT (summer) is NOT premarket."""
        # 2026-07-15 13:30:00 UTC = 9:30 AM EDT
        dt = datetime(2026, 7, 15, 13, 30, 0, tzinfo=timezone.utc)
        self.assertFalse(self.calculator.is_premarket_time(dt))
    
    def test_before_premarket(self):
        """Test 3:59 AM ET is NOT premarket."""
        # 2026-01-15 08:59:00 UTC = 3:59 AM EST
        dt = datetime(2026, 1, 15, 8, 59, 0, tzinfo=timezone.utc)
        self.assertFalse(self.calculator.is_premarket_time(dt))
    
    def test_regular_trading_hours(self):
        """Test 10:00 AM ET is NOT premarket."""
        # 2026-01-15 15:00:00 UTC = 10:00 AM EST
        dt = datetime(2026, 1, 15, 15, 0, 0, tzinfo=timezone.utc)
        self.assertFalse(self.calculator.is_premarket_time(dt))
    
    def test_naive_datetime_raises_error(self):
        """Test that naive datetime raises ValueError."""
        dt = datetime(2026, 1, 15, 9, 0, 0)  # No timezone
        with self.assertRaises(ValueError):
            self.calculator.is_premarket_time(dt)


class TestVolumeCalculation(unittest.TestCase):
    """Test the calculate_premarket_volume() method."""
    
    def setUp(self):
        """Set up test fixtures."""
        os.environ['EODHD_API_KEY'] = 'test_key'
        self.calculator = PremarketVolumeCalculator()
    
    @patch('premarket_volume.requests.get')
    def test_successful_calculation(self, mock_get):
        """Test successful premarket volume calculation."""
        # Mock API response with premarket data
        mock_response = MagicMock()
        mock_response.json.return_value = [
            {
                'datetime': '2026-01-15 09:00:00',  # 4:00 AM EST
                'volume': 1000,
                'open': 100.0,
                'high': 101.0,
                'low': 99.0,
                'close': 100.5
            },
            {
                'datetime': '2026-01-15 09:01:00',  # 4:01 AM EST
                'volume': 2000,
                'open': 100.5,
                'high': 101.5,
                'low': 100.0,
                'close': 101.0
            },
            {
                'datetime': '2026-01-15 14:30:00',  # 9:30 AM EST (market open)
                'volume': 5000,
                'open': 101.0,
                'high': 102.0,
                'low': 100.5,
                'close': 101.5
            }
        ]
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response
        
        result = self.calculator.calculate_premarket_volume('AAPL.US')
        
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['ticker'], 'AAPL.US')
        self.assertEqual(result['average_premarket_volume'], 3000)  # (1000 + 2000) / 1 day
        self.assertEqual(result['trading_days_included'], 1)
        self.assertEqual(result['average_interval_volume'], 1500)  # (1000 + 2000) / 2 candles
    
    @patch('premarket_volume.requests.get')
    def test_no_premarket_data(self, mock_get):
        """Test when API returns no premarket data."""
        mock_response = MagicMock()
        mock_response.json.return_value = [
            {
                'datetime': '2026-01-15 14:30:00',  # 9:30 AM EST (market open)
                'volume': 5000,
                'open': 101.0,
                'high': 102.0,
                'low': 100.5,
                'close': 101.5
            }
        ]
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response
        
        result = self.calculator.calculate_premarket_volume('AAPL.US')
        
        self.assertEqual(result['status'], 'error')
        self.assertIn('No premarket data found', result['error'])
    
    @patch('premarket_volume.requests.get')
    def test_empty_api_response(self, mock_get):
        """Test when API returns empty list."""
        mock_response = MagicMock()
        mock_response.json.return_value = []
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response
        
        result = self.calculator.calculate_premarket_volume('INVALID.US')
        
        self.assertEqual(result['status'], 'error')
        self.assertIn('No data returned', result['error'])
    
    @patch('premarket_volume.requests.get')
    def test_api_request_failure(self, mock_get):
        """Test when API request fails."""
        mock_get.side_effect = Exception('Connection error')
        
        result = self.calculator.calculate_premarket_volume('AAPL.US')
        
        self.assertEqual(result['status'], 'error')
        self.assertIn('Connection error', result['error'])
    
    @patch('premarket_volume.requests.get')
    def test_missing_volume_field(self, mock_get):
        """Test handling of candles with missing volume."""
        mock_response = MagicMock()
        mock_response.json.return_value = [
            {
                'datetime': '2026-01-15 09:00:00',  # 4:00 AM EST
                'volume': None,  # Missing volume
                'open': 100.0,
                'high': 101.0,
                'low': 99.0,
                'close': 100.5
            },
            {
                'datetime': '2026-01-15 09:01:00',  # 4:01 AM EST
                'volume': 2000,
                'open': 100.5,
                'high': 101.5,
                'low': 100.0,
                'close': 101.0
            }
        ]
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response
        
        result = self.calculator.calculate_premarket_volume('AAPL.US')
        
        # Should skip the candle with missing volume
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['average_premarket_volume'], 2000)
        self.assertEqual(result['average_interval_volume'], 2000)


class TestInitialization(unittest.TestCase):
    """Test calculator initialization."""
    
    def test_missing_api_key(self):
        """Test that missing API key raises ValueError."""
        if 'EODHD_API_KEY' in os.environ:
            del os.environ['EODHD_API_KEY']
        
        with self.assertRaises(ValueError) as context:
            PremarketVolumeCalculator()
        
        self.assertIn('EODHD_API_KEY', str(context.exception))
    
    def test_initialization_with_api_key(self):
        """Test successful initialization with API key."""
        os.environ['EODHD_API_KEY'] = 'test_key'
        calculator = PremarketVolumeCalculator()
        
        self.assertEqual(calculator.api_key, 'test_key')
        self.assertEqual(calculator.interval, '1m')
        self.assertEqual(calculator.days_back, 90)


if __name__ == '__main__':
    unittest.main()
