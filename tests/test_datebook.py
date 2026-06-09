from __future__ import annotations

import json
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
async def test_llm_tool_creates_and_lists_festival(main_module, tmp_path):
    plugin = make_plugin(main_module, tmp_path)
    event = DummyEvent("group-1")

    created = json.loads(
        await plugin.datebook_manage(
            event, action="create", name="摸鱼节", month=6, day=9, description="今天适合少开会"
        )
    )
    await plugin.datebook_manage(
        event, action="create", name="第二个节日", month=6, day=9
    )
    listed = json.loads(
        await plugin.datebook_manage(event, action="list", month=6, day=9)
    )

    assert created["ok"] is True
    assert created["festival"]["created_by"] == "group-1"
    assert listed["count"] == 2
    assert listed["festivals"][0]["name"] == "摸鱼节"


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
async def test_daily_broadcast_sends_once_per_session(main_module, tmp_path):
    plugin = make_plugin(
        main_module,
        tmp_path,
        {"enable_daily_broadcast": True, "broadcast_sessions": ["session-1"]},
    )
    event = DummyEvent("session-1")
    await plugin.datebook_manage(
        event, action="create", name="测试节", month=6, day=9, description="用于播报"
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

    assert len(plugin.context.sent_messages) == 1
    session_id, chain = plugin.context.sent_messages[0]
    assert session_id == "session-1"
    assert "测试节" in chain.chain[0]["text"]


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
