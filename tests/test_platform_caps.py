"""平台能力矩阵测试。"""
from __future__ import annotations

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.platform_caps import (
    capabilities,
    channel_by_name,
    channel_of_platform_id,
    platform_ids_by_channel,
)


class FakeInst:
    def __init__(self, pid, name):
        self._pid = pid
        self._name = name

    def meta(self):
        return types.SimpleNamespace(id=self._pid, name=self._name)


class FakePlugin:
    def __init__(self, insts):
        self.context = types.SimpleNamespace(
            platform_manager=types.SimpleNamespace(platform_insts=insts)
        )


def test_channel_by_name():
    assert channel_by_name("qq_official") == "official"
    assert channel_by_name("qq_official_webhook") == "official"
    assert channel_by_name("aiocqhttp") == "onebot"
    assert channel_by_name("telegram") == ""


def test_capabilities_matrix():
    assert capabilities("onebot") == {
        "user_id_is_qq": True,
        "has_card": True,
        "has_role": True,
        "can_sync_members": True,
    }
    assert capabilities("official")["can_sync_members"] is False
    assert capabilities("")["can_sync_members"] is False


def test_channel_of_platform_id():
    plugin = FakePlugin([FakeInst("A", "aiocqhttp"), FakeInst("B", "qq_official")])
    assert channel_of_platform_id(plugin, "A") == "onebot"
    assert channel_of_platform_id(plugin, "B") == "official"
    assert channel_of_platform_id(plugin, "C") == ""
    assert platform_ids_by_channel(plugin, "official") == ["B"]
