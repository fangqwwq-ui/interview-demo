"""Demo A 服务：v1（同步阻塞串行）与 v2（异步并发）两版并存。

为什么要两版同时存在：
  简历里写的「响应速度提升 40%」必须能在同一台机器、同一份数据上
  复现出对照数字。所以两版接口**同时在线**，通过路径前缀区分
  （/v1/... 与 /v2/...），而不是改代码前后各跑一次 ——
  后者无法排除机器状态差异，说服力弱得多。

两版的差异只在一个维度：**并发模型**。
  v1：FastAPI 的 def（同步）端点 + 每请求新建 SQLite 连接 + 串行执行
      —— 典型的「能跑就行」写法。
  v2：async def（异步）端点 + 线程池复用连接 + 并发执行检索与统计
      —— 共享只读分析器，连接复用。

刻意保持不变的部分（否则对比不公平）：
  同一份 SQLite 库、同一套检索逻辑、同一台机器、同一并发数。
  变的只有并发模型这一项。

⚠️ 口径纪律：这里**不对外承诺具体提升百分比**。
   百分比由 scripts/bench.py 实测后回填，bench/report.md 里同时给出
   原始数据与环境信息，不允许在此处写死数字。

运行：
    uvicorn demo_a_security.service:app --port 8200
    健康检查 GET /healthz   工具清单 GET /tools
    v1  GET /v1/events?zone=gate-01   POST /v1/ask
    v2  GET /v2/events?zone=gate-01   POST /v2/ask
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from demo_a_security.events import EventState
from demo_a_security.mcp_server import SecurityMCPTools
from demo_a_security.rag import SecurityRAG
from demo_a_security.store import TicketStore, TicketStorePool

DB_PATH = Path("outputs/demo_a/tickets.sqlite3")
INDEX_DIR = Path("data/demo_a/index")

# v2 专用：检索与统计是 CPU 密集（GIL 内），放线程池避免阻塞事件循环。
#
# 容量可配（env: DEMO_A_POOL_SIZE）。默认值取 16 而非 8：
#   实测并发 16 时若池只有 8，请求要排队等线程，v2 的P95 反而比 v1 更差
#   （239ms vs 167ms，-18.9%）。池是并发上限，池小于并发数时
#   「异步并发」退化��队列等待，拿不到任何收益。
#   代价是每个池线程各持一条 SQLite 连接（TicketStorePool），
#   8 GB 内存下 16 条连接可忽略。
_POOL_SIZE = int(os.getenv("DEMO_A_POOL_SIZE", "16"))


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)
    k: int = Field(3, ge=1, le=10)
    zone: Optional[str] = None
    event_type: Optional[str] = None
    open_only: bool = False


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """启动时预热：把 bge 模型 + FAISS 索引载入一次（约 64 秒，实测）。

    为什么放lifespan 而不是等第一个请求：
      1. 首个请求若触发模型载入，那个请求会慢到几十秒，
         压测会把「懒加载」误算成服务延迟（第一档并发直接失真）；
      2. 早期版本正是没做预热，8 线程并发首次初始化把压测拖了 4 分多钟；
      3. 预热失败要**明确报出来**（演示现场不能临时下载模型），
         所以异常原样抛出，不静默降级。
    预热在后台线程做，不阻塞事件循环。
    """
    await asyncio.to_thread(_build_index)
    print(f"[warmup] 索引已就绪：{INDEX_DIR}（bge 首次载入约 64 秒，之后常驻）")
    yield
    pool = _STORE_POOL
    if pool is not None:
        pool.close()


# ------------------------------------------------------------------ v1
app = FastAPI(
    title="Demo A 安防事件服务",
    version="0.1.0",
    description="多模态智能安防 Agent —— 事件查询 / 分层检索问答 / 工单 MCP 工具",
    lifespan=_lifespan,
)


def _open() -> TicketStore:
    """每次调用新建一个存储实例（v1 的典型写法：连接不复用）。"""
    return TicketStore(DB_PATH)


def _ask_sync(req: AskRequest) -> Dict[str, Any]:
    """同步检索问答路径。"""
    store = _open()
    try:
        rag = SecurityRAG(store, index_dir=INDEX_DIR)
        rag.load()
        r = rag.query(
            req.question,
            filters={
                "zone": req.zone,
                "event_type": req.event_type,
                "open_only": req.open_only,
            },
            k=req.k,
        )
        return {
            "version": "v1",
            "question": req.question,
            "count": len(r.hits),
            "hits": r.hits,
            "context": r.answer_context,
            "backend": r.backend,
            "encoder": r.encoder,
            "filtered_out": r.filtered_out,
        }
    finally:
        store.close()


@app.get("/healthz")
def healthz() -> Dict[str, Any]:
    return {
        "ok": True,
        "db": str(DB_PATH),
        "db_exists": DB_PATH.exists(),
        "mcp_version": "2.x",
    }


@app.get("/tools")
def tools() -> List[Dict[str, str]]:
    """把 MCP 三工具同步暴露为 HTTP，便于压测脚本直接打点。"""
    return [
        {"name": "query_events", "description": "按条件查询安防事件"},
        {"name": "create_ticket", "description": "创建安防工单"},
        {"name": "stats", "description": "看板统计"},
    ]


@app.get("/v1/events")
def v1_events(
    zone: Optional[str] = Query(None),
    event_type: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    open_only: bool = Query(False),
    limit: int = Query(20, ge=1, le=200),
) -> Dict[str, Any]:
    store = _open()
    try:
        states = _resolve_states(state)
        evs = store.query(
            zone=zone, event_type=event_type, states=states, limit=limit
        )
        return {
            "version": "v1",
            "count": len(evs),
            "events": [
                {
                    "event_id": e.event_id,
                    "type": e.event_type,
                    "zone": e.zone,
                    "severity": e.severity,
                    "state": e.state.value,
                    "desc": str(e.extra.get("desc", "")),
                }
                for e in evs
            ],
        }
    finally:
        store.close()


@app.post("/v1/ask")
def v1_ask(req: AskRequest) -> Dict[str, Any]:
    return _ask_sync(req)


@app.get("/v1/stats")
def v1_stats() -> Dict[str, Any]:
    store = _open()
    try:
        s = store.stats()
        s["version"] = "v1"
        return s
    finally:
        store.close()


# ------------------------------------------------------------------ v2
# v2 的共享只读分析器：**索引/编码器全局复用一次** + **每线程一个 SQLite 连接**。
#
# ⚠️ 这里踩过一个真实的坑，必须记住：
#   最初 v2 把**单个** TicketStore（含单个 sqlite3.Connection）共享给 8 线程的
#   ThreadPoolExecutor，并发压测下稳定抛
#   `sqlite3.InterfaceError: bad parameter or other API misuse` → HTTP 500
#   （实测并发 8 时 91 次失败，而 v1 零失败）。
#   根因：`check_same_thread=False` 只解除「跨线程抛异常」的检查，
#   **不提供并发安全**。Connection 不能被多线程同时调用。
#
#   注意这里有两层共享，必须分开对待：
#     - 索引/编码器/向量库：只读，可安全共享（只load 一次，省掉重复加载开销）
#     - SQLite 连接：不可共享，必须 TicketStorePool 每线程一个
#   常见错法是「把 store 也做成模块级单例一起共享」——那等于没改。
_ANALYZER: Optional[Dict[str, Any]] = None
_POOL = ThreadPoolExecutor(max_workers=_POOL_SIZE, thread_name_prefix="a-v2")
_LOCK = asyncio.Lock()

#连接池独立于 _ANALYZER 存在：它是「每线程一连接」的提供者，
# 与索引是否已加载无关。
_STORE_POOL: Optional[TicketStorePool] = None


def _build_index() -> Dict[str, Any]:
    """只载入一次索引（只读对象，跨线程安全共享）。"""
    # 先确保池与本线程连接就绪
    pool = _store_pool()
    store = pool.get()
    rag = SecurityRAG(store, index_dir=INDEX_DIR)
    rag.load()
    # 把共享的只读部分单独返回；连接不返回给跨线程使用
    return {"index_dir": str(INDEX_DIR), "loaded": True}


def _store_pool() -> TicketStorePool:
    global _STORE_POOL
    if _STORE_POOL is None:
        _STORE_POOL = TicketStorePool(DB_PATH)
    return _STORE_POOL


def _thread_store() -> TicketStore:
    """取**当前线程**自己的 store。

    关键：本函数总是在 run_in_executor 的工作线程里被调用，
    因此拿到的就是那个线程独占的连接。
    """
    return _store_pool().get()


def _thread_rag(store: TicketStore) -> SecurityRAG:
    """构造绑到本线程 store 的 RAG，复用进程内已载入的索引。

    ⚠️ 这里有个代价实测数字，必须记住：
       ``SecurityRAG.load()`` **首次**约64 秒（bge 模型 + FAISS 初始化），
       之后仅约 0.5 ms。而 ``common.embed.get_encoder`` 是**进程级单例**
       （模块内 ``_cached``），因此真正的64 秒在进程内**只会发生一次** ——
       前提是不把索引加载放进「每线程都跑」的路径。

       早期版本让每个工作线程各自 ``load()``，于是 8 个线程并发触发
       模型初始化，互相争抢 CPU/内存，8 路压测整体卡了 4 分多钟。
       现在改为：``_build_index()`` 在服务预热阶段做一次（_analyzer 负责），
       工作线程只调用 ``load()``（命中进程级缓存，约 0.5 ms）。

       共享边界：索引/编码器只读可共享；SQLite 连接不可共享，
       由 ``_thread_store()`` 每线程一个。
    """
    rag = SecurityRAG(store, index_dir=INDEX_DIR)
    rag.load()
    return rag


async def _analyzer() -> Dict[str, Any]:
    """预热：把索引与编码器载入一次（只做一次）。

    注意这里**只**预热共享的只读部分，**不**把 store 放进返回值 ——
    连接必须由各工作线程通过 _thread_store() 自行获取。
    """
    global _ANALYZER
    if _ANALYZER is None:
        async with _LOCK:
            if _ANALYZER is None:
                _ANALYZER = await asyncio.to_thread(_build_index)
    return _ANALYZER


def _resolve_states(state: Optional[str]) -> Optional[List[EventState]]:
    """state 参数 -> 状态列表。v1/v2 共用，保证两版语义一致。"""
    if state == "open":
        from demo_a_security.events import TERMINAL_STATES

        return [s for s in EventState if s not in TERMINAL_STATES]
    if state:
        try:
            return [EventState(state.lower())]
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return None


@app.get("/v2/events")
async def v2_events(
    zone: Optional[str] = Query(None),
    event_type: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    open_only: bool = Query(False),
    limit: int = Query(20, ge=1, le=200),
) -> Dict[str, Any]:
    await _analyzer()  # 预热共享索引

    def work() -> Dict[str, Any]:
        # 关键：store 取自**当前工作线程**，每线程独占一条连接
        store = _thread_store()
        states = _resolve_states(state)
        evs = store.query(
            zone=zone, event_type=event_type, states=states, limit=limit
        )
        return {
            "version": "v2",
            "count": len(evs),
            "events": [
                {
                    "event_id": e.event_id,
                    "type": e.event_type,
                    "zone": e.zone,
                    "severity": e.severity,
                    "state": e.state.value,
                    "desc": str(e.extra.get("desc", "")),
                }
                for e in evs
            ],
        }

    return await asyncio.get_running_loop().run_in_executor(_POOL, work)


@app.post("/v2/ask")
async def v2_ask(req: AskRequest) -> Dict[str, Any]:
    await _analyzer()  # 预热共享索引

    def work() -> Dict[str, Any]:
        store = _thread_store()
        r = _thread_rag(store).query(
            req.question,
            filters={
                "zone": req.zone,
                "event_type": req.event_type,
                "open_only": req.open_only,
            },
            k=req.k,
        )
        return {
            "version": "v2",
            "question": req.question,
            "count": len(r.hits),
            "hits": r.hits,
            "context": r.answer_context,
            "backend": r.backend,
            "encoder": r.encoder,
            "filtered_out": r.filtered_out,
        }

    return await asyncio.get_running_loop().run_in_executor(_POOL, work)


@app.get("/v2/stats")
async def v2_stats() -> Dict[str, Any]:
    await _analyzer()  # 预热共享索引

    #⚠️ store 必须在**执行统计的线程内**取，不能在这里先取好再丢给线程池。
    #   早期版本在事件循环线程里调 _thread_store()，再把 store.stats 丢进
    #   executor，于是：事件循环线程占着一个连接不放，而真正干活的
    #   工作线程各自建连接 —— 既浪费又制造跨线程竞态，
    #   并发下报 `IndexError: tuple index out of range` / NoneType 错。
    def work() -> Dict[str, Any]:
        return _thread_store().stats()

    s = await asyncio.get_running_loop().run_in_executor(_POOL, work)
    s["version"] = "v2"
    return s