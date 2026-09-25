"""群内查询指令（仅后台管理员可用）。"""
from __future__ import annotations

import re
import time
from typing import Any, AsyncIterator, Dict, List

try:  # 测试环境可无 astrbot
    from astrbot.api import logger
except Exception:  # noqa: BLE001
    import logging

    logger = logging.getLogger("nickname_registry")

from .models import MAX_COMMAND_ROWS

RE_QQ = re.compile(r"^(?:查\s*qq|查qq号|查QQ号?)\s+(\d{5,12})$", re.I)
RE_NICK = re.compile(r"^(?:查昵称|查名片|查名字)\s+(.+)$")
RE_OPENID = re.compile(r"^(?:查\s*id|查openid|查open_id)\s+([A-Za-z0-9_\-]{8,64})$", re.I)
RE_BIND = re.compile(r"^(?:绑定\s*qq|绑定QQ号?)\s+(\d{5,12})$", re.I)
RE_HELP = re.compile(r"^(?:档案馆帮助|昵称档案帮助|昵称ID档案馆帮助)$")

HELP_TEXT = (
    "📇 昵称ID档案馆 用法（仅管理员）\n"
    "• 查QQ <QQ号> ─ 查该 QQ 号对应的官方 openid\n"
    "• 查昵称 <关键词> ─ 按昵称/群名片检索成员\n"
    "• 查ID <openid> ─ 反查该 openid 的 QQ 号与昵称\n"
    "• 绑定QQ <QQ号> ─ 本人自报（需管理员在后台开启）\n"
    "说明：官方通道拿不到 QQ 号，需要先建立关联；更多内容请到后台查看。"
)

USER_COOLDOWN_SECONDS = 10.0
GROUP_WINDOW_SECONDS = 60.0
GROUP_MAX_PER_WINDOW = 6


