from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, StarTools, register

from .src.db import DatebookRepo, DBManager


@register(
    "astrbot_plugin_datebook", "lishining", "自定义节日日历与午间播报插件", "1.4.1"
)
class DatebookPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | dict | None = None):
        super().__init__(context)
        self.config = config or {}
        self._data_dir = Path(StarTools.get_data_dir())
        self.db_file = self._data_dir / "datebook.db"
        self.db = DBManager(self.db_file)
        self.repo = DatebookRepo(self.db)
        self.broadcast_task: asyncio.Task | None = None
        self.broadcast_running = True

    async def initialize(self) -> None:
        await self.repo.init()
        if self._daily_broadcast_enabled():
            await self.start_broadcast_task()

    async def terminate(self) -> None:
        self.broadcast_running = False
        if self.broadcast_task and not self.broadcast_task.done():
            self.broadcast_task.cancel()
            try:
                await self.broadcast_task
            except asyncio.CancelledError:
                pass

    @filter.llm_tool(name="datebook_manage")
    async def datebook_manage(
        self,
        event: AstrMessageEvent,
        action: str = "today",
        festival_id: int = 0,
        name: str = "",
        month: int = 0,
        day: int = 0,
        description: str = "",
        enabled: str = "",
    ) -> str:
        """Manage custom festivals. Date arguments default to today when omitted.
        Args:
            action(string, optional): Operation name: create, update, delete, list, today.
            festival_id(int, optional): Festival id for update or delete.
            name(string, optional): Festival name for create or update.
            month(int, optional): Month number. Defaults to current month for create/list/today.
            day(int, optional): Day number. Defaults to current day for create/list/today.
            description(string, optional): Festival description for create or update.
            enabled(string, optional): true/false for update. Empty means unchanged.
        """
        try:
            normalized_action = str(action or "today").strip().lower()
            if normalized_action in {"create", "add", "添加", "创建"}:
                safe_month, safe_day = self._date_or_today(month, day)
                item = await self.repo.create_festival(
                    name=self._require_name(name),
                    month=safe_month,
                    day=safe_day,
                    description=str(description or "").strip(),
                    created_by=self._event_session_id(event),
                )
                return self._json({"ok": True, "action": "create", "festival": item})

            if normalized_action in {"update", "edit", "修改", "更新"}:
                updates: dict[str, Any] = {}
                if str(name or "").strip():
                    updates["name"] = self._require_name(name)
                if month or day:
                    safe_month, safe_day = self._date_or_today(month, day)
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
                return self._json({"ok": True, "action": "update", "festival": item})

            if normalized_action in {"delete", "del", "remove", "删除"}:
                deleted = await self.repo.delete_festival(int(festival_id))
                return self._json(
                    {"ok": deleted, "action": "delete", "festival_id": int(festival_id)}
                )

            if normalized_action in {"list", "search", "query", "列表", "查询"}:
                safe_month, safe_day = self._date_or_today(month, day)
                items = await self.repo.list_festivals(
                    month=safe_month,
                    day=safe_day,
                    keyword=str(name or "").strip(),
                    include_disabled=self._parse_optional_bool(enabled, False),
                )
                return self._json(
                    {
                        "ok": True,
                        "action": "list",
                        "month": safe_month,
                        "day": safe_day,
                        "count": len(items),
                        "festivals": items,
                    }
                )

            if normalized_action in {"today", "当天", "今天"}:
                safe_month, safe_day = self._date_or_today(month, day)
                items = await self.repo.list_festivals(
                    month=safe_month,
                    day=safe_day,
                    include_disabled=False,
                )
                return self._json(
                    {
                        "ok": True,
                        "action": "today",
                        "month": safe_month,
                        "day": safe_day,
                        "count": len(items),
                        "festivals": items,
                    }
                )

            raise ValueError("action 只能是 create、update、delete、list 或 today")
        except Exception as exc:
            return self._json({"ok": False, "error": str(exc)})

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
                await asyncio.sleep(self._seconds_until_next_broadcast())
                if not self.broadcast_running:
                    break
                await self._broadcast_today()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"[datebook] 午间节日播报失败：{exc}")
                await asyncio.sleep(60)

    async def _broadcast_today(self) -> None:
        if not self._daily_broadcast_enabled():
            return
        today = date.today()
        sessions = self._broadcast_sessions()
        for session_id in sessions:
            session = await self.repo.set_session_enabled(session_id, True)
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

    def _seconds_until_next_broadcast(self, now: datetime | None = None) -> float:
        now = now or datetime.now()
        broadcast_time = self._broadcast_time()
        target = datetime.combine(now.date(), broadcast_time)
        if now > target:
            target += timedelta(days=1)
        return max(0.0, (target - now).total_seconds())

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
    def _date_or_today(month: int | str, day: int | str) -> tuple[int, int]:
        safe_month = int(month or 0)
        safe_day = int(day or 0)
        if safe_month == 0 and safe_day == 0:
            today = date.today()
            return today.month, today.day
        if safe_month == 0 or safe_day == 0:
            today = date.today()
            safe_month = safe_month or today.month
            safe_day = safe_day or today.day
        return DatebookPlugin._validate_month_day(safe_month, safe_day)

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
    def _parse_optional_bool(value: Any, default: bool) -> bool:
        if str(value or "").strip() == "":
            return default
        return DatebookPlugin._parse_bool(value)

    def _daily_broadcast_enabled(self) -> bool:
        return self._parse_optional_bool(
            self.config.get("enable_daily_broadcast", False), False
        )

    def _broadcast_sessions(self) -> list[str]:
        raw_sessions = self.config.get("broadcast_sessions", []) or []
        if isinstance(raw_sessions, str):
            raw_sessions = raw_sessions.replace("，", ",").split(",")
        sessions: list[str] = []
        for item in raw_sessions:
            normalized = str(item or "").strip()
            if normalized and normalized not in sessions:
                sessions.append(normalized)
        return sessions

    def _broadcast_time(self) -> time:
        raw_time = str(self.config.get("broadcast_time", "12:00") or "12:00").strip()
        try:
            hour_text, minute_text = raw_time.split(":", 1)
            return time(hour=int(hour_text), minute=int(minute_text))
        except Exception:
            return time(hour=12, minute=0)

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
                "/datebook 测试播报",
                "每日播报请在插件配置中开启，并填写 broadcast_sessions。",
            ]
        )

    @staticmethod
    def _json(payload: dict[str, Any]) -> str:
        return json.dumps(payload, ensure_ascii=False)
