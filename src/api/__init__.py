"""
API module for EODHD Real-Time Candle Aggregator.

Provides REST API routes and middleware for authentication and error handling.
"""

from .routes import APIRoutes
from .middleware import create_auth_middleware, error_middleware, logging_middleware

__all__ = [
    'APIRoutes',
    'create_auth_middleware',
    'error_middleware',
    'logging_middleware',
]
