"""
EODHD WebSocket connection manager.
Handles connection, reconnection, subscription management, and tick processing.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Set, Callable, Optional
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
    """
    
    EODHD_WS_URL = "wss://ws.eodhistoricaldata.com/ws/us"
    
    def __init__(self, api_key: str, reconnect_delay: int = 5, ping_interval: int = 30):
        self.api_key = api_key
        self.reconnect_delay = reconnect_delay
        self.ping_interval = ping_interval
        
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._subscribed_tickers: Set[str] = set()
        self._pending_subscribe: Set[str] = set()
        self._pending_unsubscribe: Set[str] = set()
        
        self._connected = False
        self._running = False
        self._last_message_time: Optional[datetime] = None
        self._connection_count = 0
        self._tick_count = 0
        
        # Callback for processing ticks
        self._on_tick: Optional[Callable[[str, float, int, int], None]] = None
        
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
    
    def set_on_tick(self, callback: Callable[[str, float, int, int], None]):
        """
        Set callback for tick processing.
        
        Callback signature: (ticker: str, price: float, volume: int, timestamp_ms: int)
        """
        self._on_tick = callback
    
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
    
    def _process_message(self, message: str):
        """Process incoming WebSocket message."""
        try:
            data = json.loads(message)
            
            # Check for status messages
            if 'status_code' in data:
                logger.info(f"EODHD status: {data}")
                return
            
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
                    self._on_tick(ticker, price, volume, timestamp_ms)
            else:
                logger.debug(f"Unknown message format: {message[:100]}")
                
        except json.JSONDecodeError:
            logger.warning(f"Failed to parse message: {message[:100]}")
        except Exception as e:
            logger.error(f"Error processing message: {e}")
    
    async def _connection_loop(self):
        """Main connection loop with automatic reconnection."""
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
                    self._connection_count += 1
                    
                    logger.info(f"Connected to EODHD (connection #{self._connection_count})")
                    
                    # Notify connection change
                    if self._on_connection_change:
                        self._on_connection_change(True)
                    
                    # Subscribe to pending tickers
                    all_tickers = self._subscribed_tickers | self._pending_subscribe
                    if all_tickers:
                        await self._send_subscribe(all_tickers)
                        self._subscribed_tickers = all_tickers
                        self._pending_subscribe.clear()
                    
                    # Process messages
                    async for message in ws:
                        if not self._running:
                            break
                        self._process_message(message)
                        
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
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
            finally:
                self._connected = False
                self._ws = None
                
                if self._on_connection_change:
                    self._on_connection_change(False)
            
            if self._running:
                logger.info(f"Reconnecting in {self.reconnect_delay} seconds...")
                await asyncio.sleep(self.reconnect_delay)
    
    async def start(self):
        """Start the WebSocket connection."""
        if self._running:
            return
        
        self._running = True
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
            'last_message': self._last_message_time.isoformat() if self._last_message_time else None
        }
