"""
Configuration management for the Candle Aggregator service.
Handles loading from environment variables and runtime updates.
"""

import os
from dataclasses import dataclass, field, asdict
from typing import List, Optional
from pathlib import Path
import json
import logging

logger = logging.getLogger(__name__)


def _get_default_db_path() -> str:
    """
    Get default database path.

    For Docker: /data/candles.db
    For local development: ./data/candles.db (relative to project root)
    """
    # Check if /data exists and is writable (Docker environment)
    if os.path.exists('/data') and os.access('/data', os.W_OK):
        return '/data/candles.db'

    # Use local data directory for development
    project_root = Path(__file__).parent.parent
    local_data_dir = project_root / 'data'
    return str(local_data_dir / 'candles.db')


@dataclass
class Config:
    """Application configuration with defaults from environment variables."""

    # EODHD API
    eodhd_api_key: str = field(default_factory=lambda: os.environ.get('EODHD_API_KEY', 'demo'))

    # HTTP Server
    http_host: str = field(default_factory=lambda: os.environ.get('HTTP_HOST', '0.0.0.0'))
    http_port: int = field(default_factory=lambda: int(os.environ.get('HTTP_PORT', '8765')))

    # API Authentication
    api_key: Optional[str] = field(default_factory=lambda: os.environ.get('API_KEY', '') or None)

    # Default Tickers
    default_tickers: List[str] = field(default_factory=lambda: [
        t.strip().upper()
        for t in os.environ.get('DEFAULT_TICKERS', 'AAPL,MSFT,GOOGL').split(',')
        if t.strip()
    ])

    # Ticker Management
    allow_delete_all_tickers: bool = field(default_factory=lambda: os.environ.get('ALLOW_DELETE_ALL_TICKERS', 'false').lower() == 'true')

    # Candle Configuration
    candle_interval_minutes: int = field(default_factory=lambda: int(os.environ.get('CANDLE_INTERVAL_MINUTES', '5')))
    max_candles_stored: int = field(default_factory=lambda: int(os.environ.get('MAX_CANDLES_STORED', '100')))
    max_tickers: int = field(default_factory=lambda: int(os.environ.get('MAX_TICKERS', '50')))

    # WebSocket
    ws_reconnect_delay: int = field(default_factory=lambda: int(os.environ.get('WS_RECONNECT_DELAY', '5')))
    ws_ping_interval: int = field(default_factory=lambda: int(os.environ.get('WS_PING_INTERVAL', '30')))
    ws_data_timeout: int = field(default_factory=lambda: int(os.environ.get('WS_DATA_TIMEOUT', '60')))
    ws_max_silent_timeout: int = field(default_factory=lambda: int(os.environ.get('WS_MAX_SILENT_TIMEOUT', '900')))
    ws_status_stale_seconds: int = field(default_factory=lambda: int(os.environ.get('WS_STATUS_STALE_SECONDS', '30')))
    
    # Ticker Sync (multi-worker mode)
    ticker_sync_interval_seconds: int = field(default_factory=lambda: int(os.environ.get('TICKER_SYNC_INTERVAL_SECONDS', '30')))

    # Database
    database_path: str = field(default_factory=lambda: os.environ.get('DATABASE_PATH', _get_default_db_path()))
    
    # Database Performance Tuning
    db_max_retries: int = field(default_factory=lambda: int(os.environ.get('DB_MAX_RETRIES', '3')))
    db_retry_base_delay_ms: int = field(default_factory=lambda: int(os.environ.get('DB_RETRY_BASE_DELAY_MS', '50')))
    db_busy_timeout_ms: int = field(default_factory=lambda: int(os.environ.get('DB_BUSY_TIMEOUT_MS', '10000')))
    
    # Candle Engine Performance Tuning
    candle_save_every_n_ticks: int = field(default_factory=lambda: int(os.environ.get('CANDLE_SAVE_EVERY_N_TICKS', '10')))
    candle_save_every_m_seconds: float = field(default_factory=lambda: float(os.environ.get('CANDLE_SAVE_EVERY_M_SECONDS', '5.0')))
    ticker_status_update_interval_seconds: float = field(default_factory=lambda: float(os.environ.get('TICKER_STATUS_UPDATE_INTERVAL_SECONDS', '1.0')))
    candle_write_queue_maxsize: int = field(default_factory=lambda: int(os.environ.get('CANDLE_WRITE_QUEUE_MAXSIZE', '10000')))
    tick_queue_maxsize: int = field(default_factory=lambda: int(os.environ.get('TICK_QUEUE_MAXSIZE', '50000')))
    tick_worker_concurrency: int = field(default_factory=lambda: int(os.environ.get('TICK_WORKER_CONCURRENCY', '100')))
    tick_max_age_seconds: int = field(default_factory=lambda: int(os.environ.get('TICK_MAX_AGE_SECONDS', '180')))

    # Persistence
    config_file: str = field(default_factory=lambda: os.environ.get('CONFIG_FILE', ''))
    persist_config: bool = field(default_factory=lambda: os.environ.get('PERSIST_CONFIG', 'true').lower() == 'true')

    # Logging
    log_level: str = field(default_factory=lambda: os.environ.get('LOG_LEVEL', 'INFO'))
    
    def validate(self) -> List[str]:
        """Validate configuration and return list of errors."""
        errors = []
        
        if not self.eodhd_api_key or self.eodhd_api_key == 'your_api_key_here':
            errors.append("EODHD_API_KEY is required")
        
        if self.candle_interval_minutes not in [1, 5, 15, 30, 60]:
            errors.append(f"Invalid candle_interval_minutes: {self.candle_interval_minutes}. Must be 1, 5, 15, 30, or 60")
        
        if self.max_tickers < 1 or self.max_tickers > 50:
            errors.append(f"max_tickers must be between 1 and 50, got {self.max_tickers}")
        
        if self.max_candles_stored < 1:
            errors.append(f"max_candles_stored must be at least 1")

        if self.ticker_status_update_interval_seconds <= 0:
            errors.append("ticker_status_update_interval_seconds must be > 0")

        if self.candle_write_queue_maxsize < 1:
            errors.append("candle_write_queue_maxsize must be at least 1")

        if self.tick_queue_maxsize < 1:
            errors.append(f"tick_queue_maxsize must be at least 1")

        if self.tick_worker_concurrency < 1:
            errors.append(f"tick_worker_concurrency must be at least 1")

        if self.tick_max_age_seconds < 0:
            errors.append("tick_max_age_seconds must be >= 0")
        
        return errors
    
    def to_dict(self, include_sensitive: bool = False) -> dict:
        """Convert to dictionary, optionally hiding sensitive values."""
        data = asdict(self)
        
        if not include_sensitive:
            # Hide sensitive values
            if data.get('eodhd_api_key'):
                data['eodhd_api_key'] = '***hidden***'
            if data.get('api_key'):
                data['api_key'] = '***hidden***'
        
        return data
    
    def get_public_config(self, include_source: bool = False, overrides: Optional[dict] = None) -> dict:
        """
        Get configuration safe to expose via API.

        Args:
            include_source: If True, include source info for each field
            overrides: Dictionary of runtime overrides (to determine source)

        Returns:
            Dictionary of public configuration
        """
        config = {
            'candle_interval_minutes': self.candle_interval_minutes,
            'max_candles_stored': self.max_candles_stored,
            'max_tickers': self.max_tickers,
            'ws_reconnect_delay': self.ws_reconnect_delay,
            'ws_ping_interval': self.ws_ping_interval,
            'ticker_status_update_interval_seconds': self.ticker_status_update_interval_seconds,
            'candle_write_queue_maxsize': self.candle_write_queue_maxsize,
            'tick_max_age_seconds': self.tick_max_age_seconds,
            'authentication_enabled': self.api_key is not None,
        }

        if include_source and overrides is not None:
            # Add source information for each field
            config_with_source = {}
            for key, value in config.items():
                if key == 'authentication_enabled':
                    # Skip source for derived fields
                    config_with_source[key] = value
                else:
                    config_with_source[key] = {
                        'value': value,
                        'source': 'runtime' if key in overrides else 'env'
                    }
            return config_with_source

        return config


