"""
Tests for LogBufferHandler and log buffer integration.
"""

import logging
import pytest

from src.log_buffer import LogBufferHandler, get_log_buffer, install_log_buffer


class TestLogBufferHandler:
    """Tests for the in-memory ring buffer handler."""

    def test_captures_warning(self):
        """WARNING messages are captured."""
        handler = LogBufferHandler(max_entries=10, level=logging.WARNING)
        handler.setFormatter(logging.Formatter('%(message)s'))
        record = logging.LogRecord(
            name='test', level=logging.WARNING, pathname='', lineno=0,
            msg='test warning', args=(), exc_info=None
        )
        handler.emit(record)
        entries = handler.get_entries()
        assert len(entries) == 1
        assert entries[0]['level'] == 'WARNING'
        assert entries[0]['message'] == 'test warning'

    def test_captures_error(self):
        """ERROR messages are captured."""
        handler = LogBufferHandler(max_entries=10, level=logging.WARNING)
        handler.setFormatter(logging.Formatter('%(message)s'))
        record = logging.LogRecord(
            name='test.module', level=logging.ERROR, pathname='', lineno=0,
            msg='test error', args=(), exc_info=None
        )
        handler.emit(record)
        entries = handler.get_entries()
        assert len(entries) == 1
        assert entries[0]['level'] == 'ERROR'
        assert entries[0]['logger'] == 'test.module'

    def test_ignores_info(self):
        """INFO messages below threshold are not captured."""
        handler = LogBufferHandler(max_entries=10, level=logging.WARNING)
        handler.setFormatter(logging.Formatter('%(message)s'))
        record = logging.LogRecord(
            name='test', level=logging.INFO, pathname='', lineno=0,
            msg='info message', args=(), exc_info=None
        )
        handler.emit(record)
        assert handler.count == 0

    def test_ring_buffer_evicts_oldest(self):
        """Buffer evicts oldest entries when full."""
        handler = LogBufferHandler(max_entries=3, level=logging.WARNING)
        handler.setFormatter(logging.Formatter('%(message)s'))
        for i in range(5):
            record = logging.LogRecord(
                name='test', level=logging.WARNING, pathname='', lineno=0,
                msg=f'msg {i}', args=(), exc_info=None
            )
            handler.emit(record)
        assert handler.count == 3
        entries = handler.get_entries()
        # Newest first
        assert entries[0]['message'] == 'msg 4'
        assert entries[1]['message'] == 'msg 3'
        assert entries[2]['message'] == 'msg 2'

    def test_get_entries_newest_first(self):
        """Entries are returned newest first."""
        handler = LogBufferHandler(max_entries=10, level=logging.WARNING)
        handler.setFormatter(logging.Formatter('%(message)s'))
        for i in range(3):
            record = logging.LogRecord(
                name='test', level=logging.WARNING, pathname='', lineno=0,
                msg=f'msg {i}', args=(), exc_info=None
            )
            handler.emit(record)
        entries = handler.get_entries()
        assert entries[0]['message'] == 'msg 2'
        assert entries[2]['message'] == 'msg 0'

    def test_get_entries_limit(self):
        """Limit parameter caps returned entries."""
        handler = LogBufferHandler(max_entries=10, level=logging.WARNING)
        handler.setFormatter(logging.Formatter('%(message)s'))
        for i in range(5):
            record = logging.LogRecord(
                name='test', level=logging.WARNING, pathname='', lineno=0,
                msg=f'msg {i}', args=(), exc_info=None
            )
            handler.emit(record)
        entries = handler.get_entries(limit=2)
        assert len(entries) == 2

    def test_get_entries_filter_by_level(self):
        """Level filter returns only matching entries."""
        handler = LogBufferHandler(max_entries=10, level=logging.WARNING)
        handler.setFormatter(logging.Formatter('%(message)s'))
        for level in [logging.WARNING, logging.ERROR, logging.WARNING]:
            record = logging.LogRecord(
                name='test', level=level, pathname='', lineno=0,
                msg=f'{logging.getLevelName(level)} msg', args=(), exc_info=None
            )
            handler.emit(record)
        entries = handler.get_entries(level='ERROR')
        assert len(entries) == 1
        assert entries[0]['level'] == 'ERROR'

    def test_clear(self):
        """Clear empties the buffer."""
        handler = LogBufferHandler(max_entries=10, level=logging.WARNING)
        handler.setFormatter(logging.Formatter('%(message)s'))
        record = logging.LogRecord(
            name='test', level=logging.WARNING, pathname='', lineno=0,
            msg='msg', args=(), exc_info=None
        )
        handler.emit(record)
        assert handler.count == 1
        handler.clear()
        assert handler.count == 0

    def test_entry_has_required_fields(self):
        """Each entry has timestamp, level, logger, message."""
        handler = LogBufferHandler(max_entries=10, level=logging.WARNING)
        handler.setFormatter(logging.Formatter('%(message)s'))
        record = logging.LogRecord(
            name='src.websocket_manager', level=logging.ERROR, pathname='', lineno=0,
            msg='connection failed', args=(), exc_info=None
        )
        handler.emit(record)
        entry = handler.get_entries()[0]
        assert 'timestamp' in entry
        assert 'level' in entry
        assert 'logger' in entry
        assert 'message' in entry
        assert entry['timestamp'].endswith('+00:00')


