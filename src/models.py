"""数据模型与常量。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

PLUGIN_NAME = "astrbot_plugin_nickname_registry"

#: 平台通道
CHANNEL_OFFICIAL = "official"
CHANNEL_ONEBOT = "onebot"

#: 成员来源
SOURCE_OBSERVED = "observed"
SOURCE_SYNCED = "synced"

#: 关联状态
LINK_CANDIDATE = "candidate"
LINK_CONFIRMED = "confirmed"
LINK_REJECTED = "rejected"

#: 关联来源
LINK_SRC_MANUAL = "manual"
LINK_SRC_AUTO = "auto"
LINK_SRC_SELF = "self"

LINK_STATUSES = (LINK_CANDIDATE, LINK_CONFIRMED, LINK_REJECTED)
LINK_SOURCES = (LINK_SRC_MANUAL, LINK_SRC_AUTO, LINK_SRC_SELF)

#: 各指令/接口的结果上限
MAX_COMMAND_ROWS = 10
MAX_LIST_PAGE = 200

DEFAULT_SETTINGS: Dict[str, Any] = {
    "capture_enabled": True,
    "record_card": True,
    "record_history": True,
    "auto_candidate": True,
    "self_report_enabled": False,
    "flush_interval": 5.0,
    "flush_batch": 200,
    "queue_max": 5000,
    "history_keep": 20,
    "retention_days": 0,            # 0 = 永久保留
    "sync_interval": 0,             # 0 = 关闭定时同步（秒）
    "extra_admins": [],
}


@dataclass
class SyncResult:
    ok: bool
    reason: str = ""
    added: int = 0
    updated: int = 0
    total: int = 0
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "reason": self.reason,
            "added": self.added,
            "updated": self.updated,
            "total": self.total,
            "note": self.note,
        }
