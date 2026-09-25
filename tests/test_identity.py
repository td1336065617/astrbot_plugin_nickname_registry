"""身份关联测试：候选生成、确认/驳回/解绑、解析、自报。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.identity import IdentityService
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


async def _seed(store):
    """官方群 C8D6(两个人) + OneBot 群 1019067385(两个人)。"""
    await store.upsert_members([
        {"platform_id": "qq_official", "group_id": "C8D6", "user_id": "OPENID-A",
         "channel": "official", "nickname": "小明", "card": "",
         "source": "observed", "first_seen": 1.0, "last_seen": 1.0, "msg_count": 1},
        {"platform_id": "qq_official", "group_id": "C8D6", "user_id": "OPENID-B",
         "channel": "official", "nickname": "小红", "card": "",
         "source": "observed", "first_seen": 1.0, "last_seen": 1.0, "msg_count": 1},
        {"platform_id": "aiocqhttp", "group_id": "1019067385", "user_id": "1001",
         "channel": "onebot", "nickname": "小明", "card": "",
         "source": "synced", "first_seen": 1.0, "last_seen": 1.0, "msg_count": 0},
    ])


def test_build_candidates_needs_group_pair(tmp_path):
    async def scenario():
        store = NicknameStore(tmp_path / "n.db")
        await store.initialize()
        await _seed(store)
        identity = IdentityService(FakePlugin(store))

        # 没配对 → 不生成
        assert (await identity.build_candidates())["created"] == 0

        await store.upsert_group_pair("qq_official", "C8D6", "aiocqhttp", "1019067385")
        result = await identity.build_candidates()
        assert result["created"] == 1
        assert result["pairs"] == 1

        link = await store.get_link("qq_official", "OPENID-A")
        assert link["status"] == "candidate"
        assert link["qq"] == "1001"
        assert link["link_source"] == "auto"
        assert await store.get_link("qq_official", "OPENID-B") is None   # 小红无同名

        # 再跑一次不重复生成
        assert (await identity.build_candidates())["created"] == 0
        assert (await identity.build_candidates())["skipped"] == 1
        await store.close()
    _run(scenario())


def test_confirm_reject_unbind(tmp_path):
    async def scenario():
        store = NicknameStore(tmp_path / "n.db")
        await store.initialize()
        identity = IdentityService(FakePlugin(store))

        bad = await identity.confirm(qq="abc", platform_id="qq_official", openid="OPENID-A")
        assert bad["ok"] is False and "纯数字" in bad["error"]

        ok = await identity.confirm(qq="1001", platform_id="qq_official", openid="OPENID-A")
        assert ok["ok"] is True
        assert await store.resolve_qq("OPENID-A") == "1001"

        # 改成另一个 QQ 号 → 拒绝，提示先解绑
        conflict = await identity.confirm(qq="2002", platform_id="qq_official", openid="OPENID-A")
        assert conflict["ok"] is False and "请先解绑" in conflict["error"]
        assert await store.resolve_qq("OPENID-A") == "1001"

        # 幂等：同一个 QQ 号重复确认 → 成功
        assert (await identity.confirm(qq="1001", platform_id="qq_official", openid="OPENID-A"))["ok"]

        assert await identity.reject(platform_id="qq_official", openid="OPENID-A") is not None
        assert (await store.get_link("qq_official", "OPENID-A"))["status"] == "rejected"
        assert await store.resolve_qq("OPENID-A") is None

        assert await identity.unbind(platform_id="qq_official", openid="OPENID-A") is True
        assert await store.get_link("qq_official", "OPENID-A") is None
        assert (await identity.reject(platform_id="qq_official", openid="OPENID-A"))["ok"] is False
        await store.close()
    _run(scenario())


def test_resolve_supports_multiple_openids_per_qq(tmp_path):
    async def scenario():
        store = NicknameStore(tmp_path / "n.db")
        await store.initialize()
        identity = IdentityService(FakePlugin(store))
        await identity.confirm(qq="1001", platform_id="bot-a", openid="OPENID-A")
        await identity.confirm(qq="1001", platform_id="bot-b", openid="OPENID-B")
        found = await identity.resolve_openid("1001")
        assert len(found) == 2
        assert await identity.resolve_qq("OPENID-B", platform_id="bot-b") == "1001"
        assert await identity.resolve_qq("OPENID-B", platform_id="bot-a") is None
        await store.close()
    _run(scenario())


def test_candidates_excluded_from_resolve_by_default(tmp_path):
    async def scenario():
        store = NicknameStore(tmp_path / "n.db")
        await store.initialize()
        identity = IdentityService(FakePlugin(store))
        await store.upsert_link("1001", "bot-a", "OPENID-A",
                                status="candidate", link_source="auto")
        assert await identity.resolve_openid("1001") == []
        assert await identity.resolve_qq("OPENID-A") is None
        found = await identity.resolve_openid("1001", include_candidates=True)
        assert len(found) == 1 and found[0]["status"] == "candidate"
        await store.close()
    _run(scenario())


def test_self_report_disabled_then_candidate_only(tmp_path):
    async def scenario():
        store = NicknameStore(tmp_path / "n.db")
        await store.initialize()

        disabled = IdentityService(FakePlugin(store))
        result = await disabled.self_report(platform_id="bot-a", openid="OPENID-A", qq="1001")
        assert result["ok"] is False and "未开启" in result["error"]
        assert await store.get_link("bot-a", "OPENID-A") is None

        enabled = IdentityService(FakePlugin(store, {"self_report_enabled": True}))
        result = await enabled.self_report(platform_id="bot-a", openid="OPENID-A", qq="1001")
        assert result["ok"] is True and result["status"] == "candidate"
        # 自报永不直接生效
        assert await store.resolve_qq("OPENID-A") is None
        link = await store.get_link("bot-a", "OPENID-A")
        assert link["link_source"] == "self"
        assert link["status"] == "candidate"
        await store.close()
    _run(scenario())
