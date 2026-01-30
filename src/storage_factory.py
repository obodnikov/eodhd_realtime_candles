"""
Storage factory for selecting database backend.
Supports SQLite (default) and PostgreSQL.
"""

import os
import logging
from typing import Union

from .storage import Storage
from .config import Config

logger = logging.getLogger(__name__)


def create_storage(config: Config) -> Union[Storage, 'PostgreSQLStorage']:
    """
    Create storage instance based on configuration.
    
    Args:
        config: Application configuration
        
    Returns:
        Storage instance (SQLite or PostgreSQL)
        
    Environment Variables:
        DATABASE_TYPE: 'sqlite' (default) or 'postgres'
        
        For PostgreSQL:
        POSTGRES_HOST: Database host (default: localhost)
        POSTGRES_PORT: Database port (default: 5432)
        POSTGRES_DB: Database name (default: eodhd_candles)
        POSTGRES_USER: Database user (default: eodhd_user)
        POSTGRES_PASSWORD: Database password (required for postgres)
        POSTGRES_POOL_MIN: Min pool connections (default: 2)
        POSTGRES_POOL_MAX: Max pool connections (default: 10)
    """
    database_type = os.environ.get('DATABASE_TYPE', 'sqlite').lower()
    
    if database_type == 'postgres' or database_type == 'postgresql':
        return _create_postgres_storage()
    else:
        return _create_sqlite_storage(config)


def _create_sqlite_storage(config: Config) -> Storage:
    """Create SQLite storage instance."""
    logger.info(f"Using SQLite storage at {config.database_path}")
    return Storage(config.database_path)


def _create_postgres_storage() -> 'PostgreSQLStorage':
    """Create PostgreSQL storage instance."""
    from .storage_postgres import PostgreSQLStorage, PSYCOPG2_AVAILABLE
    
    if not PSYCOPG2_AVAILABLE:
        raise ImportError(
            "PostgreSQL storage requires psycopg2. "
            "Install with: pip install psycopg2-binary"
        )
    
    # Build connection string from environment
    host = os.environ.get('POSTGRES_HOST', 'localhost')
    port = os.environ.get('POSTGRES_PORT', '5432')
    database = os.environ.get('POSTGRES_DB', 'eodhd_candles')
    user = os.environ.get('POSTGRES_USER', 'eodhd_user')
    password = os.environ.get('POSTGRES_PASSWORD', '')
    
    if not password:
        raise ValueError("POSTGRES_PASSWORD environment variable is required for PostgreSQL")
    
    connection_string = f"host={host} port={port} dbname={database} user={user} password={password}"
    
    min_connections = int(os.environ.get('POSTGRES_POOL_MIN', '2'))
    max_connections = int(os.environ.get('POSTGRES_POOL_MAX', '10'))
    
    logger.info(f"Using PostgreSQL storage at {host}:{port}/{database}")
    
    return PostgreSQLStorage(
        connection_string=connection_string,
        min_connections=min_connections,
        max_connections=max_connections
    )


def get_database_type() -> str:
    """Get configured database type."""
    return os.environ.get('DATABASE_TYPE', 'sqlite').lower()


def is_postgres() -> bool:
    """Check if PostgreSQL is configured."""
    db_type = get_database_type()
    return db_type in ('postgres', 'postgresql')
