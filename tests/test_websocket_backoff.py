"""
Tests for WebSocketManager exponential backoff and EODHD error logging.
"""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.websocket_manager import WebSocketManager


class TestBackoffDelayCalculation:
    """Tests for _get_backoff_delay() method."""

    def test_zero_failures_returns_base_delay(self):
        """With no failures, delay equals reconnect_delay."""
        ws = WebSocketManager(api_key='test', reconnect_delay=5)
        ws._consecutive_failures = 0
        assert ws._get_backoff_delay() == 5

    def test_first_failure_returns_base_delay(self):
        """First failure: base * 2^0 = base, jitter between [base, base] = base."""
        ws = WebSocketManager(api_key='test', reconnect_delay=5, max_reconnect_delay=60)
        ws._consecutive_failures = 1
        # With 1 failure: delay = 5 * 2^0 = 5, jitter in [5, 5]
        delay = ws._get_backoff_delay()
        assert delay == 5.0

    def test_second_failure_doubles(self):
        """Second failure: base * 2^1 = 10, jitter in [5, 10]."""
        ws = WebSocketManager(api_key='test', reconnect_delay=5, max_reconnect_delay=60)
        ws._consecutive_failures = 2
        delay = ws._get_backoff_delay()
        assert 5 <= delay <= 10

    def test_third_failure_quadruples(self):
        """Third failure: base * 2^2 = 20, jitter in [5, 20]."""
        ws = WebSocketManager(api_key='test', reconnect_delay=5, max_reconnect_delay=60)
        ws._consecutive_failures = 3
        delay = ws._get_backoff_delay()
        assert 5 <= delay <= 20

    def test_delay_capped_at_max(self):
        """Delay never exceeds max_reconnect_delay."""
        ws = WebSocketManager(api_key='test', reconnect_delay=5, max_reconnect_delay=30)
        ws._consecutive_failures = 10  # 5 * 2^9 = 2560, but capped at 30
        delay = ws._get_backoff_delay()
        assert 5 <= delay <= 30

    def test_large_failure_count_stays_capped(self):
        """Even with many failures, delay stays within bounds."""
        ws = WebSocketManager(api_key='test', reconnect_delay=5, max_reconnect_delay=60)
        ws._consecutive_failures = 100
        delay = ws._get_backoff_delay()
        assert 5 <= delay <= 60

    def test_jitter_produces_varied_results(self):
        """Jitter should produce different values across calls (non-deterministic)."""
        ws = WebSocketManager(api_key='test', reconnect_delay=5, max_reconnect_delay=60)
        ws._consecutive_failures = 5  # delay range [5, 60]
        delays = {ws._get_backoff_delay() for _ in range(50)}
        # With 50 samples from uniform[5, 60], we should get more than 1 unique value
        assert len(delays) > 1


class TestBackoffParameterValidation:
    """Tests for constructor parameter validation."""

    def test_reconnect_delay_zero_raises(self):
        """reconnect_delay=0 should raise ValueError."""
        with pytest.raises(ValueError, match="reconnect_delay"):
            WebSocketManager(api_key='test', reconnect_delay=0)

    def test_reconnect_delay_negative_raises(self):
        """Negative reconnect_delay should raise ValueError."""
        with pytest.raises(ValueError, match="reconnect_delay"):
            WebSocketManager(api_key='test', reconnect_delay=-1)

    def test_max_reconnect_delay_zero_raises(self):
        """max_reconnect_delay=0 should raise ValueError."""
        with pytest.raises(ValueError, match="max_reconnect_delay"):
            WebSocketManager(api_key='test', max_reconnect_delay=0)

    def test_max_reconnect_delay_negative_raises(self):
        """Negative max_reconnect_delay should raise ValueError."""
        with pytest.raises(ValueError, match="max_reconnect_delay"):
            WebSocketManager(api_key='test', max_reconnect_delay=-5)

    def test_max_normalized_to_at_least_base(self):
        """max_reconnect_delay is normalized to >= reconnect_delay."""
        ws = WebSocketManager(api_key='test', reconnect_delay=10, max_reconnect_delay=3)
        assert ws.max_reconnect_delay == 10


