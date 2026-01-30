# PostgreSQL Migration Plan

## Executive Summary

This document outlines the migration strategy from SQLite to PostgreSQL for the EODHD Real-time Candles system. The migration is designed to be low-risk, incremental, and reversible.

**Migration Trigger Criteria:**
- Retry warnings >5% of database operations
- Tracking >200 tickers with high-frequency updates
- Worker count >10-15
- Database file size >500MB
- Consistent API response times >500ms

**Estimated Effort:** 2-3 days (implementation + testing)
**Risk Level:** Low (well-abstracted storage layer)
**Downtime Required:** <5 minutes (for final cutover)

---

## Table of Contents

1. [Current Architecture](#current-architecture)
2. [Target Architecture](#target-architecture)
3. [Migration Phases](#migration-phases)
4. [Implementation Details](#implementation-details)
5. [Testing Strategy](#testing-strategy)
6. [Rollback Plan](#rollback-plan)
7. [Performance Benchmarks](#performance-benchmarks)
8. [Monitoring & Validation](#monitoring--validation)

---

## Current Architecture

### SQLite Configuration
- **File:** `/data/candles.db`
- **Journal Mode:** WAL (Write-Ahead Logging)
- **Connection Timeout:** 10 seconds
- **Busy Timeout:** 10 seconds
- **Cache Size:** 10MB per connection
- **Connections:** Thread-local (1 per worker thread)

### Current Limitations
- Single-writer bottleneck (even with WAL mode)
- Lock contention under high concurrent writes
- Limited horizontal scalability
- No built-in replication
- Performance degrades with file size >1GB

---

## Target Architecture

### PostgreSQL Configuration

**Database:** PostgreSQL 16+ (latest stable)
**Connection Pool:** pgbouncer or built-in connection pooling
**Deployment:** Docker container (official postgres:16-alpine image)

**Key Features:**
- MVCC (Multi-Version Concurrency Control) - readers don't block writers
- Superior concurrent write performance
- Native JSON/JSONB support for WebSocket status
- Better time-series performance with proper indexing
- Built-in replication support for future scaling
- Connection pooling for efficient resource usage

### Advantages Over SQLite
| Feature | SQLite | PostgreSQL |
|---------|--------|------------|
| Concurrent Writes | Limited (single writer) | Excellent (MVCC) |
| Concurrent Reads | Good (with WAL) | Excellent |
| Max Connections | Thread-local only | Pooled (100s-1000s) |
| Replication | Manual | Built-in |
| JSON Support | Basic | Native JSONB with indexing |
| Time-Series | Basic | Excellent (with TimescaleDB) |
| Horizontal Scaling | No | Yes (read replicas) |

---

## Migration Phases

### Phase 1: Preparation (Day 1 - Morning)
**Duration:** 2-3 hours

1. **Add PostgreSQL Dependencies**
   ```bash
   pip install psycopg2-binary asyncpg
   ```

2. **Create PostgreSQL Storage Adapter**
   - New file: `src/storage_postgres.py`
   - Implement same interface as `Storage` class
   - Use connection pooling from start

3. **Update Docker Compose**
   - Add PostgreSQL service
   - Add pgAdmin for management (optional)
   - Configure volumes for data persistence

4. **Create Migration Scripts**
   - Schema creation script
   - Data migration script (SQLite → PostgreSQL)
   - Validation script

### Phase 2: Implementation (Day 1 - Afternoon)
**Duration:** 4-5 hours

1. **Implement PostgreSQL Storage Class**
   - Connection pooling setup
   - All CRUD operations
   - Transaction management
   - Retry logic (simpler than SQLite)

2. **Update Configuration**
   - Add `DATABASE_TYPE` env var (sqlite/postgres)
   - Add PostgreSQL connection string
   - Factory pattern for storage selection

3. **Create Schema Migration**
   - SQL script for table creation
   - Indexes for performance
   - Constraints and foreign keys

### Phase 3: Testing (Day 2 - Morning)
**Duration:** 3-4 hours

1. **Unit Tests**
   - Run all existing storage tests against PostgreSQL
   - Add PostgreSQL-specific tests
   - Verify connection pooling

2. **Integration Tests**
   - Full system test with PostgreSQL
   - Multi-worker stress test
   - Concurrent write test

3. **Performance Benchmarks**
   - Compare SQLite vs PostgreSQL
   - Measure lock contention
   - Test under load

### Phase 4: Staging Deployment (Day 2 - Afternoon)
**Duration:** 2-3 hours

1. **Deploy to Staging**
   - Run with PostgreSQL
   - Monitor for 2-4 hours
   - Validate all operations

2. **Data Migration Test**
   - Export production SQLite data
   - Import to staging PostgreSQL
   - Verify data integrity

3. **Load Testing**
   - Simulate production load
   - Monitor performance metrics
   - Identify bottlenecks

### Phase 5: Production Migration (Day 3 or later)
**Duration:** 30 minutes + monitoring

1. **Pre-Migration**
   - Backup SQLite database
   - Announce maintenance window
   - Prepare rollback plan

2. **Migration**
   - Stop services (downtime starts)
   - Export SQLite data
   - Import to PostgreSQL
   - Validate data
   - Update configuration
   - Start services (downtime ends)

3. **Post-Migration**
   - Monitor for 24 hours
   - Validate all operations
   - Keep SQLite backup for 7 days

---

## Implementation Details

### File Structure


```
src/
├── storage.py              # SQLite implementation (current)
├── storage_postgres.py     # PostgreSQL implementation (new)
├── storage_factory.py      # Factory to select storage backend (new)
└── config.py              # Add DATABASE_TYPE config

scripts/
├── migrate_to_postgres.py  # Data migration script (new)
└── validate_migration.py   # Validation script (new)

docker-compose.yml          # Add PostgreSQL service
requirements.txt            # Add psycopg2-binary, asyncpg
```

### PostgreSQL Schema

```sql
-- Candles table with optimized indexes
CREATE TABLE candles (
    id BIGSERIAL PRIMARY KEY,
    ticker VARCHAR(10) NOT NULL,
    timestamp BIGINT NOT NULL,
    datetime_utc TIMESTAMP WITH TIME ZONE NOT NULL,
    open DECIMAL(12, 4) NOT NULL,
    high DECIMAL(12, 4) NOT NULL,
    low DECIMAL(12, 4) NOT NULL,
    close DECIMAL(12, 4) NOT NULL,
    volume BIGINT NOT NULL,
    tick_count INTEGER NOT NULL,
    is_complete BOOLEAN NOT NULL,
    interval_minutes INTEGER NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(ticker, timestamp, interval_minutes)
);

-- Optimized indexes for common queries
CREATE INDEX idx_candles_ticker_timestamp ON candles(ticker, timestamp DESC);
CREATE INDEX idx_candles_ticker_complete ON candles(ticker, is_complete) WHERE is_complete = true;
CREATE INDEX idx_candles_timestamp ON candles(timestamp DESC);

-- Tickers table
CREATE TABLE tickers (
    symbol VARCHAR(10) PRIMARY KEY,
    added_at TIMESTAMP WITH TIME ZONE NOT NULL,
    status VARCHAR(20) DEFAULT 'active',
    last_tick_at TIMESTAMP WITH TIME ZONE,
    last_price DECIMAL(12, 4),
    last_candle_request_at TIMESTAMP WITH TIME ZONE,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Config table
CREATE TABLE config (
    key VARCHAR(100) PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- WebSocket status table (using JSONB for better performance)
CREATE TABLE websocket_status (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    connected BOOLEAN NOT NULL,
    subscribed_tickers JSONB NOT NULL,
    subscribed_count INTEGER NOT NULL,
    pending_subscribe JSONB NOT NULL,
    connection_count INTEGER NOT NULL,
    tick_count BIGINT NOT NULL,
    last_message TEXT,
    last_update TIMESTAMP WITH TIME ZONE NOT NULL
);

-- Create index on JSONB columns for faster queries
CREATE INDEX idx_websocket_subscribed ON websocket_status USING GIN (subscribed_tickers);
```

### Docker Compose Configuration

```yaml
services:
  postgres:
    image: postgres:16-alpine
    container_name: eodhd_postgres
    environment:
      POSTGRES_DB: eodhd_candles
      POSTGRES_USER: eodhd_user
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_INITDB_ARGS: "-E UTF8 --locale=C"
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./scripts/init_postgres.sql:/docker-entrypoint-initdb.d/init.sql
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U eodhd_user -d eodhd_candles"]
      interval: 10s
      timeout: 5s
      retries: 5
    networks:
      - eodhd_network

  # Optional: pgAdmin for database management
  pgadmin:
    image: dpage/pgadmin4:latest
    container_name: eodhd_pgadmin
    environment:
      PGADMIN_DEFAULT_EMAIL: admin@eodhd.local
      PGADMIN_DEFAULT_PASSWORD: ${PGADMIN_PASSWORD}
    ports:
      - "5050:80"
    depends_on:
      - postgres
    networks:
      - eodhd_network

volumes:
  postgres_data:

networks:
  eodhd_network:
    driver: bridge
```

### Environment Variables

```bash
# .env additions
DATABASE_TYPE=postgres  # or 'sqlite' for rollback
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_DB=eodhd_candles
POSTGRES_USER=eodhd_user
POSTGRES_PASSWORD=your_secure_password_here
POSTGRES_POOL_SIZE=20
POSTGRES_MAX_OVERFLOW=10
```

### Connection Pooling Configuration

```python
# src/storage_postgres.py
import asyncpg
from asyncpg import Pool

class PostgreSQLStorage:
    def __init__(self, connection_string: str, pool_size: int = 20):
        self.connection_string = connection_string
        self.pool_size = pool_size
        self.pool: Optional[Pool] = None
    
    async def initialize(self):
        """Initialize connection pool."""
        self.pool = await asyncpg.create_pool(
            self.connection_string,
            min_size=5,
            max_size=self.pool_size,
            command_timeout=10.0,
            max_queries=50000,
            max_inactive_connection_lifetime=300.0
        )
    
    async def close(self):
        """Close connection pool."""
        if self.pool:
            await self.pool.close()
```

---

## Testing Strategy

### Unit Tests


**Objective:** Verify PostgreSQL storage implementation matches SQLite behavior

```bash
# Run existing tests against PostgreSQL
DATABASE_TYPE=postgres pytest tests/test_storage*.py -v

# Expected: All 51 tests pass
```

**New Tests to Add:**
- Connection pool exhaustion handling
- Concurrent write performance (should be better than SQLite)
- Transaction rollback behavior
- JSONB query performance

### Integration Tests

**Objective:** Verify full system works with PostgreSQL

```bash
# Start PostgreSQL in Docker
docker-compose up -d postgres

# Run full test suite
DATABASE_TYPE=postgres pytest tests/ -v

# Load test with multiple workers
DATABASE_TYPE=postgres python scripts/load_test.py --workers 10 --duration 300
```

### Performance Benchmarks

**Metrics to Compare:**

| Operation | SQLite (baseline) | PostgreSQL (target) | Improvement |
|-----------|-------------------|---------------------|-------------|
| Single write | ~1ms | <1ms | 0-20% faster |
| Concurrent writes (10) | ~50ms (locks) | ~5ms | 10x faster |
| Read query | ~0.5ms | ~0.5ms | Similar |
| Bulk insert (1000) | ~100ms | ~50ms | 2x faster |
| Complex query | ~10ms | ~5ms | 2x faster |

**Test Script:**
```python
# scripts/benchmark_storage.py
import time
import asyncio
from storage_factory import create_storage

async def benchmark_writes(storage, count=1000):
    start = time.time()
    for i in range(count):
        await storage.save_candle(create_test_candle(i))
    duration = time.time() - start
    print(f"Writes: {count} in {duration:.2f}s ({count/duration:.0f} ops/sec)")

async def benchmark_concurrent_writes(storage, workers=10, count=100):
    start = time.time()
    tasks = [benchmark_writes(storage, count) for _ in range(workers)]
    await asyncio.gather(*tasks)
    duration = time.time() - start
    total = workers * count
    print(f"Concurrent: {total} writes in {duration:.2f}s ({total/duration:.0f} ops/sec)")
```

---

## Rollback Plan

### Immediate Rollback (Within 1 hour of migration)

**If issues detected immediately:**

1. **Stop Services**
   ```bash
   docker-compose down
   ```

2. **Revert Configuration**
   ```bash
   # Change .env
   DATABASE_TYPE=sqlite
   ```

3. **Restart Services**
   ```bash
   docker-compose up -d
   ```

**Downtime:** ~2 minutes
**Data Loss:** None (SQLite backup still intact)

### Delayed Rollback (After 1 hour, before 7 days)

**If issues discovered later:**

1. **Export PostgreSQL Data**
   ```bash
   python scripts/export_postgres.py --output /backup/postgres_export.json
   ```

2. **Stop Services**
   ```bash
   docker-compose down
   ```

3. **Restore SQLite + Recent Data**
   ```bash
   # Restore SQLite backup
   cp /backup/candles.db.backup /data/candles.db
   
   # Import recent data from PostgreSQL
   python scripts/import_to_sqlite.py --input /backup/postgres_export.json
   ```

4. **Revert Configuration & Restart**
   ```bash
   DATABASE_TYPE=sqlite
   docker-compose up -d
   ```

**Downtime:** ~10 minutes
**Data Loss:** Minimal (only data during migration window)

### Rollback Decision Criteria

**Rollback if:**
- Error rate >1% of operations
- API response time >2x baseline
- Data corruption detected
- Critical functionality broken
- Lock contention worse than SQLite

**Monitor for 24 hours before declaring success**

---

## Data Migration Script

### Export from SQLite

```python
# scripts/migrate_to_postgres.py
import sqlite3
import asyncpg
import asyncio
from datetime import datetime

async def migrate_data(sqlite_path: str, postgres_dsn: str):
    """Migrate data from SQLite to PostgreSQL."""
    
    # Connect to both databases
    sqlite_conn = sqlite3.connect(sqlite_path)
    sqlite_conn.row_factory = sqlite3.Row
    pg_pool = await asyncpg.create_pool(postgres_dsn)
    
    try:
        # Migrate tickers
        print("Migrating tickers...")
        cursor = sqlite_conn.execute("SELECT * FROM tickers")
        tickers = cursor.fetchall()
        
        async with pg_pool.acquire() as conn:
            for ticker in tickers:
                await conn.execute('''
                    INSERT INTO tickers 
                    (symbol, added_at, status, last_tick_at, last_price, 
                     last_candle_request_at, updated_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                ''', ticker['symbol'], ticker['added_at'], ticker['status'],
                     ticker['last_tick_at'], ticker['last_price'],
                     ticker['last_candle_request_at'], ticker['updated_at'])
        
        print(f"Migrated {len(tickers)} tickers")
        
        # Migrate candles in batches
        print("Migrating candles...")
        cursor = sqlite_conn.execute("SELECT COUNT(*) FROM candles")
        total_candles = cursor.fetchone()[0]
        
        batch_size = 1000
        migrated = 0
        
        cursor = sqlite_conn.execute("SELECT * FROM candles ORDER BY id")
        
        while True:
            batch = cursor.fetchmany(batch_size)
            if not batch:
                break
            
            async with pg_pool.acquire() as conn:
                await conn.executemany('''
                    INSERT INTO candles 
                    (ticker, timestamp, datetime_utc, open, high, low, close,
                     volume, tick_count, is_complete, interval_minutes, created_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                ''', [(c['ticker'], c['timestamp'], c['datetime_utc'],
                       c['open'], c['high'], c['low'], c['close'],
                       c['volume'], c['tick_count'], bool(c['is_complete']),
                       c['interval_minutes'], c['created_at']) for c in batch])
            
            migrated += len(batch)
            print(f"Progress: {migrated}/{total_candles} candles ({migrated/total_candles*100:.1f}%)")
        
        print(f"Migrated {migrated} candles")
        
        # Migrate config
        print("Migrating config...")
        cursor = sqlite_conn.execute("SELECT * FROM config")
        configs = cursor.fetchall()
        
        async with pg_pool.acquire() as conn:
            for config in configs:
                await conn.execute('''
                    INSERT INTO config (key, value, updated_at)
                    VALUES ($1, $2, $3)
                ''', config['key'], config['value'], config['updated_at'])
        
        print(f"Migrated {len(configs)} config entries")
        
        # Migrate WebSocket status
        print("Migrating WebSocket status...")
        cursor = sqlite_conn.execute("SELECT * FROM websocket_status WHERE id = 1")
        ws_status = cursor.fetchone()
        
        if ws_status:
            async with pg_pool.acquire() as conn:
                await conn.execute('''
                    INSERT INTO websocket_status 
                    (id, connected, subscribed_tickers, subscribed_count,
                     pending_subscribe, connection_count, tick_count,
                     last_message, last_update)
                    VALUES ($1, $2, $3::jsonb, $4, $5::jsonb, $6, $7, $8, $9)
                ''', 1, bool(ws_status['connected']),
                     ws_status['subscribed_tickers'], ws_status['subscribed_count'],
                     ws_status['pending_subscribe'], ws_status['connection_count'],
                     ws_status['tick_count'], ws_status['last_message'],
                     ws_status['last_update'])
            
            print("Migrated WebSocket status")
        
        print("\n✅ Migration completed successfully!")
        
    finally:
        sqlite_conn.close()
        await pg_pool.close()

if __name__ == "__main__":
    import sys
    if len(sys.argv) != 3:
        print("Usage: python migrate_to_postgres.py <sqlite_path> <postgres_dsn>")
        sys.exit(1)
    
    asyncio.run(migrate_data(sys.argv[1], sys.argv[2]))
```

### Validation Script

```python
# scripts/validate_migration.py
import sqlite3
import asyncpg
import asyncio

async def validate_migration(sqlite_path: str, postgres_dsn: str):
    """Validate data integrity after migration."""
    
    sqlite_conn = sqlite3.connect(sqlite_path)
    pg_pool = await asyncpg.create_pool(postgres_dsn)
    
    errors = []
    
    try:
        # Validate ticker count
        sqlite_count = sqlite_conn.execute("SELECT COUNT(*) FROM tickers").fetchone()[0]
        async with pg_pool.acquire() as conn:
            pg_count = await conn.fetchval("SELECT COUNT(*) FROM tickers")
        
        if sqlite_count != pg_count:
            errors.append(f"Ticker count mismatch: SQLite={sqlite_count}, PostgreSQL={pg_count}")
        else:
            print(f"✅ Tickers: {sqlite_count} records match")
        
        # Validate candle count
        sqlite_count = sqlite_conn.execute("SELECT COUNT(*) FROM candles").fetchone()[0]
        async with pg_pool.acquire() as conn:
            pg_count = await conn.fetchval("SELECT COUNT(*) FROM candles")
        
        if sqlite_count != pg_count:
            errors.append(f"Candle count mismatch: SQLite={sqlite_count}, PostgreSQL={pg_count}")
        else:
            print(f"✅ Candles: {sqlite_count} records match")
        
        # Validate sample data integrity
        cursor = sqlite_conn.execute("""
            SELECT ticker, timestamp, open, high, low, close, volume
            FROM candles
            ORDER BY RANDOM()
            LIMIT 100
        """)
        
        sample_candles = cursor.fetchall()
        mismatches = 0
        
        async with pg_pool.acquire() as conn:
            for candle in sample_candles:
                pg_candle = await conn.fetchrow("""
                    SELECT open, high, low, close, volume
                    FROM candles
                    WHERE ticker = $1 AND timestamp = $2
                """, candle[0], candle[1])
                
                if not pg_candle:
                    mismatches += 1
                    continue
                
                # Compare values (allowing for small floating point differences)
                if (abs(float(pg_candle['open']) - candle[2]) > 0.0001 or
                    abs(float(pg_candle['high']) - candle[3]) > 0.0001 or
                    abs(float(pg_candle['low']) - candle[4]) > 0.0001 or
                    abs(float(pg_candle['close']) - candle[5]) > 0.0001 or
                    pg_candle['volume'] != candle[6]):
                    mismatches += 1
        
        if mismatches > 0:
            errors.append(f"Data integrity issues: {mismatches}/100 sample candles have mismatches")
        else:
            print(f"✅ Data integrity: 100 sample candles verified")
        
        if errors:
            print("\n❌ Validation FAILED:")
            for error in errors:
                print(f"  - {error}")
            return False
        else:
            print("\n✅ Validation PASSED: All checks successful!")
            return True
            
    finally:
        sqlite_conn.close()
        await pg_pool.close()

if __name__ == "__main__":
    import sys
    if len(sys.argv) != 3:
        print("Usage: python validate_migration.py <sqlite_path> <postgres_dsn>")
        sys.exit(1)
    
    result = asyncio.run(validate_migration(sys.argv[1], sys.argv[2]))
    sys.exit(0 if result else 1)
```

---

## Monitoring & Validation

### Key Metrics to Monitor

**Database Performance:**
```sql
-- Active connections
SELECT count(*) FROM pg_stat_activity WHERE datname = 'eodhd_candles';

-- Slow queries (>100ms)
SELECT query, mean_exec_time, calls
FROM pg_stat_statements
WHERE mean_exec_time > 100
ORDER BY mean_exec_time DESC
LIMIT 10;

-- Lock waits
SELECT * FROM pg_stat_database WHERE datname = 'eodhd_candles';

-- Table sizes
SELECT 
    schemaname,
    tablename,
    pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS size
FROM pg_tables
WHERE schemaname = 'public'
ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;
```

**Application Metrics:**
- API response times (should improve or stay same)
- Error rate (should be <0.1%)
- Database operation duration
- Connection pool utilization
- Retry frequency (should decrease significantly)

### Success Criteria

**Migration is successful if:**
- ✅ All data migrated with 100% integrity
- ✅ Error rate <0.1% for 24 hours
- ✅ API response times ≤ SQLite baseline
- ✅ No database lock errors
- ✅ Connection pool stable (<80% utilization)
- ✅ All tests passing
- ✅ No data loss or corruption

**Keep PostgreSQL if:**
- All success criteria met after 24 hours
- Performance equal or better than SQLite
- No critical issues discovered
- Team comfortable with new setup

---

## Cost Analysis

### Infrastructure Costs

**SQLite (Current):**
- Storage: Included in application server
- Backup: Minimal (file copy)
- Monitoring: None required
- **Total:** $0/month additional

**PostgreSQL (New):**
- Storage: ~$10-20/month (managed service) or $0 (self-hosted)
- Backup: ~$5/month (automated backups)
- Monitoring: ~$10/month (optional - pgAdmin, Datadog)
- **Total:** $0-35/month depending on hosting choice

### Operational Costs

**Time Investment:**
- Initial migration: 2-3 days
- Ongoing maintenance: +1 hour/month
- Learning curve: Minimal (team already knows SQL)

**Benefits:**
- Reduced debugging time (fewer lock issues)
- Better scalability (supports growth)
- Improved reliability (fewer errors)
- Future-proof architecture

---

## Timeline

### Recommended Schedule

**Week 1: Preparation**
- Day 1-2: Implement PostgreSQL storage adapter
- Day 3: Create migration scripts
- Day 4-5: Unit testing and fixes

**Week 2: Testing**
- Day 1-2: Integration testing
- Day 3: Performance benchmarking
- Day 4-5: Staging deployment and validation

**Week 3: Production**
- Day 1: Final preparation and backup
- Day 2: Production migration (low-traffic window)
- Day 3-7: Monitoring and validation

**Total Duration:** 3 weeks (can be compressed to 1 week if urgent)

---

## Conclusion

This migration plan provides a structured, low-risk approach to moving from SQLite to PostgreSQL. The well-abstracted storage layer makes this migration straightforward, and the comprehensive testing strategy ensures data integrity and system reliability.

**Key Takeaways:**
- Migration is low-risk due to good abstraction
- Rollback plan ensures safety
- Performance should improve, especially for concurrent writes
- Total effort: 2-3 days of focused work
- Downtime: <5 minutes for production cutover

**Next Steps:**
1. Monitor current SQLite performance metrics
2. Wait for migration trigger criteria
3. Execute Phase 1 when ready
4. Follow this plan step-by-step

**Questions or Concerns:**
- Review this plan with the team
- Test in staging environment first
- Keep SQLite backup for 7 days post-migration
- Monitor closely for first 24 hours

---

*Document Version: 1.0*  
*Last Updated: 2026-01-30*  
*Author: System Architecture Team*
