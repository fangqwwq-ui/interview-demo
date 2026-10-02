"""Demo A 事件状态机 + 工单存储自检。

运行：python -m demo_a_security.selfcheck

定位：这是**回归护栏**，不是演示脚本。每次改动 events.py / store.py 后必须跑通，
否则状态机与工单层的行为无凭据。断言失败直接抛 AssertionError（非零退出）。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from demo_a_security.events import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATES,
    EventState,
    IllegalTransition,
    SecurityEvent,
    can_transition,
    next_states,
)
from demo_a_security.store import TicketStore

_checks = 0


def check(cond: bool, msg: str) -> None:
    global _checks
    _checks += 1
    if not cond:
        raise AssertionError(f"[FAIL] {msg}")
    print(f"  ok  {msg}")


def ev(eid: str = "E001", zone: str = "gate-01", sev: int = 3) -> SecurityEvent:
    return SecurityEvent(
        event_id=eid,
        event_type="person",
        zone=zone,
        severity=sev,
        camera_id="cam-07",
        extra={"track_id": 42},
    )


# ------------------------------------------------------------------ 状态机
def test_state_machine() -> None:
    print("\n[1] 状态机")
    check(
        can_transition(EventState.DETECTED, EventState.ACKNOWLEDGED),
        "detected -> acknowledged 合法",
    )
    check(
        not can_transition(EventState.DETECTED, EventState.RESOLVED),
        "detected -> resolved 必须被拒绝（跳步）",
    )
    check(
        not can_transition(EventState.CLOSED, EventState.RESOLVED),
        "终态 closed 不可再流出",
    )
    check(
        not can_transition(EventState.FALSE_POSITIVE, EventState.CLOSED),
        "终态 false_positive 不可再流出",
    )

    e = ev()
    e.apply(EventState.ACKNOWLEDGED, actor="guard-1")
    e.apply(EventState.RESOLVED, actor="guard-1", note="现场确认无异常")
    e.apply(EventState.CLOSED, actor="supervisor")
    check(e.is_terminal, "closed 为终态")
    check(
        e.path() == ["detected", "acknowledged", "resolved", "closed"],
        f"状态路径可回放：{e.path()}",
    )

    # 终态再流转必须抛异常
    try:
        e.apply(EventState.RESOLVED, actor="x")
        raise AssertionError("[FAIL] 终态流转本应被拒绝")
    except IllegalTransition as exc:
        check("非法状态转移" in str(exc), f"终态流转被拒且错误信息可读：{exc}")

    # 误报是独立终态
    e2 = ev("E002")
    e2.apply(EventState.FALSE_POSITIVE, actor="guard-2", note="树影误检")
    check(
        e2.state is EventState.FALSE_POSITIVE and e2.is_terminal,
        "false_positive 为独立终态（可与resolved 分别统计）",
    )

    check(
        all(not ALLOWED_TRANSITIONS[s] for s in TERMINAL_STATES),
        "转移表中终态确实无出边",
    )
    check(
        next_states(EventState.DETECTED) == [EventState.ACKNOWLEDGED, EventState.FALSE_POSITIVE],
        "next_states 输出稳定可测",
    )


# ------------------------------------------------------------------ 存储
def test_store(tmp: Path) -> None:
    print("\n[2] SQLite 工单存储")
    db = tmp / "a_check.sqlite3"
    with TicketStore(db) as st:
        is_new = st.upsert_event(ev())
        check(is_new, "首次写入标记为新事件")

        again = st.upsert_event(ev())
        check(not again, "重复上报不再新增（幂等）")
        check(st.stats()["total_events"] == 1, "库中仍只有 1 条（无重复行）")

        for i in range(2, 6):
            st.upsert_event(ev(f"E{i:03d}", zone="yard-02", sev=i))

        st.transfer("E001", EventState.ACKNOWLEDGED, actor="guard-1")
        st.transfer("E001", EventState.RESOLVED, actor="guard-1", note="已处置")
        st.transfer("E001", EventState.CLOSED, actor="supervisor")
        st.transfer("E003", EventState.FALSE_POSITIVE, actor="guard-2")

        # 重复上报不得打回状态机状态
        st.upsert_event(ev())
        got = st.get("E001")
        check(
            got is not None and got.state is EventState.CLOSED,
            f"重复上报不重置状态（仍为 {got.state if got else 'None'}）",
        )

        # 非法流转在存储层同样被拦
        try:
            st.transfer("E001", EventState.RESOLVED, actor="bad")
            raise AssertionError("[FAIL] 存储层本应拒绝非法流转")
        except IllegalTransition:
            check(True, "存储层独立拦截非法流转（不依赖上层自觉）")

        # 不存在的事件
        try:
            st.transfer("NOPE", EventState.ACKNOWLEDGED)
            raise AssertionError("[FAIL] 本应报KeyError")
        except KeyError:
            check(True, "对不存在事件流转抛 KeyError")

        trs = st.transitions_of("E001")
        check(len(trs) == 3, f"E001 记录 3 次流转，实际 {len(trs)}")
        check(trs[0].actor == "guard-1", "流转操作者被记录")
        check(trs[1].note == "已处置", "流转备注被记录")

        # 查询过滤
        check(len(st.query(zone="yard-02")) == 4, "按 zone 过滤")
        check(len(st.query(min_severity=4)) == 2, "按 severity 过滤")
        check(
            len(st.query(states=[EventState.CLOSED, EventState.FALSE_POSITIVE])) == 2,
            "按多状态过滤（IN 查询）",
        )
        # SQL 注入尝试必须被参数化挡住
        nasty = st.query(zone="yard-02' OR '1'='1")
        check(nasty == [], f"注入式入参被参数化挡住（返回 {len(nasty)} 条）")

        # 统计
        s = st.stats()
        check(s["total_events"] == 5, f"统计总数 5，实际 {s['total_events']}")
        check(
            s["by_state"].get("closed") == 1
            and s["by_state"].get("false_positive") == 1,
            f"按状态聚合正确：{s['by_state']}",
        )
        check(s["open_events"] == 3, f"未闭环数= 3，实际 {s['open_events']}")
        check(abs(s["open_rate"] - 0.6) < 1e-9, f"未闭环率 0.6，实际 {s['open_rate']}")
        check(s["transitions_recorded"] == 4, "流转总数被统计")

        # 回放凭据
        out = st.export_jsonl(tmp / "events.jsonl")
        lines = out.read_text(encoding="utf-8").strip().splitlines()
        check(len(lines) == 5, f"导出 jsonl 5 行，实际 {len(lines)}")

    # 关闭后重开，数据仍在（证明落盘而非内存）
    with TicketStore(db) as st2:
        check(st2.stats()["total_events"] == 5, "重开数据库数据仍在（真落盘）")


def main() -> int:
    print("=" * 60)
    print("Demo A 事件状态机 + 工单存储 自检")
    print("=" * 60)
    test_state_machine()
    with tempfile.TemporaryDirectory() as td:
        test_store(Path(td))
    print("\n" + "=" * 60)
    print(f"全部通过：{_checks} 项断言")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())