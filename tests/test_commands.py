"""指令测试：权限、限流、各指令输出。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.commands import CommandHandler
from src.identity import IdentityService
from src.models import DEFAULT_SETTINGS
from src.queries import Queries
from src.store import NicknameStore


def _run(coro):
    return asyncio.run(coro)


class FakeEvent:
    def __init__(self, text, *, sender="u1", group="g1", platform="p1", admin=False):
        self.message_str = text
        self._sender = sender
        self._group = group
        self._platform = platform
        self._admin = admin
        self.results = []

    def get_sender_id(self):
        return self._sender

    def get_group_id(self):
        return self._group

    def get_platform_id(self):
        return self._platform

    def is_admin(self):
        return self._admin

    def plain_result(self, text):
        self.results.append(text)
        return text


class FakePlugin:
    def __init__(self, store, settings=None):
        self.store = store
        self.identity = IdentityService(self)
        self.queries = Queries(self)
        self._settings = dict(DEFAULT_SETTINGS)
        if settings:
            self._settings.update(settings)

    async def get_settings(self):
        return dict(self._settings)


async def _collect(gen):
    return [x async for x in gen]


async def _setup(tmp_path):
    store = NicknameStore(tmp_path / "n.db")
    await store.initialize()
    await store.upsert_members([
        {"platform_id": "qq_official", "group_id": "C8D6", "user_id": "OPENID-A",
         "channel": "official", "nickname": "小明", "card": "",
         "source": "observed", "first_seen": 1.0, "last_seen": 2.0, "msg_count": 3},
        {"platform_id": "aiocqhttp", "group_id": "1019067385", "user_id": "10001",
         "channel": "onebot", "nickname": "小明", "card": "一班小明",
         "source": "synced", "first_seen": 1.0, "last_seen": 2.0, "msg_count": 0},
    ])
    return store


def test_help_available_to_everyone(tmp_path):
    async def scenario():
        store = await _setup(tmp_path)
        handler = CommandHandler(FakePlugin(store))
        out = await _collect(handler.handle(FakeEvent("档案馆帮助")))
        assert out and "查QQ" in out[0]
        await store.close()
    _run(scenario())


def test_admin_commands_rejected_for_non_admin(tmp_path):
    async def scenario():
        store = await _setup(tmp_path)
        handler = CommandHandler(FakePlugin(store))
        for text in ("查QQ 10001", "查昵称 小明", "查ID OPENID-A"):
            out = await _collect(handler.handle(FakeEvent(text)))
            assert out == ["此指令仅限管理员"], text
        await store.close()
    _run(scenario())


def test_lookup_qq_shows_candidates(tmp_path):
    async def scenario():
        store = await _setup(tmp_path)
        await store.upsert_link("10001", "qq_official", "OPENID-A",
                                status="confirmed", link_source="manual")
        handler = CommandHandler(FakePlugin(store))
        out = await _collect(handler.handle(FakeEvent("查QQ 10001", admin=True)))
        assert out and "QQ 10001" in out[0]
        assert "OPENID-A" in out[0]
        assert "已确认" in out[0]

        # 每次查询用新的 handler，避免触发自身的每用户 10s 限流
        handler = CommandHandler(FakePlugin(store))
        out = await _collect(handler.handle(FakeEvent("查QQ 99999", admin=True)))
        assert out and "没有找到" in out[0]
        await store.close()
    _run(scenario())


def test_lookup_openid_and_nickname(tmp_path):
    async def scenario():
        store = await _setup(tmp_path)
        await store.upsert_link("10001", "qq_official", "OPENID-A",
                                status="confirmed", link_source="manual")
        handler = CommandHandler(FakePlugin(store))
        out = await _collect(handler.handle(FakeEvent("查ID OPENID-A", admin=True)))
        assert out and "QQ 10001" in out[0] and "小明" in out[0]

        handler = CommandHandler(FakePlugin(store))
        out = await _collect(handler.handle(FakeEvent("查昵称 小明", admin=True)))
        assert out and "匹配「小明」" in out[0]
        assert "OPENID-A" in out[0] or "10001" in out[0]

        handler = CommandHandler(FakePlugin(store))
        out = await _collect(handler.handle(FakeEvent("查昵称 不存在的人", admin=True)))
        assert out and "没有找到" in out[0]
        await store.close()
    _run(scenario())


def test_self_report_flow(tmp_path):
    async def scenario():
        store = await _setup(tmp_path)
        handler = CommandHandler(FakePlugin(store))

        out = await _collect(handler.handle(FakeEvent("绑定QQ 10001", sender="OPENID-A", platform="qq_official")))
        assert out and "未开启" in out[0]

        handler = CommandHandler(FakePlugin(store, {"self_report_enabled": True}))
        out = await _collect(handler.handle(FakeEvent("绑定QQ 10001", sender="OPENID-A", platform="qq_official")))
        assert out and "等待管理员确认" in out[0]
        link = await store.get_link("qq_official", "OPENID-A")
        assert link["status"] == "candidate" and link["link_source"] == "self"
        await store.close()
    _run(scenario())


def test_extra_admin_list_grants_access(tmp_path):
    async def scenario():
        store = await _setup(tmp_path)
        handler = CommandHandler(
            FakePlugin(store, {"extra_admins": ["p1:u9"]})
        )
        out = await _collect(handler.handle(FakeEvent("查昵称 小明", sender="u9", platform="p1")))
        assert out and "匹配" in out[0]

        out = await _collect(handler.handle(FakeEvent("查昵称 小明", sender="u8", platform="p1")))
        assert out == ["此指令仅限管理员"]
        await store.close()
    _run(scenario())


def test_rate_limit_blocks_second_query(tmp_path):
    async def scenario():
        store = await _setup(tmp_path)
        handler = CommandHandler(FakePlugin(store))
        first = await _collect(handler.handle(FakeEvent("查昵称 小明", admin=True)))
        assert first and "匹配" in first[0]
        second = await _collect(handler.handle(FakeEvent("查昵称 小明", admin=True)))
        assert second == ["查询太频繁，请稍后再试"]
        await store.close()
    _run(scenario())
