"""Demo B 的改写链路 —— 两条臂，用于三路对照。

三路对照的两条改写臂：

| 臂 | 实现 | 定位 |
|---|---|---|
| `kernel` | `third_party/humanize-chinese` 的规则改写（子进程调用） | 第三方基线。用来**证明**纯规则改写不够用 |
| `llm` | 本模块自研：垂类检索 + 提示词注入 + LLM 改写 | 自研增量。简历中「语义保留改写」的落地 |

第三条「臂」不是改写，是原始文本本身，作为对照组（见 `evaluate.py`）。

## 三个必须记录的口径信息

改写结果的分数**不是**一个可跨运行比较的数。它取决于：

1. **走哪条 LLM 通道**（云端 qwen-plus / 本地 qwen3:8b）—— 记录在 `chat.provider`
2. **提示词版本** —— 记录在 `PROMPT_VERSION`，改提示词必须改版本号
3. **检索到了哪些锚点** —— 记录在 `retrieved`（含相似度与文档 id）

任何一份指标表如果不带这三项，数字就不可复现。这是本模块存在的核心理由。

## 规则臂的固定参数

`humanize_cn.py` 的 `--seed` 固定为 42，且**不加** `--no-noise` / `--no-stats`：
默认设置就是该工具设计者认为的最佳用法，为了公平，基线应当用它自己的默认配置。
若加上 `--no-noise` 会让它表现更好，但那不是「工具开箱能力」，属于为基线调参。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from common import config
from common.llm.base import LLMError
from common.llm.router import LLMRouter
from demo_b_copywriting import tells
from demo_b_copywriting.retriever import Retriever

HERE = Path(__file__).resolve().parent
CLEAN_DIR = HERE / "data" / "clean"
KERNEL_SCRIPTS = Path(config.paths.humanize_chinese) / "scripts"

# 改提示词必须同步改版本号 —— 版本号是结果可复现的一部分
PROMPT_VERSION = "b-rewrite-v1.1"

KERNEL_SEED = 42
KERNEL_TIMEOUT_S = 120

# 垂类 → 内核场景参数。映射是人工设定并记录在此，不隐藏。
SCENE_BY_VERTICAL: Dict[str, str] = {
    "职场效率": "formal",
    "健康生活": "general",
    "数码评测": "tech",
    "营销种草": "social",
    "母婴育儿": "chat",
}
DEFAULT_SCENE = "general"

ARM_KERNEL = "kernel"
ARM_LLM = "llm"
ARM_ORIGINAL = "original"


# ============================================================ 结果结构


@dataclass
class RewriteResult:
    arm: str
    input_text: str
    output_text: str
    vertical: str
    ok: bool
    elapsed_s: float
    error: Optional[str] = None
    retrieved: List[Dict[str, Any]] = field(default_factory=list)
    retrieval_ms: float = 0.0
    prompt: Optional[Dict[str, str]] = None
    chat: Optional[Dict[str, Any]] = None
    provenance: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "arm": self.arm,
            "vertical": self.vertical,
            "ok": self.ok,
            "elapsed_s": round(self.elapsed_s, 3),
            "error": self.error,
            "input_chars": len(self.input_text),
            "output_chars": len(self.output_text),
            "output_text": self.output_text,
            "retrieved": self.retrieved,
            "retrieval_ms": round(self.retrieval_ms, 3),
            "chat": self.chat,
            "prompt_version": (self.prompt or {}).get("version"),
            "provenance": self.provenance,
        }


# ============================================================ 提示词


def _system_prompt() -> str:
    banned = "、".join(tells.banned_phrases(limit=30))
    tpl = "；".join(t["readable"] for t in tells.template_examples(limit=8))
    return (
        "你是一名中文内容编辑。任务：把一段「AI 味」过重的中文改写成自然的真人表达。\n"
        "\n"
        "硬性要求（违反任何一条即为失败）：\n"
        "1. 事实零增删。不得引入原文没有的数字、品牌名、时间、事件或结论。\n"
        "2. 观点不改变。原文说重要，改写后仍然说重要，只是换一种更自然的方式说。\n"
        "3. 长度控制在原文的 0.7–1.2 倍之间。\n"
        f"4. 严禁出现这些短语：{banned}。\n"
        f"5. 严禁使用这些句式：{tpl}。\n"
        "6. 不要用总结性套话收尾（如「综上所述」），除非原文本身就有对应内容。\n"
        "7. 只输出改写后的正文。不要解释、不要标题、不要引号、不要 Markdown 标记。\n"
        "\n"
        "改写方向（按重要性排序）：\n"
        "- 把抽象评价换成具体的动作、数字或场景。\n"
        "- 句子长短交错，允许出现短句，允许口语化，但不要刻意卖萌。\n"
        "- 允许保留原文的信息量，但不得为了「更像人」而注水或加感叹。\n"
        "- 原文中已经足够自然的句子可以原样保留，不必强行改动。\n"
    )


def _user_prompt(
    text: str,
    vertical: str,
    anchors: List[Dict[str, Any]],
    demos: List[Dict[str, Any]],
) -> str:
    parts: List[str] = [f"【垂类】{vertical}", ""]

    if anchors:
        parts.append("【风格锚点】以下是同一垂类里真人写作的片段。只用来感受语感，")
        parts.append("严禁照抄其中的具体内容（数字、品牌、经历都不能搬过来）。")
        for i, a in enumerate(anchors, start=1):
            parts.append(f"{i}. {a['text']}")
        parts.append("")

    if demos:
        parts.append("【对照示例】AI 味写法 → 真人写法")
        for d in demos:
            parts.append(f"AI：{d['ai_text']}")
            parts.append(f"真人：{d['human_text']}")
            if d.get("notes"):
                parts.append(f"（改写要点：{d['notes']}）")
            parts.append("")

    parts.append("【待改写文本】")
    parts.append(text)
    parts.append("")
    parts.append("请直接输出改写结果。")
    return "\n".join(parts)


_WRAPPER_RE = re.compile(r"^\s*(改写后|改写结果|输出|结果)\s*[:：]\s*")
_FENCE_RE = re.compile(r"^\s*```[a-zA-Z]*\s*\n?|\n?\s*```\s*$")


def _strip_output(raw: str) -> str:
    """清掉模型常见的包装：代码围栏、前置标签、整体包裹的引号。"""
    text = (raw or "").strip()
    text = _FENCE_RE.sub("", text).strip()
    text = _WRAPPER_RE.sub("", text).strip()
    pairs = [("「", "」"), ("“", "”"), ('"', '"'), ("'", "'")]
    for left, right in pairs:
        if len(text) > 1 and text.startswith(left) and text.endswith(right):
            text = text[1:-1].strip()
    return text


# ============================================================ 改写器


class Redactor:
    """自研改写链路：检索锚点 + 少量固定对照示例 + LLM 改写。"""

    def __init__(
        self,
        retriever: Optional[Retriever] = None,
        router: Optional[LLMRouter] = None,
        *,
        top_k: int = 3,
        few_shot: int = 1,
        temperature: float = 0.6,
        max_repair_rounds: int = 0,
    ) -> None:
        self.retriever = retriever or Retriever()
        self.router = router or LLMRouter()
        self.top_k = top_k
        self.few_shot = few_shot
        self.temperature = temperature
        self.max_repair_rounds = max_repair_rounds
        self._demos = self._load_demos()

    # ------------------------------------------------------ 对照示例

    @staticmethod
    def _load_demos(clean_dir: Path = CLEAN_DIR) -> Dict[str, List[Dict[str, Any]]]:
        """按垂类加载对照示例（few-shot）。

        ⚠️ 为什么用**固定**示例而不是从检索结果里取：
        few-shot 若随检索波动，则两次运行的差异同时来自「检索命中不同」和「模型输出不同」，
        指标变化无法归因。固定示例把变量隔离成单一来源。
        """
        path = clean_dir / "style_corpus.jsonl"
        if not path.exists():
            logger.warning(f"缺少 {path}，本次改写将不带对照示例")
            return {}
        by_vertical: Dict[str, List[Dict[str, Any]]] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("kind") != "pair":
                continue
            human = rec.get("human_text") or ""
            ai_text = rec.get("ai_text") or ""
            if not human or not ai_text:
                continue
            by_vertical.setdefault(rec.get("vertical") or "未分类", []).append(
                {"id": rec.get("id"), "ai_text": ai_text, "human_text": human, "notes": rec.get("notes", "")}
            )
        return by_vertical

    def demos_for(self, vertical: str) -> List[Dict[str, Any]]:
        return self._demos.get(vertical, [])[: self.few_shot]

    # ------------------------------------------------------ 主流程

    def rewrite(self, text: str, vertical: Optional[str] = None) -> RewriteResult:
        text = (text or "").strip()
        vertical = vertical or "未分类"
        started = time.perf_counter()

        anchors, retrieval_ms = self.retriever.search_with_latency(
            text, k=self.top_k, vertical=vertical
        )
        demo_list = self.demos_for(vertical)

        system = _system_prompt()
        user = _user_prompt(text, vertical, anchors, demo_list)
        prompt = {"version": PROMPT_VERSION, "system": system, "user": user}

        provenance: Dict[str, Any] = {
            "self_built": True,
            "prompt_version": PROMPT_VERSION,
            "retrieved_ids": [a.get("doc_id") for a in anchors],
            "retrieved_scores": [a.get("score") for a in anchors],
            "demo_ids": [d.get("id") for d in demo_list],
            "encoder": self.retriever.encoder.info.as_dict(),
            "store": self.retriever.store.info.backend,
            "temperature": self.temperature,
        }

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        max_tokens = max(256, int(len(text) * 2))

        last_error: Optional[str] = None
        for round_idx in range(self.max_repair_rounds + 1):
            try:
                result = self.router.chat(
                    messages, temperature=self.temperature, max_tokens=max_tokens
                )
            except LLMError as exc:
                return RewriteResult(
                    arm=ARM_LLM,
                    input_text=text,
                    output_text="",
                    vertical=vertical,
                    ok=False,
                    elapsed_s=time.perf_counter() - started,
                    error=f"{type(exc).__name__}: {exc}",
                    retrieved=anchors,
                    retrieval_ms=retrieval_ms,
                    prompt=prompt,
                    provenance=provenance,
                )

            output = _strip_output(result.text)
            chat = result.as_dict()
            chat["repair_round"] = round_idx
            provenance["provider"] = result.provider
            provenance["model"] = result.model
            provenance["fallback_used"] = result.fallback_used

            residual = tells.count(output)
            chat["residual_tells"] = residual.count
            if residual.count == 0 or round_idx >= self.max_repair_rounds:
                return RewriteResult(
                    arm=ARM_LLM,
                    input_text=text,
                    output_text=output,
                    vertical=vertical,
                    ok=bool(output),
                    elapsed_s=time.perf_counter() - started,
                    error=None if output else "LLM 返回空文本",
                    retrieved=anchors,
                    retrieval_ms=retrieval_ms,
                    prompt=prompt,
                    chat=chat,
                    provenance=provenance,
                )

            # 触发一轮修复：把残留的 AI 味短语列出来，让模型只针对这些位置改
            last_error = f"仍残留 {residual.count} 处 AI 味表达"
            logger.info(f"改写残留 {residual.count} 处，发起第 {round_idx + 1} 轮修复")
            messages = messages + [
                {"role": "assistant", "content": output},
                {
                    "role": "user",
                    "content": (
                        "上一次改写里仍然出现了这些 AI 味表达："
                        + "、".join(h["matched"] for h in residual.hits[:10])
                        + "。请只针对这些位置重写，其余部分保持不变，"
                        "仍然只输出正文，不要解释。"
                    ),
                },
            ]

        # 仅在 max_repair_rounds > 0 时可达
        return RewriteResult(
            arm=ARM_LLM,
            input_text=text,
            output_text="",
            vertical=vertical,
            ok=False,
            elapsed_s=time.perf_counter() - started,
            error=last_error or "改写失败",
            retrieved=anchors,
            retrieval_ms=retrieval_ms,
            prompt=prompt,
            provenance=provenance,
        )


# ============================================================ 规则臂（第三方）


def kernel_rewrite(
    text: str, vertical: Optional[str] = None, *, seed: int = KERNEL_SEED
) -> RewriteResult:
    """调用第三方内核做规则改写。这是**基线**，不是本项目的自研能力。"""
    text = (text or "").strip()
    vertical = vertical or "未分类"
    scene = SCENE_BY_VERTICAL.get(vertical, DEFAULT_SCENE)
    script = KERNEL_SCRIPTS / "humanize_cn.py"
    started = time.perf_counter()

    provenance: Dict[str, Any] = {
        "self_built": False,
        "component": "humanize-chinese",
        "repo": "https://github.com/yukinotech/humanize-chinese",
        "commit": "efdefaa0ead85cfde3fe36450a06cbe995792bda",
        "used_for": "rule-rewrite-baseline",
        "scene": scene,
        "seed": seed,
        "extra_flags": [],
        "note": "默认参数调用，未针对基线调参",
    }

    if not script.exists():
        return RewriteResult(
            arm=ARM_KERNEL,
            input_text=text,
            output_text="",
            vertical=vertical,
            ok=False,
            elapsed_s=time.perf_counter() - started,
            error=f"内核脚本不存在：{script}",
            provenance=provenance,
        )

    try:
        proc = subprocess.run(
            [sys.executable, str(script), "--scene", scene, "--seed", str(seed)],
            input=text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=KERNEL_TIMEOUT_S,
            cwd=str(KERNEL_SCRIPTS),
        )
    except subprocess.TimeoutExpired:
        return RewriteResult(
            arm=ARM_KERNEL,
            input_text=text,
            output_text="",
            vertical=vertical,
            ok=False,
            elapsed_s=time.perf_counter() - started,
            error=f"内核改写超时（>{KERNEL_TIMEOUT_S}s）",
            provenance=provenance,
        )

    if proc.returncode != 0:
        return RewriteResult(
            arm=ARM_KERNEL,
            input_text=text,
            output_text="",
            vertical=vertical,
            ok=False,
            elapsed_s=time.perf_counter() - started,
            error=f"内核返回码 {proc.returncode}: {(proc.stderr or '')[:200]}",
            provenance=provenance,
        )

    output = (proc.stdout or "").strip()
    return RewriteResult(
        arm=ARM_KERNEL,
        input_text=text,
        output_text=output,
        vertical=vertical,
        ok=bool(output),
        elapsed_s=time.perf_counter() - started,
        error=None if output else "内核输出为空",
        provenance=provenance,
    )


def rewrite(text: str, vertical: Optional[str] = None, *, prefer_encoder: str = "auto") -> RewriteResult:
    """便捷入口：默认走自研 LLM 臂。"""
    return Redactor(Retriever(prefer_encoder=prefer_encoder)).rewrite(text, vertical)
