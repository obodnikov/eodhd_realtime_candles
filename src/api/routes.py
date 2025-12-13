"""
REST API route handlers.
"""

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from aiohttp import web

if TYPE_CHECKING:
    from ..config import ConfigManager
    from ..storage import Storage
    from ..candle_engine import CandleEngine
    from ..websocket_manager import WebSocketManager

logger = logging.getLogger(__name__)


class APIRoutes:
    """
    REST API route handlers.
    
    All handlers receive dependencies via the app context.
    """
    
    def __init__(self, app: web.Application):
        self.app = app
        self._setup_routes()
    
    def _setup_routes(self):
        """Register all routes."""
        # Health & Status
        self.app.router.add_get('/health', self.health)
        self.app.router.add_get('/status', self.status)
        self.app.router.add_post('/reconnect', self.reconnect)
        
        # Configuration
        self.app.router.add_get('/config', self.get_config)
        self.app.router.add_patch('/config', self.update_config)
        self.app.router.add_post('/config/reset', self.reset_config)
        
        # Ticker Management
        self.app.router.add_get('/tickers', self.list_tickers)
        self.app.router.add_post('/tickers', self.add_tickers)
        self.app.router.add_delete('/tickers/{ticker}', self.remove_ticker)
        self.app.router.add_delete('/tickers', self.remove_tickers)
        
        # Candle Data
        self.app.router.add_get('/candles/all', self.get_all_candles)
        self.app.router.add_get('/candles/{ticker}', self.get_candles)
        self.app.router.add_get('/candles/{ticker}/latest', self.get_latest_candle)
        self.app.router.add_post('/candles/multi', self.get_multi_candles)
        self.app.router.add_delete('/candles/{ticker}', self.clear_ticker_candles)
        self.app.router.add_delete('/candles', self.clear_all_candles)
    
    # =========================================================================
    # Helper methods
    # =========================================================================
    
    @property
    def config_manager(self) -> 'ConfigManager':
        return self.app['config_manager']
    
    @property
    def storage(self) -> 'Storage':
        return self.app['storage']
    
    @property
    def candle_engine(self) -> 'CandleEngine':
        return self.app['candle_engine']
    
    @property
    def ws_manager(self) -> 'WebSocketManager':
        return self.app['ws_manager']
    
    # =========================================================================
    # Health & Status
    # =========================================================================
    
    async def health(self, request: web.Request) -> web.Response:
        """GET /health - Basic health check."""
        logger.debug("Health check endpoint called")
        return web.json_response({
            'status': 'healthy',
            'timestamp': datetime.now(timezone.utc).isoformat()
        })
    
    async def status(self, request: web.Request) -> web.Response:
        """GET /status - Detailed system status."""
        ws_status = self.ws_manager.get_status()
        db_stats = self.storage.get_stats()
        overrides = self.config_manager.get_overrides()

        return web.json_response({
            'websocket': ws_status,
            'database': db_stats,
            'config': self.config_manager.config.get_public_config(
                include_source=True,
                overrides=overrides
            ),
            'active_candles': self.candle_engine.get_active_tickers(),
            'timestamp': datetime.now(timezone.utc).isoformat()
        })
    
    async def reconnect(self, request: web.Request) -> web.Response:
        """POST /reconnect - Force WebSocket reconnection."""
        await self.ws_manager.stop()
        await self.ws_manager.start()
        
        return web.json_response({
            'message': 'Reconnection initiated',
            'timestamp': datetime.now(timezone.utc).isoformat()
        })
    
    # =========================================================================
    # Configuration
    # =========================================================================
    
    async def get_config(self, request: web.Request) -> web.Response:
        """GET /config - Get current configuration with source information."""
        overrides = self.config_manager.get_overrides()

        return web.json_response({
            'config': self.config_manager.config.get_public_config(
                include_source=True,
                overrides=overrides
            ),
            'persistence_enabled': self.config_manager.config.persist_config,
            'has_persisted_overrides': len(overrides) > 0,
            'timestamp': datetime.now(timezone.utc).isoformat()
        })
    
    async def update_config(self, request: web.Request) -> web.Response:
        """PATCH /config - Update configuration."""
        try:
            data = await request.json()
        except Exception:
            return web.json_response(
                {'error': 'Invalid JSON body'},
                status=400
            )
        
        result = self.config_manager.update(data)
        
        # Apply changes to running components
        if 'candle_interval_minutes' in result.get('updated', []):
            self.candle_engine.set_interval(
                self.config_manager.config.candle_interval_minutes
            )
        
        if 'max_candles_stored' in result.get('updated', []):
            self.candle_engine.set_max_candles(
                self.config_manager.config.max_candles_stored
            )
        
        status = 200 if not result.get('errors') else 207  # Multi-Status
        return web.json_response(result, status=status)
    
    async def reset_config(self, request: web.Request) -> web.Response:
        """POST /config/reset - Reset configuration to defaults."""
        result = self.config_manager.reset_to_defaults()
        
        # Apply to running components
        self.candle_engine.set_interval(
            self.config_manager.config.candle_interval_minutes
        )
        self.candle_engine.set_max_candles(
            self.config_manager.config.max_candles_stored
        )
        
        return web.json_response(result)
    
    # =========================================================================
    # Ticker Management
    # =========================================================================
    
    async def list_tickers(self, request: web.Request) -> web.Response:
        """GET /tickers - List all tracked tickers."""
        tickers = self.storage.get_tickers()
        max_tickers = self.config_manager.config.max_tickers
        
        return web.json_response({
            'count': len(tickers),
            'max_allowed': max_tickers,
            'available_slots': max_tickers - len(tickers),
            'tickers': [t.to_dict() for t in tickers],
            'timestamp': datetime.now(timezone.utc).isoformat()
        })
    
    async def add_tickers(self, request: web.Request) -> web.Response:
        """POST /tickers - Add ticker(s) to track."""
        try:
            data = await request.json()
        except Exception:
            return web.json_response(
                {'error': 'Invalid JSON body'},
                status=400
            )
        
        tickers = data.get('tickers', [])
        if isinstance(tickers, str):
            tickers = [tickers]
        
        if not tickers:
            return web.json_response(
                {'error': 'No tickers provided'},
                status=400
            )
        
        # Validate format
        tickers = [t.upper().strip() for t in tickers if t.strip()]
        invalid = [t for t in tickers if not t.isalpha() or len(t) > 5]
        tickers = [t for t in tickers if t not in invalid]
        
        # Check max tickers limit
        current_count = self.storage.get_ticker_count()
        max_tickers = self.config_manager.config.max_tickers
        available = max_tickers - current_count
        
        if len(tickers) > available:
            return web.json_response({
                'error': 'Would exceed maximum tickers limit',
                'current_count': current_count,
                'max_allowed': max_tickers,
                'requested': len(tickers),
                'available_slots': available
            }, status=400)
        
        # Add tickers
        added = []
        already_exists = []
        
        for ticker in tickers:
            if self.storage.add_ticker(ticker):
                added.append(ticker)
            else:
                already_exists.append(ticker)
        
        # Subscribe to WebSocket
        if added:
            await self.ws_manager.subscribe(set(added))
        
        return web.json_response({
            'added': added,
            'already_exists': already_exists,
            'invalid': invalid,
            'current_count': self.storage.get_ticker_count(),
            'timestamp': datetime.now(timezone.utc).isoformat()
        })
    
    async def remove_ticker(self, request: web.Request) -> web.Response:
        """DELETE /tickers/{ticker} - Remove a single ticker."""
        ticker = request.match_info['ticker'].upper()
        
        if not self.storage.ticker_exists(ticker):
            return web.json_response(
                {'error': f'Ticker not found: {ticker}'},
                status=404
            )
        
        # Unsubscribe from WebSocket
        await self.ws_manager.unsubscribe({ticker})
        
        # Remove from candle engine
        self.candle_engine.remove_ticker(ticker)
        
        # Remove from storage (including candles)
        self.storage.remove_ticker(ticker)
        
        return web.json_response({
            'removed': ticker,
            'current_count': self.storage.get_ticker_count(),
            'timestamp': datetime.now(timezone.utc).isoformat()
        })
    
    async def remove_tickers(self, request: web.Request) -> web.Response:
        """
        DELETE /tickers - Remove tickers.

        With body {"tickers": [...]}: Remove specific tickers
        Without body (empty request): Remove ALL tickers (requires ALLOW_DELETE_ALL_TICKERS=true and ?confirm=true)
        """
        # Try to parse JSON body
        try:
            data = await request.json()
        except Exception:
            # No body or invalid JSON - treat as "delete all" request
            data = None

        # Check if this is a "delete all tickers" request
        if data is None or not data or not data.get('tickers'):
            # Delete all tickers operation

            # Check if feature is enabled
            if not self.config_manager.config.allow_delete_all_tickers:
                return web.json_response({
                    'error': 'Removing all tickers is disabled',
                    'detail': 'Set ALLOW_DELETE_ALL_TICKERS=true in .env to enable this operation'
                }, status=403)

            # Check for confirmation parameter
            confirm = request.query.get('confirm', '').lower()
            if confirm != 'true':
                return web.json_response({
                    'error': 'Confirmation required',
                    'detail': 'Add ?confirm=true to confirm deletion of all tickers',
                    'warning': 'This will remove all tracked tickers (candle data will be preserved)'
                }, status=400)

            # Proceed with deleting all tickers
            count = self.storage.delete_all_tickers()

            # Clear WebSocket subscriptions
            await self.ws_manager.clear_subscriptions()

            # Clear all active candles from engine
            for ticker in self.candle_engine.get_active_tickers():
                self.candle_engine.remove_ticker(ticker)

            return web.json_response({
                'message': 'All tickers removed',
                'count': count,
                'candles_preserved': True,
                'current_count': 0,
                'timestamp': datetime.now(timezone.utc).isoformat()
            })

        # Remove specific tickers
        tickers = data.get('tickers', [])
        if isinstance(tickers, str):
            tickers = [tickers]

        tickers = [t.upper().strip() for t in tickers if t.strip()]

        removed = []
        not_found = []

        for ticker in tickers:
            if self.storage.ticker_exists(ticker):
                await self.ws_manager.unsubscribe({ticker})
                self.candle_engine.remove_ticker(ticker)
                self.storage.remove_ticker(ticker)
                removed.append(ticker)
            else:
                not_found.append(ticker)

        return web.json_response({
            'removed': removed,
            'not_found': not_found,
            'current_count': self.storage.get_ticker_count(),
            'timestamp': datetime.now(timezone.utc).isoformat()
        })
    
    # =========================================================================
    # Candle Data
    # =========================================================================
    
    async def get_all_candles(self, request: web.Request) -> web.Response:
        """GET /candles/all - Get candles for ALL tracked tickers (requires confirmation)."""
        # Check for confirmation parameter
        confirm = request.query.get('confirm', '').lower()
        if confirm != 'true':
            return web.json_response({
                'error': 'Confirmation required',
                'detail': 'Add ?confirm=true to confirm retrieving candles for all tickers',
                'warning': 'This operation may return large amounts of data'
            }, status=400)

        # Check for max_tickers parameter (mandatory)
        max_tickers_param = request.query.get('max_tickers')
        if not max_tickers_param:
            return web.json_response({
                'error': 'max_tickers parameter required',
                'detail': 'Add ?max_tickers=N to specify the maximum number of tickers to retrieve',
                'example': '/candles/all?confirm=true&max_tickers=50'
            }, status=400)

        try:
            max_tickers = int(max_tickers_param)
            if max_tickers < 1:
                raise ValueError("max_tickers must be >= 1")
        except (ValueError, TypeError):
            return web.json_response({
                'error': 'Invalid max_tickers parameter',
                'detail': 'max_tickers must be a positive integer >= 1'
            }, status=400)

        # Parse optional query parameters
        count = int(request.query.get('count', '10'))
        include_current = request.query.get('include_current', 'true').lower() == 'true'
        from_timestamp = request.query.get('from_timestamp')
        to_timestamp = request.query.get('to_timestamp')

        # Validate count
        count = min(max(count, 1), self.config_manager.config.max_candles_stored)

        # Get all tracked tickers
        all_tickers = self.storage.get_tickers()
        ticker_count = len(all_tickers)

        # Check if ticker count exceeds max_tickers limit
        if ticker_count > max_tickers:
            return web.json_response({
                'error': 'Too many tickers',
                'detail': f'Found {ticker_count} tracked tickers, but max_tickers limit is {max_tickers}',
                'current_ticker_count': ticker_count,
                'max_tickers_limit': max_tickers,
                'suggestion': f'Increase max_tickers parameter to at least {ticker_count}'
            }, status=400)

        # Collect all candles in a flat list
        all_candles = []
        interval_minutes = self.config_manager.config.candle_interval_minutes

        for ticker_obj in all_tickers:
            ticker = ticker_obj.ticker
            candles = self.storage.get_candles(
                ticker=ticker,
                count=count,
                include_current=include_current,
                interval_minutes=interval_minutes,
                from_timestamp=int(from_timestamp) if from_timestamp else None,
                to_timestamp=int(to_timestamp) if to_timestamp else None
            )

            # Add ticker field to each candle and append to flat list
            for candle in candles:
                candle_dict = candle.to_dict()
                candle_dict['ticker'] = ticker
                all_candles.append(candle_dict)

        return web.json_response({
            'total_tickers': ticker_count,
            'total_candles': len(all_candles),
            'max_tickers_limit': max_tickers,
            'interval': f"{interval_minutes}m",
            'candles': all_candles,
            'timestamp': datetime.now(timezone.utc).isoformat()
        })

    async def get_candles(self, request: web.Request) -> web.Response:
        """GET /candles/{ticker} - Get candles for a ticker."""
        ticker = request.match_info['ticker'].upper()

        # Parse query parameters
        count = int(request.query.get('count', '10'))
        include_current = request.query.get('include_current', 'true').lower() == 'true'
        from_timestamp = request.query.get('from_timestamp')
        to_timestamp = request.query.get('to_timestamp')

        # Validate count
        count = min(max(count, 1), self.config_manager.config.max_candles_stored)

        candles = self.storage.get_candles(
            ticker=ticker,
            count=count,
            include_current=include_current,
            interval_minutes=self.config_manager.config.candle_interval_minutes,
            from_timestamp=int(from_timestamp) if from_timestamp else None,
            to_timestamp=int(to_timestamp) if to_timestamp else None
        )

        return web.json_response({
            'ticker': ticker,
            'interval': f"{self.config_manager.config.candle_interval_minutes}m",
            'count': len(candles),
            'candles': [c.to_dict() for c in candles],
            'timestamp': datetime.now(timezone.utc).isoformat()
        })

    async def get_latest_candle(self, request: web.Request) -> web.Response:
        """GET /candles/{ticker}/latest - Get current incomplete candle."""
        ticker = request.match_info['ticker'].upper()
        
        candle = self.candle_engine.get_current_candle(ticker)
        
        if not candle:
            return web.json_response(
                {'error': f'No active candle for {ticker}'},
                status=404
            )
        
        return web.json_response({
            'ticker': ticker,
            'candle': candle,
            'timestamp': datetime.now(timezone.utc).isoformat()
        })
    
    async def get_multi_candles(self, request: web.Request) -> web.Response:
        """POST /candles/multi - Get candles for multiple tickers."""
        try:
            data = await request.json()
        except Exception:
            return web.json_response(
                {'error': 'Invalid JSON body'},
                status=400
            )
        
        tickers = data.get('tickers', [])
        count = int(data.get('count', 10))
        include_current = data.get('include_current', True)
        
        if not tickers:
            return web.json_response(
                {'error': 'No tickers provided'},
                status=400
            )
        
        count = min(max(count, 1), self.config_manager.config.max_candles_stored)
        
        result = {}
        for ticker in tickers:
            ticker = ticker.upper()
            candles = self.storage.get_candles(
                ticker=ticker,
                count=count,
                include_current=include_current,
                interval_minutes=self.config_manager.config.candle_interval_minutes
            )
            result[ticker] = [c.to_dict() for c in candles]
        
        return web.json_response({
            'interval': f"{self.config_manager.config.candle_interval_minutes}m",
            'data': result,
            'timestamp': datetime.now(timezone.utc).isoformat()
        })
    
    async def clear_ticker_candles(self, request: web.Request) -> web.Response:
        """DELETE /candles/{ticker} - Clear candle history for a ticker."""
        ticker = request.match_info['ticker'].upper()
        
        self.storage.clear_candles(ticker)
        
        return web.json_response({
            'message': f'Cleared candles for {ticker}',
            'timestamp': datetime.now(timezone.utc).isoformat()
        })
    
    async def clear_all_candles(self, request: web.Request) -> web.Response:
        """DELETE /candles - Clear all candle history."""
        self.storage.clear_candles()
        
        return web.json_response({
            'message': 'Cleared all candles',
            'timestamp': datetime.now(timezone.utc).isoformat()
        })
