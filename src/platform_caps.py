"""平台通道判定与能力矩阵。

官方（qq_official / qq_official_webhook）没有群成员列表接口，也拿不到 QQ 号；
OneBot（aiocqhttp）的 user_id 就是 QQ 号，并可拉取全量群成员。
"""
from __future__ import annotations

from typing import Any, Dict, List

from .models import CHANNEL_OFFICIAL, CHANNEL_ONEBOT

OFFICIAL_NAMES = {"qq_official", "qq_official_webhook"}
ONEBOT_NAMES = {"aiocqhttp"}

_EMPTY = {
    "user_id_is_qq": False,
    "has_card": False,
    "has_role": False,
    "can_sync_members": False,
}


def channel_by_name(name: str) -> str:
    text = str(name or "").strip()
    if text in OFFICIAL_NAMES:
        return CHANNEL_OFFICIAL
    if text in ONEBOT_NAMES:
        return CHANNEL_ONEBOT
    return ""


def capabilities(channel: str) -> Dict[str, bool]:
    if channel == CHANNEL_ONEBOT:
        return {
            "user_id_is_qq": True,
            "has_card": True,
            "has_role": True,
            "can_sync_members": True,
        }
    if channel == CHANNEL_OFFICIAL:
        return dict(_EMPTY)
    return dict(_EMPTY)


def _platform_insts(plugin: Any) -> List[Any]:
    context = getattr(plugin, "context", None)
    manager = getattr(context, "platform_manager", None)
    return list(getattr(manager, "platform_insts", []) or [])


def channel_of_platform_id(plugin: Any, platform_id: str) -> str:
    """按平台实例 ID 反查通道类型。"""
    target = str(platform_id or "")
    if not target:
        return ""
    for inst in _platform_insts(plugin):
        try:
            meta = inst.meta()
        except Exception:  # noqa: BLE001
            continue
        if str(getattr(meta, "id", "") or "") != target:
            continue
        return channel_by_name(str(getattr(meta, "name", "") or ""))
    return ""


def platform_ids_by_channel(plugin: Any, channel: str) -> List[str]:
    result: List[str] = []
    for inst in _platform_insts(plugin):
        try:
            meta = inst.meta()
        except Exception:  # noqa: BLE001
            continue
        if channel_by_name(str(getattr(meta, "name", "") or "")) != channel:
            continue
        pid = str(getattr(meta, "id", "") or "")
        if pid:
            result.append(pid)
    return result
