"""身份关联：候选生成、确认/驳回/解绑、双向解析。

关键约束：
- 一个 openid 只能属于一个 QQ 号（DB 层 UNIQUE）；
- 昵称只是线索，候选必须人工确认才生效；
- 官方群与 OneBot 群的 group_id 语义不同，需先做“群映射”再在同一真实群内匹配。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

try:  # 测试环境可无 astrbot
    from astrbot.api import logger
except Exception:  # noqa: BLE001
    import logging

    logger = logging.getLogger("nickname_registry")

from .models import (
    LINK_CANDIDATE,
    LINK_CONFIRMED,
    LINK_REJECTED,
    LINK_SRC_AUTO,
    LINK_SRC_MANUAL,
    LINK_SRC_SELF,
)
from .utils import normalize_name


class IdentityService:
    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin

    @property
    def store(self) -> Any:
        return getattr(self.plugin, "store", None)

    # ------------------------------------------------------------------
    # 候选
    # ------------------------------------------------------------------
    async def build_candidates(
        self, *, pair: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """在“群映射”配对的官方群与 OneBot 群之间，按同昵称/同群名片生成候选。"""
        store = self.store
        if store is None:
            return {"created": 0, "pairs": 0, "skipped": 0}
        pairs = [pair] if pair else await store.list_group_pairs()
        created = 0
        skipped = 0
        for item in pairs:
            o_pid = str(item.get("official_platform_id") or "")
            o_gid = str(item.get("official_group_id") or "")
            n_pid = str(item.get("onebot_platform_id") or "")
            n_gid = str(item.get("onebot_group_id") or "")
            if not (o_pid and o_gid and n_pid and n_gid):
                continue
            onebot_rows = await store.iter_group_members(n_pid, n_gid)
            official_rows = await store.iter_group_members(o_pid, o_gid)
            if not onebot_rows or not official_rows:
                continue
            index: Dict[str, List[str]] = {}
            for member in onebot_rows:
                key = normalize_name(member.get("card") or member.get("nickname"))
                if key:
                    index.setdefault(key, []).append(str(member.get("user_id") or ""))
            for member in official_rows:
                key = normalize_name(member.get("card") or member.get("nickname"))
                qqs = [q for q in index.get(key, []) if q]
                if not qqs:
                    continue
                openid = str(member.get("user_id") or "")
                if not openid:
                    continue
                if await store.get_link(o_pid, openid):
                    skipped += 1
                    continue
                await store.upsert_link(
                    qqs[0],
                    o_pid,
                    openid,
                    status=LINK_CANDIDATE,
                    link_source=LINK_SRC_AUTO,
                    note=f"同昵称候选（{n_gid}）",
                )
                created += 1
        logger.info("候选生成完成：新增 %d，跳过 %d，配对 %d", created, skipped, len(pairs))
        return {"created": created, "pairs": len(pairs), "skipped": skipped}

    # ------------------------------------------------------------------
    # 增删改
    # ------------------------------------------------------------------
    async def confirm(
        self,
        *,
        qq: str,
        platform_id: str,
        openid: str,
        note: str = "",
        link_source: str = LINK_SRC_MANUAL,
    ) -> Dict[str, Any]:
        store = self.store
        if store is None:
            return {"ok": False, "error": "数据库不可用"}
        qq_text = str(qq or "").strip()
        pid = str(platform_id or "").strip()
        oid = str(openid or "").strip()
        if not qq_text.isdigit():
            return {"ok": False, "error": "QQ 号应为纯数字"}
        if not pid or not oid:
            return {"ok": False, "error": "缺少平台实例或 openid"}
        existing = await store.get_link(pid, oid)
        if (
            existing
            and str(existing.get("status")) == LINK_CONFIRMED
            and str(existing.get("qq")) != qq_text
        ):
            return {
                "ok": False,
                "error": f"该 openid 已绑定到 QQ {existing.get('qq')}，请先解绑",
            }
        await store.upsert_link(
            qq_text, pid, oid, status=LINK_CONFIRMED, link_source=link_source, note=note
        )
        return {"ok": True, "qq": qq_text, "platform_id": pid, "openid": oid}

    async def reject(self, *, platform_id: str, openid: str, note: str = "") -> Dict[str, Any]:
        store = self.store
        if store is None:
            return {"ok": False, "error": "数据库不可用"}
        pid = str(platform_id or "").strip()
        oid = str(openid or "").strip()
        existing = await store.get_link(pid, oid)
        if existing is None:
            return {"ok": False, "error": "没有可驳回的关联记录"}
        await store.upsert_link(
            str(existing.get("qq") or ""),
            pid,
            oid,
            status=LINK_REJECTED,
            link_source=str(existing.get("link_source") or LINK_SRC_MANUAL),
            note=note,
        )
        return {"ok": True}

    async def unbind(self, *, platform_id: str, openid: str) -> bool:
        store = self.store
        if store is None:
            return False
        return await store.delete_link(str(platform_id or ""), str(openid or ""))

    async def self_report(self, *, platform_id: str, openid: str, qq: str) -> Dict[str, Any]:
        """本人自报：默认关闭；开启后也只生成候选，绝不直接生效。"""
        store = self.store
        if store is None:
            return {"ok": False, "error": "数据库不可用"}
        try:
            settings = await self.plugin.get_settings()
        except Exception:  # noqa: BLE001
            settings = {}
        if not settings.get("self_report_enabled", False):
            return {"ok": False, "error": "本人自报未开启，请联系管理员"}
        qq_text = str(qq or "").strip()
        if not qq_text.isdigit():
            return {"ok": False, "error": "QQ 号应为纯数字"}
        pid = str(platform_id or "").strip()
        oid = str(openid or "").strip()
        if not pid or not oid:
            return {"ok": False, "error": "无法识别你的身份"}
        existing = await store.get_link(pid, oid)
        if existing and str(existing.get("status")) == LINK_CONFIRMED:
            return {"ok": False, "error": "你已经关联了 QQ 号"}
        await store.upsert_link(
            qq_text, pid, oid, status=LINK_CANDIDATE, link_source=LINK_SRC_SELF, note="本人自报"
        )
        return {"ok": True, "status": LINK_CANDIDATE}

    # ------------------------------------------------------------------
    # 解析（供本插件与其他插件使用）
    # ------------------------------------------------------------------
    async def resolve_openid(
        self,
        qq: str,
        *,
        platform_id: str = "",
        include_candidates: bool = False,
    ) -> List[Dict[str, Any]]:
        store = self.store
        if store is None:
            return []
        return await store.resolve_openid(
            str(qq or "").strip(),
            platform_id=str(platform_id or ""),
            include_candidates=bool(include_candidates),
        )

    async def resolve_qq(self, openid: str, *, platform_id: str = "") -> Optional[str]:
        store = self.store
        if store is None:
            return None
        return await store.resolve_qq(str(openid or "").strip(), platform_id=str(platform_id or ""))
