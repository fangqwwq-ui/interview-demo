"""生成 Demo A 的演示工单数据（确定性，无随机）。

为什么要seed 而不是等摄像头：
  1. 演示现场不可能稳定复现「刚好有陌生人闯入东门」；
  2. **数据决定上限**。若所有工单文本都长得一样，检索准确率无法反映真实能力，
     评测集也无从构造。所以这里刻意让 zone / type / state 三维都有区分度，
     并且文本里带上可被提问定位的具体事实（如摄像头号、危险等级）。
  3. 全部确定性 —— 同一份脚本每次生成完全相同的库，评测才可复现。

运行：python -m demo_a_security.seed --out outputs/demo_a/tickets.sqlite3
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from demo_a_security.events import EventState, SecurityEvent
from demo_a_security.store import TicketStore

# (event_id, event_type, zone, camera_id, severity, 说明)
SPEC: list[tuple[str, str, str, str, int, str]] = [
    # 东门 gate-01：陌生人闯入，典型高危闭环
    ("A-0001", "intrusion", "gate-01", "cam-01", 5, "东门夜间非工作人员翻越围栏"),
    ("A-0002", "intrusion", "gate-01", "cam-01", 4, "东门尾随进入未登记"),
    ("A-0003", "person", "gate-01", "cam-02", 2, "东门快递员正常配送"),
    # 西门 gate-02：车辆
    ("A-0004", "vehicle", "gate-02", "cam-03", 3, "西门夜间货车滞留"),
    ("A-0005", "vehicle", "gate-02", "cam-03", 1, "西门白班车辆正常通行"),
    # 厂区 yard-02：烟火类高危
    ("A-0006", "fire", "yard-02", "cam-05", 5, "厂区仓库烟雾告警"),
    ("A-0007", "smoke", "yard-02", "cam-05", 4, "厂区锅炉房疑似冒烟"),
    ("A-0008", "vehicle", "yard-02", "cam-06", 2, "厂区叉车作业"),
    # 办公楼 office-03：人脸白名单识别
    ("A-0009", "person", "office-03", "cam-08", 2, "办公楼员工正常刷脸"),
    ("A-0010", "person", "office-03", "cam-08", 4, "办公楼非白名单人员进入"),
    ("A-0011", "intrusion", "office-03", "cam-09", 3, "办公楼夜间窗户开启"),
    # 仓库 depot-04
    ("A-0012", "intrusion", "depot-04", "cam-11", 5, "仓库门禁被暴力尝试"),
    ("A-0013", "vehicle", "depot-04", "cam-12", 3, "仓库区车辆逆行"),
    ("A-0014", "person", "depot-04", "cam-11", 2, "仓库管理员巡检"),
]

# 终态事件：id -> (终态, 说明)。刻意让东门的高危事件全部闭环，
# 而 office-03 的非白名单人员事件保持未闭环 —— 这样「未闭环的高危事件有哪些」
# 才是一个有区分度、且答案不平凡的查询。
TERMINALS: dict[str, tuple[EventState, str]] = {
    "A-0001": (EventState.CLOSED, "保安现场拦截并移交园区管理处"),
    "A-0002": (EventState.CLOSED, "核对为访客，已补登记"),
    "A-0003": (EventState.FALSE_POSITIVE, "快递员在白名单内，误报"),
    "A-0004": (EventState.RESOLVED, "货车已驶离并登记"),
    "A-0005": (EventState.CLOSED, "正常通行"),
    "A-0006": (EventState.CLOSED, "确认为焊接烟尘，已通风"),
    "A-0007": (EventState.FALSE_POSITIVE, "蒸汽管道水汽，误报"),
    "A-0009": (EventState.CLOSED, "员工正常出勤"),
}


def seed(db_path: Path, *, reset: bool = True) -> TicketStore:
    """写入演示工单并推进到终态。

    reset=True 时先删除旧库。**删除失败必须降级为「就地重建」而不是崩溃** ——
    实测踩过：Windows 上若目标文件正被服务进程占用（或被沙箱的删除守卫拦截），
    ``Path.unlink()`` 会抛 OSError，早期版本直接崩掉，导致「重跑 seed」这个
    最基本的操作在服务运行期间完全不可用。
    现在改为：删不掉就保留旧文件，用同一个库继续 upsert（写入是幂等的，
    结果与重建等价；代价是旧库若有多余事件会残留，此时用 --keep 可显式跳过）。
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if reset and db_path.exists():
        try:
            db_path.unlink()
        except OSError as exc:
            print(
                f"[seed] 无法删除旧库（{type(exc).__name__}），改为就地重建。"
                "写入是幂等的，结果等价。",
                file=sys.stderr,
            )

    store = TicketStore(db_path)
    # 就地重建时清掉历史流转，避免多次运行叠加出重复 transitions
    if reset:
        with store._tx() as c:  # noqa: SLF001 - 同模块内维护，清洗演示数据
            c.execute("DELETE FROM transitions")
            c.execute("DELETE FROM events")

    for eid, etype, zone, cam, sev, desc in SPEC:
        ev = SecurityEvent(
            event_id=eid,
            event_type=etype,
            zone=zone,
            severity=sev,
            camera_id=cam,
            extra={"desc": desc},
        )
        store.upsert_event(ev)

    # 按detected -> acknowledged -> resolved/closed 顺序推进，
    # 直接走transfer() 以便 transitions 表里留下完整历史。
    for eid, (final, note) in TERMINALS.items():
        store.transfer(eid, EventState.ACKNOWLEDGED, actor="guard-01", note="已确认")
        if final is EventState.FALSE_POSITIVE:
            store.transfer(eid, final, actor="guard-01", note=note)
        elif final is EventState.RESOLVED:
            store.transfer(eid, final, actor="guard-02", note=note)
        else:
            store.transfer(eid, EventState.RESOLVED, actor="guard-02", note="已处置")
            store.transfer(eid, final, actor="supervisor-01", note=note)

    return store


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成 Demo A 演示工单")
    ap.add_argument(
        "--out",
        default="outputs/demo_a/tickets.sqlite3",
        help="SQLite 路径（默认 outputs/demo_a/tickets.sqlite3）",
    )
    args = ap.parse_args(argv)

    db_path = Path(args.out)
    store = seed(db_path)
    s = store.stats()
    print(f"工单库已生成：{db_path}")
    print(f"  事件总数：{s['total_events']}")
    print(f"  按状态：{s['by_state']}")
    print(f"  按区域：{s['by_zone']}")
    print(f"  按类型：{s['by_type']}")
    print(f"  未闭环：{s['open_events']}（未闭环率 {s['open_rate']}）")
    print(f"  流转记录：{s['transitions_recorded']} 条")
    store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())