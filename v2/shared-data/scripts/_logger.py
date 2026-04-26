"""Structured logging helper for QuantAI Python scripts.

Adds a SQLiteHandler to the root logger that writes WARNING+ events into a small
inbox table in /var/dashboard/errors.db. The cron collector (every 2 min) drains
the inbox, normalizes signatures, matches catalog entries, dedups, and promotes
into the main `events` table.

Usage:
    from _logger import setup
    setup("position_monitor")  # call once near the top of the script

    import logging
    logging.warning("Close order failed for %s — will retry", trade_id)
    logging.error("Alpaca rejected order: %s", body, exc_info=True)

The handler is purely additive: it does NOT replace existing print()/log()
behavior. Existing stdout/stderr capture continues to work via the textfile
ingestors. Only `logging` calls reach the structured pipeline.

Safety: emit() is wrapped in try/except so a logging failure can NEVER crash
the calling script.
"""
import logging
import os
import sqlite3
from datetime import datetime, timezone

DB_PATH = "/var/dashboard/errors.db"
INBOX_TABLE = "pyapp_inbox"


class SQLiteHandler(logging.Handler):
    """Logging handler that inserts WARNING+ records into pyapp_inbox table.

    The collector promotes inbox rows into `events` with full normalization,
    catalog matching, and dedup. We don't do any of that here — this handler
    is a write-only path optimized for "never crash the app".
    """

    def __init__(self, source_name: str, db_path: str = DB_PATH):
        super().__init__()
        self.source = f"pyapp:{source_name}"
        self.db_path = db_path
        self._db_present = os.path.exists(db_path) or self._init_inbox()

    def _init_inbox(self) -> bool:
        """Create the inbox table if the DB exists but the table doesn't.
        Does NOT create the DB itself — that's the collector's job. We just
        ensure our table exists.
        """
        try:
            if not os.path.exists(self.db_path):
                return False
            conn = sqlite3.connect(self.db_path, timeout=2.0, isolation_level=None)
            try:
                conn.execute(f"""
                    CREATE TABLE IF NOT EXISTS {INBOX_TABLE} (
                        id        INTEGER PRIMARY KEY AUTOINCREMENT,
                        source    TEXT NOT NULL,
                        severity  TEXT NOT NULL,
                        message   TEXT NOT NULL,
                        ts        TEXT NOT NULL
                    )
                """)
            finally:
                conn.close()
            return True
        except Exception:
            return False

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = record.levelno
            if level >= logging.CRITICAL:
                severity = "critical"
            elif level >= logging.ERROR:
                severity = "error"
            elif level >= logging.WARNING:
                severity = "warning"
            else:
                return  # info/debug filtered out at handler level too
            message = self.format(record)
            if len(message) > 2000:
                message = message[:2000]
            ts = datetime.now(timezone.utc).isoformat()
            conn = sqlite3.connect(self.db_path, timeout=2.0, isolation_level=None)
            try:
                conn.execute(
                    f"INSERT INTO {INBOX_TABLE} (source, severity, message, ts) VALUES (?, ?, ?, ?)",
                    (self.source, severity, message, ts),
                )
            finally:
                conn.close()
        except Exception:
            # Logging must never crash the app. Swallow all exceptions silently.
            pass


def setup(source_name: str, level=logging.WARNING):
    """Add a SQLiteHandler to the root logger. Idempotent: calling twice does nothing."""
    root = logging.getLogger()
    # Skip if already attached (idempotent)
    for h in root.handlers:
        if isinstance(h, SQLiteHandler) and h.source == f"pyapp:{source_name}":
            return root
    handler = SQLiteHandler(source_name)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(handler)
    # Don't lower the existing root level if it's already verbose; just ensure
    # at least WARNING is enabled so our handler sees events.
    if root.level == logging.NOTSET or root.level > level:
        root.setLevel(level)
    return root
