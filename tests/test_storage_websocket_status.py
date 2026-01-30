"""
Tests for WebSocket status storage and retrieval.

Tests the database-based status sharing between WebSocket worker and API workers.
"""

import pytest
import json
import time
from datetime import datetime, timezone, timedelta

from src.storage import Storage


class TestWebSocketStatusStorage:
    """Test WebSocket status database operations."""

    @pytest.fixture
    def storage(self):
        """Create in-memory storage for testing."""
        return Storage(':memory:')

    def test_update_websocket_status(self, storage):
        """Test writing WebSocket status to database."""
        status = {
            'connected': True,
            'subscribed_tickers': ['AAPL', 'GOOGL', 'MSFT'],
            'subscribed_count': 3,
            'pending_subscribe': [],
            'connection_count': 1,
            'tick_count': 1234,
            'last_message': '2026-01-30T10:00:00Z'
        }
        
        storage.update_websocket_status(status)
        
        # Verify status was written
        result = storage.get_websocket_status()
        assert result is not None
        assert result['connected'] is True
        assert result['subscribed_count'] == 3
        assert 'AAPL' in result['subscribed_tickers']
        assert result['tick_count'] == 1234

    def test_get_websocket_status_returns_none_when_empty(self, storage):
        """Test getting status when none has been written."""
        result = storage.get_websocket_status()
        assert result is None

    def test_websocket_status_upsert(self, storage):
        """Test that updating status replaces existing row (UPSERT)."""
        # Write initial status
        status1 = {
            'connected': True,
            'subscribed_tickers': ['AAPL'],
            'subscribed_count': 1,
            'pending_subscribe': [],
            'connection_count': 1,
            'tick_count': 100,
            'last_message': None
        }
        storage.update_websocket_status(status1)
        
        # Update with new status
        status2 = {
            'connected': True,
            'subscribed_tickers': ['AAPL', 'GOOGL'],
            'subscribed_count': 2,
            'pending_subscribe': [],
            'connection_count': 1,
            'tick_count': 200,
            'last_message': None
        }
        storage.update_websocket_status(status2)
        
        # Verify only one row exists with latest data
        result = storage.get_websocket_status()
        assert result['subscribed_count'] == 2
        assert result['tick_count'] == 200

    def test_websocket_status_staleness_detection(self, storage):
        """Test staleness detection with configurable threshold."""
        status = {
            'connected': True,
            'subscribed_tickers': [],
            'subscribed_count': 0,
            'pending_subscribe': [],
            'connection_count': 1,
            'tick_count': 0,
            'last_message': None
        }
        storage.update_websocket_status(status)
        
        # Immediately after write, should not be stale
        result = storage.get_websocket_status(stale_threshold_seconds=30)
        assert result['is_stale'] is False
        assert result['age_seconds'] < 1
        
        # Simulate old status by manually updating timestamp
        conn = storage._get_connection()
        cursor = conn.cursor()
        old_time = (datetime.now(timezone.utc) - timedelta(seconds=35)).isoformat()
        cursor.execute('UPDATE websocket_status SET last_update = ? WHERE id = 1', (old_time,))
        conn.commit()
        
        # Should now be stale with 30s threshold
        result = storage.get_websocket_status(stale_threshold_seconds=30)
        assert result['is_stale'] is True
        assert result['age_seconds'] > 30

    def test_websocket_status_custom_staleness_threshold(self, storage):
        """Test custom staleness threshold."""
        status = {
            'connected': True,
            'subscribed_tickers': [],
            'subscribed_count': 0,
            'pending_subscribe': [],
            'connection_count': 1,
            'tick_count': 0,
            'last_message': None
        }
        storage.update_websocket_status(status)
        
        # Simulate status 15 seconds old
        conn = storage._get_connection()
        cursor = conn.cursor()
        old_time = (datetime.now(timezone.utc) - timedelta(seconds=15)).isoformat()
        cursor.execute('UPDATE websocket_status SET last_update = ? WHERE id = 1', (old_time,))
        conn.commit()
        
        # Not stale with 30s threshold
        result = storage.get_websocket_status(stale_threshold_seconds=30)
        assert result['is_stale'] is False
        
        # Stale with 10s threshold
        result = storage.get_websocket_status(stale_threshold_seconds=10)
        assert result['is_stale'] is True

    def test_websocket_status_json_parsing_error_handling(self, storage):
        """Test graceful handling of malformed JSON in database."""
        # Write valid status first
        status = {
            'connected': True,
            'subscribed_tickers': ['AAPL'],
            'subscribed_count': 1,
            'pending_subscribe': [],
            'connection_count': 1,
            'tick_count': 0,
            'last_message': None
        }
        storage.update_websocket_status(status)
        
        # Corrupt the JSON in database
        conn = storage._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            'UPDATE websocket_status SET subscribed_tickers = ?, pending_subscribe = ? WHERE id = 1',
            ('invalid json{', '[broken')
        )
        conn.commit()
        
        # Should handle error gracefully and return empty lists
        result = storage.get_websocket_status()
        assert result is not None
        assert result['subscribed_tickers'] == []
        assert result['pending_subscribe'] == []
        assert result['connected'] is True  # Other fields still work

    def test_websocket_status_with_pending_subscribe(self, storage):
        """Test status with pending subscriptions."""
        status = {
            'connected': True,
            'subscribed_tickers': ['AAPL'],
            'subscribed_count': 1,
            'pending_subscribe': ['GOOGL', 'MSFT'],
            'connection_count': 1,
            'tick_count': 0,
            'last_message': None
        }
        storage.update_websocket_status(status)
        
        result = storage.get_websocket_status()
        assert result['pending_subscribe'] == ['GOOGL', 'MSFT']

    def test_websocket_status_disconnected_state(self, storage):
        """Test status when WebSocket is disconnected."""
        status = {
            'connected': False,
            'subscribed_tickers': [],
            'subscribed_count': 0,
            'pending_subscribe': ['AAPL', 'GOOGL'],
            'connection_count': 0,
            'tick_count': 0,
            'last_message': None
        }
        storage.update_websocket_status(status)
        
        result = storage.get_websocket_status()
        assert result['connected'] is False
        assert result['connection_count'] == 0

    def test_websocket_status_with_last_message(self, storage):
        """Test status with last message timestamp."""
        last_msg = '2026-01-30T10:30:45.123Z'
        status = {
            'connected': True,
            'subscribed_tickers': ['AAPL'],
            'subscribed_count': 1,
            'pending_subscribe': [],
            'connection_count': 1,
            'tick_count': 5000,
            'last_message': last_msg
        }
        storage.update_websocket_status(status)
        
        result = storage.get_websocket_status()
        assert result['last_message'] == last_msg

    def test_websocket_status_age_calculation(self, storage):
        """Test age_seconds calculation."""
        status = {
            'connected': True,
            'subscribed_tickers': [],
            'subscribed_count': 0,
            'pending_subscribe': [],
            'connection_count': 1,
            'tick_count': 0,
            'last_message': None
        }
        storage.update_websocket_status(status)
        
        # Small delay to ensure age > 0
        time.sleep(0.1)
        
        result = storage.get_websocket_status()
        assert result['age_seconds'] > 0
        assert result['age_seconds'] < 1  # Should be very recent

    def test_websocket_status_empty_ticker_lists(self, storage):
        """Test status with empty ticker lists."""
        status = {
            'connected': True,
            'subscribed_tickers': [],
            'subscribed_count': 0,
            'pending_subscribe': [],
            'connection_count': 1,
            'tick_count': 0,
            'last_message': None
        }
        storage.update_websocket_status(status)
        
        result = storage.get_websocket_status()
        assert result['subscribed_tickers'] == []
        assert result['pending_subscribe'] == []
        assert result['subscribed_count'] == 0

    def test_websocket_status_large_ticker_list(self, storage):
        """Test status with many tickers."""
        tickers = [f'TICK{i}' for i in range(50)]
        status = {
            'connected': True,
            'subscribed_tickers': tickers,
            'subscribed_count': 50,
            'pending_subscribe': [],
            'connection_count': 1,
            'tick_count': 10000,
            'last_message': None
        }
        storage.update_websocket_status(status)
        
        result = storage.get_websocket_status()
        assert len(result['subscribed_tickers']) == 50
        assert result['subscribed_count'] == 50

    def test_websocket_status_malformed_timestamp(self, storage):
        """Test graceful handling of malformed timestamp in database."""
        # Write valid status first
        status = {
            'connected': True,
            'subscribed_tickers': ['AAPL'],
            'subscribed_count': 1,
            'pending_subscribe': [],
            'connection_count': 1,
            'tick_count': 0,
            'last_message': None
        }
        storage.update_websocket_status(status)
        
        # Corrupt the timestamp in database
        conn = storage._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            'UPDATE websocket_status SET last_update = ? WHERE id = 1',
            ('not-a-valid-timestamp',)
        )
        conn.commit()
        
        # Should handle error gracefully and mark as stale
        result = storage.get_websocket_status()
        assert result is not None
        assert result['is_stale'] is True
        assert result['age_seconds'] == 999999  # Very large age indicates error
        assert result['connected'] is True  # Other fields still work


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
