"""
Tests for active candles status storage (multi-worker status sharing).
"""

import pytest
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from src.storage import Storage


@pytest.fixture
def temp_storage():
    """Create a temporary storage instance for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / 'test.db'
        storage = Storage(str(db_path))
        yield storage


def test_update_and_get_active_candles(temp_storage):
    """Test writing and reading active candles status."""
    # Sample active candles data
    candles = [
        {
            'ticker': 'AAPL',
            'ticks': 42,
            'current_price': 150.25,
            'low': 149.50,
            'high': 151.00,
            'started': 1234567890,
            'started_ago': '5m ago'
        },
        {
            'ticker': 'GOOGL',
            'ticks': 38,
            'current_price': 2800.50,
            'low': 2795.00,
            'high': 2805.00,
            'started': 1234567900,
            'started_ago': '4m ago'
        }
    ]
    
    # Write active candles
    temp_storage.update_active_candles(candles)
    
    # Read back
    result = temp_storage.get_active_candles(stale_threshold_seconds=30)
    
    assert result is not None
    assert len(result) == 2
    assert result[0]['ticker'] == 'AAPL'
    assert result[0]['ticks'] == 42
    assert result[0]['current_price'] == 150.25
    assert result[1]['ticker'] == 'GOOGL'
    assert result[1]['ticks'] == 38


def test_get_active_candles_empty(temp_storage):
    """Test reading active candles when none have been written."""
    result = temp_storage.get_active_candles()
    assert result is None


def test_update_active_candles_empty_list(temp_storage):
    """Test writing empty active candles list."""
    temp_storage.update_active_candles([])
    
    result = temp_storage.get_active_candles()
    assert result is not None
    assert len(result) == 0


def test_active_candles_staleness(temp_storage):
    """Test that stale active candles return None."""
    candles = [
        {
            'ticker': 'AAPL',
            'ticks': 10,
            'current_price': 150.00,
            'low': 149.00,
            'high': 151.00,
            'started': 1234567890,
            'started_ago': '1m ago'
        }
    ]
    
    # Write active candles
    temp_storage.update_active_candles(candles)
    
    # Should be fresh with 30s threshold
    result = temp_storage.get_active_candles(stale_threshold_seconds=30)
    assert result is not None
    assert len(result) == 1
    
    # Wait 2 seconds
    time.sleep(2)
    
    # Should be stale with 1s threshold
    result = temp_storage.get_active_candles(stale_threshold_seconds=1)
    assert result is None


def test_active_candles_upsert(temp_storage):
    """Test that updating active candles replaces previous data."""
    # First write
    candles1 = [
        {'ticker': 'AAPL', 'ticks': 10, 'current_price': 150.00, 'low': 149.00, 'high': 151.00, 'started': 1234567890, 'started_ago': '1m ago'}
    ]
    temp_storage.update_active_candles(candles1)
    
    result = temp_storage.get_active_candles()
    assert len(result) == 1
    assert result[0]['ticker'] == 'AAPL'
    
    # Second write (different data)
    candles2 = [
        {'ticker': 'GOOGL', 'ticks': 20, 'current_price': 2800.00, 'low': 2795.00, 'high': 2805.00, 'started': 1234567900, 'started_ago': '2m ago'},
        {'ticker': 'MSFT', 'ticks': 15, 'current_price': 300.00, 'low': 299.00, 'high': 301.00, 'started': 1234567910, 'started_ago': '3m ago'}
    ]
    temp_storage.update_active_candles(candles2)
    
    result = temp_storage.get_active_candles()
    assert len(result) == 2
    assert result[0]['ticker'] == 'GOOGL'
    assert result[1]['ticker'] == 'MSFT'


def test_active_candles_preserves_all_fields(temp_storage):
    """Test that all fields in active candles are preserved."""
    candles = [
        {
            'ticker': 'NVDA',
            'ticks': 100,
            'current_price': 500.75,
            'low': 495.00,
            'high': 505.50,
            'started': 1234567890,
            'started_ago': '10m ago'
        }
    ]
    
    temp_storage.update_active_candles(candles)
    result = temp_storage.get_active_candles()
    
    assert result[0]['ticker'] == 'NVDA'
    assert result[0]['ticks'] == 100
    assert result[0]['current_price'] == 500.75
    assert result[0]['low'] == 495.00
    assert result[0]['high'] == 505.50
    assert result[0]['started'] == 1234567890
    assert result[0]['started_ago'] == '10m ago'


def test_active_candles_concurrent_updates(temp_storage):
    """Test multiple rapid updates to active candles."""
    for i in range(10):
        candles = [
            {
                'ticker': f'TICK{i}',
                'ticks': i * 10,
                'current_price': 100.0 + i,
                'low': 99.0 + i,
                'high': 101.0 + i,
                'started': 1234567890 + i,
                'started_ago': f'{i}m ago'
            }
        ]
        temp_storage.update_active_candles(candles)
    
    # Should have the last update
    result = temp_storage.get_active_candles()
    assert result is not None
    assert len(result) == 1
    assert result[0]['ticker'] == 'TICK9'
    assert result[0]['ticks'] == 90


def test_active_candles_with_special_characters(temp_storage):
    """Test active candles with special characters in data."""
    candles = [
        {
            'ticker': 'TEST',
            'ticks': 5,
            'current_price': 100.00,
            'low': 99.00,
            'high': 101.00,
            'started': 1234567890,
            'started_ago': '5m ago',
            'extra_field': 'value with "quotes" and \'apostrophes\''
        }
    ]
    
    temp_storage.update_active_candles(candles)
    result = temp_storage.get_active_candles()
    
    assert result is not None
    assert result[0]['extra_field'] == 'value with "quotes" and \'apostrophes\''
