"""导出 / 导入测试。"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.exporter import CSV_HEADER, Exporter
from src.models import DEFAULT_SETTINGS
from src.store import NicknameStore


def _run(coro):
    return asyncio.run(coro)


class FakePlugin:
    def __init__(self, store):
        self.store = store
        self._settings = dict(DEFAULT_SETTINGS)

    async def get_settings(self):
        return dict(self._settings)


async def _seed(store):
    await store.upsert_members([
        {"platform_id": "aiocqhttp", "group_id": "100", "user_id": "1001",
         "channel": "onebot", "nickname": "小明", "card": "一班小明",
         "source": "synced", "first_seen": 10.0, "last_seen": 20.0, "msg_count": 2},
        {"platform_id": "qq_official", "group_id": "C8D6", "user_id": "OPENID-A",
         "channel": "official", "nickname": "小红", "card": "",
         "source": "observed", "first_seen": 10.0, "last_seen": 20.0, "msg_count": 5},
    ])
    await store.upsert_link("1001", "qq_official", "OPENID-A",
                            status="confirmed", link_source="manual")
    await store.append_history([
        {"platform_id": "qq_official", "group_id": "C8D6", "user_id": "OPENID-A",
         "nickname": "小红", "card": "", "changed_at": 12.0},
    ])


def test_csv_has_bom_header_and_rows(tmp_path):
    async def scenario():
        store = NicknameStore(tmp_path / "n.db")
        await store.initialize()
        await _seed(store)
        exporter = Exporter(FakePlugin(store))

        text = await exporter.to_csv()
        assert text.startswith("\ufeff")
        rows = list(csv.reader(io.StringIO(text[1:])))
        assert rows[0] == CSV_HEADER
        assert len(rows) == 3                     # 表头 + 2 条

        by_uid = {r[4]: r for r in rows[1:]}
        onebot = by_uid["1001"]
        assert onebot[0] == "一班小明"              # 显示名优先群名片
        assert onebot[1] == "小明"
        assert onebot[3] == "1001"                 # OneBot 的 user_id 即 QQ 号
        assert onebot[9] == "未关联"

        official = by_uid["OPENID-A"]
        assert official[3] == "1001"               # 关联表给出的 QQ 号
        assert official[9] == "confirmed"
        assert official[13] == "1"                 # 历史昵称数
        await store.close()
    _run(scenario())


def test_json_round_trip(tmp_path):
    async def scenario():
        src = NicknameStore(tmp_path / "a.db")
        await src.initialize()
        await _seed(src)
        payload = json.loads(await Exporter(FakePlugin(src)).to_json())
        assert payload["version"] == 1
        assert len(payload["members"]) == 2
        assert len(payload["links"]) == 1
        assert len(payload["history"]) == 1

        dst = NicknameStore(tmp_path / "b.db")
        await dst.initialize()
        applied = await Exporter(FakePlugin(dst)).import_json(payload)
        assert applied["members"] == 2
        assert applied["links"] == 1
        assert applied["history"] == 1

        assert (await dst.get_member("aiocqhttp", "100", "1001"))["nickname"] == "小明"
        assert await dst.resolve_qq("OPENID-A") == "1001"
        assert len(await dst.list_history("qq_official", "C8D6", "OPENID-A")) == 1
        await src.close()
        await dst.close()
    _run(scenario())


def test_import_merge_rules(tmp_path):
    async def scenario():
        store = NicknameStore(tmp_path / "n.db")
        await store.initialize()
        await store.upsert_members([
            {"platform_id": "aiocqhttp", "group_id": "100", "user_id": "1001",
             "channel": "onebot", "nickname": "旧名", "card": "",
             "source": "observed", "first_seen": 1.0, "last_seen": 1.0, "msg_count": 3},
        ])
        await store.upsert_link("1001", "bot-a", "OPENID-A",
                                status="confirmed", link_source="manual")
        exporter = Exporter(FakePlugin(store))

        payload = {
            "members": [
                {"platform_id": "aiocqhttp", "group_id": "100", "user_id": "1001",
                 "channel": "onebot", "nickname": "新名", "card": "",
                 "source": "observed", "first_seen": 1.0, "last_seen": 99.0, "msg_count": 2},
            ],
            "links": [
                # 已确认的不应被候选覆盖
                {"qq": "9999", "platform_id": "bot-a", "openid": "OPENID-A",
                 "status": "candidate", "link_source": "auto", "updated_at": 999.0},
                # 新的 confirmed 应写入
                {"qq": "2002", "platform_id": "bot-b", "openid": "OPENID-B",
                 "status": "confirmed", "link_source": "manual", "updated_at": 1.0},
            ],
            "history": [
                {"platform_id": "aiocqhttp", "group_id": "100", "user_id": "1001",
                 "nickname": "新名", "card": "", "changed_at": 50.0},
                {"platform_id": "aiocqhttp", "group_id": "100", "user_id": "1001",
                 "nickname": "新名", "card": "", "changed_at": 50.0},   # 重复 → 去重
            ],
        }
        applied = await exporter.import_json(payload)
        assert applied["members"] == 1
        assert applied["links"] == 1               # 候选那条被跳过
        assert applied["history"] == 1             # 去重后只写 1 条

        row = await store.get_member("aiocqhttp", "100", "1001")
        assert row["nickname"] == "新名"
        assert row["msg_count"] == 5               # 3 + 2 求和
        assert row["last_seen"] == 99.0
        assert await store.resolve_qq("OPENID-A") == "1001"     # 未被候选覆盖
        assert await store.resolve_qq("OPENID-B") == "2002"
        await store.close()
    _run(scenario())


def test_import_rejects_invalid_payload(tmp_path):
    async def scenario():
        store = NicknameStore(tmp_path / "n.db")
        await store.initialize()
        exporter = Exporter(FakePlugin(store))
        for bad, keyword in (
            ([], "JSON 对象"),
            ({"members": "x"}, "必须是数组"),
            ({"members": [{"platform_id": "a"}]}, "缺少 platform_id"),
            ({"links": [{"platform_id": "a"}]}, "缺少 platform_id/openid"),
        ):
            try:
                await exporter.import_json(bad)
                raise AssertionError("应当拒绝: " + str(bad))
            except ValueError as exc:
                assert keyword in str(exc), f"{keyword} not in {exc}"
        assert (await store.stats())["members"] == 0
        await store.close()
    _run(scenario())
