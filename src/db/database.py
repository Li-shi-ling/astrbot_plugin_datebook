from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path


class DBManager:
    _CREATE_TABLE_SQL = (
        """
        CREATE TABLE IF NOT EXISTS datebook_festival (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            month INTEGER NOT NULL,
            day INTEGER NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1,
            created_by TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS datebook_session (
            session_id TEXT PRIMARY KEY,
            enabled INTEGER NOT NULL DEFAULT 1,
            last_sent_date TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_datebook_festival_date ON datebook_festival(month, day)",
        "CREATE INDEX IF NOT EXISTS idx_datebook_session_enabled ON datebook_session(enabled)",
    )

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_lock = asyncio.Lock()
        self._initialized = False

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    async def init_db(self) -> None:
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            await asyncio.to_thread(self._init_db_sync)
            self._initialized = True

    def _init_db_sync(self) -> None:
        with self._connect() as conn:
            for sql in self._CREATE_TABLE_SQL:
                conn.execute(sql)
            conn.commit()
