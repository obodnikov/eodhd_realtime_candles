"""
API middleware for authentication and request handling.
"""

import logging
from typing import Optional
from aiohttp import web

logger = logging.getLogger(__name__)


def create_auth_middleware(api_key: Optional[str]):
    """
    Create authentication middleware.
    
    If api_key is None or empty, authentication is disabled.
    """
    
    @web.middleware
    async def auth_middleware(request: web.Request, handler):
        # Skip auth for health check
        if request.path == '/health':
            return await handler(request)
        
        # If no API key configured, skip auth
        if not api_key:
            return await handler(request)
        
        # Check for API key in headers or query params
        provided_key = (
            request.headers.get('X-API-Key') or
            request.headers.get('Authorization', '').replace('Bearer ', '') or
            request.query.get('api_key')
        )
        
        if provided_key != api_key:
            logger.warning(f"Unauthorized request from {request.remote}")
            return web.json_response(
                {'error': 'Unauthorized', 'message': 'Invalid or missing API key'},
                status=401
            )
        
        return await handler(request)
    
    return auth_middleware


@web.middleware
async def error_middleware(request: web.Request, handler):
    """Global error handling middleware."""
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Unhandled error: {e}")
        return web.json_response(
            {'error': 'Internal Server Error', 'message': str(e)},
            status=500
        )


@web.middleware
async def logging_middleware(request: web.Request, handler):
    """Request logging middleware."""
    logger.info(f">>> REQUEST: {request.method} {request.path} from {request.remote}")
    logger.debug(f"    Headers: {dict(request.headers)}")
    try:
        response = await handler(request)
        logger.info(f"<<< RESPONSE: {request.method} {request.path} -> {response.status}")
        return response
    except Exception as e:
        logger.error(f"!!! ERROR in request handler: {e}", exc_info=True)
        raise