class ConfigManager:
    """Manages runtime configuration with persistence."""

    def __init__(self, config: Config, config_storage=None):
        """
        Initialize config manager.

        Args:
            config: Initial configuration (from .env)
            config_storage: ConfigStorage instance (optional, for testing)
        """
        self.config = config
        self._env_defaults = Config()  # Store original env defaults
        self._overrides = {}  # Track runtime overrides

        # Initialize config storage
        if config_storage is None:
            from .storage import ConfigStorage
            config_path = config.config_file if config.config_file else None
            self._config_storage = ConfigStorage(config_path)
        else:
            self._config_storage = config_storage

        # Load persisted overrides if they exist and persistence is enabled
        if self.config.persist_config:
            self._load_overrides()

    def _load_overrides(self):
        """Load and apply persisted configuration overrides."""
        overrides = self._config_storage.load_config()
        if overrides:
            self._overrides = overrides
            # Apply overrides to config
            for key, value in overrides.items():
                if hasattr(self.config, key):
                    setattr(self.config, key, value)
                    logger.info(f"Applied persisted config: {key} = {value}")

    def _save_overrides(self) -> bool:
        """Save current overrides to file if persistence is enabled."""
        if not self.config.persist_config:
            logger.debug("Config persistence disabled, not saving")
            return False

        return self._config_storage.save_config(self._overrides)

    def update(self, updates: dict) -> dict:
        """
        Update configuration with provided values.

        Args:
            updates: Dictionary of config fields to update

        Returns:
            Dictionary with 'updated' fields, 'errors', and 'persisted' status
        """
        errors = []
        updated = []

        # Validate and apply updates
        for key, value in updates.items():
            if not hasattr(self.config, key):
                errors.append(f"Unknown configuration key: {key}")
                continue

            # Type checking and validation
            if key == 'candle_interval_minutes':
                if value not in [1, 5, 15, 30, 60]:
                    errors.append(f"Invalid candle_interval_minutes: {value}. Must be 1, 5, 15, 30, or 60")
                    continue

            if key == 'max_tickers':
                if not isinstance(value, int) or value < 1 or value > 50:
                    errors.append(f"max_tickers must be between 1 and 50")
                    continue

            if key == 'max_candles_stored':
                if not isinstance(value, int) or value < 1:
                    errors.append(f"max_candles_stored must be at least 1")
                    continue

            if key == 'ws_reconnect_delay':
                if not isinstance(value, int) or value < 1:
                    errors.append(f"ws_reconnect_delay must be at least 1")
                    continue

            if key == 'ws_ping_interval':
                if not isinstance(value, int) or value < 1:
                    errors.append(f"ws_ping_interval must be at least 1")
                    continue

            # Don't allow updating sensitive fields via API
            if key in ['eodhd_api_key', 'api_key', 'database_path', 'http_host', 'http_port', 'config_file', 'persist_config']:
                errors.append(f"Cannot update {key} at runtime")
                continue

            # Apply update
            setattr(self.config, key, value)
            self._overrides[key] = value
            updated.append(key)
            logger.info(f"Config updated: {key} = {value}")

        # Persist if updates were made
        persisted = False
        if updated:
            persisted = self._save_overrides()

        return {
            'updated': updated,
            'errors': errors,
            'persisted': persisted,
            'config': self.config.get_public_config(include_source=True, overrides=self._overrides)
        }

    def reset_to_defaults(self) -> dict:
        """Reset configuration to environment variable defaults."""
        # Clear overrides
        self._overrides = {}

        # Delete persisted config file
        deleted = self._config_storage.delete_config()

        # Reset to env defaults
        self.config = Config()

        logger.info("Configuration reset to defaults")

        return {
            'message': 'Configuration reset to defaults',
            'persisted_config_deleted': deleted,
            'config': self.config.get_public_config(include_source=True, overrides={})
        }

    def get_overrides(self) -> dict:
        """Get current runtime overrides."""
        return self._overrides.copy()
