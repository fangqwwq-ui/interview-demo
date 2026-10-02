"""SQLite 工单存储。

为什么用 SQLite 而不是内存 dict：
  演示要能展示「事件落库→ 工单生成 → 状态流转 → 统计看板」完整闭环，
  进程重启后数据仍在，且统计SQL 本身就是可讲的部分（不靠Python 循环聚合）。

设计要点：
  1. **幂等 upsert**。同一``event_id`` 重复上报只更新、不新增行——
     这是安防场景的硬需求（摄像头抖动会重复推同一事件）。
     幂等靠主键冲突 +显式区分「要更新的列」实现，不依赖 ``INSERT OR REPLACE``
     （后者会连带删除未列出的列，是个常见陷阱）。
  2. **transition 表独立**。事件当前状态在 events 表，历史流转在 transitions 表。
     两者放一张表会导致「查当前状态」要排序去重。
  3. **状态校验在写库前**做一次（状态机已校验过，这里防的是绕过 API 直连SQL 的情况）。
  4. 写库用 ``sqlite3`` 的参数化查询，``extra`` 字段存JSON 文本。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence

from demo_a_security.events import (
    ALLOWED_TRANSITIONS,
    EventState,
    SecurityEvent,
    Transition,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_id      TEXT PRIMARY KEY,
    event_type    TEXT NOT NULL,
    zone          TEXT NOT NULL,
    severity      INTEGER NOT NULL DEFAULT 1,
    state         TEXT NOT NULL,
    camera_id     TEXT DEFAULT '',
    snapshot_path TEXT,
    extra_json    TEXT DEFAULT '{}',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_zone   ON events(zone);
CREATE INDEX IF NOT EXISTS idx_events_type   ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_state  ON events(state);

CREATE TABLE IF NOT EXISTS transitions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id   TEXT NOT NULL,
    src        TEXT NOT NULL,
    dst        TEXT NOT NULL,
    actor      TEXT NOT NULL,
    note       TEXT DEFAULT '',
    at         TEXT NOT NULL,
    FOREIGN KEY (event_id) REFERENCES events(event_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_transitions_event ON transitions(event_id);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class TicketStorePool:
    """按线程分配的 TicketStore 连接池。

    为什么需要它（本项目实测踩过）：
      ``sqlite3.Connection`` 即使 ``check_same_thread=False`` 也**不支持多线程
      并发调用** —— 那个参数只解除「跨线程使用会抛异常」的检查，不保证并发安全。
      实测把单个 TicketStore 共享给 8 线程的线程池，在并发压测下稳定抛
      ``sqlite3.InterfaceError: bad parameter or other API misuse``（HTTP 500）。

      正确做法就是每线程一个连接。这也正是 v2 异步版能真正并发的关键：
      并发度高时，「每请求新建连接」的开销反而比复用更高（连接建销+页缓存冷启动），
      而线程池+每线程连接的组合才既并发又稳定。

    只读查询场景下每个连接都持有同一个文件，SQLite 的WAL/共享缓存保证可见性；
    写操作（transfer/create）仍然串行化在各自连接上，Demo 规模下不构成瓶颈。
    """

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self._local = threading.local()
        self._all: List[TicketStore] = []
        self._lock = threading.Lock()

    def get(self) -> TicketStore:
        st = getattr(self._local, "store", None)
        if st is None:
            st = TicketStore(self.db_path)
            self._local.store = st
            with self._lock:
                self._all.append(st)
        return st

    def close(self) -> None:
        with self._lock:
            for st in self._all:
                try:
                    st.close()
                except Exception:
                    pass
            self._all.clear()
        self._local = threading.local()

    def __enter__(self) -> "TicketStorePool":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class TicketStore:
    """工单存储。

    单连接 + ``check_same_thread=False``：Demo 规模下足够，且避免连接跨线程问题。
    若压测阶段需要更高并发，正确做法是每线程一个连接（连接池），
    而不是把单个连接塞给多线程 —— 那会让并发度失去意义（测出来的不是服务能力）。
    """

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "TicketStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    # ------------------------------------------------------------ 写入

    def upsert_event(self, ev: SecurityEvent) -> bool:
        """幂等写入事件。返回 True 表示新事件，False 表示已存在并被更新。"""
        ts = _now_iso()
        with self._tx() as c:
            cur = c.execute(
                "SELECT 1 FROM events WHERE event_id = ?", (ev.event_id,)
            )
            exists = cur.fetchone() is not None
            c.execute(
                """
                INSERT INTO events (event_id, event_type, zone, severity, state,
                                    camera_id, snapshot_path, extra_json,
                                    created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id) DO UPDATE SET
                    event_type    = excluded.event_type,
                    zone          = excluded.zone,
                    severity      = excluded.severity,
                    camera_id     = excluded.camera_id,
                    snapshot_path = excluded.snapshot_path,
                    extra_json    = excluded.extra_json,
                    updated_at    = excluded.updated_at
                """,
                (
                    ev.event_id,
                    ev.event_type,
                    ev.zone,
                    int(ev.severity),
                    ev.state.value,
                    ev.camera_id,
                    ev.snapshot_path,
                    json.dumps(ev.extra, ensure_ascii=False),
                    ts,
                    ts,
                ),
            )
            # 注意：state 不在 DO UPDATE 列表里。
            # 重复上报不应把已推进的状态打回detected —— 状态只由 transfer() 改。
        return not exists

    def transfer(
        self,
        event_id: str,
        dst: EventState,
        *,
        actor: str = "system",
        note: str = "",
    ) -> None:
        """推进事件状态并记录流转历史。非法转移抛IllegalTransition。"""
        row = self._conn.execute(
            "SELECT state FROM events WHERE event_id = ?", (event_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"事件不存在：{event_id}")

        src = EventState(row["state"])
        if dst not in ALLOWED_TRANSITIONS.get(src, frozenset()):
            from demo_a_security.events import IllegalTransition

            raise IllegalTransition(src, dst)

        ts = _now_iso()
        with self._tx() as c:
            c.execute(
                "UPDATE events SET state = ?, updated_at = ? WHERE event_id = ?",
                (dst.value, ts, event_id),
            )
            c.execute(
                """
                INSERT INTO transitions (event_id, src, dst, actor, note, at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (event_id, src.value, dst.value, actor, note, ts),
            )

    # ------------------------------------------------------------ 读取

    def get(self, event_id: str) -> Optional[SecurityEvent]:
        row = self._conn.execute(
            "SELECT * FROM events WHERE event_id = ?", (event_id,)
        ).fetchone()
        return self._row_to_event(row) if row else None

    def _row_to_event(self, row: sqlite3.Row) -> SecurityEvent:
        trs = self._conn.execute(
            "SELECT src, dst, actor, note, at FROM transitions "
            "WHERE event_id = ? ORDER BY id ASC",
            (row["event_id"],),
        ).fetchall()
        try:
            extra = json.loads(row["extra_json"] or "{}")
        except json.JSONDecodeError:
            extra = {"_raw": row["extra_json"]}
        return SecurityEvent(
            event_id=row["event_id"],
            event_type=row["event_type"],
            zone=row["zone"],
            severity=int(row["severity"]),
            state=EventState(row["state"]),
            camera_id=row["camera_id"] or "",
            snapshot_path=row["snapshot_path"],
            extra=extra,
            transitions=[
                Transition(
                    src=EventState(t["src"]),
                    dst=EventState(t["dst"]),
                    actor=t["actor"],
                    note=t["note"] or "",
                    at=t["at"],
                )
                for t in trs
            ],
        )

    def query(
        self,
        *,
        zone: Optional[str] = None,
        event_type: Optional[str] = None,
        states: Optional[Sequence[EventState]] = None,
        min_severity: Optional[int] = None,
        camera_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[SecurityEvent]:
        """按条件查询事件。

        SQL 用参数化拼条件，绝不把用户输入拼进 SQL 字符串。
        """
        where: List[str] = []
        params: List[Any] = []
        if zone:
            where.append("zone = ?")
            params.append(zone)
        if event_type:
            where.append("event_type = ?")
            params.append(event_type)
        if camera_id:
            where.append("camera_id = ?")
            params.append(camera_id)
        if states:
            where.append(f"state IN ({','.join('?' * len(states))})")
            params.extend(s.value for s in states)
        if min_severity is not None:
            where.append("severity >= ?")
            params.append(int(min_severity))
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        #排序：severity 降序（高危在前），再按 event_id 保证稳定分页
        rows = self._conn.execute(
            f"SELECT * FROM events {clause} "
            "ORDER BY severity DESC, event_id ASC LIMIT ?",
            (*params, int(limit)),
        ).fetchall()
        return [self._row_to_event(r) for r in rows]

    def transitions_of(self, event_id: str) -> List[Transition]:
        rows = self._conn.execute(
            "SELECT src, dst, actor, note, at FROM transitions "
            "WHERE event_id = ? ORDER BY id ASC",
            (event_id,),
        ).fetchall()
        return [
            Transition(
                src=EventState(r["src"]),
                dst=EventState(r["dst"]),
                actor=r["actor"],
                note=r["note"] or "",
                at=r["at"],
            )
            for r in rows
        ]

    # ------------------------------------------------------------ 统计

    def stats(self) -> Dict[str, Any]:
        """看板统计。

        全部用 SQL 聚合而非 Python 循环 —— 这是「指标可复现」的一部分：
        同样的库同样的查询必然得到同样的数字，不依赖内存里的中间状态。
        """
        c = self._conn
        by_state = {
            r["state"]: r["n"]
            for r in c.execute(
                "SELECT state, COUNT(*) AS n FROM events GROUP BY state"
            ).fetchall()
        }
        by_type = {
            r["event_type"]: r["n"]
            for r in c.execute(
                "SELECT event_type, COUNT(*) AS n FROM events "
                "GROUP BY event_type ORDER BY n DESC"
            ).fetchall()
        }
        by_zone = {
            r["zone"]: r["n"]
            for r in c.execute(
                "SELECT zone, COUNT(*) AS n FROM events GROUP BY zone "
                "ORDER BY n DESC, zone ASC"
            ).fetchall()
        }
        total = sum(by_state.values())
        # 未闭环 = 非终态。未闭环率是安防运维里最常被问的一个数。
        closed = sum(
            n for s, n in by_state.items()
            if s in (EventState.CLOSED.value, EventState.FALSE_POSITIVE.value)
        )
        transitions = c.execute("SELECT COUNT(*) AS n FROM transitions").fetchone()["n"]
        return {
            "total_events": total,
            "by_state": by_state,
            "by_type": by_type,
            "by_zone": by_zone,
            "transitions_recorded": transitions,
            "open_events": total - closed,
            "open_rate": round((total - closed) / total, 4) if total else 0.0,
        }

    def export_jsonl(self, path: str | Path) -> Path:
        """导出全部事件（含流转历史）为jsonl，作为可审计凭据。"""
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        rows = self._conn.execute(
            "SELECT event_id FROM events ORDER BY event_id"
        ).fetchall()
        with out.open("w", encoding="utf-8") as f:
            for r in rows:
                ev = self.get(r["event_id"])
                if ev is not None:
                    f.write(json.dumps(ev.as_dict(), ensure_ascii=False) + "\n")
        return out