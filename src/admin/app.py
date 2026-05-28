"""
Flask application for admin web UI.
"""

import os
import logging
import secrets
from pathlib import Path
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify

from .api_client import APIClient
from .auth import login_required, verify_api_key

logger = logging.getLogger(__name__)


def create_app() -> Flask:
    """Create and configure the Flask application."""

    # Get configuration from environment (strip whitespace)
    api_url = os.getenv('ADMIN_API_URL', 'http://localhost:8765').strip()
    api_key = os.getenv('API_KEY', '').strip()
    admin_host = os.getenv('ADMIN_HOST', '127.0.0.1').strip()
    admin_port = int(os.getenv('ADMIN_PORT', '5000'))
    session_secret = os.getenv('ADMIN_SESSION_SECRET', '').strip() or secrets.token_hex(32)

    # Validate host
    if not admin_host:
        admin_host = '127.0.0.1'
        logger.warning("ADMIN_HOST is empty, defaulting to 127.0.0.1")

    if not api_key:
        logger.warning("No API_KEY configured - authentication will not work properly")

    # Create Flask app
    app = Flask(__name__)
    app.secret_key = session_secret

    # Store config in app context
    app.config['API_URL'] = api_url
    app.config['API_KEY'] = api_key
    app.config['ADMIN_HOST'] = admin_host
    app.config['ADMIN_PORT'] = admin_port

    # Create API client
    api_client = APIClient(api_url, api_key)
    app.config['API_CLIENT'] = api_client

    # =========================================================================
    # Template filters
    # =========================================================================

    @app.template_filter('datetime')
    def format_datetime(value, format='%Y-%m-%d %H:%M:%S'):
        """Format datetime for display."""
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value.replace('Z', '+00:00'))
            except:
                return value
        if isinstance(value, datetime):
            return value.strftime(format)
        return value

    @app.template_filter('number')
    def format_number(value, decimals=2):
        """Format number with decimals."""
        try:
            return f"{float(value):.{decimals}f}"
        except:
            return value

    # =========================================================================
    # Routes - Authentication
    # =========================================================================

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        """Login page."""
        if request.method == 'POST':
            provided_key = request.form.get('api_key', '')

            if verify_api_key(provided_key, app.config['API_KEY']):
                session['authenticated'] = True
                session.permanent = True
                flash('Successfully logged in!', 'success')

                # Redirect to next page or dashboard
                next_page = request.args.get('next')
                return redirect(next_page or url_for('dashboard'))
            else:
                flash('Invalid API key. Please try again.', 'danger')

        return render_template('login.html')

    @app.route('/logout')
    def logout():
        """Logout and clear session."""
        session.clear()
        flash('You have been logged out.', 'info')
        return redirect(url_for('login'))

    # =========================================================================
    # Routes - Main Pages
    # =========================================================================

    @app.route('/')
    @login_required
    def index():
        """Redirect to dashboard."""
        return redirect(url_for('dashboard'))

    @app.route('/dashboard')
    @login_required
    def dashboard():
        """Main dashboard page."""
        try:
            status = api_client.get_status()
            return render_template('dashboard.html', status=status)
        except Exception as e:
            logger.error(f"Failed to load dashboard: {e}")
            flash(f'Error loading dashboard: {str(e)}', 'danger')
            return render_template('dashboard.html', status=None)

    @app.route('/tickers')
    @login_required
    def tickers():
        """Ticker management page."""
        try:
            data = api_client.list_tickers()
            return render_template('tickers.html', tickers=data.get('tickers', []))
        except Exception as e:
            logger.error(f"Failed to load tickers: {e}")
            flash(f'Error loading tickers: {str(e)}', 'danger')
            return render_template('tickers.html', tickers=[])

    @app.route('/candles')
    @login_required
    def candles():
        """Candle data viewer page."""
        try:
            # Get list of tickers for dropdown
            ticker_data = api_client.list_tickers()
            tickers = ticker_data.get('tickers', [])

            # Get selected ticker from query params
            selected_ticker = request.args.get('ticker')
            candle_data = None

            if selected_ticker:
                try:
                    count = int(request.args.get('count', 50))
                    candle_data = api_client.get_candles(
                        selected_ticker,
                        count=count,
                        include_current=True
                    )
                except Exception as e:
                    logger.error(f"Failed to load candles for {selected_ticker}: {e}")
                    flash(f'Error loading candles: {str(e)}', 'danger')

            return render_template(
                'candles.html',
                tickers=[t['symbol'] for t in tickers],
                selected_ticker=selected_ticker,
                candle_data=candle_data
            )
        except Exception as e:
            logger.error(f"Failed to load candles page: {e}")
            flash(f'Error loading page: {str(e)}', 'danger')
            return render_template('candles.html', tickers=[], selected_ticker=None, candle_data=None)

    @app.route('/config')
    @login_required
    def config():
        """Configuration management page."""
        try:
            config_data = api_client.get_config()
            # Extract the 'config' key from the response
            return render_template('config.html', config=config_data.get('config'))
        except Exception as e:
            logger.error(f"Failed to load config: {e}")
            flash(f'Error loading configuration: {str(e)}', 'danger')
            return render_template('config.html', config=None)

    @app.route('/logs')
    @login_required
    def logs():
        """Log viewer page - shows recent warnings and errors."""
        try:
            level_filter = request.args.get('level', None)
            if level_filter:
                level_filter = level_filter.upper().strip()
                if level_filter not in ('WARNING', 'ERROR'):
                    level_filter = None
            log_data = api_client.get_logs(limit=200, level=level_filter)
            return render_template(
                'logs.html',
                entries=log_data.get('entries', []),
                total_buffered=log_data.get('total_buffered', 0),
                level_filter=level_filter
            )
        except Exception as e:
            logger.error(f"Failed to load logs: {e}")
            flash(f'Error loading logs: {str(e)}', 'danger')
            return render_template('logs.html', entries=[], total_buffered=0, level_filter=None)

    # =========================================================================
    # API Routes - AJAX endpoints
    # =========================================================================

    @app.route('/api/tickers/add', methods=['POST'])
    @login_required
    def api_add_tickers():
        """Add tickers via AJAX."""
        try:
            data = request.get_json()
            tickers = data.get('tickers', [])

            if not tickers:
                return jsonify({'success': False, 'error': 'No tickers provided'}), 400

            result = api_client.add_tickers(tickers)
            return jsonify({'success': True, 'result': result})
        except Exception as e:
            logger.error(f"Failed to add tickers: {e}")
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route('/api/tickers/remove', methods=['POST'])
    @login_required
    def api_remove_tickers():
        """Remove tickers via AJAX."""
        try:
            data = request.get_json()
            tickers = data.get('tickers', [])

            if not tickers:
                return jsonify({'success': False, 'error': 'No tickers provided'}), 400

            if len(tickers) == 1:
                result = api_client.remove_ticker(tickers[0])
            else:
                result = api_client.remove_tickers(tickers)

            return jsonify({'success': True, 'result': result})
        except Exception as e:
            logger.error(f"Failed to remove tickers: {e}")
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route('/api/config/update', methods=['POST'])
    @login_required
    def api_update_config():
        """Update configuration via AJAX."""
        try:
            data = request.get_json()
            result = api_client.update_config(data)
            return jsonify({'success': True, 'result': result})
        except Exception as e:
            logger.error(f"Failed to update config: {e}")
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route('/api/config/reset', methods=['POST'])
    @login_required
    def api_reset_config():
        """Reset configuration via AJAX."""
        try:
            result = api_client.reset_config()
            return jsonify({'success': True, 'result': result})
        except Exception as e:
            logger.error(f"Failed to reset config: {e}")
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route('/api/reconnect', methods=['POST'])
    @login_required
    def api_reconnect():
        """Reconnect WebSocket via AJAX."""
        try:
            result = api_client.reconnect_websocket()
            return jsonify({'success': True, 'result': result})
        except Exception as e:
            logger.error(f"Failed to reconnect: {e}")
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route('/api/logs')
    @login_required
    def api_logs():
        """Get logs via AJAX (for auto-refresh)."""
        try:
            level_filter = request.args.get('level', None)
            if level_filter:
                level_filter = level_filter.upper().strip()
                if level_filter not in ('WARNING', 'ERROR'):
                    return jsonify({'success': False, 'error': 'Invalid level, must be WARNING or ERROR'}), 400
            try:
                limit = int(request.args.get('limit', 100))
            except (ValueError, TypeError):
                return jsonify({'success': False, 'error': 'Invalid limit parameter'}), 400
            limit = max(1, min(limit, 500))
            result = api_client.get_logs(limit=limit, level=level_filter)
            return jsonify({'success': True, 'result': result})
        except Exception as e:
            logger.error(f"Failed to get logs: {e}")
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route('/api/candles/<ticker>')
    @login_required
    def api_get_candles(ticker):
        """Get candles for a ticker via AJAX."""
        try:
            count = int(request.args.get('count', 50))
            result = api_client.get_candles(ticker, count=count)
            return jsonify({'success': True, 'result': result})
        except Exception as e:
            logger.error(f"Failed to get candles: {e}")
            return jsonify({'success': False, 'error': str(e)}), 500

    # =========================================================================
    # Error handlers
    # =========================================================================

    @app.errorhandler(404)
    def not_found(error):
        """404 error handler."""
        return render_template('404.html'), 404

    @app.errorhandler(500)
    def server_error(error):
        """500 error handler."""
        logger.error(f"Server error: {error}")
        return render_template('500.html'), 500

    return app


def main():
    """Run the Flask production server."""
    from dotenv import load_dotenv
    from waitress import serve

    # Load environment variables
    env_path = Path(__file__).parent.parent.parent / '.env'
    if env_path.exists():
        load_dotenv(env_path)

    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Create app
    app = create_app()

    # Run server
    host = app.config['ADMIN_HOST']
    port = app.config['ADMIN_PORT']

    logger.info("=" * 60)
    logger.info("EODHD Candle Aggregator - Admin UI")
    logger.info("=" * 60)
    logger.info(f"Admin UI listening on {host}:{port}")
    logger.info(f"Main API: {app.config['API_URL']}")
    logger.info(f"Debug - Host: '{host}' (len={len(host)}) Port: {port}")
    logger.info("=" * 60)

    # Use Waitress for production-ready WSGI server
    try:
        serve(app, host=host, port=port, threads=4)
    except Exception as e:
        logger.error(f"Failed to start Waitress server: {e}")
        logger.error(f"Host value: '{host}' (repr: {repr(host)})")
        logger.error(f"Port value: {port}")
        raise


if __name__ == '__main__':
    main()
