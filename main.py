"""昵称ID档案馆：记录群成员昵称与 ID 关系，打通 QQ号 ↔ 官方 openid。"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

from .src.collector import Collector
from .src.utils import should_run_maintenance
from .src.commands import CommandHandler
from .src.exporter import Exporter
from .src.identity import IdentityService
from .src.models import DEFAULT_SETTINGS
from .src.queries import Queries
from .src.store import NicknameStore, default_store_path
from .src.sync import MemberSync
from .src.web_api import WebApi

DEFAULT_FLUSH_INTERVAL = 5.0


class NicknameRegistry(Star):
    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context, config)
        self.config = config if isinstance(config, dict) else {}
        self.store: NicknameStore | None = None
        self.collector: Collector | None = None
        self.identity: IdentityService | None = None
        self.syncer: MemberSync | None = None
        self.queries: Queries | None = None
        self.exporter: Exporter | None = None
        self.commands: CommandHandler | None = None
        self.webapi: WebApi | None = None
        self._flush_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    async def initialize(self) -> None:
        try:
            self.store = NicknameStore(default_store_path())
            await self.store.initialize()
        except Exception as exc:  # noqa: BLE001 - 建库失败不拖垮插件
            logger.error("昵称ID档案馆 数据库初始化失败：%s", exc, exc_info=True)
            self.store = None

        self.identity = IdentityService(self)
        self.queries = Queries(self)
        self.exporter = Exporter(self)
        self.collector = Collector(self)
        self.syncer = MemberSync(self)
        self.commands = CommandHandler(self)
        try:
            self.webapi = WebApi(self)
            self.webapi.register()
        except Exception as exc:  # noqa: BLE001 - 页面接口注册失败不影响采集
            logger.error("昵称ID档案馆 注册后台接口失败：%s", exc, exc_info=True)
            self.webapi = None
        self._flush_task = asyncio.create_task(self._flush_loop())
        logger.info(
            "昵称ID档案馆 已启动（数据库=%s）",
            "可用" if self.store is not None else "不可用",
        )

    async def terminate(self) -> None:
        if self._flush_task is not None:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
            self._flush_task = None
        if self.collector is not None:
            try:
                await self.collector.flush(force=True)
            except Exception as exc:  # noqa: BLE001
                logger.warning("昵称ID档案馆 退出前落库失败：%s", exc)
        if self.store is not None:
            await self.store.close()
        logger.info("昵称ID档案馆 已停止")

    async def _flush_loop(self) -> None:
        while True:
            settings = await self.get_settings()
            try:
                interval = float(settings.get("flush_interval") or DEFAULT_FLUSH_INTERVAL)
            except (TypeError, ValueError):
                interval = DEFAULT_FLUSH_INTERVAL
            await asyncio.sleep(max(1.0, interval))
            if self.collector is not None:
                try:
                    await self.collector.flush()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("昵称ID档案馆 定时落库异常：%s", exc)
            await self._maybe_run_maintenance()

    async def _maybe_run_maintenance(self) -> None:
        """每天最多一次的保留策略维护（对齐 qqgm _task_maintenance 的日粒度，BUG-036）。"""
        if self.store is None:
            return
        now = time.time()
        today = time.strftime("%Y%m%d")
        if not should_run_maintenance(
            today,
            now,
            getattr(self, "_last_prune_day", ""),
            getattr(self, "_last_prune_attempt", 0.0),
        ):
            return
        self._last_prune_attempt = now           # 先记尝试时间：失败也会退避 10 分钟
        try:
            settings = await self.get_settings()
            keep = int(settings.get("history_keep") or 0)
            days = int(settings.get("retention_days") or 0)
            started = time.time()
            removed = await self.store.prune_history(keep) if keep > 0 else 0
            if days > 0:
                removed += await self.store.prune_history_by_age(days)
            self._last_prune_day = today         # 成功才写日标记
            if removed:
                logger.info(
                    "昵称ID档案馆 历史维护：删除 %d 行（keep=%s days=%s 用时 %.1fs）",
                    removed,
                    keep,
                    days,
                    time.time() - started,
                )
        except Exception as exc:  # noqa: BLE001 - 维护失败不影响采集
            logger.warning("昵称ID档案馆 历史维护失败：%s", exc)

    # ------------------------------------------------------------------
    # 采集：所有群消息
    # ------------------------------------------------------------------
    @filter.platform_adapter_type(
        filter.PlatformAdapterType.QQOFFICIAL
        | filter.PlatformAdapterType.QQOFFICIAL_WEBHOOK
        | filter.PlatformAdapterType.AIOCQHTTP
    )
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        if self.collector is None or self.store is None:
            return
        try:
            await self.collector.observe_event(event)
        except Exception as exc:  # noqa: BLE001 - 采集失败不影响其他插件
            logger.warning("昵称ID档案馆 采集失败：%s", exc)

    # ------------------------------------------------------------------
    # 指令
    # ------------------------------------------------------------------
    @filter.platform_adapter_type(
        filter.PlatformAdapterType.QQOFFICIAL
        | filter.PlatformAdapterType.QQOFFICIAL_WEBHOOK
        | filter.PlatformAdapterType.AIOCQHTTP
    )
    @filter.event_message_type(
        filter.EventMessageType.GROUP_MESSAGE
        | filter.EventMessageType.PRIVATE_MESSAGE
    )
    async def on_message(self, event: AstrMessageEvent):
        if self.commands is None:
            return
        async for result in self.commands.handle(event):
            yield result

    # ------------------------------------------------------------------
    # 设置（AstrBot 插件 KV）
    # ------------------------------------------------------------------
    async def get_settings(self) -> Dict[str, Any]:
        raw = await self.get_kv_data("settings", {}) or {}
        data = dict(DEFAULT_SETTINGS)
        if isinstance(raw, dict):
            for key in DEFAULT_SETTINGS:
                if key in raw:
                    data[key] = raw[key]
        return data

    async def put_settings(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        merged = await self.get_settings()
        if isinstance(payload, dict):
            for key in DEFAULT_SETTINGS:
                if key in payload:
                    merged[key] = payload[key]
        await self.put_kv_data("settings", merged)
        return merged
