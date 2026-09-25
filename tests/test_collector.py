"""采集器测试：建档、计数、改名检测、批量落库、队列上限。"""
from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.collector import Collector
from src.models import DEFAULT_SETTINGS
from src.store import NicknameStore


def _run(coro):
    return asyncio.run(coro)


class FakePlugin:
    def __init__(self, store, settings=None):
        self.store = store
        self._settings = dict(DEFAULT_SETTINGS)
        if settings:
            self._settings.update(settings)

    async def get_settings(self):
        return dict(self._settings)


def _make_store(tmp_path):
    store = NicknameStore(tmp_path / "n.db")
    return store


def test_observe_flush_creates_member(tmp_path):
    async def scenario():
        store = _make_store(tmp_path)
        await store.initialize()
        collector = Collector(FakePlugin(store))

        ok = await collector.observe(
            platform_id="aiocqhttp", group_id="100", user_id="12345",
            nickname="小明", card="", role="member", channel="onebot",
        )
        assert ok is True
        assert collector.pending == 1
        flushed = await collector.flush()
        assert flushed == 1
        assert collector.pending == 0

        row = await store.get_member("aiocqhttp", "100", "12345")
        assert row["nickname"] == "小明"
        assert row["role"] == "member"
        assert row["channel"] == "onebot"
        assert row["msg_count"] == 1
        await store.close()
    _run(scenario())


def test_repeated_messages_accumulate_delta(tmp_path):
    async def scenario():
        store = _make_store(tmp_path)
        await store.initialize()
        collector = Collector(FakePlugin(store))
        for _ in range(3):
            await collector.observe(
                platform_id="aiocqhttp", group_id="100", user_id="1", nickname="A"
            )
            await collector.flush()
        row = await store.get_member("aiocqhttp", "100", "1")
        assert row["msg_count"] == 3          # 三次 flush 不重复计数
        await store.close()
    _run(scenario())


def test_rename_creates_history_once(tmp_path):
    async def scenario():
        store = _make_store(tmp_path)
        await store.initialize()
        collector = Collector(FakePlugin(store))

        await collector.observe(
            platform_id="aiocqhttp", group_id="100", user_id="1", nickname="旧名"
        )
        await collector.flush()
        await collector.observe(
            platform_id="aiocqhttp", group_id="100", user_id="1", nickname="新名"
        )
        await collector.flush()
        # 名字没再变 → 不应再写历史
        await collector.observe(
            platform_id="aiocqhttp", group_id="100", user_id="1", nickname="新名"
        )
        await collector.flush()

        history = await store.list_history("aiocqhttp", "100", "1")
        assert [h["nickname"] for h in history] == ["新名"]
        await store.close()
    _run(scenario())


def test_card_change_creates_history(tmp_path):
    async def scenario():
        store = _make_store(tmp_path)
        await store.initialize()
        collector = Collector(FakePlugin(store))
        await collector.observe(
            platform_id="aiocqhttp", group_id="100", user_id="1",
            nickname="小明", card="一班",
        )
        await collector.flush()
        await collector.observe(
            platform_id="aiocqhttp", group_id="100", user_id="1",
            nickname="小明", card="二班",
        )
        await collector.flush()
        history = await store.list_history("aiocqhttp", "100", "1")
        assert [h["card"] for h in history] == ["二班"]
        await store.close()
    _run(scenario())


def test_history_disabled(tmp_path):
    async def scenario():
        store = _make_store(tmp_path)
        await store.initialize()
        collector = Collector(FakePlugin(store, {"record_history": False}))
        await collector.observe(
            platform_id="aiocqhttp", group_id="100", user_id="1", nickname="A"
        )
        await collector.flush()
        await collector.observe(
            platform_id="aiocqhttp", group_id="100", user_id="1", nickname="B"
        )
        await collector.flush()
        assert await store.list_history("aiocqhttp", "100", "1") == []
        await store.close()
    _run(scenario())


def test_capture_disabled(tmp_path):
    async def scenario():
        store = _make_store(tmp_path)
        await store.initialize()
        collector = Collector(FakePlugin(store, {"capture_enabled": False}))
        ok = await collector.observe(
            platform_id="aiocqhttp", group_id="100", user_id="1", nickname="A"
        )
        assert ok is False
        assert collector.pending == 0
        await store.close()
    _run(scenario())


def test_auto_flush_on_batch(tmp_path):
    async def scenario():
        store = _make_store(tmp_path)
        await store.initialize()
        collector = Collector(FakePlugin(store, {"flush_batch": 2}))
        await collector.observe(platform_id="p", group_id="100", user_id="1", nickname="A")
        assert collector.pending == 1
        await collector.observe(platform_id="p", group_id="100", user_id="2", nickname="B")
        assert collector.pending == 0            # 达到阈值自动落库
        assert (await store.get_member("p", "100", "1"))["nickname"] == "A"
        await store.close()
    _run(scenario())


def test_queue_max_drops_overflow(tmp_path):
    async def scenario():
        store = _make_store(tmp_path)
        await store.initialize()
        collector = Collector(FakePlugin(store, {"queue_max": 1, "flush_batch": 1000}))
        assert await collector.observe(platform_id="p", group_id="1", user_id="1", nickname="A")
        assert not await collector.observe(platform_id="p", group_id="1", user_id="2", nickname="B")
        assert collector.dropped == 1
        assert collector.pending == 1
        await store.close()
    _run(scenario())


def test_extract_sender_from_onebot_raw(tmp_path):
    event = types.SimpleNamespace(
        get_sender_name=lambda: "卡片名",
        message_obj=types.SimpleNamespace(
            raw_message={"sender": {"card": "一班小明", "role": "admin", "nickname": "小明"}}
        ),
    )
    meta = Collector.extract_sender(event)
    assert meta == {"nickname": "卡片名", "card": "一班小明", "role": "admin"}


def test_extract_sender_official_without_raw(tmp_path):
    event = types.SimpleNamespace(
        get_sender_name=lambda: "官方昵称",
        message_obj=types.SimpleNamespace(raw_message=None, sender=types.SimpleNamespace(nickname="官方昵称")),
    )
    meta = Collector.extract_sender(event)
    assert meta == {"nickname": "官方昵称", "card": "", "role": ""}
