from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, StarTools, register

from .src.db import DatebookRepo, DBManager


@register(
    "astrbot_plugin_datebook", "lishining", "自定义节日日历与午间播报插件", "1.4.0"
)
class DatebookPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self._data_dir = Path(StarTools.get_data_dir())
        self.db_file = self._data_dir / "datebook.db"
        self.db = DBManager(self.db_file)
        self.repo = DatebookRepo(self.db)
        self.broadcast_task: asyncio.Task | None = None
        self.broadcast_running = True

    async def initialize(self) -> None:
        await self.repo.init()
        await self.start_broadcast_task()

    async def terminate(self) -> None:
        self.broadcast_running = False
        if self.broadcast_task and not self.broadcast_task.done():
            self.broadcast_task.cancel()
            try:
                await self.broadcast_task
            except asyncio.CancelledError:
                pass

    @filter.llm_tool(name="datebook_create_festival")
    async def datebook_create_festival(
        self,
        event: AstrMessageEvent,
        name: str,
        month: int,
        day: int,
        description: str = "",
    ) -> str:
        """Create a custom festival in the datebook.
        Args:
            name(string): Festival name.
            month(int): Month number, 1 to 12.
            day(int): Day number in the month.
            description(string, optional): Optional festival description.
        """
        try:
            safe_month, safe_day = self._validate_month_day(month, day)
            item = await self.repo.create_festival(
                name=self._require_name(name),
                month=safe_month,
                day=safe_day,
                description=str(description or "").strip(),
                created_by=self._event_session_id(event),
            )
            return self._json({"ok": True, "festival": item})
        except Exception as exc:
            return self._json({"ok": False, "error": str(exc)})

    @filter.llm_tool(name="datebook_update_festival")
    async def datebook_update_festival(
        self,
        event: AstrMessageEvent,
        festival_id: int,
        name: str = "",
        month: int = 0,
        day: int = 0,
        description: str = "",
        enabled: str = "",
    ) -> str:
        """Update a custom festival by id.
        Args:
            festival_id(int): Festival id to update.
            name(string, optional): New festival name. Empty means unchanged.
            month(int, optional): New month. Use with day. Zero means unchanged.
            day(int, optional): New day. Use with month. Zero means unchanged.
            description(string, optional): New description. Empty means unchanged.
            enabled(string, optional): true/false to enable or disable. Empty means unchanged.
        """
        try:
            updates: dict[str, Any] = {}
            if str(name or "").strip():
                updates["name"] = self._require_name(name)
            if month or day:
                safe_month, safe_day = self._validate_month_day(month, day)
                updates["month"] = safe_month
                updates["day"] = safe_day
            if str(description or "").strip():
                updates["description"] = str(description or "").strip()
            if str(enabled or "").strip():
                updates["enabled"] = self._parse_bool(enabled)
            if not updates:
                raise ValueError("没有可更新的字段")

            item = await self.repo.update_festival(int(festival_id), updates)
            if item is None:
                return self._json({"ok": False, "error": "节日不存在"})
            return self._json({"ok": True, "festival": item})
        except Exception as exc:
            return self._json({"ok": False, "error": str(exc)})

    @filter.llm_tool(name="datebook_delete_festival")
    async def datebook_delete_festival(
        self, event: AstrMessageEvent, festival_id: int
    ) -> str:
        """Delete a custom festival by id.
        Args:
            festival_id(int): Festival id to delete.
        """
        deleted = await self.repo.delete_festival(int(festival_id))
        return self._json({"ok": deleted, "festival_id": int(festival_id)})

    @filter.llm_tool(name="datebook_list_festivals")
    async def datebook_list_festivals(
        self,
        event: AstrMessageEvent,
        month: int = 0,
        day: int = 0,
        keyword: str = "",
        include_disabled: bool = False,
    ) -> str:
        """List custom festivals.
        Args:
            month(int, optional): Filter month. Zero means all months.
            day(int, optional): Filter day. Zero means all days.
            keyword(string, optional): Filter by name or description.
            include_disabled(bool, optional): Include disabled festivals.
        """
        safe_month, safe_day = self._normalize_optional_date(month, day)
        items = await self.repo.list_festivals(
            month=safe_month,
            day=safe_day,
            keyword=str(keyword or "").strip(),
            include_disabled=bool(include_disabled),
        )
        return self._json({"ok": True, "count": len(items), "festivals": items})

    @filter.llm_tool(name="datebook_set_daily_broadcast")
    async def datebook_set_daily_broadcast(
        self, event: AstrMessageEvent, enabled: bool = True
    ) -> str:
        """Enable or disable daily noon festival broadcast for the current session.
        Args:
            enabled(bool, optional): true to enable daily broadcast, false to disable it.
        """
        session_id = self._event_session_id(event)
        if not session_id:
            return self._json({"ok": False, "error": "无法获取当前会话"})
        item = await self.repo.set_session_enabled(session_id, bool(enabled))
        return self._json({"ok": True, "session": item})

    @filter.command("datebook")
    async def datebook_command(self, event: AstrMessageEvent):
        text = str(getattr(event, "message_str", "") or "").strip()
        args = self._strip_command(text).split()
        action = args[0] if args else "帮助"

        try:
            if action in {"帮助", "help"}:
                yield event.plain_result(self._help_text())
                return
            if action in {"今天", "today"}:
                festivals = await self.repo.list_today(date.today())
                yield event.plain_result(
                    self._format_daily_message(date.today(), festivals)
                )
                return
            if action in {"列表", "list"}:
                month = int(args[1]) if len(args) >= 2 else 0
                day = int(args[2]) if len(args) >= 3 else 0
                safe_month, safe_day = self._normalize_optional_date(month, day)
                items = await self.repo.list_festivals(month=safe_month, day=safe_day)
                yield event.plain_result(self._format_festival_list(items))
                return
            if action in {"添加", "add"}:
                if len(args) < 4:
                    yield event.plain_result("用法：/datebook 添加 月 日 名称 [描述]")
                    return
                month, day = self._validate_month_day(args[1], args[2])
                name = args[3]
                description = " ".join(args[4:]).strip()
                item = await self.repo.create_festival(
                    name=self._require_name(name),
                    month=month,
                    day=day,
                    description=description,
                    created_by=self._event_session_id(event),
                )
                yield event.plain_result(
                    f"已添加自定义节日：{item['id']}｜{item['month']:02d}-{item['day']:02d} {item['name']}"
                )
                return
            if action in {"删除", "delete", "del"}:
                if len(args) < 2:
                    yield event.plain_result("用法：/datebook 删除 节日ID")
                    return
                deleted = await self.repo.delete_festival(int(args[1]))
                yield event.plain_result("已删除" if deleted else "节日不存在")
                return
            if action in {"启用", "enable", "停用", "disable"}:
                if len(args) < 2:
                    yield event.plain_result(
                        "用法：/datebook 启用 节日ID 或 /datebook 停用 节日ID"
                    )
                    return
                item = await self.repo.update_festival(
                    int(args[1]), {"enabled": action in {"启用", "enable"}}
                )
                yield event.plain_result("已更新" if item else "节日不存在")
                return
            if action in {"订阅", "subscribe"}:
                await self.repo.set_session_enabled(self._event_session_id(event), True)
                yield event.plain_result("已开启本会话每日 12:00 自定义节日播报")
                return
            if action in {"取消订阅", "unsubscribe"}:
                await self.repo.set_session_enabled(
                    self._event_session_id(event), False
                )
                yield event.plain_result("已关闭本会话每日 12:00 自定义节日播报")
                return
            if action in {"测试播报", "test"}:
                await self._send_daily_message_to_session(
                    self._event_session_id(event), date.today()
                )
                yield event.plain_result("已发送今日节日播报测试消息")
                return

            yield event.plain_result(self._help_text())
        except Exception as exc:
            yield event.plain_result(f"操作失败：{exc}")

    async def start_broadcast_task(self) -> asyncio.Task:
        if self.broadcast_task and not self.broadcast_task.done():
            return self.broadcast_task
        self.broadcast_running = True
        self.broadcast_task = asyncio.create_task(self._periodic_noon_broadcast())
        return self.broadcast_task

    async def _periodic_noon_broadcast(self) -> None:
        while self.broadcast_running:
            try:
                await asyncio.sleep(self._seconds_until_next_noon())
                if not self.broadcast_running:
                    break
                await self._broadcast_today()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"[datebook] 午间节日播报失败：{exc}")
                await asyncio.sleep(60)

    async def _broadcast_today(self) -> None:
        today = date.today()
        sessions = await self.repo.list_enabled_sessions()
        for session in sessions:
            session_id = str(session["session_id"])
            if str(session.get("last_sent_date") or "") == today.isoformat():
                continue
            await self._send_daily_message_to_session(session_id, today)
            await self.repo.mark_session_sent(session_id, today.isoformat())

    async def _send_daily_message_to_session(
        self, session_id: str, target_date: date
    ) -> None:
        festivals = await self.repo.list_today(target_date)
        chain = MessageChain().message(
            self._format_daily_message(target_date, festivals)
        )
        await self._send_proactive_message(session_id, chain)

    async def _send_proactive_message(
        self, session_id: str, chain: MessageChain
    ) -> None:
        send = getattr(StarTools, "send_message", None)
        if callable(send):
            try:
                result = send(session_id, chain)
            except TypeError:
                result = send(self.context, session_id, chain)
            if asyncio.iscoroutine(result):
                await result
            return
        await self.context.send_message(session_id, chain)

    @staticmethod
    def _seconds_until_next_noon(now: datetime | None = None) -> float:
        now = now or datetime.now()
        noon = datetime.combine(now.date(), time(hour=12, minute=0))
        if now > noon:
            noon += timedelta(days=1)
        return max(0.0, (noon - now).total_seconds())

    @staticmethod
    def _validate_month_day(month: int | str, day: int | str) -> tuple[int, int]:
        safe_month = int(month)
        safe_day = int(day)
        date(2000, safe_month, safe_day)
        return safe_month, safe_day

    @staticmethod
    def _normalize_optional_date(
        month: int | str, day: int | str
    ) -> tuple[int | None, int | None]:
        safe_month = int(month or 0)
        safe_day = int(day or 0)
        if safe_month == 0 and safe_day == 0:
            return None, None
        if safe_month and safe_day:
            checked_month, checked_day = DatebookPlugin._validate_month_day(
                safe_month, safe_day
            )
            return checked_month, checked_day
        if safe_month:
            if safe_month < 1 or safe_month > 12:
                raise ValueError("月份必须在 1 到 12 之间")
            return safe_month, None
        raise ValueError("按日期筛选时不能只提供日期，请同时提供月份")

    @staticmethod
    def _parse_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        normalized = str(value).strip().lower()
        if normalized in {"true", "1", "yes", "y", "启用", "开启"}:
            return True
        if normalized in {"false", "0", "no", "n", "停用", "关闭"}:
            return False
        raise ValueError("enabled 只能是 true 或 false")

    @staticmethod
    def _require_name(name: str) -> str:
        normalized = str(name or "").strip()
        if not normalized:
            raise ValueError("节日名称不能为空")
        if len(normalized) > 80:
            raise ValueError("节日名称不能超过 80 个字符")
        return normalized

    @staticmethod
    def _event_session_id(event: AstrMessageEvent) -> str:
        return str(getattr(event, "unified_msg_origin", "") or "").strip()

    @staticmethod
    def _strip_command(text: str) -> str:
        if not text:
            return ""
        parts = text.split(maxsplit=1)
        if parts and parts[0].lstrip("/").lower() == "datebook":
            return parts[1] if len(parts) > 1 else ""
        return text

    @staticmethod
    def _format_daily_message(
        target_date: date, festivals: list[dict[str, Any]]
    ) -> str:
        title = f"{target_date.month:02d}-{target_date.day:02d} 自定义节日"
        if not festivals:
            return f"{title}\n今天没有自定义节日。"
        lines = [title]
        for item in festivals:
            line = f"- {item['name']}"
            description = str(item.get("description") or "").strip()
            if description:
                line += f"：{description}"
            lines.append(line)
        return "\n".join(lines)

    @staticmethod
    def _format_festival_list(items: list[dict[str, Any]]) -> str:
        if not items:
            return "没有找到自定义节日。"
        lines = ["自定义节日列表："]
        for item in items:
            status = "启用" if item.get("enabled") else "停用"
            description = str(item.get("description") or "").strip()
            suffix = f"｜{description}" if description else ""
            lines.append(
                f"{item['id']}. {int(item['month']):02d}-{int(item['day']):02d} {item['name']}｜{status}{suffix}"
            )
        return "\n".join(lines)

    @staticmethod
    def _help_text() -> str:
        return "\n".join(
            [
                "Datebook 自定义节日日历",
                "/datebook 今天",
                "/datebook 列表 [月] [日]",
                "/datebook 添加 月 日 名称 [描述]",
                "/datebook 删除 节日ID",
                "/datebook 启用 节日ID",
                "/datebook 停用 节日ID",
                "/datebook 订阅",
                "/datebook 取消订阅",
                "/datebook 测试播报",
            ]
        )

    @staticmethod
    def _json(payload: dict[str, Any]) -> str:
        return json.dumps(payload, ensure_ascii=False)
