from __future__ import annotations

import json
import sqlite3
from datetime import datetime

import pytest


class DummyContext:
    def __init__(self) -> None:
        self.sent_messages: list[tuple[str, object]] = []

    async def send_message(self, session_id: str, chain) -> None:
        self.sent_messages.append((session_id, chain))


class DummyEvent:
    def __init__(self, session_id: str = "session-1", message_str: str = "") -> None:
        self.unified_msg_origin = session_id
        self.message_str = message_str

    def plain_result(self, text: str):
        return {"kind": "plain", "text": text}


def make_plugin(main_module, tmp_path, config=None):
    main_module.StarTools.get_data_dir = staticmethod(lambda: str(tmp_path))
    main_module.StarTools.send_message = None
    plugin = main_module.DatebookPlugin(DummyContext(), config or {})
    plugin.db_file = tmp_path / "datebook.db"
    plugin.db = main_module.DBManager(plugin.db_file)
    plugin.repo = main_module.DatebookRepo(plugin.db)
    return plugin


@pytest.mark.asyncio
async def test_database_migrates_created_by_to_session_id(main_module, tmp_path):
    db_path = tmp_path / "datebook.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE datebook_festival (
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
            """
        )
        conn.execute(
            """
            INSERT INTO datebook_festival
                (name, month, day, description, enabled, created_by, created_at, updated_at)
            VALUES ('旧节日', 6, 9, '', 1, 'legacy-session', 'now', 'now')
            """
        )
        conn.commit()

    db = main_module.DBManager(db_path)
    await db.init_db()

    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(datebook_festival)")}
        session_id = conn.execute(
            "SELECT session_id FROM datebook_festival WHERE name = '旧节日'"
        ).fetchone()[0]

    assert "session_id" in columns
    assert session_id == "legacy-session"


@pytest.mark.asyncio
async def test_llm_tool_creates_and_lists_festival(main_module, tmp_path):
    plugin = make_plugin(main_module, tmp_path)
    event = DummyEvent("group-1")

    created = json.loads(
        await plugin.datebook_manage(
            event,
            action="create",
            name="摸鱼节",
            month=6,
            day=9,
            description="今天适合少开会",
        )
    )
    await plugin.datebook_manage(
        event, action="create", name="第二个节日", month=6, day=9
    )
    listed = json.loads(
        await plugin.datebook_manage(event, action="list", month=6, day=9)
    )

    assert created["ok"] is True
    assert created["festival"]["session_id"] == "group-1"
    assert listed["count"] == 2
    assert listed["festivals"][0]["name"] == "摸鱼节"


@pytest.mark.asyncio
async def test_festivals_are_scoped_to_session(main_module, tmp_path):
    plugin = make_plugin(main_module, tmp_path)
    await plugin.datebook_manage(
        DummyEvent("session-1"), action="create", name="一号会话节", month=6, day=9
    )
    await plugin.datebook_manage(
        DummyEvent("session-2"), action="create", name="二号会话节", month=6, day=9
    )

    session_1_list = json.loads(
        await plugin.datebook_manage(DummyEvent("session-1"), action="list", month=6, day=9)
    )
    session_2_list = json.loads(
        await plugin.datebook_manage(DummyEvent("session-2"), action="list", month=6, day=9)
    )

    assert session_1_list["count"] == 1
    assert session_1_list["festivals"][0]["name"] == "一号会话节"
    assert session_2_list["count"] == 1
    assert session_2_list["festivals"][0]["name"] == "二号会话节"


@pytest.mark.asyncio
async def test_update_and_delete_festival(main_module, tmp_path):
    plugin = make_plugin(main_module, tmp_path)
    event = DummyEvent()
    created = json.loads(
        await plugin.datebook_manage(
            event, action="create", name="旧节日", month=1, day=2
        )
    )
    festival_id = created["festival"]["id"]

    updated = json.loads(
        await plugin.datebook_manage(
            event,
            action="update",
            festival_id=festival_id,
            name="新节日",
            month=2,
            day=3,
            description="新描述",
            enabled="false",
        )
    )
    listed = json.loads(
        await plugin.datebook_manage(
            event, action="list", month=2, day=3, enabled="true"
        )
    )
    deleted = json.loads(
        await plugin.datebook_manage(event, action="delete", festival_id=festival_id)
    )

    assert updated["festival"]["name"] == "新节日"
    assert updated["festival"]["enabled"] is False
    assert listed["count"] == 1
    assert deleted == {"ok": True, "action": "delete", "festival_id": festival_id}


@pytest.mark.asyncio
async def test_update_and_delete_are_scoped_to_session(main_module, tmp_path):
    plugin = make_plugin(main_module, tmp_path)
    owner_event = DummyEvent("owner-session")
    other_event = DummyEvent("other-session")
    created = json.loads(
        await plugin.datebook_manage(
            owner_event, action="create", name="只能自己改", month=6, day=9
        )
    )
    festival_id = created["festival"]["id"]

    wrong_update = json.loads(
        await plugin.datebook_manage(
            other_event, action="update", festival_id=festival_id, name="越权修改"
        )
    )
    wrong_delete = json.loads(
        await plugin.datebook_manage(other_event, action="delete", festival_id=festival_id)
    )
    owner_list = json.loads(
        await plugin.datebook_manage(owner_event, action="list", month=6, day=9)
    )

    assert wrong_update == {"ok": False, "error": "节日不存在"}
    assert wrong_delete == {"ok": False, "action": "delete", "festival_id": festival_id}
    assert owner_list["festivals"][0]["name"] == "只能自己改"


@pytest.mark.asyncio
async def test_daily_broadcast_sends_once_per_session(main_module, tmp_path):
    plugin = make_plugin(
        main_module,
        tmp_path,
        {"enable_daily_broadcast": True},
    )
    await plugin.datebook_manage(
        DummyEvent("session-1"),
        action="create",
        name="测试节",
        month=6,
        day=9,
        description="用于播报",
    )
    await plugin.datebook_manage(
        DummyEvent("session-1"),
        action="create",
        name="同日第二节",
        month=6,
        day=9,
    )
    await plugin.datebook_manage(
        DummyEvent("session-2"),
        action="create",
        name="另一个会话的节日",
        month=6,
        day=9,
    )

    original_date = main_module.date

    class FrozenDate(original_date):
        @classmethod
        def today(cls):
            return cls(2026, 6, 9)

    main_module.date = FrozenDate
    try:
        await plugin._broadcast_today()
        await plugin._broadcast_today()
    finally:
        main_module.date = original_date

    assert len(plugin.context.sent_messages) == 2
    sent = {session_id: chain.chain[0]["text"] for session_id, chain in plugin.context.sent_messages}
    assert "测试节" in sent["session-1"]
    assert "同日第二节" in sent["session-1"]
    assert "另一个会话的节日" in sent["session-2"]


@pytest.mark.asyncio
async def test_daily_broadcast_skips_when_config_disabled(main_module, tmp_path):
    plugin = make_plugin(main_module, tmp_path, {"enable_daily_broadcast": False})
    await plugin.datebook_manage(
        DummyEvent("session-1"), action="create", name="不会播报", month=6, day=9
    )

    original_date = main_module.date

    class FrozenDate(original_date):
        @classmethod
        def today(cls):
            return cls(2026, 6, 9)

    main_module.date = FrozenDate
    try:
        await plugin._broadcast_today()
    finally:
        main_module.date = original_date

    assert plugin.context.sent_messages == []


def test_seconds_until_next_broadcast(main_module):
    plugin = main_module.DatebookPlugin(DummyContext(), {"broadcast_time": "12:00"})

    assert plugin._seconds_until_next_broadcast(datetime(2026, 6, 9, 11, 30)) == 1800
    assert plugin._seconds_until_next_broadcast(datetime(2026, 6, 9, 12, 0)) == 0
    assert plugin._seconds_until_next_broadcast(datetime(2026, 6, 9, 12, 1)) == 86340


@pytest.mark.asyncio
async def test_llm_tool_defaults_to_today(main_module, tmp_path):
    plugin = make_plugin(main_module, tmp_path)
    event = DummyEvent("session-1")
    original_date = main_module.date

    class FrozenDate(original_date):
        @classmethod
        def today(cls):
            return cls(2026, 6, 9)

    main_module.date = FrozenDate
    try:
        created = json.loads(
            await plugin.datebook_manage(event, action="create", name="默认今天")
        )
        listed = json.loads(await plugin.datebook_manage(event, action="list"))
    finally:
        main_module.date = original_date

    assert created["festival"]["month"] == 6
    assert created["festival"]["day"] == 9
    assert listed["count"] == 1


@pytest.mark.asyncio
async def test_command_add_and_today(main_module, tmp_path):
    plugin = make_plugin(main_module, tmp_path)
    add_event = DummyEvent("session-1", "/datebook 添加 6 9 命令节 用命令创建")
    today_event = DummyEvent("session-1", "/datebook 今天")

    add_result = [item async for item in plugin.datebook_command(add_event)]
    original_date = main_module.date

    class FrozenDate(original_date):
        @classmethod
        def today(cls):
            return cls(2026, 6, 9)

    main_module.date = FrozenDate
    try:
        today_result = [item async for item in plugin.datebook_command(today_event)]
    finally:
        main_module.date = original_date

    assert "已添加自定义节日" in add_result[0]["text"]
    assert "命令节" in today_result[0]["text"]
