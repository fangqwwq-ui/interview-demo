"""Demo A MCP Server —— 对外暴露三个工具。

验收要求（PLAN §7.4）：「MCP 三个工具可被外部客户端调用，调用日志可回放」。
因此这里的重点不是"能调通"，而是**每次调用都留下可回放的凭据**：
    - ``outputs/demo_a/mcp_calls.jsonl`` 逐行追加，每次调用一条；
    - 每条记录含工具名、参数、返回值摘要、耗时、成功与否；
    - ``replay()`` 能按顺序把日志里的调用重新作用于当前工单库，
      用于演示「这些结论是系统真实产出的，不是我现场编的」。

⚠️ SDK 版本说明（踩过的坑）：
   mcp 2.x 已把 ``FastMCP`` 更名为 ``MCPServer``（``mcp.server.mcpserver``），
   旧的 ``mcp.server.fastmcp`` 在 2.x 下直接抛 ModuleNotFoundError。
   本文件按 2.x API 编写。若降级到 mcp 1.x，import 路径要改回去。

运行：
    python -m demo_a_security.mcp_server            # stdio，供外部 MCP 客户端挂载
    python -m demo_a_security.mcp_server --list     # 只列出工具与参数 schema
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from demo_a_security.events import EventState, SecurityEvent
from demo_a_security.store import TicketStore

DEFAULT_DB = Path("outputs/demo_a/tickets.sqlite3")
DEFAULT_LOG = Path("outputs/demo_a/mcp_calls.jsonl")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class CallRecorder:
    """调用日志记录器。

    刻意用「追加写jsonl」而不是内存 list：
      - 进程重启后历史仍在；
      - 崩溃时已落盘的调用不会丢（排查真实故障时这点很关键）；
      - 可以直接交给第三方工具分析，不需要导出步骤。
    """

    def __init__(self, log_path: Path):
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def record(
        self,
        tool: str,
        arguments: Dict[str, Any],
        *,
        ok: bool,
        result_summary: Any,
        latency_ms: float,
        error: str = "",
    ) -> None:
        row = {
            "at": _now_iso(),
            "tool": tool,
            "arguments": arguments,
            "ok": ok,
            "result_summary": result_summary,
            "latency_ms": round(latency_ms, 3),
            "error": error,
        }
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def read(self) -> List[Dict[str, Any]]:
        if not self.log_path.exists():
            return []
        out = []
        for line in self.log_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                # 崩溃时最后一行可能只写了一半。跳过并显式标记，
                # 不让一行坏日志让整个回放失败。
                out.append({"_unparsed": line})
        return out


class SecurityMCPTools:
    """三个工具的实现。

    刻意把业务逻辑与 MCP 传输层分开：``SecurityMCPTools`` 不依赖 mcp SDK，
    可以被压测脚本、评测脚本直接调用。这样压测测的是业务路径，
    而不是「经过 MCP 协议编解码后的路径」—— 两者开销差一个数量级，
    混在一起测出来的数字没有可比性。
    """

    def __init__(
        self,
        db_path: Path = DEFAULT_DB,
        *,
        recorder: Optional[CallRecorder] = None,
    ) -> None:
        self.store = TicketStore(db_path)
        self.recorder = recorder

    # -- 工具 1：查询事件 ------------------------------------------------
    def query_events(
        self,
        event_type: Optional[str] = None,
        zone: Optional[str] = None,
        state: Optional[str] = None,
        min_severity: Optional[int] = None,
        open_only: bool = False,
        limit: int = 20,
    ) -> Dict[str, Any]:
        """按条件查询安防事件。

        state 传 "open" 等价于 open_only=true（未闭环 = 非终态）。
        """
        states: Optional[List[EventState]] = None
        if state and state.lower() == "open":
            from demo_a_security.events import TERMINAL_STATES

            states = [s for s in EventState if s not in TERMINAL_STATES]
        elif state:
            try:
                states = [EventState(state.lower())]
            except ValueError as exc:
                raise ValueError(
                    f"未知状态：{state}；合法值："
                    f"{[s.value for s in EventState]}或 open"
                ) from exc

        events = self.store.query(
            zone=zone,
            event_type=event_type,
            states=states,
            min_severity=min_severity,
            limit=limit,
        )
        return {
            "count": len(events),
            "events": [
                {
                    "event_id": e.event_id,
                    "type": e.event_type,
                    "zone": e.zone,
                    "severity": e.severity,
                    "state": e.state.value,
                    "desc": str(e.extra.get("desc", "")),
                    "path": e.path(),
                }
                for e in events
            ],
        }

    # -- 工具 2：创建工单 ------------------------------------------------
    def create_ticket(
        self,
        title: str,
        zone: str,
        event_type: str = "person",
        severity: int = 1,
        camera_id: str = "",
        desc: str = "",
    ) -> Dict[str, Any]:
        """创建一张新工单，返回 event_id 与初始状态。

        event_id 自动生成规则：``T-<时间戳36进制>-<4位随机>``。
        用时间戳而非自增整数，是为了让多进程写入时不需要协调主键。
        """
        import secrets

        ts = format(int(time.time()), "x")
        event_id = f"T-{ts}-{secrets.token_hex(2)}"
        ev = SecurityEvent(
            event_id=event_id,
            event_type=event_type,
            zone=zone,
            severity=int(severity),
            camera_id=camera_id,
            extra={"desc": desc, "title": title},
        )
        self.store.upsert_event(ev)
        return {
            "event_id": event_id,
            "state": ev.state.value,
            "title": title,
            "zone": zone,
            "message": "工单已创建，初始状态 detected（未确认）",
        }

    # -- 工具 3：统计 ----------------------------------------------------
    def stats(self) -> Dict[str, Any]:
        """看板统计：总量、按状态/类型/区域分布、未闭环率。"""
        s = self.store.stats()
        s["db_path"] = str(self.store.db_path)
        return s


def build_server(tools: SecurityMCPTools, recorder: CallRecorder):
    """用 mcp 2.x API 组装 Server。

    单独抽出来是为了让 ``--list`` 能在不起stdin/stdout 传输的情况下工作。
    """
    from mcp.server.mcpserver import MCPServer

    server = MCPServer(
        name="demo-a-security",
        version="0.1.0",
        instructions=(
            "安防事件查询与工单系统。三个工具："
            "query_events（按类型/区域/状态/危险等级查事件）、"
            "create_ticket（创建工单）、"
            "stats（看板统计）。所有查询均只读工单库，不触发外部网络请求。"
        ),
    )

    def _wrap(fn: Callable[..., Dict[str, Any]]) -> Callable[..., Dict[str, Any]]:
        """统一包一层计时 + 日志。

        ⚠️ 必须用 ``functools.wraps`` 保留原函数签名。
        mcp SDK 是**从函数签名反推 JSON Schema** 的：如果包装函数写成
        ``def inner(*a, **kw)``，SDK 只能看到 ``a`` 和 ``kw`` 两个形参，
        于是所有工具的入参 schema 都退化成 ``{"a":..., "kw":...}``，
        真实参数（zone/state/limit…）全部从schema 里消失 ——
        服务能跑，但外部客户端按 schema 传参会直接失败，且不报错在工具层。

        注意：这里捕获异常后**仍然抛出**（只记录不吞掉）。
        MCP 客户端必须看到真实错误，否则工具失败会被误认为"成功但无结果"。
        """
        import functools

        @functools.wraps(fn)
        def inner(*a: Any, **kw: Any) -> Dict[str, Any]:
            t0 = time.perf_counter()
            name = fn.__name__
            try:
                out = fn(*a, **kw)
            except Exception as exc:
                recorder.record(
                    name,
                    {**kw},
                    ok=False,
                    result_summary=None,
                    latency_ms=(time.perf_counter() - t0) * 1000,
                    error=f"{type(exc).__name__}: {exc}",
                )
                raise
            latency = (time.perf_counter() - t0) * 1000
            # 结果摘要只留标量字段，避免把完整事件列表写进日志膨胀。
            summary = (
                {k: v for k, v in out.items() if isinstance(v, (int, float, str))}
                if isinstance(out, dict)
                else str(out)[:200]
            )
            recorder.record(name, {**kw}, ok=True, result_summary=summary, latency_ms=latency)
            return out

        return inner

    server.add_tool(_wrap(tools.query_events), name="query_events",
                    description="按类型/区域/状态/最低危险等级查询安防事件")
    server.add_tool(_wrap(tools.create_ticket), name="create_ticket",
                    description="创建一张安防工单，返回 event_id 与初始状态")
    server.add_tool(_wrap(tools.stats), name="stats",
                    description="看板统计：事件总量、按状态/类型/区域分布、未闭环率")
    return server


# ------------------------------------------------------------------ 回放
def replay(log_path: Path, tools: SecurityMCPTools) -> Dict[str, Any]:
    """按日志顺序重放调用，验证历史调用在当前库上是否仍可复现。

    回放会**重新执行** query_events / stats 这类只读工具；
    create_ticket 不重放（会污染库）。这一点必须在报告里说明，
    否则「回放通过」会被误解为"日志是完整可重放的写入记录"。

    ⚠️ 失败调用必须与「结果不一致」分开计数。
    一次非法入参被拒绝，重放时**再次被拒绝**才是正确结果 ——
    若把它记成 mismatch，日志里每条失败记录都会让回放永久"不一致"，
    这个指标就废了（实测踩过：日志里有 2 条预期失败，回放直接consistent=false）。
    """
    recorder = CallRecorder(log_path)
    rows = recorder.read()
    replayed = skipped = 0
    expected_failures = 0  # 重放时同样被拒 —— 行为一致，算通过
    mismatched: List[Dict[str, Any]] = []

    for row in rows:
        if "_unparsed" in row:
            skipped += 1
            continue
        tool = row.get("tool")
        args = row.get("arguments", {})
        if tool not in {"query_events", "stats"}:
            skipped += 1
            continue

        recorded_ok = bool(row.get("ok"))
        try:
            out = getattr(tools, tool)(**args)
        except Exception as exc:
            if not recorded_ok:
                # 当初失败、现在也失败 —— 行为可复现
                expected_failures += 1
            else:
                mismatched.append(
                    {
                        "tool": tool,
                        "args": args,
                        "kind": "unexpected_error",
                        "recorded": "success",
                        "replayed": f"{type(exc).__name__}: {exc}",
                    }
                )
            continue

        replayed += 1
        if not recorded_ok:
            # 当初失败现在却成功了 —— 同样是行为漂移，必须报出来
            mismatched.append(
                {
                    "tool": tool,
                    "args": args,
                    "kind": "unexpected_success",
                    "recorded": "error",
                    "replayed": "success",
                }
            )
            continue

        # 只核对计数类标量；列表内容随库演进而变，不做全量比对
        old = row.get("result_summary") or {}
        for key in ("count", "total_events", "open_events"):
            if isinstance(old.get(key), int) and out.get(key) != old[key]:
                mismatched.append(
                    {
                        "tool": tool,
                        "args": args,
                        "kind": "value_drift",
                        "key": key,
                        "recorded": old[key],
                        "replayed": out.get(key),
                    }
                )

    return {
        "log_entries": len(rows),
        "replayed_ok": replayed,
        "expected_failures_reproduced": expected_failures,
        "skipped": skipped,
        "mismatched": mismatched,
        "consistent": not mismatched,
        "note": (
            "create_ticket 不参与回放（会写入库）；仅核对 count 类标量；"
            "「预期失败且同样失败」记为行为可复现，不算不一致"
        ),
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Demo A MCP Server")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--log", default=str(DEFAULT_LOG))
    ap.add_argument("--list", action="store_true", help="只列出工具，不启动服务")
    ap.add_argument("--replay", action="store_true", help="回放调用日志")
    ap.add_argument("--transport", default="stdio", choices=["stdio", "sse", "streamable-http"])
    args = ap.parse_args(argv)

    db_path = Path(args.db)
    recorder = CallRecorder(Path(args.log))

    if not db_path.exists():
        print(
            f"工单库不存在：{db_path}\n"
            f"请先运行：python -m demo_a_security.seed --out {db_path}",
            file=sys.stderr,
        )
        return 2

    tools = SecurityMCPTools(db_path, recorder=recorder)

    if args.replay:
        out = replay(Path(args.log), tools)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if out["consistent"] else 1

    server = build_server(tools, recorder)

    if args.list:
        # 不启动传输层，直接打印注册好的工具与入参 schema
        import asyncio

        tools_meta = asyncio.run(server.list_tools())
        for t in tools_meta:
            print(f"- {t.name}: {t.description or ''}")
            print(f"    入参: {json.dumps(t.input_schema, ensure_ascii=False)}")
        print(f"\n共 {len(tools_meta)} 个工具；调用日志 -> {recorder.log_path}")
        return 0

    print(f"Demo A MCP Server 启动（transport={args.transport}），按 Ctrl+C 退出。", file=sys.stderr)
    server.run(args.transport)
    return 0


if __name__ == "__main__":
    sys.exit(main())