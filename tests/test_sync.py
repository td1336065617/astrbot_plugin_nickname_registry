"""OneBot 成员同步测试。"""
from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.models import DEFAULT_SETTINGS
from src.store import NicknameStore
from src.sync import MemberSync


def _run(coro):
    return asyncio.run(coro)


class FakeBot:
    def __init__(self, payload=None, error=None, delay=0.0):
        self.payload = payload
        self.error = error
        self.delay = delay
        self.calls = []

    async def call_action(self, action, **params):
        self.calls.append((action, params))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.payload


class FakeInst:
    def __init__(self, pid, name, bot):
        self._meta = types.SimpleNamespace(id=pid, name=name)
        self.bot = bot

    def meta(self):
        return self._meta


class FakePlugin:
    def __init__(self, store, insts=None, settings=None):
        self.store = store
        self.context = types.SimpleNamespace(
            platform_manager=types.SimpleNamespace(platform_insts=insts or []),
            get_platform_inst=lambda pid: next(
                (i for i in (insts or []) if i.meta().id == pid), None
            ),
        )
        self._settings = dict(DEFAULT_SETTINGS)
        if settings:
            self._settings.update(settings)

    async def get_settings(self):
        return dict(self._settings)


def test_official_is_unsupported(tmp_path):
    async def scenario():
        store = NicknameStore(tmp_path / "n.db")
        await store.initialize()
        plugin = FakePlugin(store, [FakeInst("B", "qq_official", FakeBot([]))])
        result = await MemberSync(plugin).sync_group("B", "100")
        assert result.ok is False
        assert result.reason == "unsupported"
        assert "官方接口不提供群成员列表" in result.note
        await store.close()
    _run(scenario())


def test_onebot_sync_writes_members(tmp_path):
    async def scenario():
        store = NicknameStore(tmp_path / "n.db")
        await store.initialize()
        bot = FakeBot([
            {"user_id": 1001, "nickname": "小明", "card": "一班小明", "role": "admin"},
            {"user_id": 1002, "nickname": "小红", "card": "", "role": "member"},
        ])
        plugin = FakePlugin(store, [FakeInst("A", "aiocqhttp", bot)])
        result = await MemberSync(plugin).sync_group("A", "100")
        assert result.ok is True
        assert result.total == 2
        assert bot.calls[0][0] == "get_group_member_list"
        assert bot.calls[0][1]["group_id"] == 100

        row = await store.get_member("A", "100", "1001")
        assert row["nickname"] == "小明"
        assert row["card"] == "一班小明"
        assert row["role"] == "admin"
        assert row["source"] == "synced"
        assert row["msg_count"] == 0

        overview = await store.group_overview()
        assert overview[0]["member_total"] == 2
        assert overview[0]["last_sync_at"] > 0
        await store.close()
    _run(scenario())


def test_sync_timeout_classified(tmp_path):
    async def scenario():
        store = NicknameStore(tmp_path / "n.db")
        await store.initialize()
        bot = FakeBot([], delay=0.2)
        plugin = FakePlugin(store, [FakeInst("A", "aiocqhttp", bot)], {"sync_timeout": 0.05})
        result = await MemberSync(plugin).sync_group("A", "100")
        assert result.ok is False
        assert result.reason == "timeout"
        await store.close()
    _run(scenario())


def test_sync_permission_classified(tmp_path):
    async def scenario():
        store = NicknameStore(tmp_path / "n.db")
        await store.initialize()
        bot = FakeBot(error=RuntimeError("permission denied"))
        plugin = FakePlugin(store, [FakeInst("A", "aiocqhttp", bot)])
        result = await MemberSync(plugin).sync_group("A", "100")
        assert result.ok is False
        assert result.reason == "permission"
        await store.close()
    _run(scenario())


def test_sync_via_event_group(tmp_path):
    async def scenario():
        store = NicknameStore(tmp_path / "n.db")
        await store.initialize()
        plugin = FakePlugin(store, [FakeInst("A", "aiocqhttp", FakeBot(error=RuntimeError("should not call")))])

        class FakeEvent:
            async def get_group(self, group_id=None, **kwargs):
                return types.SimpleNamespace(
                    members=[
                        types.SimpleNamespace(user_id="1001", nickname="小明"),
                        types.SimpleNamespace(user_id="1002", nickname="小红"),
                    ],
                    group_owner="1001",
                    group_admins=["1002"],
                )

        result = await MemberSync(plugin).sync_group("A", "100", event=FakeEvent())
        assert result.ok is True and result.total == 2
        assert (await store.get_member("A", "100", "1001"))["role"] == "owner"
        assert (await store.get_member("A", "100", "1002"))["role"] == "admin"
        await store.close()
    _run(scenario())
