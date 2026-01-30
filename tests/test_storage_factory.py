"""
Tests for storage factory module.

Tests the factory pattern for selecting between SQLite and PostgreSQL backends.
"""

import pytest
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

from src.config import Config
from src.storage import Storage
from src.storage_factory import (
    create_storage,
    get_database_type,
    is_postgres,
    _create_sqlite_storage
)


class TestGetDatabaseType:
    """Test get_database_type function."""

    def test_default_is_sqlite(self):
        """Test that default database type is sqlite."""
        with patch.dict(os.environ, {}, clear=True):
            # Remove DATABASE_TYPE if it exists
            os.environ.pop('DATABASE_TYPE', None)
            assert get_database_type() == 'sqlite'

    def test_sqlite_explicit(self):
        """Test explicit sqlite configuration."""
        with patch.dict(os.environ, {'DATABASE_TYPE': 'sqlite'}):
            assert get_database_type() == 'sqlite'

    def test_postgres_lowercase(self):
        """Test postgres configuration (lowercase)."""
        with patch.dict(os.environ, {'DATABASE_TYPE': 'postgres'}):
            assert get_database_type() == 'postgres'

    def test_postgresql_full_name(self):
        """Test postgresql configuration (full name)."""
        with patch.dict(os.environ, {'DATABASE_TYPE': 'postgresql'}):
            assert get_database_type() == 'postgresql'

    def test_case_insensitive(self):
        """Test that database type is case insensitive."""
        with patch.dict(os.environ, {'DATABASE_TYPE': 'POSTGRES'}):
            assert get_database_type() == 'postgres'

        with patch.dict(os.environ, {'DATABASE_TYPE': 'PostgreSQL'}):
            assert get_database_type() == 'postgresql'