class CommandHandler:
    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin
        self._user_last: Dict[str, float] = {}
        self._group_hits: Dict[str, List[float]] = {}

    # ------------------------------------------------------------------
    async def handle(self, event: Any) -> AsyncIterator[Any]:
        raw = str(getattr(event, "message_str", "") or "").strip()
        if raw.startswith("/"):
            raw = raw[1:].lstrip()
        if not raw:
            return
        if RE_HELP.match(raw):
            yield event.plain_result(HELP_TEXT)
            return

        match = RE_QQ.match(raw)
        if match:
            async for item in self._admin_command(event, "_render_qq", match.group(1)):
                yield item
            return
        match = RE_OPENID.match(raw)
        if match:
            async for item in self._admin_command(event, "_render_openid", match.group(1)):
                yield item
            return
        match = RE_NICK.match(raw)
        if match:
            async for item in self._admin_command(
                event, "_render_nickname", match.group(1).strip()
            ):
                yield item
            return
        match = RE_BIND.match(raw)
        if match:
            async for item in self._self_report(event, match.group(1)):
                yield item
            return

    async def _admin_command(
        self, event: Any, renderer: str, *args: Any
    ) -> AsyncIterator[Any]:
        """先鉴权、再限流、最后才渲染（避免未鉴权时创建无用的协程）。"""
        if not await self._is_admin(event):
            yield event.plain_result("此指令仅限管理员")
            return
        if self._rate_limited(event):
            yield event.plain_result("查询太频繁，请稍后再试")
            return
        text = await getattr(self, renderer)(*args)
        if text:
            yield event.plain_result(text)

    # ------------------------------------------------------------------
    async def _is_admin(self, event: Any) -> bool:
        try:
            if event.is_admin():
                return True
        except Exception:  # noqa: BLE001 - 老版本可能没有该方法
            pass
        try:
            settings = await self.plugin.get_settings()
            extra = settings.get("extra_admins") or []
        except Exception:  # noqa: BLE001
            extra = []
        sender = str(getattr(event, "get_sender_id", lambda: "")() or "")
        platform_id = str(getattr(event, "get_platform_id", lambda: "")() or "")
        for entry in extra:
            entry = str(entry or "").strip()
            if not entry:
                continue
            if ":" in entry:
                pid, uid = entry.split(":", 1)
                if pid == platform_id and uid == sender:
                    return True
            elif entry == sender:
                return True
        return False

    def _rate_limited(self, event: Any) -> bool:
        now = time.time()
        sender = str(getattr(event, "get_sender_id", lambda: "")() or "")
        group_id = str(getattr(event, "get_group_id", lambda: "")() or "")
        if sender:
            last = self._user_last.get(sender, 0.0)
            if now - last < USER_COOLDOWN_SECONDS:
                return True
            self._user_last[sender] = now
        if group_id:
            hits = [t for t in self._group_hits.get(group_id, []) if now - t < GROUP_WINDOW_SECONDS]
            if len(hits) >= GROUP_MAX_PER_WINDOW:
                self._group_hits[group_id] = hits
                return True
            hits.append(now)
            self._group_hits[group_id] = hits
        return False

    # ------------------------------------------------------------------
    async def _render_qq(self, qq: str) -> str:
        links = await self.plugin.identity.resolve_openid(qq, include_candidates=True)
        if not links:
            return f"没有找到 QQ {qq} 的关联记录。\n官方通道拿不到 QQ 号，需要先建立关联（后台可手工关联或生成同昵称候选）。"
        lines = [f"📇 QQ {qq} 的关联（{len(links)} 条）"]
        for link in links[:MAX_COMMAND_ROWS]:
            status = "已确认" if link.get("status") == "confirmed" else "候选"
            name = await self._display_name(str(link.get("platform_id") or ""), str(link.get("openid") or ""))
            suffix = f"（{name}）" if name else ""
            lines.append(f"• [{status}] {link.get('platform_id')} / {link.get('openid')}{suffix}")
        if len(links) > MAX_COMMAND_ROWS:
            lines.append(f"…共 {len(links)} 条，请到后台查看")
        return "\n".join(lines)

    async def _render_openid(self, openid: str) -> str:
        qq = await self.plugin.identity.resolve_qq(openid)
        name = ""
        platform_id = ""
        store = getattr(self.plugin, "store", None)
        if store is not None:
            try:
                rows, _ = await store.search_members(openid=openid, size=1)
                if rows:
                    name = str(rows[0].get("nickname") or "")
                    platform_id = str(rows[0].get("platform_id") or "")
            except Exception:  # noqa: BLE001 - 展示信息缺失不影响主流程
                pass
        if not qq:
            hint = f"openid {openid} 暂无已确认的 QQ 号"
            if name:
                hint += f"（昵称：{name}）"
            return hint + "\n可在后台查看候选或手工关联。"
        lines = [f"📇 openid {openid} → QQ {qq}"]
        if name:
            lines.append(f"昵称：{name}")
        if platform_id:
            lines.append(f"平台实例：{platform_id}")
        return "\n".join(lines)

    async def _render_nickname(self, keyword: str) -> str:
        if not keyword:
            return "请提供昵称关键词"
        data = await self.plugin.queries.search(keyword=keyword, size=MAX_COMMAND_ROWS)
        items = data.get("items") or []
        if not items:
            return f"没有找到匹配「{keyword}」的成员"
        lines = [f"🔍 匹配「{keyword}」的成员（{data.get('total', len(items))} 条）"]
        for row in items:
            qq = row.get("qq_display") or "未关联"
            lines.append(
                f"• {row.get('nickname') or '-'} / {row.get('card') or '-'} · "
                f"QQ:{qq} · {row.get('platform_id')} · 群 {row.get('group_id')}"
            )
        if (data.get("total") or 0) > len(items):
            lines.append("…更多结果请到后台查看")
        return "\n".join(lines)

    async def _self_report(self, event: Any, qq: str) -> AsyncIterator[Any]:
        platform_id = str(getattr(event, "get_platform_id", lambda: "")() or "")
        openid = str(getattr(event, "get_sender_id", lambda: "")() or "")
        result = await self.plugin.identity.self_report(
            platform_id=platform_id, openid=openid, qq=qq
        )
        if not result.get("ok"):
            yield event.plain_result("⚠️ " + str(result.get("error") or "自报失败"))
            return
        yield event.plain_result(
            f"✅ 已记录你的 QQ 号 {qq}，等待管理员确认后生效（当前仅为候选）。"
        )

    async def _display_name(self, platform_id: str, openid: str) -> str:
        store = getattr(self.plugin, "store", None)
        if store is None:
            return ""
        try:
            rows, _ = await store.search_members(platform_id=platform_id, openid=openid, size=1)
        except Exception:  # noqa: BLE001
            return ""
        if not rows:
            return ""
        return str(rows[0].get("nickname") or rows[0].get("card") or "")
