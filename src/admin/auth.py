"""
Authentication and session management for admin UI.
"""

import logging
from functools import wraps
from typing import Callable
from flask import session, redirect, url_for, request, flash

logger = logging.getLogger(__name__)


def login_required(f: Callable) -> Callable:
    """
    Decorator to require login for routes.

    Usage:
        @app.route('/protected')
        @login_required
        def protected_route():
            return 'Protected content'
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('authenticated'):
            flash('Please log in to access this page.', 'warning')
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function


def verify_api_key(provided_key: str, expected_key: str) -> bool:
    """
    Verify API key.

    Args:
        provided_key: API key provided by user
        expected_key: Expected API key from configuration

    Returns:
        True if keys match, False otherwise
    """
    if not expected_key:
        logger.warning("No API key configured - all authentication attempts will fail")
        return False

    return provided_key == expected_key
