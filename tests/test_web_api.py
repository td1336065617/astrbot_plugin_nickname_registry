"""后台接口测试：路由注册与关键 handler。"""
from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import web_api as web_mod
from src.exporter import Exporter
from src.identity import IdentityService
from src.models import DEFAULT_SETTINGS
from src.queries import Queries
from src.store import NicknameStore
from src.sync import MemberSync

# 用身份函数替换真实 JSON 响应包装，便于断言 payload
web_mod.json_response = lambda value: value
web_mod.error_response = lambda value: value


def _run(coro):
    return asyncio.run(coro)


class FakeRequest:
    def __init__(self, body=None, args=None):
        self.body = body
        self.args = args or {}

    async def json(self, default=None):
        return self.body if self.body is not None else default


class FakeInst:
    def __init__(self, pid, name):
        self._meta = types.SimpleNamespace(id=pid, name=name)

    def meta(self):
        return self._meta


class FakePlugin:
    def __init__(self, store, insts=None):
        self.store = store
        self.routes = []
        self.context = types.SimpleNamespace(
            platform_manager=types.SimpleNamespace(platform_insts=insts or []),
            register_web_api=lambda route, handler, methods, desc: self.routes.append(
                (route, methods[0], handler.__name__)
            ),
        )
        self._settings = dict(DEFAULT_SETTINGS)
        self.queries = Queries(self)
        self.identity = IdentityService(self)
        self.exporter = Exporter(self)
        self.syncer = MemberSync(self)

    async def get_settings(self):
        return dict(self._settings)

    async def put_settings(self, payload):
        self._settings.update({k: v for k, v in payload.items() if k in DEFAULT_SETTINGS})
        return dict(self._settings)


async def _setup(tmp_path):
    store = NicknameStore(tmp_path / "n.db")
    await store.initialize()
    return store


def test_register_all_routes(tmp_path):
    async def scenario():
        store = await _setup(tmp_path)
        plugin = FakePlugin(store)
        web_mod.WebApi(plugin).register()
        prefix = web_mod.PLUGIN_NAME + "/"
        names = {(r[0].split(prefix, 1)[1], r[1]) for r in plugin.routes}
        assert ("summary", "GET") in names
        assert ("members", "GET") in names
        assert ("links/confirm", "POST") in names
        assert ("group-pairs", "GET") in names and ("group-pairs", "POST") in names
        assert ("export", "GET") in names and ("import", "POST") in names
        assert len(plugin.routes) == len(web_mod.ROUTES)
        await store.close()
    _run(scenario())


def test_summary_members_and_detail(tmp_path):
    async def scenario():
        store = await _setup(tmp_path)
        await store.upsert_members([
            {"platform_id": "qq_official", "group_id": "C8D6", "user_id": "OPENID-A",
             "channel": "official", "nickname": "小明", "card": "",
             "source": "observed", "first_seen": 1.0, "last_seen": 2.0, "msg_count": 4},
        ])
        plugin = FakePlugin(store)
        api = web_mod.WebApi(plugin)

        web_mod.request = FakeRequest(args={})
        out = await api._summary()
        assert out["data"]["stats"]["members"] == 1

        web_mod.request = FakeRequest(args={"q": "小明", "size": "10"})
        out = await api._members()
        item = out["data"]["items"][0]
        assert item["user_id"] == "OPENID-A"
        assert item["qq_display"] == ""          # 官方成员未关联 → 不显示 QQ 号

        web_mod.request = FakeRequest(args={"platform_id": "qq_official",
                                            "group_id": "C8D6", "user_id": "OPENID-A"})
        out = await api._member()
        assert out["data"]["member"]["nickname"] == "小明"

        web_mod.request = FakeRequest(args={})
        missing = await api._member()
        assert isinstance(missing, str) and "缺少" in missing
        await store.close()
    _run(scenario())


def test_links_confirm_reject_unbind(tmp_path):
    async def scenario():
        store = await _setup(tmp_path)
        plugin = FakePlugin(store)
        api = web_mod.WebApi(plugin)

        web_mod.request = FakeRequest({"qq": "1001", "platform_id": "b", "openid": "O"})
        out = await api._links_confirm()
        assert out["data"]["ok"] is True
        assert await store.resolve_qq("O", platform_id="b") == "1001"

        web_mod.request = FakeRequest({"qq": "abc", "platform_id": "b", "openid": "O"})
        bad = await api._links_confirm()
        assert isinstance(bad, str) and "纯数字" in bad

        web_mod.request = FakeRequest({"platform_id": "b", "openid": "O"})
        out = await api._links_reject()
        assert out["data"]["ok"] is True

        web_mod.request = FakeRequest({"platform_id": "b", "openid": "O"})
        out = await api._links_unbind()
        assert out["data"]["removed"] is True

        web_mod.request = FakeRequest({"platform_id": "b", "openid": "O"})
        gone = await api._links_unbind()
        assert isinstance(gone, str)
        await store.close()
    _run(scenario())


