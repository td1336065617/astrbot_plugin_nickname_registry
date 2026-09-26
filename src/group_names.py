"""群名解析：被动捕获 + 主动拉取。

AstrBot 的 qq_official 适配器**不带群名**，只有 aiocqhttp 会填 abm.group.group_name，
因此官方群名必须自己调开放接口：
    Route("GET", "/v2/groups/{group_openid}/info")  → 复用 botpy 已持有的 access_token
OneBot 侧则用 get_group_info，事件里本来就带群名可被动捕获。
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

try:  # 测试环境可无 astrbot
    from astrbot.api import logger
except Exception:  # noqa: BLE001
    import logging

    logger = logging.getLogger("nickname_registry")

from .models import CHANNEL_OFFICIAL, CHANNEL_ONEBOT
from .platform_caps import channel_of_platform_id

REFRESH_LIMIT = 50
OFFICIAL_GROUP_INFO_PATH = "/v2/groups/{group_openid}/info"


def event_group_name(event: Any) -> str:
    """事件自带的群名（OneBot 有，官方没有）。"""
    group = getattr(getattr(event, "message_obj", None), "group", None)
    return str(getattr(group, "group_name", "") or "").strip()


async def capture_from_event(
    plugin: Any, event: Any, state: Dict[Tuple[str, str], str]
) -> str:
    """被动捕获群名；state 是进程内缓存，避免每条消息都写库。"""
    name = event_group_name(event)
    if not name:
        return ""
    platform_id = str(getattr(event, "get_platform_id", lambda: "")() or "")
    group_id = str(getattr(event, "get_group_id", lambda: "")() or "")
    if not (platform_id and group_id):
        return ""
    key = (platform_id, group_id)
    if state.get(key) == name:
        return name
    store = getattr(plugin, "store", None)
    if store is None:
        return name
    try:
        await store.upsert_group_name(platform_id, group_id, name, source="event")
        state[key] = name
    except Exception as exc:  # noqa: BLE001 - 群名缓存失败不影响采集
        logger.warning("缓存群名失败：%s", exc)
    return name


def _platform_inst(plugin: Any, platform_id: str) -> Any:
    context = getattr(plugin, "context", None)
    getter = getattr(context, "get_platform_inst", None)
    if not callable(getter):
        return None
    try:
        return getter(platform_id)
    except Exception:  # noqa: BLE001
        return None


async def fetch_group_name(
    plugin: Any, platform_id: str, group_id: str, channel: str = ""
) -> str:
    """主动拉取群名；拿不到返回空串（调用方按“未获取”展示）。"""
    resolved = channel or channel_of_platform_id(plugin, platform_id)
    if resolved == CHANNEL_ONEBOT:
        return await _fetch_onebot(plugin, platform_id, group_id)
    if resolved == CHANNEL_OFFICIAL:
        return await _fetch_official(plugin, platform_id, group_id)
    return ""


async def _fetch_onebot(plugin: Any, platform_id: str, group_id: str) -> str:
    inst = _platform_inst(plugin, platform_id)
    bot = getattr(inst, "bot", None) if inst is not None else None
    if bot is None:
        return ""
    try:
        payload = await bot.call_action("get_group_info", group_id=int(group_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("OneBot 取群名失败（%s/%s）：%s", platform_id, group_id, exc)
        return ""
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        payload = payload["data"]
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("group_name") or "").strip()


async def _fetch_official(plugin: Any, platform_id: str, group_id: str) -> str:
    inst = _platform_inst(plugin, platform_id)
    client = getattr(inst, "client", None) if inst is not None else None
    http = getattr(getattr(client, "api", None), "_http", None)
    if http is None:
        return ""
    try:  # 延迟导入：测试环境无需安装 botpy
        from botpy.http import Route
    except Exception:  # noqa: BLE001
        logger.warning("botpy 不可用，无法获取官方群名")
        return ""
    route = Route("GET", OFFICIAL_GROUP_INFO_PATH, group_openid=group_id)
    try:
        payload = await http.request(route)
    except Exception as exc:  # noqa: BLE001
        logger.warning("官方取群名失败（%s/%s）：%s", platform_id, group_id, exc)
        return ""
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("group_name") or "").strip()


async def refresh_group_names(plugin: Any, *, limit: int = REFRESH_LIMIT) -> Dict[str, Any]:
    """后台「刷新群名」：只给还没有名字的群补一次。"""
    store = getattr(plugin, "store", None)
    if store is None:
        return {"updated": 0, "failed": 0, "skipped": 0, "pending": 0}
    pending: List[Tuple[str, str]] = await store.groups_without_name()
    updated = 0
    failed = 0
    for platform_id, group_id in pending[:limit]:
        name = await fetch_group_name(plugin, platform_id, group_id)
        if not name:
            failed += 1
            continue
        try:
            await store.upsert_group_name(platform_id, group_id, name, source="api")
            updated += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("写入群名失败：%s", exc)
            failed += 1
    return {
        "updated": updated,
        "failed": failed,
        "skipped": max(0, len(pending) - limit),
        "pending": len(pending),
    }
