"""审核层的 **LLM 通道**。

## 它补的正是规则通道补不了的洞

规则通道只能命中「已知模式」。真实漫剧内容里的问题大多是**语义层**的：
分镜描述与提示词指向的不是同一个场景、对白与画面情绪矛盾、
提示词描述的画面在现有模型能力下不可执行 —— 这些没有可枚举的模式。

## 诚实边界（必须主动交代，否则面试会翻车）

1. **本通道只看文本，不看图。** 送审的是「脚本 + 出图提示词」，
   **图像像素从未送给模型**。因此 LLM 判定的是「这段分镜描述与这句提示词
   是否自洽」，**不是「这张图里的内容是否合规」**。
   报告里 `vision_available=False` 会如实标出这一点。
   要真正审图，需要多模态模型（本项目当前通道不含）。
2. **不确定性。** 温度设为 0 只是降低随机性，不等于确定性。
   同一份内容两次调用可能给出不同 `script_asset_match` 分。
   所以 LLM 通道的结论**只用于分流**，不作为唯一依据。
3. **不可用时如实失败**，不静默降级成「通过」。降级成通过等于把
   「没审」伪装成「审过了」，是本项目明确拒绝的做法。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

from loguru import logger

from common.llm.base import LLMError
from common.llm.router import LLMRouter

# 低于此分视为分镜与画面语义脱节
MATCH_SCORE_FLOOR = 6

_SYSTEM = (
    "你是一名漫剧内容审核员。你会收到一段分镜脚本和它的出图提示词。\n"
    "请判断三件事：\n"
    "1) 提示词描述的画面与脚本情节是否自洽（是否在讲同一件事）；\n"
    "2) 脚本是否存在内容风险或明显不可用之处（空对白、逻辑断裂等）；\n"
    "3) 提示词描述的画面在文本层面是否可执行（有没有自相矛盾的描述）。\n"
    "只输出 JSON，不要解释，不要 Markdown 围栏。格式：\n"
    '{"compliant": true/false, "script_asset_match": 1-10, '
    '"issues": [{"type": "简短类型", "detail": "一句话"}], '
    '"notes": "一句话总评"}'
)

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def judge(
    script: str,
    prompt: str,
    *,
    router: Optional[LLMRouter] = None,
    item_id: str = "?",
) -> Dict[str, Any]:
    """对单条内容做 LLM 判定。

    返回字典恒含 `ok` 字段。失败时 `ok=False` 且带 `error`，**不抛异常**，
    以免单条失败打断整批审核。
    """
    router = router or LLMRouter()
    user = (
        f"【分镜脚本】\n{script or '(空)'}\n\n"
        f"【出图提示词】\n{prompt or '(空)'}\n\n"
        "请按要求输出 JSON。"
    )
    try:
        result = router.chat(
            [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}],
            temperature=0.0,
            max_tokens=512,
        )
    except LLMError as exc:
        logger.warning("LLM 审核通道不可用（{}）：{}", item_id, exc)
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM 审核通道异常（{}）：{}", item_id, exc)
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    raw = result.text or ""
    match = _JSON_RE.search(raw)
    if not match:
        return {
            "ok": False,
            "error": "审核输出无法解析为 JSON",
            "raw": raw[:200],
            "provider": result.provider,
            "model": result.model,
        }
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        return {
            "ok": False,
            "error": f"JSON 解析失败：{exc}",
            "raw": raw[:200],
            "provider": result.provider,
            "model": result.model,
        }

    issues = data.get("issues") or []
    if not isinstance(issues, list):
        issues = [{"type": "raw", "detail": str(issues)}]

    score = data.get("script_asset_match")
    try:
        score = int(score) if score is not None else None
    except (TypeError, ValueError):
        score = None

    return {
        "ok": True,
        "compliant": bool(data.get("compliant", True)),
        "script_asset_match": score,
        "low_match": score is not None and score < MATCH_SCORE_FLOOR,
        "issues": issues,
        "notes": data.get("notes") or "",
        "provider": result.provider,
        "model": result.model,
        "elapsed_s": round(result.elapsed_s, 3),
    }


def channel_provenance() -> Dict[str, Any]:
    return {
        "self_built": True,
        "channel": "LLM",
        "mode": "text-only",
        "vision_available": False,
        "vision_note": (
            "送审内容为脚本与提示词文本，图像像素未送模型；"
            "因此本通道不构成「审图」，只审文本自洽性与合规风险。"
        ),
        "match_score_floor": MATCH_SCORE_FLOOR,
        "determinism": "temperature=0，降低随机性但不构成确定性保证",
    }
