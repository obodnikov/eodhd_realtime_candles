"""
Tests for WebSocketManager authorization flow.
"""

import asyncio
import json
import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock

from src.websocket_manager import WebSocketManager


class TestWebSocketManagerAuthorization:
    """Tests for WebSocket authorization flow."""
    
    def test_init_default_auth_timeout(self):
        """Test default auth_timeout is 10 seconds."""
        ws_manager = WebSocketManager(api_key='test_key')
        assert ws_manager.auth_timeout == 10
    
    def test_init_custom_auth_timeout(self):
        """Test custom auth_timeout can be set."""
        ws_manager = WebSocketManager(api_key='test_key', auth_timeout=30)
        assert ws_manager.auth_timeout == 30
    
    def test_init_authorized_false(self):
        """Test _authorized starts as False."""
        ws_manager = WebSocketManager(api_key='test_key')
        assert ws_manager._authorized is False
    
    @pytest.mark.asyncio
    async def test_process_message_auth_success(self):
        """Test _process_message returns True and sets _authorized on auth success."""
        ws_manager = WebSocketManager(api_key='test_key')
        
        auth_message = json.dumps({'status_code': 200, 'message': 'Authorized'})
        result = await ws_manager._process_message(auth_message)
        
        assert result is True
        assert ws_manager._authorized is True
    
    @pytest.mark.asyncio
    async def test_process_message_auth_failure(self):
        """Test _process_message returns False on auth failure."""
        ws_manager = WebSocketManager(api_key='test_key')
        
        auth_message = json.dumps({'status_code': 401, 'message': 'Unauthorized'})
        result = await ws_manager._process_message(auth_message)
        
        assert result is False
        assert ws_manager._authorized is False
    
    @pytest.mark.asyncio
    async def test_process_message_ignores_ticks_before_auth(self):
        """Test _process_message ignores tick data before authorization."""
        ws_manager = WebSocketManager(api_key='test_key')
        ws_manager._on_tick = Mock()
        
        tick_message = json.dumps({'s': 'AAPL', 'p': 150.0, 'v': 100, 't': 1234567890})
        result = await ws_manager._process_message(tick_message)
        
        assert result is False
        assert ws_manager._tick_count == 0
        ws_manager._on_tick.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_process_message_processes_ticks_after_auth(self):
        """Test _process_message processes tick data after authorization."""
        ws_manager = WebSocketManager(api_key='test_key')
        ws_manager._authorized = True
        
        # Use async mock for callback
        mock_callback = AsyncMock()
        ws_manager._on_tick = mock_callback
        
        tick_message = json.dumps({'s': 'AAPL', 'p': 150.0, 'v': 100, 't': 1234567890})
        result = await ws_manager._process_message(tick_message)
        
        assert result is False
        assert ws_manager._tick_count == 1
        # Give time for the background task to run
        await asyncio.sleep(0.1)
    
    @pytest.mark.asyncio
    async def test_process_message_invalid_json(self):
        """Test _process_message handles invalid JSON gracefully."""
        ws_manager = WebSocketManager(api_key='test_key')
        
        result = await ws_manager._process_message('not valid json')
        
        assert result is False
        assert ws_manager._authorized is False


class TestWebSocketManagerWaitForAuthorization:
    """Tests for _wait_for_authorization method."""
    
    @pytest.mark.asyncio
    async def test_wait_for_authorization_success(self):
        """Test _wait_for_authorization returns True on successful auth."""
        ws_manager = WebSocketManager(api_key='test_key', auth_timeout=5)
        ws_manager._running = True
        
        # Mock WebSocket that yields auth message
        mock_ws = AsyncMock()
        auth_message = json.dumps({'status_code': 200, 'message': 'Authorized'})
        mock_ws.__aiter__.return_value = [auth_message].__iter__()
        
        result = await ws_manager._wait_for_authorization(mock_ws)
        
        assert result is True
        assert ws_manager._authorized is True
    
    @pytest.mark.asyncio
    async def test_wait_for_authorization_ignores_non_auth_messages(self):
        """Test _wait_for_authorization ignores non-auth messages."""
        ws_manager = WebSocketManager(api_key='test_key', auth_timeout=5)
        ws_manager._running = True
        
        # Mock WebSocket that yields tick then auth
        mock_ws = AsyncMock()
        tick_message = json.dumps({'s': 'AAPL', 'p': 150.0, 'v': 100, 't': 1234567890})
        auth_message = json.dumps({'status_code': 200, 'message': 'Authorized'})
        mock_ws.__aiter__.return_value = [tick_message, auth_message].__iter__()
        
        result = await ws_manager._wait_for_authorization(mock_ws)
        
        assert result is True
        assert ws_manager._authorized is True
    
    @pytest.mark.asyncio
    async def test_wait_for_authorization_returns_false_when_stopped(self):
        """Test _wait_for_authorization returns False when _running is False."""
        ws_manager = WebSocketManager(api_key='test_key', auth_timeout=5)
        ws_manager._running = False
        
        mock_ws = AsyncMock()
        auth_message = json.dumps({'status_code': 200, 'message': 'Authorized'})
        mock_ws.__aiter__.return_value = [auth_message].__iter__()
        
        result = await ws_manager._wait_for_authorization(mock_ws)
        
        assert result is False
    
    @pytest.mark.asyncio
    async def test_wait_for_authorization_timeout_no_messages(self):
        """Test _wait_for_authorization returns False on timeout when no messages arrive."""
        ws_manager = WebSocketManager(api_key='test_key', auth_timeout=0.1)  # Short timeout for test
        ws_manager._running = True
        
        # Mock WebSocket that never yields any messages (hangs forever)
        async def never_yield():
            await asyncio.sleep(10)  # Longer than auth_timeout
            yield "never reached"
        
        mock_ws = MagicMock()
        mock_ws.__aiter__ = lambda self: never_yield()
        
        start_time = asyncio.get_event_loop().time()
        result = await ws_manager._wait_for_authorization(mock_ws)
        elapsed = asyncio.get_event_loop().time() - start_time
        
        assert result is False
        assert ws_manager._authorized is False
        # Should timeout around 0.1 seconds, not 10 seconds
        assert elapsed < 1.0


class TestWebSocketManagerStatus:
    """Tests for get_status method."""
    
    def test_get_status_includes_all_fields(self):
        """Test get_status returns all expected fields."""
        ws_manager = WebSocketManager(api_key='test_key')
        
        status = ws_manager.get_status()
        
        assert 'connected' in status
        assert 'subscribed_tickers' in status
        assert 'subscribed_count' in status
        assert 'pending_subscribe' in status
        assert 'connection_count' in status
        assert 'tick_count' in status
        assert 'last_message' in status
    
    def test_get_status_initial_values(self):
        """Test get_status returns correct initial values."""
        ws_manager = WebSocketManager(api_key='test_key')
        
        status = ws_manager.get_status()
        
        assert status['connected'] is False
        assert status['subscribed_tickers'] == []
        assert status['subscribed_count'] == 0
        assert status['connection_count'] == 0
        assert status['tick_count'] == 0
        assert status['last_message'] is None
