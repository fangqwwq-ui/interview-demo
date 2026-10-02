"""Demo A 分层检索 RAG。

「分层」指什么（必须说清，否则会被问倒）：
  第一层：**结构化前置过滤**（zone / event_type / 时间窗 / severity 阈值）
          —— 直接用SQL 过滤，把不可能相关的候选先砍掉；
  第二层：**语义向量召回**（bge-small-zh-v1.5）+ 重排；
  第三层：**会话记忆**（同一会话内的多轮上下文）。

为什么不是纯向量检索：
  安防运维问的是「东门今天有没有未闭环的陌生人事件」——这类问题的关键是
  **结构化条件**（zone=东门、state≠closed、type=person），不是语义相似度。
  纯向量检索会把「东门已闭环」的高分结果排在「西门未闭环」前面，
  因为字面更接近。分层就是先把硬条件用SQL 落实，再在幸存者里做语义排序。

工程纪律（沿用 Demo B 的既定口径，勿破坏）：
  1. **编码器必须与建库时一致**，不一致直接抛 EncoderMismatchError。
     混用不会报错，只会静默产出垃圾相似度。
  2. **结构化过滤后若候选为空，必须如实上报filtered_out，而非静默回退到全库**。
     静默回退会让「我查了东门」变成「我查了全部」，是错误答案的来源。
  3. 检索后端（FAISS / numpy）与向量化后端（bge / hashing）都记进结果。

用法：
    from demo_a_security.rag import SecurityRAG
    rag = SecurityRAG()
    rag.build()                                  # 建库（首次）
    out = rag.query("东门今天有哪些陌生人没闭环？",
                    filters={"zone": "gate-01", "open_only": True})
    print(out["answer_context"], out["backend"])
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from loguru import logger

from common.embed import get_encoder
from common.vectorstore import get_store
from demo_a_security.store import TicketStore

# Demo A 语料与索引落在项目内独立目录，不与 Demo B 混用
DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "demo_a"
INDEX_DIR = DATA_DIR / "index"

_ENCODER_PREFER = {
    "sentence-transformers": "st",
    "hashing-ngram": "hashing",
}

# 结构化过滤后，为语义召回超额取数的倍数
_OVERFETCH = 8

# 会话记忆保留的最大轮数。刻意设小：Demo 场景够用，且可解释。
_MEMORY_MAX_TURNS = 4

# ---------------------------------------------------------------- 查询意图
# 结构化字段**必须走结构化通道**，不能指望向量能分清 cam-01 和 cam-08。
# 实测（未解析前）：「摄像头 cam-01 记录了什么事件？」top-1 命中 A-0010
# （cam-08），因为 bge-small 对这类短 token 区分度很差，
# 文本里明明写了 cam-01 也救不回来。
# 所以把「问句里出现的结构化线索」显式抽成过滤条件，这才是分层的本意。
_CAMERA_RE = re.compile(r"\bcam[-_ ]?(\d{1,3})\b", re.IGNORECASE)
_SEV_RE = re.compile(r"(?:危险等级|风险|等级)\D{0,4}(\d)")
_EVENT_TYPE_HINTS = {
    "入侵": "intrusion", "闯入": "intrusion", "翻越": "intrusion", "尾随": "intrusion",
    "车辆": "vehicle", "货车": "vehicle", "叉车": "vehicle", "车辆逆行": "vehicle",
    "火灾": "fire", "着火": "fire", "起火": "fire",
    "烟雾": "smoke", "冒烟": "smoke",
    "人员": "person", "人员进入": "person", "刷脸": "person",
}
# 「未闭环 / 已闭环 / 误报」等状态词 -> 过滤条件。
# 注意「已闭环」与「未闭环」方向相反，且closed 与 false_positive 都算终态，
# 不能简单映射成单个 state，必须用 open_only 的取反。
_STATE_HINTS = (
    ("未闭环", True), ("没有闭环", True), ("还没闭环", True), ("待处理", True),
    ("待处置", True), ("未处置", True), ("未解决", True), ("未关闭", True),
)
_CLOSED_HINTS = ("已闭环", "已经闭环", "已关闭", "已处置", "已解决", "处理完了")


def infer_filters(question: str, base: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """从问句里抽取结构化过滤条件（不改变既有 base 的显式条件）。

    只抽「有确定含义」的线索，抽取不出来就不加 —— 宁可少过滤，
    也不能凭空造一个过滤条件把正确答案挡在外面。
    """
    f: Dict[str, Any] = dict(base or {})
    q = question or ""

    m = _CAMERA_RE.search(q)
    if m and "camera_id" not in f:
        f["camera_id"] = f"cam-{int(m.group(1)):02d}"

    m = _SEV_RE.search(q)
    if m and "min_severity" not in f:
        try:
            f["min_severity"] = int(m.group(1))
        except ValueError:
            pass

    # 状态词：先判否定式（未闭环），再判肯定式（已闭环）。
    # 顺序不能反 —— 「未闭环」里也含「闭环」两个字。
    if "open_only" not in f and "states" not in f:
        if any(w in q for w, _ in _STATE_HINTS):
            f["open_only"] = True
        elif any(w in q for w in _CLOSED_HINTS):
            f["open_only"] = False
            f["terminal_only"] = True

    if "event_type" not in f:
        for hint, et in _EVENT_TYPE_HINTS.items():
            if hint in q:
                f["event_type"] = et
                break

    return f


class EncoderMismatchError(RuntimeError):
    """索引编码器与当前可用编码器不一致。"""


@dataclass
class SessionMemory:
    """极简会话记忆。

    只保留最近 N 轮的 (提问, 命中文档 id) 对。刻意不存完整历史 ——
    一是Demo 场景不需要，二是不存原文可避免把敏感描述长期留在内存里。
    """

    turns: List[Dict[str, Any]] = field(default_factory=list)

    def add(self, question: str, doc_ids: Sequence[str], answer_hint: str = "") -> None:
        self.turns.append(
            {"question": question, "doc_ids": list(doc_ids), "answer_hint": answer_hint}
        )
        if len(self.turns) > _MEMORY_MAX_TURNS:
            self.turns = self.turns[-_MEMORY_MAX_TURNS:]

    def recent_doc_ids(self) -> List[str]:
        """最近几轮命中的文档 id，供结果去重加权。"""
        out: List[str] = []
        for t in self.turns:
            for d in t["doc_ids"]:
                if d not in out:
                    out.append(d)
        return out

    def as_dict(self) -> dict:
        return {"turns": self.turns, "max_turns": _MEMORY_MAX_TURNS}


@dataclass
class RetrievalResult:
    """检索结果。所有字段都进 JSON，便于指标与回放。"""

    question: str
    hits: List[Dict[str, Any]]
    backend: Dict[str, Any]
    encoder: Dict[str, Any]
    latency_ms: float
    n_candidates: int
    n_structured: int
    filtered_out: int
    filters_applied: Dict[str, Any]
    memory_used: List[str] = field(default_factory=list)

    @property
    def answer_context(self) -> str:
        """拼接给 LLM 的上下文片段。没有命中时返回明确说明，不编造。"""
        if not self.hits:
            return "（无匹配记录：当前结构化条件下没有可引用的事件，请如实说明无数据）"
        lines = []
        for i, h in enumerate(self.hits, 1):
            lines.append(
                f"[{i}] {h['title']}（{h['doc_id']}）\n{h['text']}"
            )
        return "\n\n".join(lines)


class SecurityRAG:
    def __init__(
        self,
        store: TicketStore,
        *,
        index_dir: Path = INDEX_DIR,
        prefer_encoder: str = "auto",
        prefer_store: str = "auto",
        allow_encoder_mismatch: bool = False,
    ) -> None:
        self.store = store
        self.index_dir = Path(index_dir)
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self.allow_encoder_mismatch = allow_encoder_mismatch
        self._prefer_encoder = prefer_encoder
        self._prefer_store = prefer_store
        self._enc = None
        self._store = None
        self._meta: Dict[str, Any] = {}
        self.memory = SessionMemory()

    # ------------------------------------------------------------ 建库

    def build(self, docs: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """把工单转换为知识文档并建向量索引。

        知识文档来源是**已落库的真实工单**，不是手工编写的语料——
        这样「检索到什么」与「库里有什么」天然一致，不会出现演示时
        引用了一条并不存在的记录。
        """
        docs = docs if docs is not None else self._docs_from_store()
        if not docs:
            raise RuntimeError(
                "没有可建库的文档。请先向 TicketStore 写入事件，"
                "或显式传入 docs（见 seed.py）。"
            )

        enc = get_encoder(self._prefer_encoder)
        self._enc = enc
        store = get_store(enc.info.dim, self._prefer_store)
        self._store = store

        texts = [d["text"] for d in docs]
        vectors = enc.encode(texts)
        metas = [
            {
                "doc_id": d["doc_id"],
                "title": d["title"],
                "event_id": d.get("event_id", ""),
                "zone": d.get("zone", ""),
                "event_type": d.get("event_type", ""),
                "state": d.get("state", ""),
                "severity": d.get("severity", 0),
                "text": d["text"],
            }
            for d in docs
        ]
        store.add(vectors, metas)
        store.save(str(self.index_dir))

        self._meta = {
            "n_docs": len(docs),
            "encoder": enc.info.as_dict(),
            "store": store.info.as_dict(),
            "dim": enc.info.dim,
        }
        (self.index_dir / "meta.json").write_text(
            json.dumps(self._meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info(
            f"Demo A 检索库已建：{len(docs)} 条/ {enc.info.backend} / {store.info.backend}"
        )
        return self._meta

    def _docs_from_store(self) -> List[Dict[str, Any]]:
        """从工单库生成知识文档。文本模板固定，保证可复现。

        ⚠️ 必须把 ``desc``（人工描述）写进索引文本。
        早期版本只索引结构化字段（区域/类型/状态），导致「仓库门禁被暴力尝试
        吗」这类**用描述词提问**的查询完全检索不到对应工单 —— 因为关键词
        只存在于 desc 里，而 desc 没被索引。这不是相似度调参问题，
        是数据没进索引。
        """
        events = self.store.query(limit=1000)
        docs: List[Dict[str, Any]] = []
        for ev in events:
            desc = str(ev.extra.get("desc", "")).strip()
            parts = [
                f"事件 {ev.event_id}，类型 {ev.event_type}，",
                f"区域 {ev.zone}，摄像头 {ev.camera_id or '未知'}，",
                f"危险等级 {ev.severity}，",
                f"当前状态 {ev.state.value}，",
                f"状态路径 {' -> '.join(ev.path())}，",
                f"流转记录 {len(ev.transitions)} 次。",
            ]
            if desc:
                parts.append(f"现场描述：{desc}。")
            # 最后一条流转备注也进文本：运维常问「后来怎么处理的」，
            # 而处置结论只存在于 transitions.note，不在 events 表里。
            if ev.transitions and ev.transitions[-1].note:
                parts.append(f"处置结论：{ev.transitions[-1].note}。")
            docs.append(
                {
                    "doc_id": f"doc::{ev.event_id}",
                    "title": f"{ev.zone} 的 {ev.event_type} 事件 {ev.event_id}",
                    "text": "".join(parts),
                    "event_id": ev.event_id,
                    "zone": ev.zone,
                    "event_type": ev.event_type,
                    "state": ev.state.value,
                    "severity": ev.severity,
                }
            )
        return docs

    # ------------------------------------------------------------ 加载

    def load(self) -> None:
        """加载已建索引。同样校验编码器一致性。"""
        meta_path = self.index_dir / "meta.json"
        if not meta_path.exists():
            raise FileNotFoundError(
                f"索引不存在：{meta_path}。请先调用 build() 或运行 scripts/seed_demo_a.py"
            )
        self._meta = json.loads(meta_path.read_text(encoding="utf-8"))

        recorded = self._meta.get("encoder", {})
        recorded_backend = recorded.get("backend")
        prefer = _ENCODER_PREFER.get(recorded_backend or "", "auto")
        enc = get_encoder(prefer)
        if (
            not self.allow_encoder_mismatch
            and recorded_backend
            and enc.info.backend != recorded_backend
        ):
            raise EncoderMismatchError(
                f"索引使用编码器 {recorded_backend}，当前可用 {enc.info.backend}。"
                "不同编码器的向量不在同一空间，相似度无意义。"
                "请用相同编码器重建索引，或显式传 allow_encoder_mismatch=True 并"
                "在报告中说明该结果不可跨编码器比较。"
            )
        self._enc = enc
        store = get_store(enc.info.dim, self._prefer_store)
        store.load(str(self.index_dir))
        self._store = store

    # ------------------------------------------------------------ 检索

    def query(
        self,
        question: str,
        *,
        filters: Optional[Dict[str, Any]] = None,
        k: int = 5,
        use_memory: bool = True,
        infer: bool = True,
    ) -> RetrievalResult:
        if self._store is None or self._enc is None:
            self.load()
        assert self._store is not None and self._enc is not None

        """检索主入口。

        infer=True 时从问句抽取结构化条件（摄像头号 / 危险等级 /
        未闭环·已闭环 / 事件类型），与显式 filters 合并（显式优先，不被覆盖）。
        infer=False 用于 A/B 对照 —— baseline 臂不能有这层，
        否则两臂差异就不是「分层过滤」这一个变量了。
        """
        requested_filters = (
            infer_filters(question, filters) if infer else dict(filters or {})
        )
        t0 = time.perf_counter()

        # ---- 第一层：结构化前置过滤（真过滤，不静默回退）----
        structured = self._structured_candidates(requested_filters)
        allowed_ids = {d["doc_id"] for d in structured}
        total_docs = self._meta.get("n_docs", 0)
        filtered_out = max(total_docs - len(structured), 0)

        # ---- 第二层：语义召回 + 重排 ----
        pool = max(k * _OVERFETCH, 32)
        qvec = self._enc.encode_one(question)
        raw_hits = self._store.search(qvec, k=pool)

        recent = set(self.memory.recent_doc_ids()) if use_memory else set()
        kept: List[Dict[str, Any]] = []
        for h in raw_hits:
            meta = h.meta
            # 硬过滤：不在结构化候选集内的直接丢弃。
            # ⚠️ 这里**不能**写成 `if allowed_ids and ...`：空集合是 falsy，
            # 过滤条件把候选全滤光时会静默退化成「不过滤」，返回全库结果 ——
            # 表现为「我查了东门」却给出西门的答案。宁可返回空。
            if meta.get("doc_id") not in allowed_ids:
                continue
            d = meta.get("doc_id")
            # 重排信号：会话内最近命中过的文档小幅加权（仅重排，不改变过滤）
            boost = 0.02 if d in recent else 0.0
            kept.append({**h.as_dict(), "score": round(h.score + boost, 4)})

        kept.sort(key=lambda x: (-x["score"], x.get("doc_id", "")))
        hits = kept[:k]
        latency_ms = (time.perf_counter() - t0) * 1000

        if use_memory:
            self.memory.add(question, [h.get("doc_id", "") for h in hits])

        return RetrievalResult(
            question=question,
            hits=hits,
            backend=self._store.info.as_dict(),
            encoder=self._enc.info.as_dict(),
            latency_ms=round(latency_ms, 3),
            n_candidates=len(raw_hits),
            n_structured=len(structured),
            filtered_out=filtered_out,
            filters_applied=requested_filters,
            memory_used=sorted(recent),
        )

    def _structured_candidates(
        self, filters: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """按结构化条件从工单库取候选。

        直连 TicketStore 而不是过滤向量结果 —— 这样「查东门」就是真的只查东门，
        而不是先全库检索再事后丢弃（后者会让 filtered_out 失去意义）。

        ⚠️ 必须只读 ``filters``，不得 pop：调用方（query）依赖这份字典
        来记录 filters_applied。这里统一复制一份来改。
        """
        f = dict(filters)
        states = None
        if f.pop("open_only", False):
            from demo_a_security.events import TERMINAL_STATES, EventState

            states = [s for s in EventState if s not in TERMINAL_STATES]
        elif f.pop("terminal_only", False):
            from demo_a_security.events import TERMINAL_STATES

            states = [s for s in TERMINAL_STATES]
        min_sev = f.pop("min_severity", None)
        zone = f.pop("zone", None)
        event_type = f.pop("event_type", None)
        camera_id = f.pop("camera_id", None)

        events = self.store.query(
            zone=zone,
            event_type=event_type,
            states=states,
            min_severity=min_sev,
            camera_id=camera_id,
            limit=1000,
        )
        return [
            {
                "doc_id": f"doc::{e.event_id}",
                "event_id": e.event_id,
                "zone": e.zone,
                "event_type": e.event_type,
                "state": e.state.value,
                "severity": e.severity,
            }
            for e in events
        ]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "index_dir": str(self.index_dir),
            "encoder": self._meta.get("encoder", {}),
            "store": self._meta.get("store", {}),
            "n_docs": self._meta.get("n_docs", 0),
            "memory": self.memory.as_dict(),
        }