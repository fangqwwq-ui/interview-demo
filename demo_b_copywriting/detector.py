"""检测器：封装 third_party/humanize-chinese 的打分能力。

定位说明（贯穿本项目口径）：
本内核是**第三方开源组件**，不是本项目自研。这里只做三件事：
  1. 把它的 import 路径与调用方式收敛到一个入口
  2. 把它的输出整理成结构化结果（分数 / 等级 / 分类明细 / 统计指标）
  3. 记录来源与版本，供指标口径页溯源

为什么选用它的「检测」而不用它的「改写」：
实测 `humanize_cn.py --scene social` 会把「时间管理之于每个人」改成
「时间管理之于于每个人来说都，有着…」这类病句，分数仅 95→62。见 third_party/README.md。
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from common import config

KERNEL_NAME = "humanize-chinese"
KERNEL_REPO = "https://github.com/yukinotech/humanize-chinese"
KERNEL_COMMIT = "efdefaa0ead85cfde3fe36450a06cbe995792bda"
KERNEL_LICENSE = "MIT（上游 README 声明，仓库未附独立 LICENSE 文件）"


@dataclass
class DetectionResult:
    score: int
    level: str
    issue_count: int
    categories: Dict[str, int] = field(default_factory=dict)
    severities: Dict[str, int] = field(default_factory=dict)
    stats: Dict[str, Any] = field(default_factory=dict)
    worst_sentences: List[Dict[str, Any]] = field(default_factory=list)
    kernel: Dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "score": self.score,
            "level": self.level,
            "issue_count": self.issue_count,
            "categories": self.categories,
            "severities": self.severities,
            "stats": self.stats,
            "worst_sentences": self.worst_sentences,
            "kernel": self.kernel,
        }


def kernel_dir() -> Path:
    return Path(config.paths.humanize_chinese) / "scripts"


@lru_cache(maxsize=1)
def _load_kernel():
    """进程内导入内核。失败则返回 None，由调用方决定是否走子进程。"""
    scripts = kernel_dir()
    if not (scripts / "detect_cn.py").exists():
        return None
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    try:
        import detect_cn  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"内核进程内导入失败，将回退子进程模式：{type(exc).__name__}: {exc}")
        return None
    return detect_cn


def _provenance() -> Dict[str, str]:
    return {
        "name": KERNEL_NAME,
        "repo": KERNEL_REPO,
        "commit": KERNEL_COMMIT,
        "license": KERNEL_LICENSE,
        "used_for": "detection-only",
    }


def _normalize_metrics(metrics: Dict[str, Any]) -> Dict[str, Any]:
    """挑出可分页展示的统计量。

    ⚠️ 内核的 metrics 是 defaultdict，`perplexity` / `burstiness` 等键
    **仅在 n-gram 模型成功产出统计量时才存在**（见 detect_cn.py 的
    `if ngram_stats:` 分支）。文本过短时这些键会缺失，因此必须用 .get() 取值，
    不能假设键一定存在 —— 缺失时要原样暴露为 None，让口径页显示「未产出」。
    """
    keep = (
        "char_count",
        "sentence_count",
        "paragraph_count",
        "entropy",
        "emotional_density",
        "perplexity",
        "burstiness",
        "paragraph_entropy_cv",
    )
    return {k: metrics.get(k) for k in keep}


def detect(text: str) -> DetectionResult:
    """对文本打分。返回结构化结果。"""
    if not text or not text.strip():
        raise ValueError("detect() 收到空文本")

    kernel = _load_kernel()
    if kernel is not None:
        issues, metrics = kernel.detect_patterns(text)
        score = kernel.calculate_score(issues, metrics)
        level = kernel.score_to_level(score)
        worst = kernel.analyze_sentences(text, 5) or []
    else:
        score, level, issues, metrics, worst = _detect_via_subprocess(text)

    # ⚠️ 内核返回的 issues 是 defaultdict(list)：{类别: [命中项, ...]}，
    # 不是扁平列表。直接 for 迭代会拿到类别名字符串。
    categories: Dict[str, int] = {}
    severities: Dict[str, int] = {}
    total = 0
    for cat, items in (issues or {}).items():
        if not items:
            continue
        categories[str(cat)] = len(items)
        total += len(items)
        for item in items:
            sev = (item or {}).get("severity", "unknown")
            severities[str(sev)] = severities.get(str(sev), 0) + 1

    return DetectionResult(
        score=int(score),
        level=str(level),
        issue_count=total,
        categories=dict(sorted(categories.items(), key=lambda kv: -kv[1])),
        severities=dict(sorted(severities.items(), key=lambda kv: -kv[1])),
        stats=_normalize_metrics(metrics or {}),
        worst_sentences=list(worst),
        kernel=_provenance(),
    )


def _detect_via_subprocess(text: str):
    """子进程兜底：内核脚本支持 stdin + --json。"""
    import json  # noqa: PLC0415

    scripts = kernel_dir()
    proc = subprocess.run(
        [sys.executable, str(scripts / "detect_cn.py"), "--json"],
        input=text,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"kernel subprocess failed: {proc.stderr[:300]}")
    payload = json.loads(proc.stdout)
    return (
        payload.get("score", 0),
        payload.get("level", "UNKNOWN"),
        payload.get("issues", []),
        payload.get("metrics", {}),
        payload.get("worst_sentences", []),
    )


def health() -> Dict[str, Any]:
    """内核可用性自检。"""
    scripts = kernel_dir()
    probe = "综上所述，值得注意的是，我们要赋能数字化转型，降本增效。"
    try:
        result = detect(probe)
        return {
            "ok": True,
            "mode": "in-process" if _load_kernel() is not None else "subprocess",
            "kernel_dir": str(scripts),
            "probe_score": result.score,
            "probe_level": result.level,
            "provenance": _provenance(),
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "kernel_dir": str(scripts), "error": f"{type(exc).__name__}: {exc}"}
