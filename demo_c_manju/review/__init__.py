"""Demo C 审核层 —— 规则通道 + LLM 通道。

定位（与 PLAN.md 8.2 保持一致）：
    本模块是 **本次新增** 能力。既有 `E:\\aigc-comic` 提供分镜 / 出图 / 配音 / 合成
    四个执行服务，但**没有**质量与合规模块。对应简历第 8 条「漫剧内容批量生成
    与审核流程」。

为什么是双通道而不是单一通道：
    规则通道确定性高、可逐条指认，但只能命中「已知模式」；
    LLM 通道能理解语义与画面意图，但不确定、不可逐条回归。
    两者命中集合不重合的部分，正是双通道存在的证据 —— 报告里会显式输出
    `unique_to_rule` 与 `unique_to_llm` 两个数字，而不是把它们合并成一个总分。

导入顺序说明：先 rules / llm_judge，后 reviewer。
reviewer 依赖前两者，反过来不依赖 → 这个顺序不会踩到包内循环导入。
"""

from demo_c_manju.review import llm_judge, rules
from demo_c_manju.review.reviewer import (
    ReviewBatch,
    ReviewItem,
    load_batch,
    review_batch,
    review_item,
    write_report,
)
from demo_c_manju.review.rules import RULES, RuleFinding, RuleOutcome, scan_item_rules

__all__ = [
    "RULES",
    "ReviewBatch",
    "ReviewItem",
    "RuleFinding",
    "RuleOutcome",
    "llm_judge",
    "load_batch",
    "review_batch",
    "review_item",
    "rules",
    "scan_item_rules",
    "write_report",
]