class TestInstallLogBuffer:
    """Tests for install_log_buffer idempotency."""

    def test_install_is_idempotent(self):
        """Calling install_log_buffer multiple times doesn't add duplicate handlers."""
        root = logging.getLogger()
        initial_count = len(root.handlers)

        install_log_buffer()
        install_log_buffer()
        install_log_buffer()

        buffer_handlers = [h for h in root.handlers if isinstance(h, LogBufferHandler)]
        assert len(buffer_handlers) == 1

        # Cleanup
        for h in buffer_handlers:
            root.removeHandler(h)

    def test_get_log_buffer_returns_same_instance(self):
        """get_log_buffer() always returns the same singleton."""
        buf1 = get_log_buffer()
        buf2 = get_log_buffer()
        assert buf1 is buf2


class TestGetLogsEndpoint:
    """Tests for GET /logs API endpoint validation logic.
    
    These test the route handler behavior by calling it with mocked requests,
    verifying parameter validation, response shape, and error handling.
    """

    @pytest.mark.asyncio
    async def test_valid_request_returns_entries(self):
        """Valid request returns entries with correct response shape."""
        from unittest.mock import MagicMock, patch
        from aiohttp import web
        from src.api.routes import APIRoutes

        # Setup a minimal app
        app = web.Application()
        app['config_manager'] = MagicMock()
        storage_mock = MagicMock()
        del storage_mock.get_log_entries  # force the in-memory buffer fallback
        app['storage'] = storage_mock
        app['candle_engine'] = MagicMock()
        app['ws_manager'] = MagicMock()
        routes = APIRoutes(app)

        # Emit a test warning into the buffer
        from src.log_buffer import get_log_buffer, install_log_buffer
        install_log_buffer()
        buf = get_log_buffer()
        buf.clear()
        record = logging.LogRecord(
            name='test', level=logging.WARNING, pathname='', lineno=0,
            msg='test warning for endpoint', args=(), exc_info=None
        )
        buf.emit(record)

        # Create mock request
        mock_request = MagicMock()
        mock_request.query = {}

        response = await routes.get_logs(mock_request)
        import json
        data = json.loads(response.body)

        assert response.status == 200
        assert 'entries' in data
        assert 'returned_count' in data
        assert 'source' in data
        assert 'limit' in data
        assert 'level_filter' in data
        assert len(data['entries']) >= 1

    @pytest.mark.asyncio
    async def test_invalid_limit_returns_400(self):
        """Invalid limit parameter returns 400."""
        from unittest.mock import MagicMock
        from aiohttp import web
        from src.api.routes import APIRoutes

        app = web.Application()
        app['config_manager'] = MagicMock()
        storage_mock = MagicMock()
        del storage_mock.get_log_entries  # force the in-memory buffer fallback
        app['storage'] = storage_mock
        app['candle_engine'] = MagicMock()
        app['ws_manager'] = MagicMock()
        routes = APIRoutes(app)

        mock_request = MagicMock()
        mock_request.query = {'limit': 'abc'}

        response = await routes.get_logs(mock_request)
        assert response.status == 400

    @pytest.mark.asyncio
    async def test_invalid_level_returns_400(self):
        """Invalid level parameter returns 400."""
        from unittest.mock import MagicMock
        from aiohttp import web
        from src.api.routes import APIRoutes

        app = web.Application()
        app['config_manager'] = MagicMock()
        storage_mock = MagicMock()
        del storage_mock.get_log_entries  # force the in-memory buffer fallback
        app['storage'] = storage_mock
        app['candle_engine'] = MagicMock()
        app['ws_manager'] = MagicMock()
        routes = APIRoutes(app)

        mock_request = MagicMock()
        mock_request.query = {'level': 'DEBUG'}

        response = await routes.get_logs(mock_request)
        assert response.status == 400

    @pytest.mark.asyncio
    async def test_level_filter_case_insensitive(self):
        """Level filter accepts lowercase and normalizes."""
        from unittest.mock import MagicMock
        from aiohttp import web
        from src.api.routes import APIRoutes

        app = web.Application()
        app['config_manager'] = MagicMock()
        storage_mock = MagicMock()
        del storage_mock.get_log_entries  # force the in-memory buffer fallback
        app['storage'] = storage_mock
        app['candle_engine'] = MagicMock()
        app['ws_manager'] = MagicMock()
        routes = APIRoutes(app)

        mock_request = MagicMock()
        mock_request.query = {'level': 'warning'}

        response = await routes.get_logs(mock_request)
        import json
        data = json.loads(response.body)
        assert response.status == 200
        assert data['level_filter'] == 'WARNING'

    @pytest.mark.asyncio
    async def test_limit_clamped_to_bounds(self):
        """Limit is clamped between 1 and 500."""
        from unittest.mock import MagicMock
        from aiohttp import web
        from src.api.routes import APIRoutes

        app = web.Application()
        app['config_manager'] = MagicMock()
        storage_mock = MagicMock()
        del storage_mock.get_log_entries  # force the in-memory buffer fallback
        app['storage'] = storage_mock
        app['candle_engine'] = MagicMock()
        app['ws_manager'] = MagicMock()
        routes = APIRoutes(app)

        # Test negative limit clamped to 1
        mock_request = MagicMock()
        mock_request.query = {'limit': '-5'}
        response = await routes.get_logs(mock_request)
        import json
        data = json.loads(response.body)
        assert data['limit'] == 1

        # Test over-limit clamped to 500
        mock_request.query = {'limit': '9999'}
        response = await routes.get_logs(mock_request)
        data = json.loads(response.body)
        assert data['limit'] == 500
