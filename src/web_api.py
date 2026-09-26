"""WebUI 后台接口（context.register_web_api）。"""
from __future__ import annotations

from typing import Any, Dict, Optional

from astrbot.api import logger
from astrbot.api.web import error_response, json_response, request

from .models import LINK_STATUSES, MAX_LIST_PAGE, PLUGIN_NAME

ROUTES = (
    ("summary", "GET", "_summary"),
    ("analysis", "GET", "_analysis"),
    ("members", "GET", "_members"),
    ("member", "GET", "_member"),
    ("links", "GET", "_links"),
    ("links/confirm", "POST", "_links_confirm"),
    ("links/reject", "POST", "_links_reject"),
    ("links/unbind", "POST", "_links_unbind"),
    ("links/auto", "POST", "_links_auto"),
    ("group-pairs", "GET", "_pairs_list"),
    ("group-pairs", "POST", "_pairs_write"),
    ("unlinked", "GET", "_unlinked"),
    ("groups", "GET", "_groups"),
    ("groups/refresh", "POST", "_groups_refresh"),
    ("sync", "POST", "_sync"),
    ("export", "GET", "_export"),
    ("import", "POST", "_import"),
    ("purge", "POST", "_purge"),
    ("settings", "GET", "_settings_get"),
    ("settings", "POST", "_settings_set"),
)


def _q(name: str, default: str = "") -> str:
    """读取 GET 查询参数（兼容不同 AstrBot 版本的 request 实现）。"""
    for source in (getattr(request, "args", None), getattr(request, "query", None)):
        if source is None:
            continue
        getter = getattr(source, "get", None)
        if not callable(getter):
            continue
        try:
            value = getter(name)
        except Exception:  # noqa: BLE001
            continue
        if value is not None:
            return str(value)
    return default