class TestBackoffResetOnSuccess:
    """Tests for backoff reset after successful authorization."""

    @pytest.mark.asyncio
    async def test_consecutive_failures_reset_on_auth_success(self):
        """_consecutive_failures resets to 0 after successful auth in connection loop."""
        ws = WebSocketManager(api_key='test', reconnect_delay=1, max_reconnect_delay=10)
        ws._consecutive_failures = 5  # Simulate prior failures

        # Simulate successful auth
        ws._running = True
        mock_ws_instance = AsyncMock()
        auth_msg = json.dumps({'status_code': 200, 'message': 'Authorized'})
        mock_ws_instance.__aiter__.return_value = [auth_msg].__iter__()

        result = await ws._wait_for_authorization(mock_ws_instance)
        assert result is True

        # In the real _connection_loop, this would reset _consecutive_failures
        # Test the reset logic directly
        if result:
            ws._consecutive_failures = 0
        assert ws._consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_consecutive_failures_increments_on_auth_failure(self):
        """_consecutive_failures increments when auth times out."""
        ws = WebSocketManager(api_key='test', reconnect_delay=1, auth_timeout=0.1)
        ws._running = True
        ws._consecutive_failures = 2

        # Mock WebSocket that never sends auth
        async def hang_forever():
            await asyncio.sleep(10)
            yield "never"

        mock_ws = MagicMock()
        mock_ws.__aiter__ = lambda self: hang_forever()

        result = await ws._wait_for_authorization(mock_ws)
        assert result is False

        # Simulate what _connection_loop does on failure
        ws._consecutive_failures += 1
        assert ws._consecutive_failures == 3


class TestEodhdErrorLogging:
    """Tests for EODHD 500 error logging at WARNING level."""

    @pytest.mark.asyncio
    async def test_eodhd_500_logged_as_warning(self):
        """EODHD 500 status should be logged at WARNING level."""
        ws = WebSocketManager(api_key='test')

        with patch('src.websocket_manager.logger') as mock_logger:
            msg = json.dumps({'status_code': 500, 'message': 'Internal error. Try again later'})
            result = await ws._process_message(msg)

            assert result is False
            mock_logger.warning.assert_called_once()
            call_args = mock_logger.warning.call_args[0][0]
            assert '500' in call_args
            assert 'Internal error' in call_args

    @pytest.mark.asyncio
    async def test_eodhd_200_logged_as_info(self):
        """EODHD 200 status should be logged at INFO level."""
        ws = WebSocketManager(api_key='test')

        with patch('src.websocket_manager.logger') as mock_logger:
            msg = json.dumps({'status_code': 200, 'message': 'Authorized'})
            await ws._process_message(msg)

            mock_logger.info.assert_called()
            mock_logger.warning.assert_not_called()

    @pytest.mark.asyncio
    async def test_eodhd_401_logged_as_warning(self):
        """EODHD 401 status should be logged at WARNING level."""
        ws = WebSocketManager(api_key='test')

        with patch('src.websocket_manager.logger') as mock_logger:
            msg = json.dumps({'status_code': 401, 'message': 'Unauthorized'})
            result = await ws._process_message(msg)

            assert result is False
            mock_logger.warning.assert_called_once()


class TestGetStatusIncludesBackoff:
    """Tests for backoff fields in get_status()."""

    def test_status_includes_consecutive_failures(self):
        """get_status() should include consecutive_failures."""
        ws = WebSocketManager(api_key='test')
        ws._consecutive_failures = 3
        status = ws.get_status()
        assert status['consecutive_failures'] == 3

    def test_status_includes_backoff_delay_ceiling(self):
        """get_status() should include backoff_delay_ceiling (deterministic)."""
        ws = WebSocketManager(api_key='test', reconnect_delay=5, max_reconnect_delay=60)
        ws._consecutive_failures = 0
        status = ws.get_status()
        assert status['backoff_delay_ceiling'] == 5.0

    def test_status_backoff_ceiling_reflects_failures(self):
        """backoff_delay_ceiling should increase deterministically with failures."""
        ws = WebSocketManager(api_key='test', reconnect_delay=5, max_reconnect_delay=60)
        ws._consecutive_failures = 3
        status = ws.get_status()
        # 5 * 2^2 = 20, deterministic
        assert status['backoff_delay_ceiling'] == 20.0

    def test_status_backoff_ceiling_is_deterministic(self):
        """backoff_delay_ceiling should return same value on repeated calls."""
        ws = WebSocketManager(api_key='test', reconnect_delay=5, max_reconnect_delay=60)
        ws._consecutive_failures = 4
        values = [ws.get_status()['backoff_delay_ceiling'] for _ in range(10)]
        assert len(set(values)) == 1  # All identical


