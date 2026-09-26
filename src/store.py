"""昵称ID档案馆 的 SQLite 存储层。

约定（沿用 astrbot_plugin_qq_group_manager）：
- 只用标准库 sqlite3，异步通过 asyncio.to_thread，不引入第三方依赖；
- 写操作由 asyncio.Lock 串行化；批量写用单事务 + executemany；
- 启动时建表并按 SCHEMA_VERSION 迁移（迁移前备份一份 .bak-<ts>）。
"""
from __future__ import annotations

import asyncio
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:  # 测试环境可无 astrbot
    from astrbot.api import logger
except Exception:  # noqa: BLE001
    import logging

    logger = logging.getLogger("nickname_registry")

from .models import PLUGIN_NAME

SCHEMA_VERSION = 1

DDL_STATEMENTS: Tuple[str, ...] = (
    "CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL)",
    """
    CREATE TABLE IF NOT EXISTS member (
      platform_id TEXT NOT NULL,
      group_id    TEXT NOT NULL,
      user_id     TEXT NOT NULL,
      channel     TEXT NOT NULL DEFAULT '',
      nickname    TEXT NOT NULL DEFAULT '',
      card        TEXT NOT NULL DEFAULT '',
      role        TEXT NOT NULL DEFAULT '',
      source      TEXT NOT NULL DEFAULT 'observed',
      first_seen  REAL NOT NULL,
      last_seen   REAL NOT NULL,
      msg_count   INTEGER NOT NULL DEFAULT 0,
      PRIMARY KEY (platform_id, group_id, user_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_member_nickname ON member (nickname)",
    "CREATE INDEX IF NOT EXISTS idx_member_card ON member (card)",
    "CREATE INDEX IF NOT EXISTS idx_member_user ON member (user_id)",
    "CREATE INDEX IF NOT EXISTS idx_member_channel ON member (channel, last_seen DESC)",
    """
    CREATE TABLE IF NOT EXISTS nickname_history (
      seq         INTEGER PRIMARY KEY AUTOINCREMENT,
      platform_id TEXT NOT NULL,
      group_id    TEXT NOT NULL,
      user_id     TEXT NOT NULL,
      nickname    TEXT NOT NULL DEFAULT '',
      card        TEXT NOT NULL DEFAULT '',
      changed_at  REAL NOT NULL,
      source      TEXT NOT NULL DEFAULT 'observed'
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_hist_user ON nickname_history (platform_id, group_id, user_id, changed_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS identity_link (
      seq         INTEGER PRIMARY KEY AUTOINCREMENT,
      qq          TEXT NOT NULL,
      platform_id TEXT NOT NULL,
      openid      TEXT NOT NULL,
      status      TEXT NOT NULL DEFAULT 'candidate',
      link_source TEXT NOT NULL DEFAULT 'manual',
      note        TEXT NOT NULL DEFAULT '',
      created_at  REAL NOT NULL,
      updated_at  REAL NOT NULL,
      UNIQUE (platform_id, openid)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_link_qq ON identity_link (qq)",
    "CREATE INDEX IF NOT EXISTS idx_link_status ON identity_link (status)",
    """
    CREATE TABLE IF NOT EXISTS group_sync (
      platform_id  TEXT NOT NULL,
      group_id     TEXT NOT NULL,
      last_sync_at REAL NOT NULL DEFAULT 0,
      member_total INTEGER NOT NULL DEFAULT 0,
      synced_ok    INTEGER NOT NULL DEFAULT 0,
      note         TEXT NOT NULL DEFAULT '',
      PRIMARY KEY (platform_id, group_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS group_pair (
      seq                  INTEGER PRIMARY KEY AUTOINCREMENT,
      official_platform_id TEXT NOT NULL,
      official_group_id    TEXT NOT NULL,
      onebot_platform_id   TEXT NOT NULL,
      onebot_group_id      TEXT NOT NULL,
      created_at           REAL NOT NULL,
      UNIQUE (official_platform_id, official_group_id,
              onebot_platform_id, onebot_group_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS group_meta (
      platform_id TEXT NOT NULL,
      group_id    TEXT NOT NULL,
      name        TEXT NOT NULL DEFAULT '',
      source      TEXT NOT NULL DEFAULT '',
      updated_at  REAL NOT NULL,
      PRIMARY KEY (platform_id, group_id)
    )
    """,
)

_UPSERT_MEMBER_SQL = """
INSERT INTO member(platform_id, group_id, user_id, channel, nickname, card, role,
                   source, first_seen, last_seen, msg_count)
VALUES (?,?,?,?,?,?,?,?,?,?,?)
ON CONFLICT(platform_id, group_id, user_id) DO UPDATE SET
  channel   = excluded.channel,
  nickname  = excluded.nickname,
  card      = excluded.card,
  role      = CASE WHEN excluded.role <> '' THEN excluded.role ELSE member.role END,
  source    = CASE WHEN member.source = 'observed' THEN member.source ELSE excluded.source END,
  last_seen = MAX(member.last_seen, excluded.last_seen),
  msg_count = member.msg_count + excluded.msg_count
"""

_INSERT_HISTORY_SQL = """
INSERT INTO nickname_history(platform_id, group_id, user_id, nickname, card,
                             changed_at, source)
VALUES (?,?,?,?,?,?,?)
"""

_UPSERT_LINK_SQL = """
INSERT INTO identity_link(qq, platform_id, openid, status, link_source, note,
                          created_at, updated_at)
VALUES (?,?,?,?,?,?,?,?)
ON CONFLICT(platform_id, openid) DO UPDATE SET
  qq          = excluded.qq,
  status      = excluded.status,
  link_source = excluded.link_source,
  note        = excluded.note,
  updated_at  = excluded.updated_at
"""


def default_store_path() -> Path:
    """data/plugin_data/<插件名>/nickname.db。"""
    try:
        from astrbot.core.star.star_tools import StarTools

        return Path(StarTools.get_data_dir()) / "nickname.db"
    except Exception:  # noqa: BLE001 - 兼容旧版本/测试环境
        try:
            from astrbot.core.utils.astrbot_path import get_astrbot_data_path

            return Path(get_astrbot_data_path()) / "plugin_data" / PLUGIN_NAME / "nickname.db"
        except Exception:  # noqa: BLE001
            return Path(__file__).resolve().parents[1] / "data" / "nickname.db"


class NicknameStore:
    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self._write_lock = asyncio.Lock()
        self._ready = False

    # ---------------- 基础 ----------------
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.Error:
            pass
        return conn

    async def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(self._initialize_sync)
        self._ready = True

    def _initialize_sync(self) -> None:
        conn = self._connect()
        try:
            with conn:
                for stmt in DDL_STATEMENTS:
                    conn.execute(stmt)
                row = conn.execute(
                    "SELECT v FROM meta WHERE k='schema_version'"
                ).fetchone()
                current = int(row["v"]) if row else 0
                if current == 0:
                    conn.execute(
                        "INSERT OR REPLACE INTO meta(k, v) VALUES('schema_version', ?)",
                        (str(SCHEMA_VERSION),),
                    )
                    conn.execute(
                        "INSERT OR REPLACE INTO meta(k, v) VALUES('created_at', ?)",
                        (str(time.time()),),
                    )
                elif current < SCHEMA_VERSION:
                    backup = self.db_path.with_name(
                        self.db_path.name
                        + ".bak-"
                        + time.strftime("%Y%m%d%H%M%S")
                    )
                    try:
                        shutil.copy2(self.db_path, backup)
                    except OSError as exc:  # noqa: BLE001
                        logger.warning("迁移前备份失败（继续迁移）: %s", exc)
                    # 预留：后续版本在此按序执行迁移
                    conn.execute(
                        "INSERT OR REPLACE INTO meta(k, v) VALUES('schema_version', ?)",
                        (str(SCHEMA_VERSION),),
                    )
        finally:
            conn.close()

    async def close(self) -> None:
        self._ready = False

    @property
    def ready(self) -> bool:
        return self._ready

    # ---------------- 成员 ----------------
    async def upsert_members(self, rows: Sequence[Dict[str, Any]]) -> int:
        if not rows:
            return 0
        async with self._write_lock:
            return await asyncio.to_thread(self._upsert_members_sync, list(rows))

    def _upsert_members_sync(self, rows: List[Dict[str, Any]]) -> int:
        payload = [
            (
                str(r.get("platform_id") or ""),
                str(r.get("group_id") or ""),
                str(r.get("user_id") or ""),
                str(r.get("channel") or ""),
                str(r.get("nickname") or ""),
                str(r.get("card") or ""),
                str(r.get("role") or ""),
                str(r.get("source") or "observed"),
                float(r.get("first_seen") or time.time()),
                float(r.get("last_seen") or time.time()),
                int(r.get("msg_count") or 0),
            )
            for r in rows
        ]
        conn = self._connect()
        try:
            with conn:
                conn.executemany(_UPSERT_MEMBER_SQL, payload)
        finally:
            conn.close()
        return len(payload)

    async def append_history(self, rows: Sequence[Dict[str, Any]]) -> int:
        if not rows:
            return 0
        async with self._write_lock:
            return await asyncio.to_thread(self._append_history_sync, list(rows))

    def _append_history_sync(self, rows: List[Dict[str, Any]]) -> int:
        payload = [
            (
                str(r.get("platform_id") or ""),
                str(r.get("group_id") or ""),
                str(r.get("user_id") or ""),
                str(r.get("nickname") or ""),
                str(r.get("card") or ""),
                float(r.get("changed_at") or time.time()),
                str(r.get("source") or "observed"),
            )
            for r in rows
        ]
        conn = self._connect()
        try:
            with conn:
                conn.executemany(_INSERT_HISTORY_SQL, payload)
        finally:
            conn.close()
        return len(payload)

    async def get_member(
        self, platform_id: str, group_id: str, user_id: str
    ) -> Optional[Dict[str, Any]]:
        return await asyncio.to_thread(
            self._get_member_sync, str(platform_id), str(group_id), str(user_id)
        )

    def _get_member_sync(
        self, platform_id: str, group_id: str, user_id: str
    ) -> Optional[Dict[str, Any]]:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM member WHERE platform_id=? AND group_id=? AND user_id=?",
                (platform_id, group_id, user_id),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    async def search_members(
        self,
        *,
        platform_id: str = "",
        group_id: str = "",
        channel: str = "",
        openid: str = "",
        qq: str = "",
        keyword: str = "",
        linked: Optional[bool] = None,
        page: int = 1,
        size: int = 50,
    ) -> Tuple[List[Dict[str, Any]], int]:
        return await asyncio.to_thread(
            self._search_members_sync,
            platform_id,
            group_id,
            channel,
            openid,
            qq,
            keyword,
            linked,
            max(1, int(page)),
            max(1, int(size)),
        )

    def _search_members_sync(self, platform_id, group_id, channel, openid, qq,
                             keyword, linked, page, size):
        linked_flag = -1 if linked is None else (1 if linked else 0)
        where = """
            WHERE (? = '' OR m.platform_id = ?)
              AND (? = '' OR m.group_id = ?)
              AND (? = '' OR m.channel = ?)
              AND (? = '' OR m.user_id LIKE ?)
              AND (? = '' OR IFNULL(l.qq, '') LIKE ?)
              AND (? = '' OR m.nickname LIKE ? OR m.card LIKE ?)
              AND (? = -1
                   OR (? = 1 AND l.qq IS NOT NULL)
                   OR (? = 0 AND l.qq IS NULL))
        """
        base_from = (
            "FROM member m LEFT JOIN identity_link l"
            " ON l.platform_id = m.platform_id AND l.openid = m.user_id"
            " AND l.status = 'confirmed'"
        )
        pid = str(platform_id or "")
        gid = str(group_id or "")
        chn = str(channel or "")
        uid_like = ("%" + openid + "%") if openid else ""
        qq_like = ("%" + qq + "%") if qq else ""
        kw_like = ("%" + keyword + "%") if keyword else ""
        params = (
            pid, pid,
            gid, gid,
            chn, chn,
            uid_like, uid_like,
            qq_like, qq_like,
            kw_like, kw_like, kw_like,
            linked_flag, linked_flag, linked_flag,
        )
        conn = self._connect()
        try:
            total = int(
                conn.execute(
                    "SELECT COUNT(*) AS c " + base_from + where, params
                ).fetchone()["c"]
            )
            rows = conn.execute(
                "SELECT m.*, l.qq AS qq, l.status AS link_status "
                + base_from
                + where
                + " ORDER BY m.last_seen DESC LIMIT ? OFFSET ?",
                params + (size, (page - 1) * size),
            ).fetchall()
            return [dict(r) for r in rows], total
        finally:
            conn.close()

    async def list_history(
        self, platform_id: str, group_id: str, user_id: str, limit: int = 50
    ) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(
            self._list_history_sync, platform_id, group_id, user_id, limit
        )

    def _list_history_sync(self, platform_id, group_id, user_id, limit):
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM nickname_history"
                " WHERE platform_id=? AND group_id=? AND user_id=?"
                " ORDER BY changed_at DESC, seq DESC LIMIT ?",
                (str(platform_id), str(group_id), str(user_id), int(limit)),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    async def prune_history(self, keep: int, *, batch: int = 2000) -> int:
        """每人每群只保留最新 keep 条；分批执行，避免长时间持写锁（BUG-036）。"""
        keep = int(keep or 0)
        batch = max(1, int(batch or 2000))
        if keep <= 0:
            return 0
        total = 0
        while True:
            async with self._write_lock:
                removed = await asyncio.to_thread(self._prune_history_batch_sync, keep, batch)
            if removed <= 0:
                break
            total += removed
            await asyncio.sleep(0)          # 让出事件循环，热路径可以插进来
            if removed < batch:
                break
        return total

    def _prune_history_batch_sync(self, keep: int, batch: int) -> int:
        conn = self._connect()
        try:
            with conn:
                cur = conn.execute(
                    """
                    DELETE FROM nickname_history WHERE seq IN (
                      SELECT seq FROM (
                        SELECT seq,
                               ROW_NUMBER() OVER (
                                 PARTITION BY platform_id, group_id, user_id
                                 ORDER BY changed_at DESC, seq DESC
                               ) AS rn
                        FROM nickname_history
                      ) WHERE rn > ?
                      LIMIT ?
                    )
                    """,
                    (keep, batch),
                )
                return int(cur.rowcount or 0)
        finally:
            conn.close()

    async def prune_history_by_age(self, days: int, *, batch: int = 2000) -> int:
        """删除超过 days 天的改名历史（0 = 不删）；同样分批短事务。"""
        days = int(days or 0)
        batch = max(1, int(batch or 2000))
        if days <= 0:
            return 0
        cutoff = time.time() - days * 86400
        total = 0
        while True:
            async with self._write_lock:
                removed = await asyncio.to_thread(self._prune_history_age_batch_sync, cutoff, batch)
            if removed <= 0:
                break
            total += removed
            await asyncio.sleep(0)
            if removed < batch:
                break
        return total

    def _prune_history_age_batch_sync(self, cutoff: float, batch: int) -> int:
        conn = self._connect()
        try:
            with conn:
                cur = conn.execute(
                    """
                    DELETE FROM nickname_history WHERE seq IN (
                      SELECT seq FROM nickname_history WHERE changed_at < ? LIMIT ?
                    )
                    """,
                    (float(cutoff), batch),
                )
                return int(cur.rowcount or 0)
        finally:
            conn.close()

    # ---------------- 身份关联 ----------------
    async def get_link(self, platform_id: str, openid: str) -> Optional[Dict[str, Any]]:
        return await asyncio.to_thread(self._get_link_sync, platform_id, openid)

    def _get_link_sync(self, platform_id: str, openid: str) -> Optional[Dict[str, Any]]:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM identity_link WHERE platform_id=? AND openid=?",
                (str(platform_id), str(openid)),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    async def upsert_link(
        self,
        qq: str,
        platform_id: str,
        openid: str,
        *,
        status: str,
        link_source: str,
        note: str = "",
    ) -> None:
        async with self._write_lock:
            await asyncio.to_thread(
                self._upsert_link_sync, qq, platform_id, openid, status, link_source, note
            )

    def _upsert_link_sync(self, qq, platform_id, openid, status, link_source, note):
        now = time.time()
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    _UPSERT_LINK_SQL,
                    (
                        str(qq),
                        str(platform_id),
                        str(openid),
                        str(status),
                        str(link_source),
                        str(note or ""),
                        now,
                        now,
                    ),
                )
        finally:
            conn.close()

    async def delete_link(self, platform_id: str, openid: str) -> bool:
        async with self._write_lock:
            return await asyncio.to_thread(self._delete_link_sync, platform_id, openid)

    def _delete_link_sync(self, platform_id: str, openid: str) -> bool:
        conn = self._connect()
        try:
            with conn:
                cur = conn.execute(
                    "DELETE FROM identity_link WHERE platform_id=? AND openid=?",
                    (str(platform_id), str(openid)),
                )
                return bool(cur.rowcount)
        finally:
            conn.close()

    async def list_links(
        self, status: str = "", page: int = 1, size: int = 50
    ) -> Tuple[List[Dict[str, Any]], int]:
        return await asyncio.to_thread(
            self._list_links_sync, str(status or ""), max(1, int(page)), max(1, int(size))
        )

    def _list_links_sync(self, status, page, size):
        conn = self._connect()
        try:
            where = " WHERE (? = '' OR status = ?)"
            params = (status, status)
            total = int(
                conn.execute(
                    "SELECT COUNT(*) AS c FROM identity_link" + where, params
                ).fetchone()["c"]
            )
            rows = conn.execute(
                "SELECT * FROM identity_link"
                + where
                + " ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                params + (size, (page - 1) * size),
            ).fetchall()
            return [dict(r) for r in rows], total
        finally:
            conn.close()

    async def resolve_openid(
        self, qq: str, *, platform_id: str = "", include_candidates: bool = False
    ) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(
            self._resolve_openid_sync, str(qq), str(platform_id or ""), bool(include_candidates)
        )

    def _resolve_openid_sync(self, qq, platform_id, include_candidates):
        statuses = ("confirmed", "candidate") if include_candidates else ("confirmed",)
        placeholders = ",".join("?" for _ in statuses)
        sql = (
            "SELECT * FROM identity_link WHERE qq=?"
            f" AND status IN ({placeholders})"
            " AND (? = '' OR platform_id = ?)"
            " ORDER BY updated_at DESC"
        )
        conn = self._connect()
        try:
            rows = conn.execute(
                sql, (qq, *statuses, platform_id, platform_id)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    async def resolve_qq(self, openid: str, *, platform_id: str = "") -> Optional[str]:
        return await asyncio.to_thread(self._resolve_qq_sync, str(openid), str(platform_id or ""))

    def _resolve_qq_sync(self, openid, platform_id):
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT qq FROM identity_link WHERE openid=? AND status='confirmed'"
                " AND (? = '' OR platform_id = ?) ORDER BY updated_at DESC LIMIT 1",
                (openid, platform_id, platform_id),
            ).fetchone()
            return str(row["qq"]) if row else None
        finally:
            conn.close()

    async def unlinked_members(
        self, page: int = 1, size: int = 50
    ) -> Tuple[List[Dict[str, Any]], int]:
        return await asyncio.to_thread(
            self._unlinked_sync, max(1, int(page)), max(1, int(size))
        )

    def _unlinked_sync(self, page, size):
        base = (
            "FROM member m LEFT JOIN identity_link l"
            " ON l.platform_id = m.platform_id AND l.openid = m.user_id"
            " WHERE m.channel='official' AND l.seq IS NULL"
        )
        conn = self._connect()
        try:
            total = int(
                conn.execute("SELECT COUNT(*) AS c " + base).fetchone()["c"]
            )
            rows = conn.execute(
                "SELECT m.* " + base + " ORDER BY m.last_seen DESC LIMIT ? OFFSET ?",
                (size, (page - 1) * size),
            ).fetchall()
            return [dict(r) for r in rows], total
        finally:
            conn.close()

    # ---------------- 群同步 ----------------
    async def record_sync(
        self, platform_id: str, group_id: str, total: int, ok: int, note: str = ""
    ) -> None:
        async with self._write_lock:
            await asyncio.to_thread(
                self._record_sync_sync, platform_id, group_id, total, ok, note
            )

    def _record_sync_sync(self, platform_id, group_id, total, ok, note):
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO group_sync(platform_id, group_id, last_sync_at,
                                           member_total, synced_ok, note)
                    VALUES (?,?,?,?,?,?)
                    ON CONFLICT(platform_id, group_id) DO UPDATE SET
                      last_sync_at = excluded.last_sync_at,
                      member_total = excluded.member_total,
                      synced_ok    = excluded.synced_ok,
                      note         = excluded.note
                    """,
                    (str(platform_id), str(group_id), time.time(), int(total), int(ok), str(note or "")),
                )
        finally:
            conn.close()

    async def group_overview(self) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self._group_overview_sync)

    def _group_overview_sync(self) -> List[Dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT m.platform_id AS platform_id,
                       m.group_id    AS group_id,
                       MAX(m.channel) AS channel,
                       COUNT(*)      AS member_count,
                       SUM(CASE WHEN m.source='synced' THEN 1 ELSE 0 END) AS synced_count,
                       MIN(m.first_seen) AS first_seen,
                       MAX(m.last_seen)  AS last_seen,
                       IFNULL(s.last_sync_at, 0) AS last_sync_at,
                       IFNULL(s.member_total, 0) AS member_total,
                       IFNULL(s.note, '')        AS note
                FROM member m
                LEFT JOIN group_sync s
                  ON s.platform_id = m.platform_id AND s.group_id = m.group_id
                GROUP BY m.platform_id, m.group_id
                ORDER BY member_count DESC
                """
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ---------------- 统计与清理 ----------------
    async def stats(self) -> Dict[str, Any]:
        return await asyncio.to_thread(self._stats_sync)

    def _stats_sync(self) -> Dict[str, Any]:
        conn = self._connect()
        try:
            def one(sql, params=()):
                row = conn.execute(sql, params).fetchone()
                return row[0] if row else 0

            link_rows = conn.execute(
                "SELECT status, COUNT(*) AS c FROM identity_link GROUP BY status"
            ).fetchall()
            links = {str(r["status"]): int(r["c"]) for r in link_rows}
            return {
                "members": int(one("SELECT COUNT(*) FROM member")),
                "distinct_users": int(one("SELECT COUNT(DISTINCT user_id) FROM member")),
                "groups": int(one("SELECT COUNT(DISTINCT platform_id || ':' || group_id) FROM member")),
                "official_members": int(one("SELECT COUNT(*) FROM member WHERE channel='official'")),
                "onebot_members": int(one("SELECT COUNT(*) FROM member WHERE channel='onebot'")),
                "history": int(one("SELECT COUNT(*) FROM nickname_history")),
                "links": links,
                "confirmed": int(links.get("confirmed", 0)),
                "candidate": int(links.get("candidate", 0)),
                "rejected": int(links.get("rejected", 0)),
            }
        finally:
            conn.close()

    async def purge(
        self,
        scope: str,
        *,
        platform_id: str = "",
        group_id: str = "",
        user_id: str = "",
    ) -> Dict[str, int]:
        async with self._write_lock:
            return await asyncio.to_thread(
                self._purge_sync, scope, platform_id, group_id, user_id
            )

    def _purge_sync(self, scope, platform_id, group_id, user_id) -> Dict[str, int]:
        scope = str(scope or "")
        pid = str(platform_id or "")
        gid = str(group_id or "")
        uid = str(user_id or "")
        conditions = []
        params: List[Any] = []
        if pid:
            conditions.append("platform_id = ?")
            params.append(pid)
        if gid:
            conditions.append("group_id = ?")
            params.append(gid)
        if uid:
            conditions.append("user_id = ?")
            params.append(uid)
        conn = self._connect()
        counts = {"members": 0, "history": 0, "links": 0, "sync": 0}
        try:
            with conn:
                if scope == "all" and not conditions:
                    counts["members"] = int(conn.execute("DELETE FROM member").rowcount or 0)
                    counts["history"] = int(conn.execute("DELETE FROM nickname_history").rowcount or 0)
                    counts["links"] = int(conn.execute("DELETE FROM identity_link").rowcount or 0)
                    counts["sync"] = int(conn.execute("DELETE FROM group_sync").rowcount or 0)
                    return counts
                where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
                counts["members"] = int(
                    conn.execute("DELETE FROM member" + where, tuple(params)).rowcount or 0
                )
                counts["history"] = int(
                    conn.execute("DELETE FROM nickname_history" + where, tuple(params)).rowcount or 0
                )
                # 关联是 (platform_id, openid) 维度的，不随“某个群”删除；
                # 只在删成员（member）或整个平台（platform）时清理。
                if scope == "member" and pid and uid:
                    counts["links"] = int(conn.execute(
                        "DELETE FROM identity_link WHERE platform_id=? AND openid=?",
                        (pid, uid),
                    ).rowcount or 0)
                elif scope == "platform" and pid:
                    counts["links"] = int(conn.execute(
                        "DELETE FROM identity_link WHERE platform_id=?", (pid,)
                    ).rowcount or 0)
                if pid and gid:
                    counts["sync"] = int(conn.execute(
                        "DELETE FROM group_sync WHERE platform_id=? AND group_id=?",
                        (pid, gid),
                    ).rowcount or 0)
            return counts
        finally:
            conn.close()

    # ---------------- 群映射（官方群 ↔ OneBot 群） ----------------
    async def upsert_group_pair(
        self,
        official_platform_id: str,
        official_group_id: str,
        onebot_platform_id: str,
        onebot_group_id: str,
    ) -> None:
        async with self._write_lock:
            await asyncio.to_thread(
                self._upsert_group_pair_sync,
                official_platform_id,
                official_group_id,
                onebot_platform_id,
                onebot_group_id,
            )

    def _upsert_group_pair_sync(self, o_pid, o_gid, n_pid, n_gid) -> None:
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO group_pair(official_platform_id, official_group_id,
                                           onebot_platform_id, onebot_group_id, created_at)
                    VALUES (?,?,?,?,?)
                    ON CONFLICT(official_platform_id, official_group_id,
                                onebot_platform_id, onebot_group_id)
                    DO NOTHING
                    """,
                    (str(o_pid), str(o_gid), str(n_pid), str(n_gid), time.time()),
                )
        finally:
            conn.close()

    async def list_group_pairs(self) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self._list_group_pairs_sync)

    def _list_group_pairs_sync(self) -> List[Dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM group_pair ORDER BY created_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    async def delete_group_pair(self, seq: int) -> bool:
        async with self._write_lock:
            return await asyncio.to_thread(self._delete_group_pair_sync, int(seq))

    def _delete_group_pair_sync(self, seq: int) -> bool:
        conn = self._connect()
        try:
            with conn:
                cur = conn.execute("DELETE FROM group_pair WHERE seq=?", (seq,))
                return bool(cur.rowcount)
        finally:
            conn.close()

    async def iter_group_members(
        self, platform_id: str, group_id: str
    ) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(
            self._iter_group_members_sync, str(platform_id), str(group_id)
        )

    def _iter_group_members_sync(self, platform_id: str, group_id: str) -> List[Dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM member WHERE platform_id=? AND group_id=?"
                " ORDER BY last_seen DESC",
                (platform_id, group_id),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    async def history_counts(self) -> Dict[Tuple[str, str, str], int]:
        return await asyncio.to_thread(self._history_counts_sync)

    def _history_counts_sync(self):
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT platform_id, group_id, user_id, COUNT(*) AS c"
                " FROM nickname_history GROUP BY platform_id, group_id, user_id"
            ).fetchall()
            return {
                (str(r["platform_id"]), str(r["group_id"]), str(r["user_id"])): int(r["c"])
                for r in rows
            }
        finally:
            conn.close()

    async def append_history_dedup(self, rows: Sequence[Dict[str, Any]]) -> int:
        if not rows:
            return 0
        async with self._write_lock:
            return await asyncio.to_thread(self._append_history_dedup_sync, list(rows))

    def _append_history_dedup_sync(self, rows: List[Dict[str, Any]]) -> int:
        sql = """
        INSERT INTO nickname_history(platform_id, group_id, user_id, nickname, card,
                                     changed_at, source)
        SELECT ?,?,?,?,?,?,?
        WHERE NOT EXISTS (
          SELECT 1 FROM nickname_history
          WHERE platform_id=? AND group_id=? AND user_id=? AND changed_at=?
        )
        """
        payload = []
        for r in rows:
            pid = str(r.get("platform_id") or "")
            gid = str(r.get("group_id") or "")
            uid = str(r.get("user_id") or "")
            changed = float(r.get("changed_at") or 0)
            payload.append((
                pid, gid, uid,
                str(r.get("nickname") or ""),
                str(r.get("card") or ""),
                changed,
                str(r.get("source") or "observed"),
                pid, gid, uid, changed,
            ))
        conn = self._connect()
        try:
            with conn:
                cur = conn.executemany(sql, payload)
                return int(cur.rowcount or 0)
        finally:
            conn.close()

    async def analysis_duplicate_names(self, limit: int = 50) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self._analysis_duplicate_names_sync, int(limit))

    def _analysis_duplicate_names_sync(self, limit: int) -> List[Dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT nickname,
                       COUNT(DISTINCT user_id) AS c,
                       COUNT(DISTINCT user_id) AS count,
                       COUNT(DISTINCT group_id) AS groups,
                       GROUP_CONCAT(DISTINCT user_id) AS users
                FROM member WHERE nickname <> ''
                GROUP BY nickname HAVING COUNT(DISTINCT user_id) > 1
                ORDER BY c DESC, nickname LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    async def analysis_rename_rank(self, limit: int = 50) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self._analysis_rename_rank_sync, int(limit))

    def _analysis_rename_rank_sync(self, limit: int) -> List[Dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT user_id, COUNT(*) AS changes, MAX(changed_at) AS last_change
                FROM nickname_history GROUP BY user_id
                ORDER BY changes DESC, last_change DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ---------------- 群名缓存 ----------------
    async def upsert_group_name(
        self, platform_id: str, group_id: str, name: str, source: str = ""
    ) -> None:
        text = str(name or "").strip()
        if not text:
            return
        async with self._write_lock:
            await asyncio.to_thread(
                self._upsert_group_name_sync, str(platform_id), str(group_id), text, str(source)
            )

    def _upsert_group_name_sync(self, platform_id: str, group_id: str, name: str, source: str) -> None:
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO group_meta(platform_id, group_id, name, source, updated_at)
                    VALUES (?,?,?,?,?)
                    ON CONFLICT(platform_id, group_id)
                    DO UPDATE SET name=excluded.name,
                                  source=excluded.source,
                                  updated_at=excluded.updated_at
                    """,
                    (platform_id, group_id, name, source, time.time()),
                )
        finally:
            conn.close()

    async def get_group_name(self, platform_id: str, group_id: str) -> str:
        return await asyncio.to_thread(
            self._get_group_name_sync, str(platform_id), str(group_id)
        )

    def _get_group_name_sync(self, platform_id: str, group_id: str) -> str:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT name FROM group_meta WHERE platform_id=? AND group_id=?",
                (platform_id, group_id),
            ).fetchone()
            return str(row["name"]) if row is not None else ""
        finally:
            conn.close()

    async def group_names(self) -> Dict[Tuple[str, str], str]:
        return await asyncio.to_thread(self._group_names_sync)

    def _group_names_sync(self) -> Dict[Tuple[str, str], str]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT platform_id, group_id, name FROM group_meta WHERE name <> ''"
            ).fetchall()
            return {
                (str(r["platform_id"]), str(r["group_id"])): str(r["name"]) for r in rows
            }
        finally:
            conn.close()

    async def groups_without_name(self) -> List[Tuple[str, str]]:
        return await asyncio.to_thread(self._groups_without_name_sync)

    def _groups_without_name_sync(self) -> List[Tuple[str, str]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT m.platform_id AS platform_id, m.group_id AS group_id
                FROM member m
                LEFT JOIN group_meta g
                       ON g.platform_id = m.platform_id AND g.group_id = m.group_id
                WHERE IFNULL(g.name, '') = ''
                GROUP BY m.platform_id, m.group_id
                ORDER BY MAX(m.last_seen) DESC
                """
            ).fetchall()
            return [(str(r["platform_id"]), str(r["group_id"])) for r in rows]
        finally:
            conn.close()
