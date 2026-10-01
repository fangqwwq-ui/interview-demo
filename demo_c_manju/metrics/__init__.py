"""Demo C 指标层（本次新增能力之三）。

回答的问题：**各阶段花了多久，以及「效率提升」这个说法能不能立住。**

模块划分：

| 文件 | 职责 |
|---|---|
| `baseline.py` | 人工基线建模为**可质疑的假设**（带依据 + 可信度），不让它冒充实测 |
| `compare.py` | 摄入实测运行、识别缓存命中、生成对比表与敏感性分析 |
| `__main__.py` | 命令行入口 |

与 PLAN.md §8.3 的对应关系：
§8.3 要求「效率提升 160% 需给出对比基线」。本模块的立场是 ——
**本机拿不到可信的人工基线，因此不引用倍数，只报绝对耗时**；
同时把人工假设完整摊开、参数化，让面试官可以当场改参数重算。
"""

from demo_c_manju.metrics.baseline import (
    STAGE_LABEL,
    STAGE_ORDER,
    BaselineAssumption,
    ManualBaseline,
    default_baseline,
)
from demo_c_manju.metrics.compare import (
    MeasuredRun,
    build_comparison,
    flag_cache_hits,
    image_stage_stats,
    load_measured_runs,
    render_markdown,
    write_report,
)

__all__ = [
    "STAGE_LABEL",
    "STAGE_ORDER",
    "BaselineAssumption",
    "ManualBaseline",
    "MeasuredRun",
    "build_comparison",
    "default_baseline",
    "flag_cache_hits",
    "image_stage_stats",
    "load_measured_runs",
    "render_markdown",
    "write_report",
]
