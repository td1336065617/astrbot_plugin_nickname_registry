"""CSV / JSON 导出与 JSON 导入合并。"""
from __future__ import annotations

import csv
import io
import json
from typing import Any, Dict, List

from .models import LINK_CONFIRMED, MAX_LIST_PAGE
from .utils import display_qq, to_iso

CSV_HEADER = [
    "显示名", "昵称", "群名片", "QQ号", "openid", "平台实例", "群",
    "角色", "来源", "关联状态", "首次出现", "最近出现", "发言数", "历史昵称数",
]

EXPORT_PAGE = 500


class Exporter:
    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin

    @property
    def store(self) -> Any:
        return getattr(self.plugin, "store", None)

    # ------------------------------------------------------------------
    # 导出
    # ------------------------------------------------------------------
    async def export_rows(self, **filters) -> List[Dict[str, Any]]:
        store = self.store
        if store is None:
            return []
        rows: List[Dict[str, Any]] = []
        page = 1
        size = EXPORT_PAGE
        while True:
            chunk, total = await store.search_members(page=page, size=size, **filters)
            rows.extend(chunk)
            if not chunk or len(rows) >= total:
                break
            page += 1
        return rows

    async def to_csv(self, **filters) -> str:
        rows = await self.export_rows(**filters)
        counts = await self.store.history_counts() if self.store is not None else {}
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(CSV_HEADER)
        for row in rows:
            key = (
                str(row.get("platform_id") or ""),
                str(row.get("group_id") or ""),
                str(row.get("user_id") or ""),
            )
            writer.writerow([
                row.get("card") or row.get("nickname") or "",
                row.get("nickname") or "",
                row.get("card") or "",
                display_qq(row),
                row.get("user_id") or "",
                row.get("platform_id") or "",
                row.get("group_id") or "",
                row.get("role") or "",
                row.get("source") or "",
                row.get("link_status") or "未关联",
                to_iso(row.get("first_seen")),
                to_iso(row.get("last_seen")),
                int(row.get("msg_count") or 0),
                int(counts.get(key, 0)),
            ])
        return "\ufeff" + buffer.getvalue()

    async def to_json(self) -> str:
        store = self.store
        if store is None:
            return "{}"
        members = await self.export_rows()
        links, _ = await store.list_links(page=1, size=MAX_LIST_PAGE)
        payload = {
            "version": 1,
            "exported_at": to_iso(__import__("time").time()),
            "members": [
                {
                    "platform_id": m.get("platform_id"),
                    "group_id": m.get("group_id"),
                    "user_id": m.get("user_id"),
                    "channel": m.get("channel"),
                    "nickname": m.get("nickname"),
                    "card": m.get("card"),
                    "role": m.get("role"),
                    "source": m.get("source"),
                    "first_seen": m.get("first_seen"),
                    "last_seen": m.get("last_seen"),
                    "msg_count": m.get("msg_count"),
                }
                for m in members
            ],
            "links": [
                {
                    "qq": link.get("qq"),
                    "platform_id": link.get("platform_id"),
                    "openid": link.get("openid"),
                    "status": link.get("status"),
                    "link_source": link.get("link_source"),
                    "note": link.get("note"),
                    "updated_at": link.get("updated_at"),
                }
                for link in links
            ],
            "history": await self._all_history(),
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    async def _all_history(self) -> List[Dict[str, Any]]:
        store = self.store
        if store is None:
            return []
        rows: List[Dict[str, Any]] = []
        seen: set = set()
        members = await self.export_rows()
        for m in members:
            key = (m.get("platform_id"), m.get("group_id"), m.get("user_id"))
            if key in seen:
                continue
            seen.add(key)
            rows.extend(
                await store.list_history(
                    str(m.get("platform_id") or ""),
                    str(m.get("group_id") or ""),
                    str(m.get("user_id") or ""),
                    limit=200,
                )
            )
        for row in rows:
            row.pop("seq", None)
        return rows

    # ------------------------------------------------------------------
    # 导入
    # ------------------------------------------------------------------
    async def import_json(self, payload: Any) -> Dict[str, int]:
        store = self.store
        if store is None:
            raise ValueError("数据库不可用")
        if not isinstance(payload, dict):
            raise ValueError("导入内容必须是 JSON 对象")

        members = payload.get("members") or []
        links = payload.get("links") or []
        history = payload.get("history") or []
        for name, value in (("members", members), ("links", links), ("history", history)):
            if not isinstance(value, list):
                raise ValueError(f"{name} 必须是数组")

        member_rows: List[Dict[str, Any]] = []
        for index, item in enumerate(members):
            if not isinstance(item, dict):
                raise ValueError(f"members 第 {index + 1} 项不是对象")
            if not item.get("platform_id") or not item.get("group_id") or not item.get("user_id"):
                raise ValueError(f"members 第 {index + 1} 项缺少 platform_id/group_id/user_id")
            member_rows.append(item)

        link_rows: List[Dict[str, Any]] = []
        for index, item in enumerate(links):
            if not isinstance(item, dict):
                raise ValueError(f"links 第 {index + 1} 项不是对象")
            if not item.get("platform_id") or not item.get("openid"):
                raise ValueError(f"links 第 {index + 1} 项缺少 platform_id/openid")
            link_rows.append(item)

        history_rows: List[Dict[str, Any]] = []
        for index, item in enumerate(history):
            if not isinstance(item, dict):
                raise ValueError(f"history 第 {index + 1} 项不是对象")
            history_rows.append(item)

        applied = {"members": 0, "links": 0, "history": 0}
        if member_rows:
            applied["members"] = await store.upsert_members(member_rows)
        for item in link_rows:
            pid = str(item.get("platform_id"))
            oid = str(item.get("openid"))
            incoming_status = str(item.get("status") or "candidate")
            existing = await store.get_link(pid, oid)
            if existing is not None:
                existing_status = str(existing.get("status") or "")
                if existing_status == LINK_CONFIRMED and incoming_status != LINK_CONFIRMED:
                    continue                                   # 已确认的不被候选覆盖
                if existing_status == incoming_status:
                    try:
                        if float(existing.get("updated_at") or 0) >= float(item.get("updated_at") or 0):
                            continue                               # 保留较新的
                    except (TypeError, ValueError):
                        pass
            await store.upsert_link(
                str(item.get("qq") or ""),
                pid,
                oid,
                status=incoming_status,
                link_source=str(item.get("link_source") or "manual"),
                note=str(item.get("note") or ""),
            )
            applied["links"] += 1
        if history_rows:
            applied["history"] = await store.append_history_dedup(history_rows)
        return applied
