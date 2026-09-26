"""群名解析测试：缓存、被动捕获、主动拉取（OneBot / 官方）、后台刷新。"""
from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.group_names import (
    capture_from_event,
    event_group_name,
    fetch_group_name,
    refresh_group_names,
)
from src.models import DEFAULT_SETTINGS
from src.queries import Queries
from src.store import NicknameStore


def _run(coro):
    return asyncio.run(coro)


class FakeBot:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error
        self.calls = []

    async def call_action(self, action, **params):
        self.calls.append((action, params))
        if self.error is not None:
            raise self.error
        return self.payload


class FakeHttp:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error
        self.routes = []

    async def request(self, route, **kwargs):
        self.routes.append((route, kwargs))
        if self.error is not None:
            raise self.error
        return self.payload


class FakeInst:
    def __init__(self, pid, name, bot=None, http=None):
        self._meta = types.SimpleNamespace(id=pid, name=name)
        self.bot = bot
        self.client = (
            types.SimpleNamespace(api=types.SimpleNamespace(_http=http)) if http else None
        )

    def meta(self):
        return self._meta


class FakePlugin:
    def __init__(self, store, insts=None):
        insts = insts or []
        self.store = store
        self.context = types.SimpleNamespace(
            platform_manager=types.SimpleNamespace(platform_insts=insts),
            get_platform_inst=lambda pid: next(
                (i for i in insts if i.meta().id == pid), None
            ),
        )
        self._settings = dict(DEFAULT_SETTINGS)

    async def get_settings(self):
        return dict(self._settings)


async def _setup(tmp_path, insts=None):
    store = NicknameStore(tmp_path / "n.db")
    await store.initialize()
    return store, FakePlugin(store, insts)


async def _seed_members(store):
    await store.upsert_members([
        {"platform_id": "aiocqhttp", "group_id": "100", "user_id": "1001",
         "channel": "onebot", "nickname": "小明", "card": "",
         "source": "synced", "first_seen": 1.0, "last_seen": 2.0, "msg_count": 0},
        {"platform_id": "qq_official", "group_id": "C8D6", "user_id": "OPENID-A",
         "channel": "official", "nickname": "小红", "card": "",
         "source": "observed", "first_seen": 1.0, "last_seen": 2.0, "msg_count": 1},
    ])


def test_store_group_name_crud(tmp_path):
    async def scenario():
        store = NicknameStore(tmp_path / "n.db")
        await store.initialize()

        assert await store.get_group_name("p", "1") == ""
        await store.upsert_group_name("p", "1", "  测试群  ", source="event")
        assert await store.get_group_name("p", "1") == "测试群"
        # 空名不覆盖
        await store.upsert_group_name("p", "1", "   ")
        assert await store.get_group_name("p", "1") == "测试群"
        # 同名覆盖
        await store.upsert_group_name("p", "1", "改名后的群")
        assert await store.get_group_name("p", "1") == "改名后的群"
        assert await store.group_names() == {("p", "1"): "改名后的群"}
        await store.close()
    _run(scenario())


def test_groups_without_name(tmp_path):
    async def scenario():
        store, plugin = await _setup(tmp_path)
        await _seed_members(store)
        pending = await store.groups_without_name()
        assert sorted(pending) == [("aiocqhttp", "100"), ("qq_official", "C8D6")]

        await store.upsert_group_name("aiocqhttp", "100", "集训群")
        assert await store.groups_without_name() == [("qq_official", "C8D6")]
        await store.close()
    _run(scenario())


def test_capture_from_event_only_when_changed(tmp_path):
    async def scenario():
        store, plugin = await _setup(tmp_path)
        state = {}

        event = types.SimpleNamespace(
            get_platform_id=lambda: "aiocqhttp",
            get_group_id=lambda: "100",
            message_obj=types.SimpleNamespace(
                group=types.SimpleNamespace(group_name="集训群")
            ),
        )
        assert event_group_name(event) == "集训群"
        assert await capture_from_event(plugin, event, state) == "集训群"
        assert await store.get_group_name("aiocqhttp", "100") == "集训群"

        # 同名再捕获：命中进程内缓存，不再写库
        await store.upsert_group_name("aiocqhttp", "100", "手动覆盖")
        assert await capture_from_event(plugin, event, state) == "集训群"
        assert await store.get_group_name("aiocqhttp", "100") == "手动覆盖"

        # 官方事件没有群名
        official = types.SimpleNamespace(
            get_platform_id=lambda: "qq_official",
            get_group_id=lambda: "C8D6",
            message_obj=types.SimpleNamespace(group=types.SimpleNamespace(group_name="")),
        )
        assert await capture_from_event(plugin, official, state) == ""
        await store.close()
    _run(scenario())