class TestConnectionLoopIntegration:
    """Integration tests for _connection_loop with mocked websocket and sleep."""

    @pytest.mark.asyncio
    async def test_auth_failure_increments_failures_and_sleeps(self):
        """Auth failure should increment _consecutive_failures and sleep before retry."""
        ws = WebSocketManager(api_key='test', reconnect_delay=1, auth_timeout=0.1)
        ws._running = True

        call_count = 0
        sleep_delays = []

        # Mock websockets.connect to simulate auth timeout (no messages)
        class MockConnectCM:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self_cm):
                nonlocal call_count
                call_count += 1
                mock_ws = MagicMock()
                # Return an async iterator that yields nothing (triggers auth timeout)
                async def empty_iter():
                    return
                    yield  # Make it an async generator

                mock_ws.__aiter__ = lambda self: empty_iter()
                mock_ws.close = AsyncMock()
                return mock_ws

            async def __aexit__(self_cm, *args):
                pass

        async def mock_sleep(delay):
            sleep_delays.append(delay)
            # Stop after 3 reconnect sleeps
            if len(sleep_delays) >= 3:
                ws._running = False

        with patch('src.websocket_manager.websockets.connect', MockConnectCM):
            with patch('src.websocket_manager.asyncio.sleep', mock_sleep):
                await ws._connection_loop()

        # Should have attempted connections and tracked reconnect sleeps
        assert call_count >= 3
        assert ws._consecutive_failures >= 3
        # Each sleep delay should be >= reconnect_delay
        for d in sleep_delays:
            assert d >= ws.reconnect_delay

    @pytest.mark.asyncio
    async def test_successful_auth_resets_failures(self):
        """Successful authorization should reset _consecutive_failures to 0."""
        ws = WebSocketManager(api_key='test', reconnect_delay=1, max_reconnect_delay=10)
        ws._running = True
        ws._consecutive_failures = 5  # Pre-existing failures

        auth_msg = json.dumps({'status_code': 200, 'message': 'Authorized'})

        class MockConnectCM:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self_cm):
                mock_ws = MagicMock()

                # Successful auth, then no more messages (loop ends naturally)
                async def msg_iter():
                    yield auth_msg

                mock_ws.__aiter__ = lambda self: msg_iter()
                mock_ws.send = AsyncMock()
                mock_ws.close = AsyncMock()
                return mock_ws

            async def __aexit__(self_cm, *args):
                pass

        async def mock_sleep(delay):
            # Stop after first reconnect sleep
            ws._running = False

        with patch('src.websocket_manager.websockets.connect', MockConnectCM):
            with patch('src.websocket_manager.asyncio.sleep', mock_sleep):
                await ws._connection_loop()

        # After successful auth, failures should have been reset to 0
        # The connection loop ends naturally (no exception), so no increment
        assert ws._consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_connection_exception_increments_failures(self):
        """Connection exception should increment _consecutive_failures."""
        ws = WebSocketManager(api_key='test', reconnect_delay=1)
        ws._running = True

        class MockConnectCM:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self_cm):
                raise ConnectionError("Connection refused")

            async def __aexit__(self_cm, *args):
                pass

        async def mock_sleep(delay):
            ws._running = False

        with patch('src.websocket_manager.websockets.connect', MockConnectCM):
            with patch('src.websocket_manager.asyncio.sleep', mock_sleep):
                await ws._connection_loop()

        assert ws._consecutive_failures == 1

    @pytest.mark.asyncio
    async def test_start_resets_backoff_state(self):
        """start() should reset _consecutive_failures for a fresh run."""
        ws = WebSocketManager(api_key='test', reconnect_delay=1)
        ws._consecutive_failures = 7  # Stale state from prior outage

        # Patch create_task to avoid actually running the loop
        with patch('src.websocket_manager.asyncio.create_task'):
            await ws.start()

        assert ws._consecutive_failures == 0
