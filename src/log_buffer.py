"""
In-memory ring buffer for WARNING/ERROR log entries, with optional
database persistence for cross-process visibility.

Attaches to the root logger and captures recent warning/error messages
for display in the admin panel without file I/O.
"""

import logging
import threading
import queue
from collections import deque
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional


class LogBufferHandler(logging.Handler):
    """
    A logging handler that stores recent WARNING+ entries in a ring buffer
    and optionally persists them to the database for cross-process access.
    
    Database writes are non-blocking: entries are queued and written by a
    background thread. If the queue is full, entries are dropped silently
    to avoid blocking the logging path.
    
    Thread-safe. Designed to be attached to the root logger so it captures
    messages from all modules.
    """

    def __init__(self, max_entries: int = 500, level: int = logging.WARNING):
        super().__init__(level=level)
        self._buffer: deque = deque(maxlen=max_entries)
        self._lock = threading.Lock()
        self._storage = None
        self._db_queue: queue.Queue = queue.Queue(maxsize=200)
        self._db_thread: Optional[threading.Thread] = None
        self._db_drop_count = 0

    def set_storage(self, storage):
        """
        Attach a storage backend for database persistence.
        Only enables DB persistence if the storage has save_log_entry().
        Starts a background thread that drains the queue to the DB.
        """
        if not hasattr(storage, 'save_log_entry') or not callable(getattr(storage, 'save_log_entry')):
            return  # Storage doesn't support log persistence
        self._storage = storage
        if self._db_thread is None or not self._db_thread.is_alive():
            self._db_thread = threading.Thread(
                target=self._db_writer_loop, daemon=True, name="log-db-writer"
            )
            self._db_thread.start()

    def _db_writer_loop(self):
        """Background thread that writes queued log entries to the database."""
        while True:
            try:
                entry = self._db_queue.get(timeout=5.0)
                if entry is None:
                    break  # Shutdown signal
                if self._storage is not None:
                    try:
                        self._storage.save_log_entry(
                            level=entry['level'],
                            logger_name=entry['logger'],
                            message=entry['message']
                        )
                    except Exception:
                        pass  # Don't recurse on DB errors
            except queue.Empty:
                continue

    def emit(self, record: logging.LogRecord):
        """Store a log record in the buffer and queue for DB persistence."""
        # Defensive level check for direct emit() calls
        if record.levelno < self.level:
            return
        try:
            entry = {
                'timestamp': datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
                'level': record.levelname,
                'logger': record.name,
                'message': self.format(record),
            }
            with self._lock:
                self._buffer.append(entry)
            
            # Non-blocking enqueue for DB persistence
            if self._storage is not None:
                try:
                    self._db_queue.put_nowait(entry)
                except queue.Full:
                    self._db_drop_count += 1
        except Exception:
            self.handleError(record)

    def shutdown(self):
        """Flush pending log entries and stop the background writer thread."""
        if self._db_thread is not None and self._db_thread.is_alive():
            self._db_queue.put(None)  # Sentinel to stop the loop
            self._db_thread.join(timeout=5.0)
        self._storage = None

    def get_entries(self, limit: int = 100, level: str = None) -> List[Dict[str, Any]]:
        """
        Get recent log entries, newest first.
        
        Args:
            limit: Maximum entries to return.
            level: Filter by level name (e.g., 'ERROR'). None returns all.
            
        Returns:
            List of log entry dicts.
        """
        with self._lock:
            entries = list(self._buffer)

        # Filter by level if specified
        if level:
            level_upper = level.upper()
            entries = [e for e in entries if e['level'] == level_upper]

        # Return newest first, limited
        entries.reverse()
        return entries[:limit]

    def clear(self):
        """Clear all buffered entries."""
        with self._lock:
            self._buffer.clear()

    @property
    def count(self) -> int:
        """Number of entries currently buffered."""
        with self._lock:
            return len(self._buffer)


# Process-local singleton. Each worker process has its own buffer.
# The admin panel queries the API worker's buffer via GET /logs.
_log_buffer: LogBufferHandler = None


def get_log_buffer() -> LogBufferHandler:
    """Get or create the global log buffer handler."""
    global _log_buffer
    if _log_buffer is None:
        _log_buffer = LogBufferHandler(max_entries=500, level=logging.WARNING)
        _log_buffer.setFormatter(logging.Formatter('%(message)s'))
    return _log_buffer


def install_log_buffer():
    """
    Install the log buffer handler on the root logger.
    
    Call this once during application startup, after logging.basicConfig().
    """
    handler = get_log_buffer()
    root_logger = logging.getLogger()
    # Avoid duplicate handlers
    if handler not in root_logger.handlers:
        root_logger.addHandler(handler)
