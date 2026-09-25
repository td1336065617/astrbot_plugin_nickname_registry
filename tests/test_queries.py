"""查询与分析测试。"""
from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.models import DEFAULT_SETTINGS
from src.queries import Queries
from src.store import NicknameStore


def _run(coro):
    return asyncio.run(coro)


class FakeInst:
    def __init__(self, pid, name):
        self._meta = types.SimpleNamespace(id=pid, name=name)

    def meta(self):
        return self._meta


class FakePlugin:
    def __init__(self, store, insts=None, settings=None):
        self.store = store
        self.context = types.SimpleNamespace(
            platform_manager=types.SimpleNamespace(platform_insts=insts or [])
        )
        self._settings = dict(DEFAULT_SETTINGS)
        if settings:
            self._settings.update(settings)

    async def get_settings(self):
        return dict(self._settings)


async def _seed(store):
    await store.upsert_members([
        {"platform_id": "qq_official", "group_id": "C8D6", "user_id": "OPENID-A",
         "channel": "official", "nickname": "小明", "card": "",
         "source": "observed", "first_seen": 10.0, "last_seen": 20.0, "msg_count": 3},
        {"platform_id": "qq_official", "group_id": "C8D6", "user_id": "OPENID-B",
         "channel": "official", "nickname": "小明", "card": "",
         "source": "observed", "first_seen": 10.0, "last_seen": 15.0, "msg_count": 1},
        {"platform_id": "aiocqhttp", "group_id": "1019067385", "user_id": "1001",
         "channel": "onebot", "nickname": "小明", "card": "一班小明",
         "source": "synced", "first_seen": 5.0, "last_seen": 30.0, "msg_count": 0},
    ])
    await store.append_history([
        {"platform_id": "qq_official", "group_id": "C8D6", "user_id": "OPENID-A",
         "nickname": "小明", "card": "", "changed_at": 11.0},
        {"platform_id": "qq_official", "group_id": "C8D6", "user_id": "OPENID-A",
         "nickname": "明明", "card": "", "changed_at": 12.0},
    ])


def test_summary_and_search(tmp_path):
    async def scenario():
        store = NicknameStore(tmp_path / "n.db")
        await store.initialize()
        await _seed(store)
        plugin = FakePlugin(store, [FakeInst("A", "aiocqhttp")])
        queries = Queries(plugin)

        summary = await queries.summary()
        assert summary["available"] is True
        assert summary["stats"]["members"] == 3
        assert summary["platform_id"] == "A"

        found = await queries.search(keyword="小明")
        assert found["total"] == 3
        found = await queries.search(channel="official", size=1)
        assert found["total"] == 2 and len(found["items"]) == 1
        await store.close()
    _run(scenario())


def test_member_detail_returns_link_and_history(tmp_path):
    async def scenario():
        store = NicknameStore(tmp_path / "n.db")
        await store.initialize()
        await _seed(store)
        await store.upsert_link("1001", "qq_official", "OPENID-A",
                                status="confirmed", link_source="manual")
        queries = Queries(FakePlugin(store))

        detail = await queries.member_detail("qq_official", "C8D6", "OPENID-A")
        assert detail["member"]["nickname"] == "小明"
        assert detail["link"]["qq"] == "1001"
        assert [h["nickname"] for h in detail["history"]] == ["明明", "小明"]

        missing = await queries.member_detail("qq_official", "C8D6", "NOPE")
        assert missing["member"] is None and missing["link"] is None
        await store.close()
    _run(scenario())


def test_unlinked_groups_and_analysis(tmp_path):
    async def scenario():
        store = NicknameStore(tmp_path / "n.db")
        await store.initialize()
        await _seed(store)
        plugin = FakePlugin(store, [FakeInst("aiocqhttp", "aiocqhttp"), FakeInst("qq_official", "qq_official")])
        queries = Queries(plugin)

        unlinked = await queries.unlinked()
        assert unlinked["total"] == 2          # 两个官方 openid 都没关联

        groups = await queries.groups()
        by_pid = {g["platform_id"]: g for g in groups}
        assert by_pid["qq_official"]["channel"] == "official"
        assert by_pid["qq_official"]["capabilities"]["can_sync_members"] is False
        assert by_pid["aiocqhttp"]["capabilities"]["can_sync_members"] is True

        analysis = await queries.analysis()
        assert analysis["duplicate_names"][0]["nickname"] == "小明"
        assert analysis["duplicate_names"][0]["c"] == 3
        assert analysis["rename_rank"][0]["user_id"] == "OPENID-A"
        assert analysis["rename_rank"][0]["changes"] == 2

        await store.upsert_group_pair("qq_official", "C8D6", "aiocqhttp", "1019067385")
        assert len(await queries.group_pairs()) == 1
        assert (await queries.summary())["group_pairs"] == 1
        await store.close()
    _run(scenario())