def test_links_auto_builds_pair_and_candidates(tmp_path):
    async def scenario():
        store = await _setup(tmp_path)
        await store.upsert_members([
            {"platform_id": "qq_official", "group_id": "C8D6", "user_id": "OPENID-A",
             "channel": "official", "nickname": "小明", "card": "",
             "source": "observed", "first_seen": 1.0, "last_seen": 1.0, "msg_count": 1},
            {"platform_id": "aiocqhttp", "group_id": "1019067385", "user_id": "1001",
             "channel": "onebot", "nickname": "小明", "card": "",
             "source": "synced", "first_seen": 1.0, "last_seen": 1.0, "msg_count": 0},
        ])
        api = web_mod.WebApi(FakePlugin(store))
        web_mod.request = FakeRequest({
            "official_platform_id": "qq_official", "official_group_id": "C8D6",
            "onebot_platform_id": "aiocqhttp", "onebot_group_id": "1019067385",
        })
        out = await api._links_auto()
        assert out["data"]["created"] == 1
        assert len(await store.list_group_pairs()) == 1

        web_mod.request = FakeRequest({})
        bad = await api._links_auto()
        assert isinstance(bad, str) and "请先选择" in bad
        await store.close()
    _run(scenario())


def test_sync_handler_reports_unsupported(tmp_path):
    async def scenario():
        store = await _setup(tmp_path)
        plugin = FakePlugin(store, [FakeInst("B", "qq_official")])
        api = web_mod.WebApi(plugin)
        web_mod.request = FakeRequest({"platform_id": "B", "group_id": "C8D6"})
        out = await api._sync()
        assert isinstance(out, str) and "官方接口不提供群成员列表" in out
        await store.close()
    _run(scenario())


def test_export_import_purge_settings(tmp_path):
    async def scenario():
        store = await _setup(tmp_path)
        await store.upsert_members([
            {"platform_id": "aiocqhttp", "group_id": "100", "user_id": "1001",
             "channel": "onebot", "nickname": "小明", "card": "",
             "source": "synced", "first_seen": 1.0, "last_seen": 2.0, "msg_count": 1},
        ])
        plugin = FakePlugin(store)
        api = web_mod.WebApi(plugin)

        web_mod.request = FakeRequest(args={"format": "csv"})
        out = await api._export()
        assert out["data"]["format"] == "csv"
        assert out["data"]["content"].startswith("\ufeff")

        web_mod.request = FakeRequest(args={"format": "xml"})
        bad = await api._export()
        assert isinstance(bad, str) and "csv / json" in bad

        web_mod.request = FakeRequest({"payload": {"members": [], "links": [], "history": []}})
        out = await api._import()
        assert out["data"]["applied"]["members"] == 0

        web_mod.request = FakeRequest({"payload": {"members": "bad"}})
        bad = await api._import()
        assert isinstance(bad, str) and "必须是数组" in bad

        web_mod.request = FakeRequest({"scope": "nope"})
        bad = await api._purge()
        assert isinstance(bad, str) and "scope" in bad

        web_mod.request = FakeRequest({"scope": "all"})
        out = await api._purge()
        assert out["data"]["members"] == 1

        web_mod.request = FakeRequest({"settings": {"capture_enabled": False}})
        out = await api._settings_set()
        assert out["data"]["capture_enabled"] is False
        web_mod.request = FakeRequest(args={})
        out = await api._settings_get()
        assert out["data"]["capture_enabled"] is False
        await store.close()
    _run(scenario())


def test_analysis_route_and_payload(tmp_path):
    """BUG-013：概览页要用的 analysis 路由必须注册且返回两张表。"""

    async def scenario():
        store = await _setup(tmp_path)
        plugin = FakePlugin(store)
        web_mod.WebApi(plugin).register()
        prefix = web_mod.PLUGIN_NAME + "/"
        names = {(r[0].split(prefix, 1)[1], r[1]) for r in plugin.routes}
        assert ("analysis", "GET") in names
        payload = await web_mod.WebApi(plugin)._analysis()
        assert set(payload["data"]) == {"duplicate_names", "rename_rank"}
        await store.close()

    _run(scenario())
