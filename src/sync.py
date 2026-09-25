"""OneBot 全量群成员同步。

两条取数路径：
- 事件内：await event.get_group(group_id) → group.members（还能拿群主/管理补 role）
- 无事件（后台按钮/定时）：context.get_platform_inst(platform_id).bot.call_action("get_group_member_list")
官方通道没有该能力，直接返回 unsupported 并给出原因。
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List

try:  # 测试环境可无 astrbot
    from astrbot.api import logger
except Exception:  # noqa: BLE001
    import logging

    logger = logging.getLogger("nickname_registry")

from .models import CHANNEL_ONEBOT, SOURCE_SYNCED, SyncResult
from .platform_caps import channel_of_platform_id

BATCH_SIZE = 500
DEFAULT_TIMEOUT = 20.0

UNSUPPORTED_NOTE = "官方接口不提供群成员列表，只能记录发过言的人"


def _classify(exc: Exception) -> str:
    if isinstance(exc, asyncio.TimeoutError):
        return "timeout"
    text = str(exc).lower()
    if "permission" in text or "权限" in text or "denied" in text or "forbidden" in text:
        return "permission"
    return "unknown"


def _member_row(platform_id: str, group_id: str, member: Dict[str, Any], role: str = "") -> Dict[str, Any]:
    user_id = str(member.get("user_id") or member.get("id") or "").strip()
    return {
        "platform_id": str(platform_id),
        "group_id": str(group_id),
        "user_id": user_id,
        "channel": "onebot",
        "nickname": str(member.get("nickname") or "").strip(),
        "card": str(member.get("card") or "").strip(),
        "role": str(role or member.get("role") or "").strip(),
        "source": SOURCE_SYNCED,
        "msg_count": 0,
    }


class MemberSync:
    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin

    async def sync_group(
        self,
        platform_id: str,
        group_id: str,
        *,
        event: Any = None,
    ) -> SyncResult:
        pid = str(platform_id or "")
        gid = str(group_id or "")
        if not pid or not gid:
            return SyncResult(ok=False, reason="unknown", note="缺少平台实例或群号")
        if channel_of_platform_id(self.plugin, pid) != CHANNEL_ONEBOT:
            return SyncResult(ok=False, reason="unsupported", note=UNSUPPORTED_NOTE)

        timeout = DEFAULT_TIMEOUT
        try:
            settings = await self.plugin.get_settings()
            timeout = float(settings.get("sync_timeout", DEFAULT_TIMEOUT) or DEFAULT_TIMEOUT)
        except Exception:  # noqa: BLE001
            timeout = DEFAULT_TIMEOUT

        try:
            members = await asyncio.wait_for(
                self._fetch_members(pid, gid, event=event), timeout=timeout
            )
        except Exception as exc:  # noqa: BLE001 - 失败分类后交给调用方展示
            reason = _classify(exc)
            logger.warning("群 %s 成员同步失败（%s）: %s", gid, reason, exc)
            return SyncResult(ok=False, reason=reason, note=str(exc)[:200])

        rows = [_member_row(pid, gid, m) for m in members]
        rows = [r for r in rows if r["user_id"]]
        written = 0
        store = getattr(self.plugin, "store", None)
        if store is not None:
            for start in range(0, len(rows), BATCH_SIZE):
                chunk = rows[start:start + BATCH_SIZE]
                await store.upsert_members(chunk)
                written += len(chunk)
            try:
                await store.record_sync(pid, gid, total=len(rows), ok=1, note="")
            except Exception as exc:  # noqa: BLE001 - 覆盖度记录失败不影响同步结果
                logger.warning("记录群同步状态失败: %s", exc)
        logger.info("群 %s 成员同步完成：%d 人", gid, written)
        return SyncResult(ok=True, added=written, total=len(rows), note="")

    async def _fetch_members(
        self, platform_id: str, group_id: str, *, event: Any = None
    ) -> List[Dict[str, Any]]:
        if event is not None:
            group = await event.get_group(group_id)
            members = list(getattr(group, "members", None) or [])
            owner = str(getattr(group, "group_owner", "") or "")
            admins = {str(x) for x in (getattr(group, "group_admins", None) or [])}
            result = []
            for m in members:
                uid = str(getattr(m, "user_id", "") or "")
                role = "owner" if uid and uid == owner else ("admin" if uid in admins else "member")
                result.append({
                    "user_id": uid,
                    "nickname": str(getattr(m, "nickname", "") or ""),
                    "card": "",
                    "role": role,
                })
            return result

        context = getattr(self.plugin, "context", None)
        inst = None
        getter = getattr(context, "get_platform_inst", None)
        if callable(getter):
            inst = getter(platform_id)
        if inst is None:
            raise RuntimeError("未找到平台实例")
        bot = getattr(inst, "bot", None)
        if bot is None:
            raise RuntimeError("该平台实例不支持成员查询")
        params: Dict[str, Any] = {"group_id": int(group_id)}
        raw = await bot.call_action("get_group_member_list", **params)
        if isinstance(raw, dict) and "data" in raw:
            raw = raw.get("data")
        if not isinstance(raw, list):
            raise RuntimeError("协议端返回格式异常")
        return [m for m in raw if isinstance(m, dict)]
