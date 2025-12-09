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
    
    # Candle Configuration
    candle_interval_minutes: int = field(default_factory=lambda: int(os.environ.get('CANDLE_INTERVAL_MINUTES', '5')))
    max_candles_stored: int = field(default_factory=lambda: int(os.environ.get('MAX_CANDLES_STORED', '100')))
    max_tickers: int = field(default_factory=lambda: int(os.environ.get('MAX_TICKERS', '50')))
    
    # WebSocket
    ws_reconnect_delay: int = field(default_factory=lambda: int(os.environ.get('WS_RECONNECT_DELAY', '5')))
    ws_ping_interval: int = field(default_factory=lambda: int(os.environ.get('WS_PING_INTERVAL', '30')))
    
    # Database
    database_path: str = field(default_factory=lambda: os.environ.get('DATABASE_PATH', _get_default_db_path()))
    
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
    
    def get_public_config(self) -> dict:
        """Get configuration safe to expose via API."""
        return {
            'candle_interval_minutes': self.candle_interval_minutes,
            'max_candles_stored': self.max_candles_stored,
            'max_tickers': self.max_tickers,
            'ws_reconnect_delay': self.ws_reconnect_delay,
            'ws_ping_interval': self.ws_ping_interval,
            'authentication_enabled': self.api_key is not None,
        }


class ConfigManager:
    """Manages runtime configuration with persistence."""
    
    def __init__(self, config: Config):
        self.config = config
        self._env_defaults = Config()  # Store original env defaults
        
    def update(self, updates: dict) -> dict:
        """
        Update configuration with provided values.
        Returns dict with 'updated' fields and any 'errors'.
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
            
            # Don't allow updating sensitive fields via API
            if key in ['eodhd_api_key', 'api_key', 'database_path', 'http_host', 'http_port']:
                errors.append(f"Cannot update {key} at runtime")
                continue
            
            setattr(self.config, key, value)
            updated.append(key)
            logger.info(f"Config updated: {key} = {value}")
        
        return {
            'updated': updated,
            'errors': errors,
            'config': self.config.get_public_config()
        }
    
    def reset_to_defaults(self) -> dict:
        """Reset configuration to environment variable defaults."""
        self.config = Config()
        logger.info("Configuration reset to defaults")
        return {
            'message': 'Configuration reset to defaults',
            'config': self.config.get_public_config()
        }
