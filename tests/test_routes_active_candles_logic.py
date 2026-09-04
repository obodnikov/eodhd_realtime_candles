"""
Test to verify active_candles fetch logic in API routes.

This test verifies that the code review bug report is incorrect -
active_candles are fetched for API workers regardless of ws_status staleness.
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch
from aiohttp import web
from src.api.routes import APIRoutes


@pytest.fixture
def mock_app():
    """Create a mock aiohttp application."""
    app = web.Application()
    
    # Mock dependencies
    app['config_manager'] = Mock()
    app['config_manager'].config.ws_status_stale_seconds = 30
    app['config_manager'].get_overrides.return_value = {}
    app['config_manager'].config.get_public_config.return_value = {}
    
    app['storage'] = Mock()
    # /status now also reports subscription freshness; these tests are about
    # active candles, so give it an empty watchlist.
    app['storage'].get_tickers.return_value = []
    app['config_manager'].config.subscription_silence_minutes = 15
    app['candle_engine'] = Mock()
    app['ws_manager'] = Mock()
    
    return app


@pytest.mark.asyncio
async def test_api_worker_fetches_active_candles_when_ws_status_fresh(mock_app):
    """
    Test that API workers fetch active_candles even when ws_status is fresh (not stale).
    
    This verifies the code review bug report is incorrect.
    """
    # Setup: API worker (dummy WebSocketManager)
    mock_app['ws_manager'].is_dummy = True
    
    # Mock WebSocket status as FRESH (not stale)
    fresh_ws_status = {
        'connected': True,
        'subscribed_tickers': ['AAPL', 'GOOGL'],
        'subscribed_count': 2,
        'pending_subscribe': [],
        'connection_count': 1,
        'tick_count': 1000,
        'last_message': '2026-01-30T12:00:00Z',
        'last_update': '2026-01-30T12:00:00Z',
        'is_stale': False,  # ✅ NOT STALE
        'age_seconds': 5
    }
    
    # Mock active candles data
    active_candles_data = [
        {'ticker': 'AAPL', 'ticks': 10, 'current_price': 150.0, 'low': 149.0, 'high': 151.0, 'started': 123456, 'started_ago': '1m ago'},
        {'ticker': 'GOOGL', 'ticks': 15, 'current_price': 2800.0, 'low': 2795.0, 'high': 2805.0, 'started': 123457, 'started_ago': '2m ago'}
    ]
    
    # Mock storage methods
    mock_app['storage'].get_websocket_status = Mock(return_value=fresh_ws_status)
    mock_app['storage'].get_active_candles = Mock(return_value=active_candles_data)
    mock_app['storage'].get_stats = Mock(return_value={
        'ticker_count': 2,
        'total_candles': 100,
        'complete_candles': 90,
        'incomplete_candles': 10,
        'candles_per_ticker': {},
        'oldest_candle_timestamp': None,
        'newest_candle_timestamp': None
    })
    
    # Create routes and mock request
    routes = APIRoutes(mock_app)
    mock_request = Mock()
    
    # Patch asyncio.to_thread to execute synchronously
    with patch('asyncio.to_thread', side_effect=lambda func, *args: func(*args)):
        response = await routes.status(mock_request)
    
    # Verify response
    assert response.status == 200
    response_data = response.body
    
    # Parse JSON response
    import json
    data = json.loads(response_data)
    
    # ✅ CRITICAL ASSERTION: active_candles should be fetched even when ws_status is fresh
    assert 'active_candles' in data
    assert len(data['active_candles']) == 2
    assert data['active_candles'][0]['ticker'] == 'AAPL'
    assert data['active_candles'][1]['ticker'] == 'GOOGL'
    
    # Verify get_active_candles was called (proves it runs regardless of staleness)
    mock_app['storage'].get_active_candles.assert_called_once_with(30)


@pytest.mark.asyncio
async def test_api_worker_fetches_active_candles_when_ws_status_stale(mock_app):
    """
    Test that API workers fetch active_candles when ws_status is stale.
    """
    # Setup: API worker (dummy WebSocketManager)
    mock_app['ws_manager'].is_dummy = True
    
    # Mock WebSocket status as STALE
    stale_ws_status = {
        'connected': True,
        'subscribed_tickers': ['AAPL'],
        'subscribed_count': 1,
        'pending_subscribe': [],
        'connection_count': 1,
        'tick_count': 500,
        'last_message': '2026-01-30T11:00:00Z',
        'last_update': '2026-01-30T11:00:00Z',
        'is_stale': True,  # ✅ STALE
        'age_seconds': 60
    }
    
    # Mock active candles data
    active_candles_data = [
        {'ticker': 'AAPL', 'ticks': 5, 'current_price': 150.0, 'low': 149.0, 'high': 151.0, 'started': 123456, 'started_ago': '30s ago'}
    ]
    
    # Mock storage methods
    mock_app['storage'].get_websocket_status = Mock(return_value=stale_ws_status)
    mock_app['storage'].get_active_candles = Mock(return_value=active_candles_data)
    mock_app['storage'].get_stats = Mock(return_value={
        'ticker_count': 1,
        'total_candles': 50,
        'complete_candles': 45,
        'incomplete_candles': 5,
        'candles_per_ticker': {},
        'oldest_candle_timestamp': None,
        'newest_candle_timestamp': None
    })
    
    # Create routes and mock request
    routes = APIRoutes(mock_app)
    mock_request = Mock()
    
    # Patch asyncio.to_thread to execute synchronously
    with patch('asyncio.to_thread', side_effect=lambda func, *args: func(*args)):
        response = await routes.status(mock_request)
    
    # Verify response
    assert response.status == 200
    response_data = response.body
    
    # Parse JSON response
    import json
    data = json.loads(response_data)
    
    # ✅ ASSERTION: active_candles should be fetched when ws_status is stale
    assert 'active_candles' in data
    assert len(data['active_candles']) == 1
    assert data['active_candles'][0]['ticker'] == 'AAPL'
    
    # Verify get_active_candles was called
    mock_app['storage'].get_active_candles.assert_called_once_with(30)


@pytest.mark.asyncio
async def test_api_worker_handles_none_active_candles(mock_app):
    """
    Test that API workers handle None active_candles gracefully (returns empty list).
    """
    # Setup: API worker (dummy WebSocketManager)
    mock_app['ws_manager'].is_dummy = True
    
    # Mock WebSocket status
    ws_status = {
        'connected': True,
        'subscribed_tickers': [],
        'subscribed_count': 0,
        'pending_subscribe': [],
        'connection_count': 1,
        'tick_count': 0,
        'last_message': None,
        'last_update': '2026-01-30T12:00:00Z',
        'is_stale': False,
        'age_seconds': 5
    }
    
    # Mock storage methods - active_candles returns None (stale or no data)
    mock_app['storage'].get_websocket_status = Mock(return_value=ws_status)
    mock_app['storage'].get_active_candles = Mock(return_value=None)  # ✅ Returns None
    mock_app['storage'].get_stats = Mock(return_value={
        'ticker_count': 0,
        'total_candles': 0,
        'complete_candles': 0,
        'incomplete_candles': 0,
        'candles_per_ticker': {},
        'oldest_candle_timestamp': None,
        'newest_candle_timestamp': None
    })
    
    # Create routes and mock request
    routes = APIRoutes(mock_app)
    mock_request = Mock()
    
    # Patch asyncio.to_thread to execute synchronously
    with patch('asyncio.to_thread', side_effect=lambda func, *args: func(*args)):
        response = await routes.status(mock_request)
    
    # Verify response
    assert response.status == 200
    response_data = response.body
    
    # Parse JSON response
    import json
    data = json.loads(response_data)
    
    # ✅ ASSERTION: active_candles should be empty list when None is returned
    assert 'active_candles' in data
    assert data['active_candles'] == []
    
    # Verify get_active_candles was called
    mock_app['storage'].get_active_candles.assert_called_once_with(30)