class TestIsPostgres:
    """Test is_postgres function."""

    def test_false_for_sqlite(self):
        """Test is_postgres returns False for sqlite."""
        with patch.dict(os.environ, {'DATABASE_TYPE': 'sqlite'}):
            assert is_postgres() is False

    def test_false_for_default(self):
        """Test is_postgres returns False when not configured."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop('DATABASE_TYPE', None)
            assert is_postgres() is False

    def test_true_for_postgres(self):
        """Test is_postgres returns True for postgres."""
        with patch.dict(os.environ, {'DATABASE_TYPE': 'postgres'}):
            assert is_postgres() is True

    def test_true_for_postgresql(self):
        """Test is_postgres returns True for postgresql."""
        with patch.dict(os.environ, {'DATABASE_TYPE': 'postgresql'}):
            assert is_postgres() is True


class TestCreateSqliteStorage:
    """Test SQLite storage creation."""

    def test_creates_sqlite_storage(self):
        """Test that _create_sqlite_storage creates Storage instance."""
        # Use in-memory database to avoid file locking issues on Windows
        config = Config()
        config.database_path = ':memory:'
        
        storage = _create_sqlite_storage(config)
        
        assert isinstance(storage, Storage)

    def test_uses_config_path(self):
        """Test that storage uses path from config."""
        config = Config()
        config.database_path = ':memory:'
        
        storage = _create_sqlite_storage(config)
        
        assert storage.db_path == ':memory:'


class TestCreatePostgresStorage:
    """Test PostgreSQL storage creation."""

    def test_raises_without_password(self):
        """Test that missing password raises ValueError."""
        # Skip if psycopg2 not available
        psycopg2 = pytest.importorskip("psycopg2", reason="psycopg2 required")
        
        from src.storage_factory import _create_postgres_storage
        
        with patch.dict(os.environ, {
            'POSTGRES_HOST': 'localhost',
            'POSTGRES_PORT': '5432',
            'POSTGRES_DB': 'test',
            'POSTGRES_USER': 'user',
            'POSTGRES_PASSWORD': ''
        }):
            with pytest.raises(ValueError, match="POSTGRES_PASSWORD"):
                _create_postgres_storage()

    def test_creates_postgres_storage_with_valid_config(self):
        """Test PostgreSQL storage creation with valid configuration."""
        # Skip if psycopg2 not available
        psycopg2_mod = pytest.importorskip("psycopg2", reason="psycopg2 required")
        
        from src.storage_factory import _create_postgres_storage
        
        # Mock at the pool level to prevent actual connection
        with patch('src.storage_postgres.pool.ThreadedConnectionPool') as mock_pool_class:
            mock_pool = MagicMock()
            mock_pool_class.return_value = mock_pool
            
            # Mock connection for schema init
            mock_conn = MagicMock()
            mock_pool.getconn.return_value = mock_conn
            mock_cursor = MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            
            with patch.dict(os.environ, {
                'POSTGRES_HOST': 'localhost',
                'POSTGRES_PORT': '5432',
                'POSTGRES_DB': 'eodhd_candles',
                'POSTGRES_USER': 'eodhd_user',
                'POSTGRES_PASSWORD': 'secret',
                'POSTGRES_POOL_MIN': '2',
                'POSTGRES_POOL_MAX': '10'
            }):
                from src.storage_postgres import PostgreSQLStorage
                
                storage = _create_postgres_storage()
                
                assert isinstance(storage, PostgreSQLStorage)


class TestCreateStorage:
    """Test main create_storage factory function."""

    def test_creates_sqlite_by_default(self):
        """Test that create_storage creates SQLite storage by default."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop('DATABASE_TYPE', None)
            
            config = Config()
            config.database_path = ':memory:'
            
            storage = create_storage(config)
            
            assert isinstance(storage, Storage)

    def test_creates_sqlite_when_configured(self):
        """Test that create_storage creates SQLite when explicitly configured."""
        with patch.dict(os.environ, {'DATABASE_TYPE': 'sqlite'}):
            config = Config()
            config.database_path = ':memory:'
            
            storage = create_storage(config)
            
            assert isinstance(storage, Storage)

    @patch('src.storage_factory._create_postgres_storage')
    def test_creates_postgres_when_configured(self, mock_create_postgres):
        """Test that create_storage creates PostgreSQL when configured."""
        mock_storage = MagicMock()
        mock_create_postgres.return_value = mock_storage
        
        with patch.dict(os.environ, {'DATABASE_TYPE': 'postgres'}):
            config = Config()
            
            storage = create_storage(config)
            
            mock_create_postgres.assert_called_once()
            assert storage == mock_storage

    @patch('src.storage_factory._create_postgres_storage')
    def test_creates_postgres_for_postgresql_type(self, mock_create_postgres):
        """Test that create_storage handles 'postgresql' type."""
        mock_storage = MagicMock()
        mock_create_postgres.return_value = mock_storage
        
        with patch.dict(os.environ, {'DATABASE_TYPE': 'postgresql'}):
            config = Config()
            
            storage = create_storage(config)
            
            mock_create_postgres.assert_called_once()


class TestStorageFactoryIntegration:
    """Integration tests for storage factory."""

    def test_sqlite_storage_is_functional(self):
        """Test that created SQLite storage is fully functional."""
        with patch.dict(os.environ, {'DATABASE_TYPE': 'sqlite'}):
            config = Config()
            config.database_path = ':memory:'
            
            storage = create_storage(config)
            
            # Test basic operations
            assert storage.add_ticker('AAPL') is True
            assert storage.ticker_exists('AAPL') is True
            assert storage.get_ticker_count() == 1
            
            tickers = storage.get_ticker_symbols()
            assert 'AAPL' in tickers

    def test_storage_interface_compatibility(self):
        """Test that SQLite storage has expected interface."""
        config = Config()
        config.database_path = ':memory:'
        
        storage = create_storage(config)
        
        # Verify all expected methods exist
        assert hasattr(storage, 'add_ticker')
        assert hasattr(storage, 'remove_ticker')
        assert hasattr(storage, 'get_tickers')
        assert hasattr(storage, 'get_ticker_symbols')
        assert hasattr(storage, 'ticker_exists')
        assert hasattr(storage, 'get_ticker')
        assert hasattr(storage, 'get_ticker_count')
        assert hasattr(storage, 'save_candle')
        assert hasattr(storage, 'get_candles')
        assert hasattr(storage, 'get_current_candle')
        assert hasattr(storage, 'get_stats')
        assert hasattr(storage, 'update_websocket_status')
        assert hasattr(storage, 'get_websocket_status')
        assert hasattr(storage, 'update_active_candles')
        assert hasattr(storage, 'get_active_candles')


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
