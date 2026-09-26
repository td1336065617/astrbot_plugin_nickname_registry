"""群消息采集：内存聚合 + 改名检测 + 批量落库。

热路径只做内存操作；落库由定时器 / 阈值触发，失败保留待写队列。
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

try:  # 测试环境可无 astrbot
    from astrbot.api import logger
except Exception:  # noqa: BLE001
    import logging

    logger = logging.getLogger("nickname_registry")

from .models import (
    SOURCE_OBSERVED,
    SOURCE_SYNCED,
)
from .utils import now_ts

MemberKey = Tuple[str, str, str]


class Collector:
    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin
        self._current: Dict[MemberKey, Dict[str, Any]] = {}
        self._delta: Dict[MemberKey, int] = {}
        self._dirty: set[MemberKey] = set()
        self._history: List[Dict[str, Any]] = []
        self._dropped = 0
        #: 进程内群名缓存，避免每条消息都写库
        self._group_names: Dict[Tuple[str, str], str] = {}

    # ------------------------------------------------------------------
    # 事件入口
    # ------------------------------------------------------------------
    async def observe_event(self, event: Any) -> None:
        platform_id = str(getattr(event, "get_platform_id", lambda: "")() or "")
        group_id = str(getattr(event, "get_group_id", lambda: "")() or "")
        user_id = str(getattr(event, "get_sender_id", lambda: "")() or "")
        if not group_id or not user_id:
            return
        meta = self.extract_sender(event)
        channel = ""
        try:
            from .platform_caps import channel_of_platform_id

            channel = channel_of_platform_id(self.plugin, platform_id)
        except Exception:  # noqa: BLE001 - 能力探测失败不阻断采集
            channel = ""
        try:  # 群名捕获失败不影响采集
            from .group_names import capture_from_event

            await capture_from_event(self.plugin, event, self._group_names)
        except Exception as exc:  # noqa: BLE001
            logger.warning("群名捕获失败：%s", exc)
        await self.observe(
            platform_id=platform_id,
            group_id=group_id,
            user_id=user_id,
            nickname=meta["nickname"],
            card=meta["card"],
            role=meta["role"],
            channel=channel,
            source=SOURCE_OBSERVED,
        )

    @staticmethod
    def extract_sender(event: Any) -> Dict[str, str]:
        """从事件里取昵称 / 群名片 / 角色（官方侧通常只有昵称）。"""
        nickname = ""
        for method_name in ("get_sender_name", "get_sender_nickname"):
            method = getattr(event, method_name, None)
            if not callable(method):
                continue
            try:
                value = method()
            except Exception:  # noqa: BLE001
                value = None
            if value:
                nickname = str(value).strip()
                break

        card = role = ""
        raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
        if isinstance(raw, dict):
            sender = raw.get("sender")
            if isinstance(sender, dict):
                card = str(sender.get("card") or "").strip()
                role = str(sender.get("role") or "").strip()
                if not nickname:
                    nickname = str(sender.get("nickname") or "").strip()
        if not nickname:
            sender_obj = getattr(getattr(event, "message_obj", None), "sender", None)
            nickname = str(getattr(sender_obj, "nickname", "") or "").strip()
        return {"nickname": nickname, "card": card, "role": role}

    # ------------------------------------------------------------------
    # 采集
    # ------------------------------------------------------------------
    async def observe(
        self,
        *,
        platform_id: str,
        group_id: str,
        user_id: str,
        nickname: str = "",
        card: str = "",
        role: str = "",
        channel: str = "",
        source: str = SOURCE_OBSERVED,
        count: int = 1,
    ) -> bool:
        settings = await self.plugin.get_settings()
        if not settings.get("capture_enabled", True):
            return False
        if not settings.get("record_card", True):
            card = ""
        record_history = bool(settings.get("record_history", True))
        batch = int(settings.get("flush_batch", 200) or 200)
        queue_max = int(settings.get("queue_max", 5000) or 5000)

        key: MemberKey = (str(platform_id), str(group_id), str(user_id))
        is_new_dirty = key not in self._dirty
        if is_new_dirty and len(self._dirty) >= queue_max:
            self._dropped += 1
            return False

        is_first_in_process = key not in self._current
        row = self._current.get(key)
        if row is None:
            row = await self._load_row(key)
            self._current[key] = row

        now = now_ts()
        if (row.get("nickname"), row.get("card")) != (nickname, card):
            # 首次见到该成员（且库里原本没有）不记历史；
            # 库里已有记录说明是"离线期间改的名"，要记；同进程内后续变化也要记。
            should_record = row.get("_loaded") or not is_first_in_process
            if record_history and should_record:
                self._history.append(
                    {
                        "platform_id": key[0],
                        "group_id": key[1],
                        "user_id": key[2],
                        "nickname": nickname,
                        "card": card,
                        "changed_at": now,
                        "source": source,
                    }
                )
            row["nickname"] = nickname
            row["card"] = card
        if role:
            row["role"] = role
        if channel:
            row["channel"] = channel
        if source == SOURCE_OBSERVED:
            row["source"] = SOURCE_OBSERVED
        row["last_seen"] = now
        row["msg_count"] = int(row.get("msg_count") or 0) + int(count)

        self._delta[key] = self._delta.get(key, 0) + int(count)
        self._dirty.add(key)

        if len(self._dirty) >= batch:
            await self.flush()
        return True

    async def _load_row(self, key: MemberKey) -> Dict[str, Any]:
        row: Dict[str, Any] = {
            "platform_id": key[0],
            "group_id": key[1],
            "user_id": key[2],
            "channel": "",
            "nickname": "",
            "card": "",
            "role": "",
            "source": SOURCE_OBSERVED,
            "first_seen": now_ts(),
            "last_seen": now_ts(),
            "msg_count": 0,
            "_loaded": False,
        }
        store = getattr(self.plugin, "store", None)
        if store is None:
            return row
        try:
            existing = await store.get_member(key[0], key[1], key[2])
        except Exception as exc:  # noqa: BLE001 - 读失败按“新成员”处理
            logger.warning("读取成员失败（按新成员处理）: %s", exc)
            return row
        if isinstance(existing, dict):
            row.update({k: existing.get(k, row[k]) for k in row if k in existing})
            row["_loaded"] = True
        return row

    # ------------------------------------------------------------------
    # 落库
    # ------------------------------------------------------------------
    async def flush(self, *, force: bool = False) -> int:
        if not self._dirty and not self._history:
            return 0
        keys = list(self._dirty)
        rows: List[Dict[str, Any]] = []
        deltas: Dict[MemberKey, int] = {}
        for key in keys:
            row = self._current.get(key)
            if row is None:
                continue
            delta = int(self._delta.get(key, 0))
            payload = {k: v for k, v in row.items() if not k.startswith("_")}
            payload["msg_count"] = delta
            if delta <= 0 and force is False and payload.get("source") == SOURCE_SYNCED:
                pass
            rows.append(payload)
            deltas[key] = delta
        history = list(self._history)

        store = getattr(self.plugin, "store", None)
        if store is None:
            self._dirty.clear()
            self._delta.clear()
            self._history.clear()
            return 0
        try:
            if rows:
                await store.upsert_members(rows)
            if history:
                await store.append_history(history)
        except Exception as exc:  # noqa: BLE001 - 失败保留队列，下轮重试
            logger.warning("批量落库失败，保留待写队列: %s", exc)
            return 0

        for key, delta in deltas.items():
            remain = int(self._delta.get(key, 0)) - delta
            if remain > 0:
                self._delta[key] = remain
            else:
                self._delta.pop(key, None)
                self._dirty.discard(key)
        if history:
            self._history = self._history[len(history):]
        return len(rows)

    @property
    def pending(self) -> int:
        return len(self._dirty)

    @property
    def dropped(self) -> int:
        return self._dropped

    def reset(self) -> None:
        self._current.clear()
        self._delta.clear()
        self._dirty.clear()
        self._history.clear()
        self._dropped = 0
