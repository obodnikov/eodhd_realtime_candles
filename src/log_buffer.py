"""
In-memory ring buffer for WARNING/ERROR log entries.

Attaches to the root logger and captures recent warning/error messages
for display in the admin panel without file I/O.
"""

import logging
import threading
from collections import deque
from datetime import datetime, timezone
from typing import List, Dict, Any


class LogBufferHandler(logging.Handler):
    """
    A logging handler that stores recent WARNING+ entries in a ring buffer.
    
    Thread-safe. Designed to be attached to the root logger so it captures
    messages from all modules.
    """

    def __init__(self, max_entries: int = 500, level: int = logging.WARNING):
        super().__init__(level=level)
        self._buffer: deque = deque(maxlen=max_entries)
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord):
        """Store a log record in the buffer."""
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
        except Exception:
            self.handleError(record)

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
