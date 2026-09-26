"""后台查询与分析。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .models import MAX_LIST_PAGE
from .platform_caps import capabilities, channel_of_platform_id
from .utils import display_qq


class Queries:
    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin

    @property
    def store(self) -> Any:
        return getattr(self.plugin, "store", None)

    async def summary(self) -> Dict[str, Any]:
        store = self.store
        if store is None:
            return {"available": False}
        stats = await store.stats()
        pairs = await store.list_group_pairs()
        return {
            "available": True,
            "stats": stats,
            "group_pairs": len(pairs),
            "platform_id": self._default_platform_id(),
        }

    def _default_platform_id(self) -> str:
        try:
            context = getattr(self.plugin, "context", None)
            manager = getattr(context, "platform_manager", None)
            for inst in getattr(manager, "platform_insts", []) or []:
                meta = inst.meta()
                if channel_of_platform_id(self.plugin, str(getattr(meta, "id", ""))):
                    return str(getattr(meta, "id", "") or "")
        except Exception:  # noqa: BLE001
            pass
        return ""

    async def search(
        self,
        *,
        platform_id: str = "",
        group_id: str = "",
        channel: str = "",
        openid: str = "",
        qq: str = "",
        keyword: str = "",
        linked: Optional[bool] = None,
        page: int = 1,
        size: int = 50,
    ) -> Dict[str, Any]:
        store = self.store
        if store is None:
            return {"items": [], "total": 0}
        size = max(1, min(MAX_LIST_PAGE, int(size or 50)))
        rows, total = await store.search_members(
            platform_id=platform_id,
            group_id=group_id,
            channel=channel,
            openid=openid,
            qq=qq,
            keyword=keyword,
            linked=linked,
            page=page,
            size=size,
        )
        names = await store.group_names()
        for row in rows:
            row["qq_display"] = display_qq(row)
            row["group_name"] = names.get(
                (str(row.get("platform_id") or ""), str(row.get("group_id") or "")), ""
            )
        return {"items": rows, "total": total, "page": int(page), "size": size}

    async def member_detail(
        self, platform_id: str, group_id: str, user_id: str
    ) -> Dict[str, Any]:
        store = self.store
        if store is None:
            return {"member": None, "link": None, "history": []}
        member = await store.get_member(platform_id, group_id, user_id)
        link = await store.get_link(platform_id, user_id)
        history = await store.list_history(platform_id, group_id, user_id, limit=100)
        return {
            "member": member,
            "link": link,
            "history": history,
            "group_name": await store.get_group_name(platform_id, group_id),
        }

    async def unlinked(self, page: int = 1, size: int = 50) -> Dict[str, Any]:
        store = self.store
        if store is None:
            return {"items": [], "total": 0}
        rows, total = await store.unlinked_members(page=page, size=size)
        names = await store.group_names()
        for row in rows:
            row["group_name"] = names.get(
                (str(row.get("platform_id") or ""), str(row.get("group_id") or "")), ""
            )
        return {"items": rows, "total": total, "page": int(page), "size": int(size)}

    async def groups(self) -> List[Dict[str, Any]]:
        store = self.store
        if store is None:
            return []
        result = []
        names = await store.group_names()
        for item in await store.group_overview():
            pid = str(item.get("platform_id") or "")
            channel = channel_of_platform_id(self.plugin, pid)
            result.append(
                {
                    **item,
                    "channel": channel,
                    "capabilities": capabilities(channel),
                    "group_name": names.get((pid, str(item.get("group_id") or "")), ""),
                }
            )
        return result

    async def group_pairs(self) -> List[Dict[str, Any]]:
        store = self.store
        return await store.list_group_pairs() if store is not None else []

    async def analysis(self) -> Dict[str, Any]:
        store = self.store
        if store is None:
            return {"duplicate_names": [], "rename_rank": []}
        return {
            "duplicate_names": await store.analysis_duplicate_names(),
            "rename_rank": await store.analysis_rename_rank(),
        }
