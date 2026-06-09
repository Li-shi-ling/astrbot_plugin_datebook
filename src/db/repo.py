from __future__ import annotations

import asyncio
from datetime import date, datetime
from typing import Any

from .database import DBManager


class DatebookRepo:
    def __init__(self, db_manager: DBManager):
        self.db = db_manager

    async def init(self) -> None:
        await self.db.init_db()

    async def create_festival(
        self,
        *,
        name: str,
        month: int,
        day: int,
        description: str,
        created_by: str,
    ) -> dict[str, Any]:
        await self.db.init_db()
        return await asyncio.to_thread(
            self._create_festival_sync,
            name,
            month,
            day,
            description,
            created_by,
        )

    def _create_festival_sync(
        self,
        name: str,
        month: int,
        day: int,
        description: str,
        created_by: str,
    ) -> dict[str, Any]:
        now = self._now()
        with self.db._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO datebook_festival
                    (name, month, day, description, enabled, created_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (name, int(month), int(day), description, created_by, now, now),
            )
            conn.commit()
            row = conn.execute(
                """
                SELECT id, name, month, day, description, enabled, created_by, created_at, updated_at
                FROM datebook_festival
                WHERE id = ?
                """,
                (cursor.lastrowid,),
            ).fetchone()
        return self._festival_row_to_dict(row)

    async def update_festival(
        self, festival_id: int, updates: dict[str, Any]
    ) -> dict[str, Any] | None:
        await self.db.init_db()
        return await asyncio.to_thread(self._update_festival_sync, festival_id, updates)

    def _update_festival_sync(
        self, festival_id: int, updates: dict[str, Any]
    ) -> dict[str, Any] | None:
        allowed = {"name", "month", "day", "description", "enabled"}
        fields = {key: value for key, value in updates.items() if key in allowed}
        if not fields:
            return self.get_festival_sync(festival_id)

        assignments = [f"{key} = ?" for key in fields]
        values: list[Any] = list(fields.values())
        assignments.append("updated_at = ?")
        values.append(self._now())
        values.append(int(festival_id))

        with self.db._connect() as conn:
            conn.execute(
                f"""
                UPDATE datebook_festival
                SET {", ".join(assignments)}
                WHERE id = ?
                """,
                values,
            )
            conn.commit()
        return self.get_festival_sync(festival_id)

    async def delete_festival(self, festival_id: int) -> bool:
        await self.db.init_db()
        return await asyncio.to_thread(self._delete_festival_sync, festival_id)

    def _delete_festival_sync(self, festival_id: int) -> bool:
        with self.db._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM datebook_festival WHERE id = ?", (int(festival_id),)
            )
            conn.commit()
        return cursor.rowcount > 0

    async def list_festivals(
        self,
        *,
        month: int | None = None,
        day: int | None = None,
        keyword: str = "",
        include_disabled: bool = False,
    ) -> list[dict[str, Any]]:
        await self.db.init_db()
        return await asyncio.to_thread(
            self._list_festivals_sync,
            month,
            day,
            keyword,
            include_disabled,
        )

    async def list_today(self, target_date: date) -> list[dict[str, Any]]:
        return await self.list_festivals(
            month=target_date.month,
            day=target_date.day,
            include_disabled=False,
        )

    def _list_festivals_sync(
        self,
        month: int | None,
        day: int | None,
        keyword: str,
        include_disabled: bool,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if not include_disabled:
            clauses.append("enabled = 1")
        if month is not None:
            clauses.append("month = ?")
            values.append(int(month))
        if day is not None:
            clauses.append("day = ?")
            values.append(int(day))
        normalized_keyword = str(keyword or "").strip()
        if normalized_keyword:
            clauses.append("(name LIKE ? OR description LIKE ?)")
            like_value = f"%{normalized_keyword}%"
            values.extend([like_value, like_value])

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.db._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT id, name, month, day, description, enabled, created_by, created_at, updated_at
                FROM datebook_festival
                {where_sql}
                ORDER BY month, day, id
                """,
                values,
            ).fetchall()
        return [self._festival_row_to_dict(row) for row in rows]

    async def set_session_enabled(
        self, session_id: str, enabled: bool
    ) -> dict[str, Any]:
        await self.db.init_db()
        return await asyncio.to_thread(
            self._set_session_enabled_sync, session_id, enabled
        )

    def _set_session_enabled_sync(
        self, session_id: str, enabled: bool
    ) -> dict[str, Any]:
        now = self._now()
        normalized_session = str(session_id or "").strip()
        with self.db._connect() as conn:
            conn.execute(
                """
                INSERT INTO datebook_session
                    (session_id, enabled, last_sent_date, created_at, updated_at)
                VALUES (?, ?, '', ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    enabled = excluded.enabled,
                    updated_at = excluded.updated_at
                """,
                (normalized_session, 1 if enabled else 0, now, now),
            )
            conn.commit()
            row = conn.execute(
                """
                SELECT session_id, enabled, last_sent_date, created_at, updated_at
                FROM datebook_session
                WHERE session_id = ?
                """,
                (normalized_session,),
            ).fetchone()
        return self._session_row_to_dict(row)

    async def list_enabled_sessions(self) -> list[dict[str, Any]]:
        await self.db.init_db()
        return await asyncio.to_thread(self._list_enabled_sessions_sync)

    def _list_enabled_sessions_sync(self) -> list[dict[str, Any]]:
        with self.db._connect() as conn:
            rows = conn.execute(
                """
                SELECT session_id, enabled, last_sent_date, created_at, updated_at
                FROM datebook_session
                WHERE enabled = 1
                ORDER BY session_id
                """
            ).fetchall()
        return [self._session_row_to_dict(row) for row in rows]

    async def mark_session_sent(self, session_id: str, sent_date: str) -> None:
        await self.db.init_db()
        await asyncio.to_thread(self._mark_session_sent_sync, session_id, sent_date)

    def _mark_session_sent_sync(self, session_id: str, sent_date: str) -> None:
        now = self._now()
        with self.db._connect() as conn:
            conn.execute(
                """
                UPDATE datebook_session
                SET last_sent_date = ?, updated_at = ?
                WHERE session_id = ?
                """,
                (sent_date, now, str(session_id or "").strip()),
            )
            conn.commit()

    def get_festival_sync(self, festival_id: int) -> dict[str, Any] | None:
        with self.db._connect() as conn:
            row = conn.execute(
                """
                SELECT id, name, month, day, description, enabled, created_by, created_at, updated_at
                FROM datebook_festival
                WHERE id = ?
                """,
                (int(festival_id),),
            ).fetchone()
        return self._festival_row_to_dict(row) if row else None

    @staticmethod
    def _festival_row_to_dict(row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "name": str(row["name"]),
            "month": int(row["month"]),
            "day": int(row["day"]),
            "description": str(row["description"] or ""),
            "enabled": bool(row["enabled"]),
            "created_by": str(row["created_by"] or ""),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }

    @staticmethod
    def _session_row_to_dict(row) -> dict[str, Any]:
        return {
            "session_id": str(row["session_id"]),
            "enabled": bool(row["enabled"]),
            "last_sent_date": str(row["last_sent_date"] or ""),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec="seconds")
