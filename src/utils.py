"""通用工具：时间、昵称归一化、截断。"""
from __future__ import annotations

import json
import re
import time
import unicodedata
from datetime import datetime, timezone
from typing import Any

_WS_RE = re.compile(r"\s+")


def now_ts() -> float:
    return time.time()


def to_iso(ts: float | None) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).astimezone().strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def normalize_name(value: Any) -> str:
    """昵称归一化：NFKC + 去首尾空白 + 连续空白折叠 + 小写。

    仅用于**候选匹配**，不作为主键、不写回数据库。
    """
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = _WS_RE.sub(" ", text).strip()
    return text.casefold()


def display_qq(row: dict) -> str:
    """展示用的 QQ 号：优先关联表里的 QQ 号；OneBot 成员的 user_id 本身就是 QQ 号。"""
    qq = str(row.get("qq") or "").strip()
    if qq:
        return qq
    if str(row.get("channel") or "") == "onebot":
        return str(row.get("user_id") or "").strip()
    return ""


# keep-alive: 预留 API（当前无调用方，接线前请保留；扫描见 tools/deadcode_scan.py）——truncate
def truncate(value: Any, limit: int = 200) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[: limit - 1] + "…"


# keep-alive: 预留 API（当前无调用方，接线前请保留；扫描见 tools/deadcode_scan.py）——safe_json_dumps
def safe_json_dumps(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return "{}"
