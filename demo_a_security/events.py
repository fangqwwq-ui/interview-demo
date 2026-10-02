"""安防事件状态机。

为什么显式建模状态机，而不是在代码里散落 if判断：
  1. 演示时最容易被追问「重复告警怎么收敛」「同一事件重复上报会不会
     产生 N 条工单」—— 状态机是唯一能给出确定答案的结构；
  2. 非法转移必须在数据层被拦住，而不是靠调用方自觉；
  3. 状态流转本身是可比对的证据（transition 表落盘，可回放）。

状态定义（刻意保持最小闭环）：
    detected ──► acknowledged ──► resolved ──► closed
        │              │
        └──────────────┴──────► false_positive

设计约束：
  - ``TERMINAL`` 状态不允许再流出，防止「已关闭工单被重新打开」这类脏数据；
  - ``false_positive`` 是独立终态而非 ``resolved`` 的一个 flag，因为
    「误报」与「已处置」在安防业务里的处置成本完全不同，必须能分别统计；
  - 每次转移记录操作者与时间戳，写入 ``transitions`` 表用于回放。
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional


class EventState(str, enum.Enum):
    """事件状态。值即落库值，改动会破坏历史数据，禁止随意重命名。"""

    DETECTED = "detected"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
    CLOSED = "closed"
    FALSE_POSITIVE = "false_positive"

    def __str__(self) -> str:  # pragma: no cover - 仅用于日志可读性
        return self.value


TERMINAL_STATES: FrozenSet[EventState] = frozenset(
    {EventState.CLOSED, EventState.FALSE_POSITIVE}
)

# 允许的转移表。这是唯一的裁决点：不在表内的转移一律拒绝。
ALLOWED_TRANSITIONS: Dict[EventState, FrozenSet[EventState]] = {
    EventState.DETECTED: frozenset(
        {EventState.ACKNOWLEDGED, EventState.FALSE_POSITIVE}
    ),
    EventState.ACKNOWLEDGED: frozenset(
        {EventState.RESOLVED, EventState.FALSE_POSITIVE}
    ),
    EventState.RESOLVED: frozenset(
        {EventState.CLOSED, EventState.FALSE_POSITIVE}
    ),
    # 终态：不可流出（CLOSED 与 FALSE_POSITIVE 都是）
    EventState.CLOSED: frozenset(),
    EventState.FALSE_POSITIVE: frozenset(),
}


class IllegalTransition(RuntimeError):
    """非法状态转移。

    单独一个异常类型而不是复用 ValueError，这样调用方可以精确捕获
    「状态机拒绝」而不会误吞掉参数校验错误。
    """

    def __init__(self, src: EventState, dst: EventState):
        self.src = src
        self.dst = dst
        allowed = ", ".join(sorted(s.value for s in ALLOWED_TRANSITIONS[src])) or "(无)"
        super().__init__(
            f"非法状态转移：{src.value} -> {dst.value}；"
            f"{src.value} 允许流转到：{allowed}"
        )


def can_transition(src: EventState, dst: EventState) -> bool:
    """判断转移是否合法。不抛异常，供调用方先做校验。"""
    return dst in ALLOWED_TRANSITIONS.get(src, frozenset())


def assert_transition(src: EventState, dst: EventState) -> None:
    """断言转移合法，否则抛 IllegalTransition。"""
    if not can_transition(src, dst):
        raise IllegalTransition(src, dst)


def next_states(src: EventState) -> List[EventState]:
    """列出允许的下一状态（排序后返回，保证输出稳定可测）。"""
    return sorted(ALLOWED_TRANSITIONS.get(src, frozenset()), key=lambda s: s.value)


@dataclass(frozen=True)
class Transition:
    """一次状态流转的完整记录，落库到 transitions 表。"""

    src: EventState
    dst: EventState
    actor: str
    note: str = ""
    at: Optional[str] = None  # ISO8601；由store 层在写入时补齐

    def as_dict(self) -> dict:
        return {
            "src": self.src.value,
            "dst": self.dst.value,
            "actor": self.actor,
            "note": self.note,
            "at": self.at,
        }


@dataclass
class SecurityEvent:
    """一条安防事件（= 一张工单的业务载荷）。

    字段口径：
      event_id     事件唯一 ID，同时作为工单号前缀，便于人工追溯
      event_type   person / vehicle / intrusion / fire / smoke / unknown
      zone         摄像头/区域标识（分层检索的第一层分区键）
      severity     1..5，5最高；由规则层给定，不由本模块推断
      snapshot_path 截图路径（可选，相对项目根目录）
    """

    event_id: str
    event_type: str
    zone: str
    severity: int = 1
    state: EventState = EventState.DETECTED
    camera_id: str = ""
    snapshot_path: Optional[str] = None
    extra: Dict[str, object] = field(default_factory=dict)
    transitions: List[Transition] = field(default_factory=list)

    def apply(
        self, dst: EventState, *, actor: str = "system", note: str = ""
    ) -> Transition:
        """执行一次状态转移。非法转移直接抛 IllegalTransition。"""
        assert_transition(self.state, dst)
        tr = Transition(src=self.state, dst=dst, actor=actor, note=note)
        self.state = dst
        self.transitions.append(tr)
        return tr

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    def path(self) -> List[str]:
        """完整状态路径，含初始状态，用于回放展示。"""
        if not self.transitions:
            return [self.state.value]
        return [self.transitions[0].src.value] + [t.dst.value for t in self.transitions]

    def as_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "zone": self.zone,
            "severity": self.severity,
            "state": self.state.value,
            "camera_id": self.camera_id,
            "snapshot_path": self.snapshot_path,
            "extra": self.extra,
            "transitions": [t.as_dict() for t in self.transitions],
        }


# 供 UI / MCP 返回可选类型清单
KNOWN_EVENT_TYPES: FrozenSet[str] = frozenset(
    {"person", "vehicle", "intrusion", "fire", "smoke", "unknown"}
)

# 状态机完整性自检用：所有状态都能在转移表里找到，且终态确实无出边。
# 这个断言在 import 时执行，任何人误改转移表导致状态不可达都会立刻暴露。
assert set(ALLOWED_TRANSITIONS) == set(EventState), "转移表与状态集合不一致"
assert all(not ALLOWED_TRANSITIONS[s] for s in TERMINAL_STATES), "终态不得有出边"
# 可达性：从 DETECTED 出发必须能到所有状态
_reached = {EventState.DETECTED}
_frontier = [EventState.DETECTED]
while _frontier:
    cur = _frontier.pop()
    for nxt in ALLOWED_TRANSITIONS[cur]:
        if nxt not in _reached:
            _reached.add(nxt)
            _frontier.append(nxt)
assert _reached == set(EventState), f"存在不可达状态：{set(EventState) - _reached}"