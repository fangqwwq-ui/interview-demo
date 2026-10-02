"""MCP 客户端端到端验证：用真实 MCP 协议调三个工具。

为什么必须走真实协议（而不是直接调函数）：
  协议层才有编解码、schema 校验与错误传播。直接调函数测不出
  「schema 丢失」「错误被吞」这类只在协议层暴露的问题 ——
  本项目就曾因此踩到包装函数签名被抹掉、三个工具 schema 全部退化成
  ``{"a":..., "kw":...}`` 的坑。

运行：
    python scripts/verify_mcp_client.py            # 全新日志
    python scripts/verify_mcp_client.py --keep-log  # 追加到已有日志后再回放

输出：outputs/demo_a/mcp_verify.json（含每步结果与回放一致性）
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

PY_EXE = ROOT / ".venv" / "Scripts" / "python.exe"
LOG_PATH = ROOT / "outputs" / "demo_a" / "mcp_calls.jsonl"
OUT_PATH = ROOT / "outputs" / "demo_a" / "mcp_verify.json"
DB_PATH = ROOT / "outputs" / "demo_a" / "tickets.sqlite3"

_steps: list[dict] = []


def step(name: str, ok: bool, detail: str = "") -> None:
    _steps.append({"step": name, "ok": ok, "detail": detail})
    print(f"  [{'ok  ' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


async def run(keep_log: bool) -> int:
    if not keep_log and LOG_PATH.exists():
        LOG_PATH.unlink()

    params = StdioServerParameters(
        command=str(PY_EXE),
        args=["-m", "demo_a_security.mcp_server", "--db", str(DB_PATH),
              "--log", str(LOG_PATH)],
        cwd=str(ROOT),
    )

    print("== MCP 客户端端到端验证 ==")
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()

            tools = await s.list_tools()
            names = sorted(t.name for t in tools.tools)
            step("工具注册", names == ["create_ticket", "query_events", "stats"],
                 f"{len(tools.tools)} 个: {names}")

            # schema 必须含真实参数名。退化�� {"a","kw"} 即为BUG。
            by_name = {t.name: t for t in tools.tools}
            qprops = set(by_name["query_events"].input_schema.get("properties", {}))
            expected = {"event_type", "zone", "state", "min_severity", "open_only", "limit"}
            step("query_events 入参 schema 完整", expected <= qprops,
                 f"实际字段 {sorted(qprops)}")
            cprops = set(by_name["create_ticket"].input_schema.get("properties", {}))
            step("create_ticket 必填项正确",
                 set(by_name["create_ticket"].input_schema.get("required", [])) == {"title", "zone"},
                 f"必填 {by_name['create_ticket'].input_schema.get('required')}")

            # 1) 只读查询
            res = await s.call_tool("query_events", {"zone": "yard-02", "open_only": True})
            d = json.loads(res.content[0].text)
            step("query_events 分层过滤", d["count"] > 0,
                 f"zone=yard-02 且未闭环 -> {d['count']} 条")

            # 2) 写入
            res = await s.call_tool("create_ticket", {
                "title": "西门夜班保安离岗", "zone": "gate-02",
                "event_type": "person", "severity": 3,
                "desc": "夜班 02:10-03:00 岗亭无人",
            })
            d = json.loads(res.content[0].text)
            step("create_ticket 建单", bool(d.get("event_id")) and d.get("state") == "detected",
                 f"{d.get('event_id')} 初始状态 {d.get('state')}")

            # 3) 统计
            res = await s.call_tool("stats", {})
            d = json.loads(res.content[0].text)
            step("stats 聚合", d.get("total_events", 0) > 0,
                 f"total={d.get('total_events')} open={d.get('open_events')} "
                 f"rate={d.get('open_rate')}")

            # 4) 错误必须回传客户端，不能被吞成"成功但空结果"
            res = await s.call_tool("query_events", {"state": "不存在的状态"})
            is_err = getattr(res, "is_error", None)
            step("非法入参回传错误", bool(is_err),
                 (res.content[0].text[:70] if res.content else "无内容"))

    # 5) 回放
    from demo_a_security.mcp_server import CallRecorder, SecurityMCPTools, replay

    tools_obj = SecurityMCPTools(DB_PATH, recorder=CallRecorder(LOG_PATH))
    rp = replay(LOG_PATH, tools_obj)
    step("调用日志可回放", rp["consistent"],
         f"日志 {rp['log_entries']} 条 / 成功重放 {rp['replayed_ok']} / "
         f"预期失败复现 {rp['expected_failures_reproduced']} / 不一致 {len(rp['mismatched'])}")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps({"steps": _steps, "replay": rp}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n结果已写入：{OUT_PATH}")
    passed = sum(1 for s in _steps if s["ok"])
    print(f"通过 {passed}/{len(_steps)} 项")
    return 0 if passed == len(_steps) else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep-log", action="store_true", help="不清空既有日志")
    args = ap.parse_args()
    return asyncio.run(run(args.keep_log))


if __name__ == "__main__":
    sys.exit(main())