def _i(name: str, default: int, minimum: int = 1, maximum: int = MAX_LIST_PAGE) -> int:
    try:
        value = int(_q(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _linked() -> Optional[bool]:
    raw = _q("linked", "").strip()
    if raw == "1":
        return True
    if raw == "0":
        return False
    return None


class WebApi:
    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin

    def register(self) -> None:
        for name, method, handler in ROUTES:
            self.plugin.context.register_web_api(
                f"/{PLUGIN_NAME}/{name}",
                getattr(self, handler),
                [method],
                f"昵称ID档案馆 {name}",
            )
        logger.info("昵称ID档案馆 已注册 %d 条后台接口", len(ROUTES))

    # ------------------------------------------------------------------
    async def _analysis(self):
        """同名多人 / 改名排行（概览页用；只读）。"""
        data = await self.plugin.queries.analysis()
        return json_response({"status": "success", "data": data})

    async def _summary(self):
        return json_response({"status": "success", "data": await self.plugin.queries.summary()})

    async def _members(self):
        data = await self.plugin.queries.search(
            platform_id=_q("platform_id"),
            group_id=_q("group_id"),
            channel=_q("channel"),
            openid=_q("openid"),
            qq=_q("qq"),
            keyword=_q("q"),
            linked=_linked(),
            page=_i("page", 1, 1, 100000),
            size=_i("size", 50),
        )
        return json_response({"status": "success", "data": data})

    async def _member(self):
        platform_id = _q("platform_id")
        group_id = _q("group_id")
        user_id = _q("user_id")
        if not (platform_id and group_id and user_id):
            return error_response("缺少 platform_id / group_id / user_id")
        return json_response(
            {
                "status": "success",
                "data": await self.plugin.queries.member_detail(platform_id, group_id, user_id),
            }
        )

    async def _links(self):
        status = _q("status").strip()
        if status and status not in LINK_STATUSES:
            return error_response("不支持的关联状态")
        rows, total = await self.plugin.store.list_links(
            status=status, page=_i("page", 1, 1, 100000), size=_i("size", 50)
        )
        return json_response(
            {"status": "success", "data": {"items": rows, "total": total}}
        )

    async def _body(self) -> Optional[Dict[str, Any]]:
        payload = await request.json(default=None)
        if not isinstance(payload, dict):
            return None
        return payload

    async def _links_confirm(self):
        payload = await self._body()
        if payload is None:
            return error_response("请求体格式不正确")
        result = await self.plugin.identity.confirm(
            qq=str(payload.get("qq") or ""),
            platform_id=str(payload.get("platform_id") or ""),
            openid=str(payload.get("openid") or ""),
            note=str(payload.get("note") or ""),
        )
        if not result.get("ok"):
            return error_response(result.get("error") or "确认失败")
        return json_response({"status": "success", "data": result})

    async def _links_reject(self):
        payload = await self._body()
        if payload is None:
            return error_response("请求体格式不正确")
        result = await self.plugin.identity.reject(
            platform_id=str(payload.get("platform_id") or ""),
            openid=str(payload.get("openid") or ""),
            note=str(payload.get("note") or ""),
        )
        if not result.get("ok"):
            return error_response(result.get("error") or "驳回失败")
        return json_response({"status": "success", "data": result})

    async def _links_unbind(self):
        payload = await self._body()
        if payload is None:
            return error_response("请求体格式不正确")
        removed = await self.plugin.identity.unbind(
            platform_id=str(payload.get("platform_id") or ""),
            openid=str(payload.get("openid") or ""),
        )
        if not removed:
            return error_response("没有可解绑的关联")
        return json_response({"status": "success", "data": {"removed": True}})

    async def _links_auto(self):
        payload = await self._body()
        if payload is None:
            return error_response("请求体格式不正确")
        o_pid = str(payload.get("official_platform_id") or "")
        o_gid = str(payload.get("official_group_id") or "")
        n_pid = str(payload.get("onebot_platform_id") or "")
        n_gid = str(payload.get("onebot_group_id") or "")
        if not (o_pid and o_gid and n_pid and n_gid):
            return error_response("请先选择官方群与 OneBot 群")
        await self.plugin.store.upsert_group_pair(o_pid, o_gid, n_pid, n_gid)
        result = await self.plugin.identity.build_candidates(
            pair={
                "official_platform_id": o_pid,
                "official_group_id": o_gid,
                "onebot_platform_id": n_pid,
                "onebot_group_id": n_gid,
            }
        )
        return json_response({"status": "success", "data": result})

    async def _pairs_list(self):
        return json_response(
            {"status": "success", "data": {"items": await self.plugin.queries.group_pairs()}}
        )

    async def _pairs_write(self):
        payload = await self._body()
        if payload is None:
            return error_response("请求体格式不正确")
        action = str(payload.get("action") or "add")
        if action == "delete":
            try:
                seq = int(payload.get("seq") or 0)
            except (TypeError, ValueError):
                return error_response("seq 必须是整数")
            if not await self.plugin.store.delete_group_pair(seq):
                return error_response("群映射不存在")
            return json_response({"status": "success", "data": {"deleted": True}})
        o_pid = str(payload.get("official_platform_id") or "")
        o_gid = str(payload.get("official_group_id") or "")
        n_pid = str(payload.get("onebot_platform_id") or "")
        n_gid = str(payload.get("onebot_group_id") or "")
        if not (o_pid and o_gid and n_pid and n_gid):
            return error_response("请填写官方群与 OneBot 群")
        await self.plugin.store.upsert_group_pair(o_pid, o_gid, n_pid, n_gid)
        return json_response({"status": "success", "data": {"saved": True}})

    async def _unlinked(self):
        data = await self.plugin.queries.unlinked(
            page=_i("page", 1, 1, 100000), size=_i("size", 50)
        )
        return json_response({"status": "success", "data": data})

    async def _groups(self):
        return json_response(
            {"status": "success", "data": {"items": await self.plugin.queries.groups()}}
        )

    async def _groups_refresh(self):
        """批量补群名：官方走开放接口，OneBot 走 get_group_info。"""
        from .group_names import refresh_group_names

        payload = await self._body() or {}
        try:
            limit = int(payload.get("limit") or 50)
        except (TypeError, ValueError):
            limit = 50
        limit = max(1, min(200, limit))
        data = await refresh_group_names(self.plugin, limit=limit)
        return json_response({"status": "success", "data": data})

    async def _sync(self):
        payload = await self._body()
        if payload is None:
            return error_response("请求体格式不正确")
        platform_id = str(payload.get("platform_id") or "")
        group_id = str(payload.get("group_id") or "")
        if not (platform_id and group_id):
            return error_response("缺少 platform_id / group_id")
        result = await self.plugin.syncer.sync_group(platform_id, group_id)
        if not result.ok:
            return error_response(result.note or result.reason or "同步失败")
        return json_response({"status": "success", "data": result.to_dict()})

    async def _export(self):
        fmt = (_q("format", "json") or "json").lower()
        filters = {
            "platform_id": _q("platform_id"),
            "group_id": _q("group_id"),
            "channel": _q("channel"),
            "keyword": _q("q"),
            "linked": _linked(),
        }
        if fmt == "csv":
            return json_response(
                {"status": "success", "data": {"format": "csv", "content": await self.plugin.exporter.to_csv(**filters)}}
            )
        if fmt == "json":
            return json_response(
                {"status": "success", "data": {"format": "json", "content": await self.plugin.exporter.to_json()}}
            )
        return error_response("format 只支持 csv / json")

    async def _import(self):
        payload = await self._body()
        if payload is None:
            return error_response("请求体格式不正确")
        data = payload.get("payload")
        try:
            applied = await self.plugin.exporter.import_json(data)
        except ValueError as exc:
            return error_response(str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.error("导入失败：%s", exc, exc_info=True)
            return error_response("导入失败：内容无法解析")
        return json_response({"status": "success", "data": {"applied": applied}})

    async def _purge(self):
        payload = await self._body()
        if payload is None:
            return error_response("请求体格式不正确")
        scope = str(payload.get("scope") or "")
        if scope not in ("group", "member", "platform", "all"):
            return error_response("scope 只支持 group / member / platform / all")
        counts = await self.plugin.store.purge(
            scope,
            platform_id=str(payload.get("platform_id") or ""),
            group_id=str(payload.get("group_id") or ""),
            user_id=str(payload.get("user_id") or ""),
        )
        return json_response({"status": "success", "data": counts})

    async def _settings_get(self):
        return json_response(
            {"status": "success", "data": await self.plugin.get_settings()}
        )

    async def _settings_set(self):
        payload = await self._body()
        if payload is None:
            return error_response("请求体格式不正确")
        data = payload.get("settings") if "settings" in payload else payload
        if not isinstance(data, dict):
            return error_response("settings 必须是对象")
        merged = await self.plugin.put_settings(data)
        return json_response({"status": "success", "data": merged})