def test_fetch_onebot_group_name(tmp_path):
    async def scenario():
        bot = FakeBot({"group_name": "集训群", "member_count": 42})
        store, plugin = await _setup(
            tmp_path, [FakeInst("aiocqhttp", "aiocqhttp", bot=bot)]
        )
        assert await fetch_group_name(plugin, "aiocqhttp", "100") == "集训群"
        assert bot.calls[0][0] == "get_group_info"
        assert bot.calls[0][1]["group_id"] == 100
        await store.close()
    _run(scenario())


def test_fetch_official_group_name(tmp_path):
    async def scenario():
        http = FakeHttp({"group_name": "官方测试群", "group_member_num": 7})
        store, plugin = await _setup(
            tmp_path, [FakeInst("qq_official", "qq_official", http=http)]
        )
        assert await fetch_group_name(plugin, "qq_official", "C8D6") == "官方测试群"
        route, _kwargs = http.routes[0]
        assert route.method == "GET"
        assert route.path == "/v2/groups/{group_openid}/info"
        assert route.parameters.get("group_openid") == "C8D6"
        await store.close()
    _run(scenario())


def test_fetch_failure_returns_empty(tmp_path):
    async def scenario():
        store, plugin = await _setup(
            tmp_path, [FakeInst("aiocqhttp", "aiocqhttp", bot=FakeBot(error=RuntimeError("boom")))]
        )
        assert await fetch_group_name(plugin, "aiocqhttp", "100") == ""
        assert await fetch_group_name(plugin, "unknown", "100") == ""
        await store.close()
    _run(scenario())


def test_refresh_group_names(tmp_path):
    async def scenario():
        store, plugin = await _setup(tmp_path, [
            FakeInst("aiocqhttp", "aiocqhttp", bot=FakeBot({"group_name": "集训群"})),
            FakeInst("qq_official", "qq_official", http=FakeHttp({"group_name": "官方测试群"})),
        ])
        await _seed_members(store)

        result = await refresh_group_names(plugin)
        assert result["updated"] == 2 and result["failed"] == 0
        assert await store.get_group_name("aiocqhttp", "100") == "集训群"
        assert await store.get_group_name("qq_official", "C8D6") == "官方测试群"

        # 已全部有名字 → 不再请求
        again = await refresh_group_names(plugin)
        assert again["pending"] == 0 and again["updated"] == 0
        await store.close()
    _run(scenario())


def test_refresh_respects_limit(tmp_path):
    async def scenario():
        store, plugin = await _setup(tmp_path, [
            FakeInst("aiocqhttp", "aiocqhttp", bot=FakeBot({"group_name": "集训群"})),
            FakeInst("qq_official", "qq_official", http=FakeHttp({"group_name": "官方测试群"})),
        ])
        await _seed_members(store)
        result = await refresh_group_names(plugin, limit=1)
        assert result["updated"] == 1 and result["pending"] == 2 and result["skipped"] == 1
        await store.close()
    _run(scenario())


def test_queries_attach_group_name(tmp_path):
    async def scenario():
        store, plugin = await _setup(tmp_path)
        await _seed_members(store)
        await store.upsert_group_name("aiocqhttp", "100", "集训群")
        queries = Queries(plugin)

        found = await queries.search(platform_id="aiocqhttp")
        assert found["items"][0]["group_name"] == "集训群"

        unlinked = await queries.unlinked()
        assert unlinked["items"][0]["group_name"] == ""      # 官方群还没名字

        groups = await queries.groups()
        by_gid = {g["group_id"]: g for g in groups}
        assert by_gid["100"]["group_name"] == "集训群"
        assert by_gid["C8D6"]["group_name"] == ""

        detail = await queries.member_detail("aiocqhttp", "100", "1001")
        assert detail["group_name"] == "集训群"
        await store.close()
    _run(scenario())
