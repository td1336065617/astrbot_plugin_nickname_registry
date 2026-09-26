"""概览分析表字段契约（BUG-049：前端列名与后端字段必须对得上）。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.store import NicknameStore

PAGE = Path(__file__).resolve().parents[1] / "pages" / "manage" / "app.js"


def _member(platform_id: str, group_id: str, user_id: str, nickname: str) -> dict:
    return {
        "platform_id": platform_id,
        "group_id": group_id,
        "user_id": user_id,
        "nickname": nickname,
        "card": "",
        "channel": "official",
        "role": "",
        "source": "observed",
        "first_seen": 100.0,
        "last_seen": 200.0,
        "msg_count": 1,
    }


def test_duplicate_names_reports_count_and_groups(tmp_path):
    async def scenario():
        store = NicknameStore(tmp_path / "n.db")
        await store.initialize()
        # 同一昵称：3 个用户、跨 2 个群
        await store.upsert_members([
            _member("p1", "g1", "u1", "江莉"),
            _member("p1", "g1", "u2", "江莉"),
            _member("p1", "g2", "u3", "江莉"),
            _member("p1", "g1", "u9", "独一份"),
        ])
        rows = await store.analysis_duplicate_names()
        assert len(rows) == 1
        row = rows[0]
        assert row["nickname"] == "江莉"
        assert row["count"] == 3          # 人数（前端「人数」列读这个）
        assert row["groups"] == 2         # 涉及群（此前后端不返回，前端恒显示 0）
        assert row["c"] == 3              # 兼容旧字段名
        await store.close()

    asyncio.run(scenario())


def test_frontend_reads_backend_field_names():
    """源码级契约：前端「人数/涉及群」列必须读 count/groups（防止再次错位）。"""
    text = PAGE.read_text(encoding="utf-8")
    assert "r.count || r.c" in text
    assert "r.groups" in text
