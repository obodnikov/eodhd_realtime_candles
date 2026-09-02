"""
EODHD Real-Time Candle Aggregator.

Converts EODHD WebSocket tick data into configurable OHLCV candles and serves
them through a REST API.

This is the single source of truth for the service version. Nothing else should
define a version number -- see the "Versioning and changelog" section in
CLAUDE.md.
"""

__version__ = '0.9.9'
