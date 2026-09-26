"""保留策略维护回归测试（BUG-012 / BUG-036）。"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.models import DEFAULT_SETTINGS
from src.store import NicknameStore
from src.utils import should_run_maintenance


def _run(coro):
    return asyncio.run(coro)


def test_sync_timeout_is_declared():
    """BUG-012：sync_timeout 必须在默认设置里，否则设置页保存后被过滤掉。"""
    assert DEFAULT_SETTINGS.get("sync_timeout") == 20.0


def test_should_run_maintenance_day_granularity_and_backoff():
    now = 1_790_000_000.0
    day = "20260926"
    assert should_run_maintenance(day, now, day, 0.0) is False          # 今天已跑过
    assert should_run_maintenance(day, now, "20260925", now - 10) is False   # 退避未到
    assert should_run_maintenance(day, now, "20260925", now - 601) is True   # 隔日且退避已过
    assert should_run_maintenance("", now, "20260925", 0.0) is False        # 无日期不跑


def _history_rows(rows):
    return [
        {
            "platform_id": pid,
            "group_id": gid,
            "user_id": uid,
            "nickname": f"名{i}",
            "card": "",
            "changed_at": 100.0 + i,
        }
        for (pid, gid, uid), count in rows.items()
        for i in range(count)
    ]


def test_prune_history_keeps_latest_per_scope(tmp_path):
    async def scenario():
        store = NicknameStore(tmp_path / "n.db")
        await store.initialize()
        data = _history_rows({("p1", "g1", "u1"): 5, ("p1", "g1", "u2"): 3, ("p1", "g2", "u1"): 2})
        assert await store.append_history(data) == 10
        removed = await store.prune_history(1)
        assert removed == 7                                  # (5-1)+(3-1)+(2-1)
        for (pid, gid, uid) in (("p1", "g1", "u1"), ("p1", "g1", "u2"), ("p1", "g2", "u1")):
            rows = await store.list_history(pid, gid, uid, limit=10)
            assert len(rows) == 1, (pid, gid, uid)
        await store.close()

    _run(scenario())


def test_prune_history_batched_matches_single_pass(tmp_path):
    async def scenario():
        store = NicknameStore(tmp_path / "n.db")
        await store.initialize()
        await store.append_history(_history_rows({("p1", "g1", "u1"): 5}))
        # batch=1 强制走多轮循环，结果应与一次性删除一致
        assert await store.prune_history(2, batch=1) == 3
        rows = await store.list_history("p1", "g1", "u1", limit=10)
        assert [r["nickname"] for r in rows] == ["名4", "名3"]
        await store.close()

    _run(scenario())


def test_prune_history_by_age_only_removes_old_rows(tmp_path):
    async def scenario():
        store = NicknameStore(tmp_path / "n.db")
        await store.initialize()
        now = time.time()
        rows = [
            {"platform_id": "p1", "group_id": "g1", "user_id": "u1", "nickname": "old", "card": "", "changed_at": now - 3 * 86400},
            {"platform_id": "p1", "group_id": "g1", "user_id": "u1", "nickname": "new", "card": "", "changed_at": now - 60},
        ]
        await store.append_history(rows)
        assert await store.prune_history_by_age(1) == 1
        left = await store.list_history("p1", "g1", "u1", limit=10)
        assert [r["nickname"] for r in left] == ["new"]
        await store.close()

    _run(scenario())


def test_prune_zero_limits_do_nothing(tmp_path):
    async def scenario():
        store = NicknameStore(tmp_path / "n.db")
        await store.initialize()
        await store.append_history(_history_rows({("p1", "g1", "u1"): 3}))
        assert await store.prune_history(0) == 0
        assert await store.prune_history_by_age(0) == 0
        assert len(await store.list_history("p1", "g1", "u1", limit=10)) == 3
        await store.close()

    _run(scenario())
