"""
API Client for communicating with the main REST API.
"""

import logging
from typing import Dict, Any, Optional, List
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


class APIClient:
    """Client for interacting with the main REST API."""

    def __init__(self, base_url: str, api_key: str):
        """
        Initialize API client.

        Args:
            base_url: Base URL of the main API (e.g., http://localhost:8765)
            api_key: API key for authentication
        """
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.session = self._create_session()

    def _create_session(self) -> requests.Session:
        """Create a requests session with retry logic."""
        session = requests.Session()

        # Retry configuration
        retry_strategy = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)

        # Set default headers
        session.headers.update({
            'X-API-Key': self.api_key,
            'Content-Type': 'application/json'
        })

        return session

    def _request(self, method: str, endpoint: str, **kwargs) -> Dict[str, Any]:
        """
        Make a request to the API.

        Args:
            method: HTTP method (GET, POST, DELETE, etc.)
            endpoint: API endpoint (e.g., /status)
            **kwargs: Additional arguments for requests

        Returns:
            JSON response as dict

        Raises:
            Exception: If request fails
        """
        url = f"{self.base_url}{endpoint}"

        try:
            response = self.session.request(method, url, timeout=10, **kwargs)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"API request failed: {method} {endpoint} - {e}")
            raise

    # =========================================================================
    # Health & Status
    # =========================================================================

    def get_health(self) -> Dict[str, Any]:
        """Get health status."""
        return self._request('GET', '/health')

    def get_status(self) -> Dict[str, Any]:
        """Get detailed system status."""
        return self._request('GET', '/status')

    def get_logs(self, limit: int = 100, level: str = None) -> Dict[str, Any]:
        """
        Get recent WARNING/ERROR log entries.
        
        Args:
            limit: Max entries to return.
            level: Filter by level ('WARNING', 'ERROR', or None for both).
        """
        params = {'limit': limit}
        if level:
            params['level'] = level
        return self._request('GET', '/logs', params=params)

    def reconnect_websocket(self) -> Dict[str, Any]:
        """Force WebSocket reconnection."""
        return self._request('POST', '/reconnect')

    # =========================================================================
    # Configuration
    # =========================================================================

    def get_config(self) -> Dict[str, Any]:
        """Get current configuration."""
        return self._request('GET', '/config')

    def update_config(self, config_updates: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update configuration.

        Args:
            config_updates: Configuration fields to update

        Returns:
            Updated configuration
        """
        return self._request('PATCH', '/config', json=config_updates)

    def reset_config(self) -> Dict[str, Any]:
        """Reset configuration to defaults."""
        return self._request('POST', '/config/reset')

    # =========================================================================
    # Ticker Management
    # =========================================================================

    def list_tickers(self) -> Dict[str, Any]:
        """List all tracked tickers."""
        return self._request('GET', '/tickers')

    def add_tickers(self, tickers: List[str]) -> Dict[str, Any]:
        """
        Add tickers.

        Args:
            tickers: List of ticker symbols to add

        Returns:
            Result of operation
        """
        return self._request('POST', '/tickers', json={'tickers': tickers})

    def remove_ticker(self, ticker: str) -> Dict[str, Any]:
        """
        Remove a single ticker.

        Args:
            ticker: Ticker symbol to remove

        Returns:
            Result of operation
        """
        return self._request('DELETE', f'/tickers/{ticker}')

    def remove_tickers(self, tickers: List[str]) -> Dict[str, Any]:
        """
        Remove multiple tickers.

        Args:
            tickers: List of ticker symbols to remove

        Returns:
            Result of operation
        """
        return self._request('DELETE', '/tickers', json={'tickers': tickers})

    # =========================================================================
    # Candle Data
    # =========================================================================

    def get_candles(
        self,
        ticker: str,
        count: int = 10,
        include_current: bool = True,
        from_timestamp: Optional[int] = None,
        to_timestamp: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Get candles for a ticker.

        Args:
            ticker: Ticker symbol
            count: Number of candles to retrieve
            include_current: Include incomplete candle
            from_timestamp: Filter by start time
            to_timestamp: Filter by end time

        Returns:
            Candles data
        """
        params = {
            'count': count,
            'include_current': str(include_current).lower()
        }

        if from_timestamp is not None:
            params['from_timestamp'] = from_timestamp
        if to_timestamp is not None:
            params['to_timestamp'] = to_timestamp

        return self._request('GET', f'/candles/{ticker}', params=params)

    def get_latest_candle(self, ticker: str) -> Dict[str, Any]:
        """
        Get latest candle for a ticker.

        Args:
            ticker: Ticker symbol

        Returns:
            Latest candle data
        """
        return self._request('GET', f'/candles/{ticker}/latest')

    def get_multi_candles(
        self,
        tickers: List[str],
        count: int = 10,
        include_current: bool = True
    ) -> Dict[str, Any]:
        """
        Get candles for multiple tickers.

        Args:
            tickers: List of ticker symbols
            count: Number of candles per ticker
            include_current: Include incomplete candles

        Returns:
            Candles data for all tickers
        """
        return self._request(
            'POST',
            '/candles/multi',
            json={
                'tickers': tickers,
                'count': count,
                'include_current': include_current
            }
        )

    def clear_ticker_candles(self, ticker: str) -> Dict[str, Any]:
        """
        Clear candle history for a ticker.

        Args:
            ticker: Ticker symbol

        Returns:
            Result of operation
        """
        return self._request('DELETE', f'/candles/{ticker}')

    def clear_all_candles(self) -> Dict[str, Any]:
        """Clear all candle history."""
        return self._request('DELETE', '/candles')
