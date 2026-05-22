"""
EODHD WebSocket connection manager.
Handles connection, reconnection, subscription management, and tick processing.
"""

import asyncio
import json
import logging
import random
from datetime import datetime, timezone
from typing import Set, Callable, Optional, Union, Awaitable
import websockets
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger(__name__)


class WebSocketManager:
    """
    Manages WebSocket connection to EODHD real-time data feed.
    
    Features:
    - Automatic reconnection with configurable delay
    - Dynamic subscription management (add/remove tickers without restart)
    - Connection health monitoring
    - Async tick processing to avoid blocking event loop
    """
    
    EODHD_WS_URL = "wss://ws.eodhistoricaldata.com/ws/us"
    
    def __init__(self, api_key: str, reconnect_delay: int = 5, ping_interval: int = 30, 
                 auth_timeout: int = 10, is_dummy: bool = False,
                 max_reconnect_delay: int = 60):
        if reconnect_delay < 1:
            raise ValueError(f"reconnect_delay must be >= 1, got {reconnect_delay}")
        if max_reconnect_delay < 1:
            raise ValueError(f"max_reconnect_delay must be >= 1, got {max_reconnect_delay}")
        # Ensure max is at least as large as base delay
        max_reconnect_delay = max(max_reconnect_delay, reconnect_delay)

        self.api_key = api_key
        self.reconnect_delay = reconnect_delay
        self.ping_interval = ping_interval
        self.auth_timeout = auth_timeout  # Timeout for authorization response
        self.is_dummy = is_dummy  # Flag to identify dummy instances in API workers
        self.max_reconnect_delay = max_reconnect_delay  # Cap for exponential backoff
        
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._subscribed_tickers: Set[str] = set()
        self._pending_subscribe: Set[str] = set()
        self._pending_unsubscribe: Set[str] = set()
        
        self._connected = False
        self._authorized = False  # Track authorization state
        self._running = False
        self._last_message_time: Optional[datetime] = None
        self._connection_count = 0
        self._tick_count = 0
        self._fire_and_forget_ticks = True
        self._consecutive_failures = 0  # Track consecutive failed connections for backoff
        
        # Callback for processing ticks (auto-detects sync vs async)
        self._on_tick: Optional[Callable[[str, float, int, int], Union[None, Awaitable[None]]]] = None
        
        # Callback for connection status changes
        self._on_connection_change: Optional[Callable[[bool], None]] = None
    
    @property
    def connected(self) -> bool:
        return self._connected
    
    @property
    def subscribed_tickers(self) -> Set[str]:
        return self._subscribed_tickers.copy()
    
    @property
    def url(self) -> str:
        return f"{self.EODHD_WS_URL}?api_token={self.api_key}"
    
    def set_on_tick(
        self,
        callback: Callable[[str, float, int, int], Union[None, Awaitable[None]]],
        fire_and_forget: bool = True
    ):
        """
        Set callback for tick processing.
        
        Callback signature: (ticker: str, price: float, volume: int, timestamp_ms: int)
        
        Supports both sync and async callbacks:
        - Async callbacks are fired as background tasks (non-blocking)
        - Sync callbacks are automatically run in thread pool as background tasks
        
        Note: When fire_and_forget=True, callbacks are scheduled as background
        tasks to avoid blocking the WebSocket message loop.
        """
        self._on_tick = callback
        self._fire_and_forget_ticks = fire_and_forget
    
    async def _safe_tick_callback(self, ticker: str, price: float, volume: int, timestamp_ms: int):
        """Wrapper for async tick callback with error handling."""
        try:
            await self._on_tick(ticker, price, volume, timestamp_ms)
        except Exception as e:
            logger.error(f"Error in tick callback for {ticker}: {e}")
    
    async def _safe_sync_tick_callback(self, ticker: str, price: float, volume: int, timestamp_ms: int):
        """Wrapper for sync tick callback - runs in thread pool with error handling."""
        try:
            await asyncio.to_thread(self._on_tick, ticker, price, volume, timestamp_ms)
        except Exception as e:
            logger.error(f"Error in tick callback for {ticker}: {e}")
    
    def set_on_connection_change(self, callback: Callable[[bool], None]):
        """Set callback for connection status changes."""
        self._on_connection_change = callback
    
    async def subscribe(self, tickers: Set[str]):
        """
        Subscribe to tickers. Can be called before or during connection.
        """
        tickers = {t.upper() for t in tickers}
        new_tickers = tickers - self._subscribed_tickers
        
        if not new_tickers:
            return
        
        if self._connected and self._ws:
            # Send subscription immediately
            await self._send_subscribe(new_tickers)
            self._subscribed_tickers.update(new_tickers)
        else:
            # Queue for when connected
            self._pending_subscribe.update(new_tickers)
        
        logger.info(f"Subscribe requested for: {new_tickers}")
    
    async def unsubscribe(self, tickers: Set[str]):
        """Unsubscribe from tickers."""
        tickers = {t.upper() for t in tickers}
        existing = tickers & self._subscribed_tickers

        if not existing:
            return

        if self._connected and self._ws:
            await self._send_unsubscribe(existing)

        self._subscribed_tickers -= existing
        self._pending_subscribe -= tickers

        logger.info(f"Unsubscribed from: {existing}")

    async def clear_subscriptions(self):
        """Clear all ticker subscriptions and trigger reconnection."""
        if self._subscribed_tickers:
            logger.info(f"Clearing {len(self._subscribed_tickers)} subscriptions")

            if self._connected and self._ws:
                await self._send_unsubscribe(self._subscribed_tickers)

            self._subscribed_tickers.clear()
            self._pending_subscribe.clear()
            self._pending_unsubscribe.clear()

            # Trigger reconnection to ensure clean state
            if self._running:
                await self.stop()
                await self.start()
    
    async def _send_subscribe(self, tickers: Set[str]):
        """Send subscription message to WebSocket."""
        if not self._ws or not tickers:
            return
        
        msg = {
            "action": "subscribe",
            "symbols": ",".join(tickers)
        }
        await self._ws.send(json.dumps(msg))
        logger.debug(f"Sent subscribe: {tickers}")
    
    async def _send_unsubscribe(self, tickers: Set[str]):
        """Send unsubscribe message to WebSocket."""
        if not self._ws or not tickers:
            return
        
        msg = {
            "action": "unsubscribe",
            "symbols": ",".join(tickers)
        }
        await self._ws.send(json.dumps(msg))
        logger.debug(f"Sent unsubscribe: {tickers}")
    
    async def _process_message(self, message: str) -> bool:
        """
        Process incoming WebSocket message.
        
        Only processes tick data when authorized. Status messages are always processed.
        
        Returns:
            True if this was a successful authorization message, False otherwise.
        """
        try:
            data = json.loads(message)
            
            # Check for status messages (including authorization)
            if 'status_code' in data:
                status_code = data.get('status_code')
                if status_code == 200:
                    logger.info(f"EODHD status: {data}")
                else:
                    # Non-200 from EODHD is a server-side problem — log as warning
                    logger.warning(f"EODHD upstream error (status {status_code}): {data.get('message', 'unknown')}")
                # Check for successful authorization
                if status_code == 200 and data.get('message') == 'Authorized':
                    self._authorized = True
                    return True
                return False
            
            # Only process tick data when authorized
            if not self._authorized:
                logger.debug(f"Ignoring message before authorization: {message[:50]}...")
                return False
            
            # Process trade tick
            # Format: {"s": "AAPL", "p": 227.31, "v": 100, "t": 1725198451165, ...}
            if 's' in data and 'p' in data and 't' in data:
                ticker = data['s']
                price = float(data['p'])
                volume = int(data.get('v', 0))
                timestamp_ms = int(data['t'])
                
                self._tick_count += 1
                self._last_message_time = datetime.now(timezone.utc)
                
                if self._on_tick:
                    if self._fire_and_forget_ticks:
                        # Fire-and-forget mode: don't await tick processing.
                        if asyncio.iscoroutinefunction(self._on_tick):
                            asyncio.create_task(self._safe_tick_callback(ticker, price, volume, timestamp_ms))
                        else:
                            # Sync callback - wrap in task with thread pool
                            asyncio.create_task(self._safe_sync_tick_callback(ticker, price, volume, timestamp_ms))
                    else:
                        # Backpressure mode: await callback to prevent unbounded task growth.
                        if asyncio.iscoroutinefunction(self._on_tick):
                            await self._safe_tick_callback(ticker, price, volume, timestamp_ms)
                        else:
                            await self._safe_sync_tick_callback(ticker, price, volume, timestamp_ms)
            else:
                logger.debug(f"Unknown message format: {message[:100]}")
            
            return False
                
        except json.JSONDecodeError:
            logger.warning(f"Failed to parse message: {message[:100]}")
            return False
        except Exception as e:
            logger.error(f"Error processing message: {e}")
            return False
    
    async def _wait_for_authorization(self, ws) -> bool:
        """
        Wait for authorization message from EODHD.
        
        Uses asyncio.wait_for to enforce a hard timeout even if no messages arrive.
        
        Args:
            ws: WebSocket connection
            
        Returns:
            True if authorized successfully, False on timeout or error.
        """
        try:
            return await asyncio.wait_for(
                self._wait_for_auth_message(ws),
                timeout=self.auth_timeout
            )
        except asyncio.TimeoutError:
            logger.error(f"Authorization timeout after {self.auth_timeout}s - no auth response received")
            return False
    
    async def _wait_for_auth_message(self, ws) -> bool:
        """
        Internal method to wait for authorization message.
        
        Separated from _wait_for_authorization to allow asyncio.wait_for wrapping.
        """
        async for message in ws:
            if not self._running:
                return False
            
            # Only look for authorization message, ignore everything else
            try:
                data = json.loads(message)
                if 'status_code' in data:
                    status_code = data.get('status_code')
                    if status_code == 200:
                        logger.info(f"EODHD status: {data}")
                    else:
                        logger.warning(f"EODHD upstream error (status {status_code}): {data.get('message', 'unknown')}")
                    if status_code == 200 and data.get('message') == 'Authorized':
                        self._authorized = True
                        return True
                else:
                    # Non-status message before auth - log and ignore
                    logger.debug(f"Ignoring pre-auth message: {message[:50]}...")
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse message during auth: {message[:50]}...")
        
        return False
    
    def _get_backoff_delay(self) -> float:
        """Calculate reconnect delay with exponential backoff and jitter."""
        if self._consecutive_failures == 0:
            return self.reconnect_delay
        # Cap the exponent to avoid computing huge integers unnecessarily
        max_useful_exp = 0
        if self.max_reconnect_delay > self.reconnect_delay:
            import math
            max_useful_exp = int(math.ceil(
                math.log2(self.max_reconnect_delay / self.reconnect_delay)
            ))
        exponent = min(self._consecutive_failures - 1, max_useful_exp)
        delay = self.reconnect_delay * (2 ** exponent)
        delay = min(delay, self.max_reconnect_delay)
        # Full jitter: randomize between base delay and computed delay
        # Prevents thundering herd when multiple workers reconnect simultaneously
        return random.uniform(self.reconnect_delay, delay)

    def _get_backoff_delay_deterministic(self) -> float:
        """Calculate the deterministic (non-jittered) backoff ceiling for status reporting."""
        if self._consecutive_failures == 0:
            return float(self.reconnect_delay)
        import math
        max_useful_exp = 0
        if self.max_reconnect_delay > self.reconnect_delay:
            max_useful_exp = int(math.ceil(
                math.log2(self.max_reconnect_delay / self.reconnect_delay)
            ))
        exponent = min(self._consecutive_failures - 1, max_useful_exp)
        delay = self.reconnect_delay * (2 ** exponent)
        return float(min(delay, self.max_reconnect_delay))

    async def _connection_loop(self):
        """Main connection loop with automatic reconnection and exponential backoff."""
        while self._running:
            try:
                logger.info(f"Connecting to EODHD WebSocket...")
                
                async with websockets.connect(
                    self.url,
                    ping_interval=self.ping_interval,
                    ping_timeout=10
                ) as ws:
                    self._ws = ws
                    self._connected = True
                    self._authorized = False  # Reset authorization state on new connection
                    self._connection_count += 1
                    
                    logger.info(f"Connected to EODHD (connection #{self._connection_count})")
                    
                    # Notify connection change
                    if self._on_connection_change:
                        self._on_connection_change(True)
                    
                    # Wait for authorization before proceeding
                    if not await self._wait_for_authorization(ws):
                        logger.warning("Authorization failed, will reconnect...")
                        self._consecutive_failures += 1
                        # Fall through to finally + reconnect sleep below
                    else:
                        # Authorization successful — reset backoff
                        self._consecutive_failures = 0
                        
                        # Send subscriptions
                        logger.info("Authorization received, sending subscriptions...")
                        all_tickers = self._subscribed_tickers | self._pending_subscribe
                        if all_tickers:
                            await self._send_subscribe(all_tickers)
                            self._subscribed_tickers = all_tickers
                            self._pending_subscribe.clear()
                        
                        # Process messages after authorization
                        async for message in ws:
                            if not self._running:
                                break
                            await self._process_message(message)
                            
                            # Handle pending operations
                            if self._pending_subscribe:
                                await self._send_subscribe(self._pending_subscribe)
                                self._subscribed_tickers.update(self._pending_subscribe)
                                self._pending_subscribe.clear()
                            
                            if self._pending_unsubscribe:
                                await self._send_unsubscribe(self._pending_unsubscribe)
                                self._subscribed_tickers -= self._pending_unsubscribe
                                self._pending_unsubscribe.clear()
                    
            except ConnectionClosed as e:
                logger.warning(f"WebSocket connection closed: {e}")
                self._consecutive_failures += 1
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
                self._consecutive_failures += 1
            finally:
                self._connected = False
                self._authorized = False
                self._ws = None
                
                if self._on_connection_change:
                    self._on_connection_change(False)
            
            if self._running:
                delay = self._get_backoff_delay()
                if self._consecutive_failures > 1:
                    logger.warning(
                        f"Reconnecting in {delay:.0f}s "
                        f"(attempt {self._consecutive_failures}, backoff active)"
                    )
                else:
                    logger.info(f"Reconnecting in {delay:.0f}s...")
                await asyncio.sleep(delay)
    
    async def start(self):
        """Start the WebSocket connection."""
        if self._running:
            return
        
        self._running = True
        self._consecutive_failures = 0  # Fresh start with base reconnect timing
        asyncio.create_task(self._connection_loop())
        logger.info("WebSocket manager started")
    
    async def stop(self):
        """Stop the WebSocket connection."""
        self._running = False
        
        if self._ws:
            await self._ws.close()
        
        logger.info("WebSocket manager stopped")
    
    def get_status(self) -> dict:
        """Get current connection status."""
        return {
            'connected': self._connected,
            'subscribed_tickers': list(self._subscribed_tickers),
            'subscribed_count': len(self._subscribed_tickers),
            'pending_subscribe': list(self._pending_subscribe),
            'connection_count': self._connection_count,
            'tick_count': self._tick_count,
            'last_message': self._last_message_time.isoformat() if self._last_message_time else None,
            'consecutive_failures': self._consecutive_failures,
            'backoff_delay_ceiling': self._get_backoff_delay_deterministic(),
        }
