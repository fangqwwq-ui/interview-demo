"""统一日志与阶段计时。

Demo C 需要「各阶段耗时」作为效率口径的原始数据，Demo B 需要「每次改写的耗时」，
因此把计时埋点做成公共能力，避免各模块各写一套导致口径不一致。
"""

from __future__ import annotations

import sys
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List

from loguru import logger

_configured = False
_timings: List[Dict[str, Any]] = []


def setup(level: str = "INFO") -> None:
    global _configured
    if _configured:
        return
    logger.remove()
    logger.add(
        sys.stderr,
        level=level,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | {message}",
        colorize=True,
    )
    _configured = True


@contextmanager
def stage(name: str, **fields: Any) -> Iterator[Dict[str, Any]]:
    """记录一个处理阶段的耗时。

    用法：
        with stage("改写") as st:
            ...
            st["tokens"] = 123
        # 退出时自动记录耗时，并累加到 timings()

    产出可直接用于效率对比表（人工基线 vs 流水线实测）。
    """
    setup()
    record: Dict[str, Any] = {"stage": name, **fields}
    started = time.perf_counter()
    logger.info(f"▶ {name} 开始")
    try:
        yield record
    finally:
        elapsed = time.perf_counter() - started
        record["elapsed_s"] = round(elapsed, 3)
        _timings.append(record)
        logger.info(f"✔ {name} 完成，耗时 {elapsed:.2f}s")


def timings() -> List[Dict[str, Any]]:
    """返回本次进程内所有已记录阶段，供指标表生成。"""
    return list(_timings)


def total_seconds() -> float:
    return round(sum(t.get("elapsed_s", 0.0) for t in _timings), 3)


def reset() -> None:
    _timings.clear()
