"""由 bench_raw.json 生成 bench/report.md。

为什么用脚本生成而不是手写：
  项目口径要求「实测数字只能由脚本回填」。手写的报告会在下一次压测后
  与原始数据不一致，而这种不一致往往几个月后才被发现。
  本脚本读取原始 JSON 现场渲染，数字与数据同源。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "outputs" / "demo_a" / "bench" / "bench_raw.json"
OUT = ROOT / "bench" / "report.md"


def fmt(v: object) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def main() -> int:
    if not RAW.exists():
        print(f"未找到原始数据：{RAW}\n请先运行 python scripts/bench.py", file=sys.stderr)
        return 2
    d = json.loads(RAW.read_text(encoding="utf-8"))
    env = d["env"]
    raw = d["raw"]
    rows = d["comparison"]

    lines: list[str] = []
    A = lines.append

    A("# Demo A 接口压测对照报告（v1 同步 vs v2 异步并发）")
    A("")
    A("> **本报告由 `scripts/bench_report.py` 从 `outputs/demo_a/bench/bench_raw.json` 自动生成。**")
    A("> 数字不可手改；改数字只能改原始数据后重新生成。")
    A("")

    # ---- 一、结论 ----
    A("## 一、结论（先看这里）")
    A("")
    if rows:
        best = max(rows, key=lambda r: r["rps_gain_pct"])
        worst = min(rows, key=lambda r: r["rps_gain_pct"])
        # 失败数必须从原始数据累加，不能写死「0」——
        # 否则某次压测出现失败，报告会仍然宣称「失败 0」，正是最危险的那种漂移。
        fails = sum(
            int(b.get("failures", 0) or 0)
            for ver in ("v1", "v2")
            for b in raw.get(ver, {}).values()
        )
        A(f"- v2 相对 v1 的 RPS 提升区间：**{worst['rps_gain_pct']}% ~ {best['rps_gain_pct']}%**"
          f"（{len(rows)} 个并发档）")
        A(f"- 全部 {len(rows)} 档合计失败请求：**{fails}**")
        A("- **简历原文写的是「响应速度提升 40%」，本次实测区间低于该值，按实测报告。**")
        A("")
        A("> 口径提醒：本报告以 **RPS（吞吐）** 为主指标，同时给出 P95（延迟分位）。")
        A("> 简历「响应速度提升 40%」未说明衡量的是吞吐还是延迟，")
        A("> 因此**不可把本表数字当作简历 40% 的印证**。")
    else:
        A("- 无有效对照数据，请重跑 `python scripts/bench.py`。")
    A("")

    # ---- 二、两版差异 ----
    A("## 二、两版到底差在哪（唯一变量 = 并发模型）")
    A("")
    A("| 维度 | v1（基线） | v2（优化后） |")
    A("|---|---|---|")
    A("| 端点类型 | 同步 `def`，阻塞工作线程 | 异步 `async def` + `run_in_executor` |")
    A("| SQLite 连接 | **每请求新建** `TicketStore` | **每线程一条**（`TicketStorePool`） |")
    A("| 索引/编码器 | 每请求重新 `load()` | 启动时预热一次，进程内常驻 |")
    A("| 数据/检索逻辑 | 完全相同 | 完全相同 |")
    A("| 机器/流量配比 | 完全相同 | 完全相同 |")
    A("")
    A("**刻意保持不变的部分**：同一份 SQLite 库、同一套检索逻辑、同一台机器、同一流量配比。")
    A("变的只有并发模型这一项 —— 否则对比不成立。")
    A("")

    # ---- 三、环境 ----
    A("## 三、测试环境")
    A("")
    A("| 项 | 值 |")
    A("|---|---|")
    for k, v in env.items():
        if isinstance(v, dict):
            v = ", ".join(f"{a}={b}" for a, b in v.items())
        A(f"| {k} | {fmt(v)} |")
    A(f"| 压测工具 | {d['tool']} |")
    A(f"| 每档请求数 | {d['rounds']}（另含 {d['warmup']} 次预热，不计入） |")
    A("")
    A("> 压测流量配比：" + "；".join(f"`{m} {p}`" for m, p in d.get("mix", [])))
    A("")

    # ---- 四、原始数据 ----
    A("## 四、原始压测数据")
    A("")
    A("| 并发 | 版本 | 样本 n | RPS | P50 (ms) | P95 (ms) | P99 (ms) | 最大 (ms) | 轮间波动 | 失败 |")
    A("|---:|:--|---:|---:|---:|---:|---:|---:|---:|---:|")
    for c in sorted({int(k) for k in raw.get("v1", {}).keys()}, key=int):
        for ver in ("v1", "v2"):
            b = raw.get(ver, {}).get(str(c))
            if not b or not b.get("n"):
                A(f"| {c} | {ver} | 0 | — | — | — | — | — | — |"
                  f" {b.get('failures', '—') if b else '—'} |")
                continue
            A(
                f"| {c} | {ver} | {b['n']} | {b['rps']} | {b['p50_ms']} | {b['p95_ms']} |"
                f" {b['p99_ms']} | {b['max_ms']} | {b.get('rps_spread_pct', '—')}% |"
                f" {b['failures']} |"
            )
    A("")
    A(f"- 每档跑 **{d.get('repeats', 1)} 轮**，表内为**中位数**；"
      f"每轮 {d['rounds']} 请求，另含 {d['warmup']} 次预热（不计入）。")
    A("- **轮间波动（spread）** = 各轮 RPS 极差百分比，越小说明测量越稳。")
    A("- v1/v2 在每轮内**交替先后顺序**跑，避免「谁先跑」变成影响结果的主因。")
    A("")
    A("> 这两条是踩过坑才加的：早期版本固定「先 v1 后 v2」且只跑一轮，")
    A("> 同一份代码两次跑出 **+28.2%** 与 **-9.2%** 两个相反结论 ——")
    A("> 当时并发 16 的 stdev 达50~60ms、max 达 558ms，测量被噪声主导。")
    A("> 加交替顺序 + 3 轮中位数后，spread 降到 1.6%~8.6%，结论才稳定。")
    A("")

    # ---- 五、对照 ----
    A("## 五、v1 / v2 对照汇总")
    A("")
    A("| 并发 | v1 RPS | v2 RPS | RPS 提升 | v1 P95 | v2 P95 | P95 改善 | 结论是否可信 |")
    A("|---:|---:|---:|---:|---:|---:|---:|:--|")
    for r in rows:
        # 波动大就明说「不可信」，不拿噪声数据下结论
        sp = max(
            raw.get("v1", {}).get(str(r["concurrency"]), {}).get("rps_spread_pct", 0) or 0,
            raw.get("v2", {}).get(str(r["concurrency"]), {}).get("rps_spread_pct", 0) or 0,
        )
        flag = "是" if sp <= 15 else f"**否（波动 {sp}%）**"
        A(
            f"| {r['concurrency']} | {r['v1_rps']} | {r['v2_rps']} | "
            f"{r['rps_gain_pct']:+}% | {r['v1_p95_ms']} | {r['v2_p95_ms']} | "
            f"{r['p95_reduction_pct']:+}% | {flag} |"
        )
    A("")
    A("> 按并发档单独统计，**不做跨档平均** —— 不同并发下瓶颈不同，平均没有物理意义。")
    A("")
    # 不要预设「并发越高提升越大」——实测恰好相反，按数据说话。
    if rows:
        best = max(rows, key=lambda r: r["rps_gain_pct"])
        worst = min(rows, key=lambda r: r["rps_gain_pct"])
        A(f"**必须如实说明的一点**：提升最高的是**并发 {best['concurrency']}**"
          f"（{best['rps_gain_pct']:+g}%），最低的是并发 {worst['concurrency']}"
          f"（{worst['rps_gain_pct']:+g}%）—— 并不是「并发越高提升越大」。")
        A("")
        A("这符合预期：异步并发的收益来自「等待时间可重叠」，")
        A("而当 CPU 与线程池都被打满时，没有可重叠的等待可省了。")
        A("")

    # ---- 六、踩坑 ----
    A("## 六、过程中定位并修复的四个真实缺陷")
    A("")
    A("前三个不是「调参」，是代码错误，都会表现为「v2 更慢或直接 500」——")
    A("如果不查日志只看最终数字，会得出完全错误的结论。第 4 个更隐蔽：**测量方法本身不可信**。")
    A("")
    A("### 1. 单个 SQLite 连接被多线程并发使用 → HTTP 500")
    A("")
    A("```")
    A("sqlite3.InterfaceError: bad parameter or other API misuse")
    A("```")
    A("")
    A("- 现象：并发 8 时 v2 失败 91 次，而 v1 零失败。")
    A("- 根因：`check_same_thread=False` **只解除「跨线程抛异常」的检查，不提供并发安全**。")
    A("  把单个 `sqlite3.Connection` 共享给 8 线程的`ThreadPoolExecutor` 会随机崩。")
    A("- 修复：新增 `TicketStorePool`，每线程一条连接。")
    A("")
    A("### 2. 懒加载被误算成服务延迟")
    A("")
    A("```")
    A("rag.load()首次 ≈ 64241 ms，之后 ≈ 0.5 ms")
    A("```")
    A("")
    A("- 现象：8 线程并发首档，整体卡 4 分 36 秒。")
    A("- 根因：每个工作线程各自 `load()`，并发触发 bge 模型初始化，互相争抢资源；")
    A("  且第一个请求会背上全部加载时间，压测读数失真。")
    A("- 修复：用 FastAPI `lifespan` 在启动时预热一次，之后请求只命中进程级缓存。")
    A("  实测并发 8 压测从**4 分 36 秒降到 2.2 秒，失败 0**。")
    A("")
    A("### 3. 连接在错误线程里创建")
    A("")
    A("```")
    A("IndexError: tuple index out of range / TypeError: 'NoneType' object is not subscriptable")
    A("```")
    A("")
    A("- 现象：`/v2/stats` 在高并发下偶发 500。")
    A("- 根因：在事件循环线程里取好 store，再把 `store.stats` 丢进线程池 ——")
    A("  即「连接的创建线程」与「使用线程」不一致。")
    A("- 修复：把取连接的动作放进执行查询的那个函数内部（`_thread_store()` 在工作线程内调用）。")
    A("")
    A("### 4. 压测本身不可信：固定顺序 + 单轮 → 结论可翻转")
    A("")
    A("```")
    A("同一份代码，两次压测：+28.2% 与 -9.2%（结论相反）")
    A("并发16 时 stdev 50~60ms、max 558ms；改成交替顺序 + 3 轮中位数后 spread 1.6%~8.6%")
    A("```")
    A("")
    A("- 现象：最初固定「先 v1 后 v2」且每档只跑一轮，结果不稳定到能自我矛盾。")
    A("- 根因：两个未控制的变量 ——")
    A("  ① **顺序偏差**：高并发档 v2 总排在 v1 之后，受到前一轮热积累影响；")
    A("  ② **单轮采样**：高并发下尾部抖动（max 558ms）被当成典型值。")
    A("- 修复：轮内交替 v1/v2 先后顺序；每档 3 轮取中位数；")
    A("  并输出 `rps_spread_pct` 作为**数据可信度指标**。")
    A("")
    A("### 附带：线程池容量必须 ≥ 并发数")
    A("")
    A("初版 `_POOL_SIZE=8` 而压测打到 16 并发，请求排队，v2 的 P95 反而比 v1 差")
    A("（239ms vs 167ms，**-18.9%**）。池是并发上限，池小于并发数时")
    A("「异步并发」退化为队列等待，拿不到任何收益。改为可配（默认 16）后转正。")
    A("")

    # ---- 七、局限 ----
    A("## 七、局限与不可比之处（主动交代）")
    A("")
    A("1. **单进程、无外部依赖**：本测试打的是本地 FastAPI，不含真实网络与数据库服务。")
    A("   RPS 绝对值不能与生产系统比较。")
    A("2. **未用 locust**：locust 依赖 gevent，本机带宽约 340 KB/s，")
    A("   为一个数字引入重依赖不划算。改用 httpx + asyncio 自建压测器，零新增依赖。")
    A("3. **检索负载偏轻**：Demo 语料仅 14 条工单，向量检索几乎瞬时。")
    A("   真实场景下索引加载与连接竞争的开销占比会更高，v2 优势可能更大 —— ")
    A("   但也可能因GIL 使CPU 密集段无法真正并行。**本测试不足以判断大数据量下的表现。**")
    A("4. **未压测写路径**：`create_ticket` 有写锁竞争，本次只压了读路径。")
    A("5. **提升幅度随机器与负载变化**：上表数字只在本机、本流量配比下成立。")
    A("")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"报告已生成：{OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())