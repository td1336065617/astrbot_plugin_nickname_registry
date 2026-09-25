"""昵称ID档案馆 存储层测试。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.store import NicknameStore


def _run(coro):
    return asyncio.run(coro)


def _member(**kw):
    base = {
        "platform_id": "aiocqhttp",
        "group_id": "100",
        "user_id": "12345",
        "channel": "onebot",
        "nickname": "小明",
        "card": "",
        "role": "",
        "source": "observed",
        "first_seen": 100.0,
        "last_seen": 100.0,
        "msg_count": 1,
    }
    base.update(kw)
    return base


def test_initialize_creates_schema(tmp_path):
    async def scenario():
        store = NicknameStore(tmp_path / "n.db")
        await store.initialize()
        assert store.ready
        stats = await store.stats()
        assert stats["members"] == 0
        assert stats["confirmed"] == 0
        await store.close()
    _run(scenario())


def test_upsert_members_merge_semantics(tmp_path):
    async def scenario():
        store = NicknameStore(tmp_path / "n.db")
        await store.initialize()
        await store.upsert_members([_member(role="admin", source="synced")])
        # 第二条：role 为空不覆盖；observed 优先保留；msg_count 累加；last_seen 取大
        await store.upsert_members(
            [_member(nickname="小明2", role="", source="observed", msg_count=2, last_seen=200.0)]
        )
        row = await store.get_member("aiocqhttp", "100", "12345")
        assert row["nickname"] == "小明2"
        assert row["role"] == "admin"
        assert row["source"] == "observed"
        assert row["msg_count"] == 3
        assert row["last_seen"] == 200.0
        await store.close()
    _run(scenario())


def test_history_append_list_prune(tmp_path):
    async def scenario():
        store = NicknameStore(tmp_path / "n.db")
        await store.initialize()
        rows = [
            {"platform_id": "aiocqhttp", "group_id": "100", "user_id": "1",
             "nickname": f"名{i}", "card": "", "changed_at": 100.0 + i}
            for i in range(5)
        ]
        assert await store.append_history(rows) == 5
        listed = await store.list_history("aiocqhttp", "100", "1", limit=10)
        assert [r["nickname"] for r in listed] == ["名4", "名3", "名2", "名1", "名0"]
        deleted = await store.prune_history(2)
        assert deleted == 3
        listed = await store.list_history("aiocqhttp", "100", "1", limit=10)
        assert [r["nickname"] for r in listed] == ["名4", "名3"]
        await store.close()
    _run(scenario())


def test_search_members_filters(tmp_path):
    async def scenario():
        store = NicknameStore(tmp_path / "n.db")
        await store.initialize()
        await store.upsert_members([
            _member(user_id="111", nickname="张三", channel="onebot"),
            _member(user_id="222", nickname="李四", channel="official", platform_id="qq_official"),
            _member(user_id="333", nickname="张四", channel="official", platform_id="qq_official", group_id="200"),
        ])
        await store.upsert_link("111", "aiocqhttp", "111", status="confirmed", link_source="manual")

        rows, total = await store.search_members(keyword="张")
        assert total == 2
        rows, total = await store.search_members(channel="official")
        assert total == 2
        rows, total = await store.search_members(group_id="200")
        assert total == 1 and rows[0]["user_id"] == "333"
        rows, total = await store.search_members(qq="111")
        assert total == 1 and rows[0]["user_id"] == "111"
        rows, total = await store.search_members(linked=True)
        assert total == 1
        rows, total = await store.search_members(linked=False)
        assert total == 2
        rows, total = await store.search_members(page=2, size=2)
        assert total == 3 and len(rows) == 1
        await store.close()
    _run(scenario())


def test_identity_link_crud_and_resolve(tmp_path):
    async def scenario():
        store = NicknameStore(tmp_path / "n.db")
        await store.initialize()
        await store.upsert_link("12345", "qq_official", "OPENID-A",
                                status="candidate", link_source="auto")
        link = await store.get_link("qq_official", "OPENID-A")
        assert link["status"] == "candidate"
        assert await store.resolve_qq("OPENID-A") is None          # 候选不算确认
        assert await store.resolve_openid("12345") == []

        await store.upsert_link("12345", "qq_official", "OPENID-A",
                                status="confirmed", link_source="manual")
        assert await store.resolve_qq("OPENID-A") == "12345"
        found = await store.resolve_openid("12345")
        assert len(found) == 1 and found[0]["status"] == "confirmed"

        # 一个 QQ 号可有多个 openid
        await store.upsert_link("12345", "qq_official_2", "OPENID-B",
                                status="confirmed", link_source="manual")
        assert len(await store.resolve_openid("12345")) == 2

        # 同一 (platform_id, openid) 唯一：改绑是更新而不是新增
        await store.upsert_link("99999", "qq_official", "OPENID-A",
                                status="confirmed", link_source="manual")
        assert await store.resolve_qq("OPENID-A") == "99999"
        items, total = await store.list_links(status="confirmed")
        assert total == 2   # OPENID-A 是改绑（更新），不是新增

        assert await store.delete_link("qq_official", "OPENID-A") is True
        assert await store.get_link("qq_official", "OPENID-A") is None
        await store.close()
    _run(scenario())


def test_unlinked_members_and_group_overview(tmp_path):
    async def scenario():
        store = NicknameStore(tmp_path / "n.db")
        await store.initialize()
        await store.upsert_members([
            _member(user_id="OPENID-A", channel="official", platform_id="qq_official"),
            _member(user_id="OPENID-B", channel="official", platform_id="qq_official", group_id="200"),
            _member(user_id="12345", channel="onebot"),
        ])
        rows, total = await store.unlinked_members()
        assert total == 2
        await store.upsert_link("12345", "qq_official", "OPENID-A",
                                status="confirmed", link_source="manual")
        rows, total = await store.unlinked_members()
        assert total == 1 and rows[0]["user_id"] == "OPENID-B"

        await store.record_sync("aiocqhttp", "100", total=50, ok=1, note="")
        overview = await store.group_overview()
        onebot = [g for g in overview if g["group_id"] == "100"][0]
        assert onebot["member_count"] == 1
        assert onebot["member_total"] == 50
        assert onebot["last_sync_at"] > 0
        await store.close()
    _run(scenario())


def test_stats_and_purge(tmp_path):
    async def scenario():
        store = NicknameStore(tmp_path / "n.db")
        await store.initialize()
        await store.upsert_members([
            _member(user_id="1"),                                              # aiocqhttp/100
            _member(user_id="2", group_id="200"),                               # aiocqhttp/200
            _member(user_id="OPENID", channel="official", platform_id="qq_official"),
        ])
        await store.upsert_link("1", "aiocqhttp", "1", status="confirmed", link_source="manual")
        stats = await store.stats()
        assert stats["members"] == 3
        assert stats["distinct_users"] == 3
        assert stats["groups"] == 3
        assert stats["confirmed"] == 1

        # 删「某个群」→ 只删该群成员，平台级关联不受影响
        counts = await store.purge("group", platform_id="aiocqhttp", group_id="100")
        assert counts["members"] == 1
        assert counts["links"] == 0
        assert (await store.stats())["members"] == 2
        assert (await store.stats())["confirmed"] == 1

        # 删「某成员」→ 成员行 + 他的关联一起清
        counts = await store.purge("member", platform_id="aiocqhttp", user_id="2")
        assert counts["members"] == 1
        assert counts["links"] == 0
        assert (await store.stats())["members"] == 1

        counts = await store.purge("member", platform_id="aiocqhttp", user_id="1")
        assert counts["members"] == 0          # 该成员行已被“按群删除”删掉
        assert counts["links"] == 1            # 关联此时才被清
        assert (await store.stats())["confirmed"] == 0

        counts = await store.purge("all")
        assert counts["members"] == 1
        stats = await store.stats()
        assert stats["members"] == 0 and stats["confirmed"] == 0
        await store.close()
    _run(scenario())
