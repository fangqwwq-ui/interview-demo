r"""Demo C 指标层：把「实测」与「申报假设」严格分开，再算对比。

## 三条硬规则

**规则 1：实测与假设绝不混算。**
对比表里每一行都必须标明该格数字的来源是 `measured` 还是 `declared`。
没有实测的阶段**留空**，不填估算值。空着比填错强。

**规则 2：倍数只在实测覆盖的阶段上算。**
出图阶段有真实实测（`outputs/demo_c/*/result.json`），其余阶段未接通。
因此本模块输出的主结论是「出图阶段实测 X 秒/张」，
而不是一个把假设当实测的「提速 N 倍」。

**规则 3：缓存命中必须被识别并剔除。**
实测数据里有一条 0.49s 的出图记录 —— 同提示词同 seed 重复提交，
ComfyUI 命中节点缓存直接返回，**不含真实推理**。
若把它算进平均耗时，平均出图时间会从 46s 掉到 23s，
吞吐指标凭空翻倍。因此本模块在统计前先做缓存识别，并把剔除记录单独列出。
这是效率口径里最常见的自欺方式，必须显式拦截。
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from common import config
from common.metrics import registry
from demo_c_manju.metrics.baseline import (
    STAGE_LABEL,
    STAGE_ORDER,
    ManualBaseline,
    default_baseline,
)

OUTPUT_DIR = config.paths.outputs / "demo_c"
REPORT_DIR = config.paths.outputs / "demo_c_metrics"

# 同一配置组内，耗时低于「组内最大耗时」的这个比例 → 判为缓存命中
CACHE_HIT_RATIO = 0.20

# 简历口径：内容处理效率提升 160% ⇔ 同等时间产出 2.6 倍 ⇔ 耗时降为 1/2.6 ≈ 38.5%
RESUME_SPEEDUP = 2.6

IMAGE_STAGE_DEFINITION = (
    "单张出图的端到端墙钟耗时：提交 → 排队 → 推理 → 下载图片 → 写盘。"
    "**包含下载与写盘**，不只是推理时间。不同分辨率/步数视为不同配置，分组统计。"
)


# ------------------------------------------------------------ 实测摄入


@dataclass
class MeasuredRun:
    run_id: str
    ok: bool
    elapsed_s: float
    size: str
    steps: int
    checkpoint: str
    seed: int
    width: Optional[int]
    height: Optional[int]
    bytes_size: Optional[int]
    source: str
    vram_free_before_gb: Optional[float] = None
    vram_total_gb: Optional[float] = None
    cache_suspect: bool = False
    cache_note: str = ""
    load_state: str = "unknown"   # cold | warm | unknown

    def as_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "ok": self.ok,
            "elapsed_s": self.elapsed_s,
            "size": self.size,
            "steps": self.steps,
            "checkpoint": self.checkpoint,
            "seed": self.seed,
            "width": self.width,
            "height": self.height,
            "bytes_size": self.bytes_size,
            "source": self.source,
            "cache_suspect": self.cache_suspect,
            "cache_note": self.cache_note,
            "load_state": self.load_state,
            "vram_free_before_gb": self.vram_free_before_gb,
            "vram_total_gb": self.vram_total_gb,
        }


# 空闲显存与本批次「模型未驻留基线」的接近程度：≥ 此比例判为 cold
COLD_VRAM_RATIO = 0.90


def classify_load_states(runs: List[MeasuredRun]) -> List[MeasuredRun]:
    """按**本批次观测到的显存基线**判定每条记录是冷启动还是模型常驻。

    ## 为什么不能用固定阈值（这是踩过的坑）

    第一版用的是「空闲显存 ≥ 总显存的 60% 即判为 cold」。实测数据是：

    | 状态 | 提交前空闲显存 | 占总显存 |
    |---|---|---|
    | `/free` 卸载模型后 | 6.70 GB | 84.2% |
    | 模型常驻 | 4.77–4.79 GB | 59.9–60.2% |

    **60.18% 正好压在 60% 阈值上**，会把模型常驻的运行误判成冷启动。
    SDXL 权重在本机只占约 1.9 GB 的「空闲显存差」，所以任何以总显存比例
    划分的固定阈值都必然处在噪声带里。

    ## 现在的规则

    以本批次**观测到的最大空闲显存**作为「模型未驻留」的经验基线，
    距基线 10% 以内判为 `cold`。这是**数据驱动**的，不依赖任何拍脑袋的比例：

        cold  ⟺  free_before ≥ 0.90 × max(free_before)

    代价要说清楚：该规则要求批次内**至少有一条**模型未驻留的记录，
    否则基线无从取得，全部记录只能记为 `unknown` 并排除。
    这是有意的取舍 —— 宁可报告「无法判定」，也不要猜。
    """
    frees = [r.vram_free_before_gb for r in runs if r.vram_free_before_gb is not None]
    if not frees:
        for r in runs:
            r.load_state = "unknown"
        return runs

    baseline = max(frees)
    for r in runs:
        if r.vram_free_before_gb is None:
            r.load_state = "unknown"
        elif r.vram_free_before_gb >= baseline * COLD_VRAM_RATIO:
            r.load_state = "cold"
        else:
            r.load_state = "warm"
    return runs


def load_measured_runs(root: Optional[Path] = None) -> List[MeasuredRun]:
    """从 `outputs/demo_c/*/result.json` 摄入真实出图记录。

    ⚠️ `outputs/` 是构建产物、不入库。因此在没跑过出图的机器上这里会返回空列表 ——
    此时对比表只出「申报假设」一列，实测列留空。这是正确行为，不是故障。
    """
    base = Path(root) if root else OUTPUT_DIR
    if not base.exists():
        logger.warning("未找到实测出图目录 {}，实测列将留空", base)
        return []

    runs: List[MeasuredRun] = []
    for result_file in sorted(base.glob("*/result.json")):
        try:
            rec = json.loads(result_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            logger.warning("跳过无法解析的 {}：{}", result_file, exc)
            continue
        images = rec.get("images") or [{}]
        first = images[0] if images else {}
        runs.append(
            MeasuredRun(
                run_id=result_file.parent.name,
                ok=bool(rec.get("ok")),
                elapsed_s=float(rec.get("elapsed_s") or 0.0),
                size=str(rec.get("size") or "?"),
                steps=int(rec.get("steps") or 0),
                checkpoint=str(rec.get("checkpoint") or "?"),
                seed=int(rec.get("seed") or 0),
                width=first.get("width"),
                height=first.get("height"),
                bytes_size=first.get("bytes_size"),
                source=str(result_file),
                vram_free_before_gb=rec.get("vram_free_before_gb"),
                vram_total_gb=rec.get("vram_total_gb"),
            )
        )
    return classify_load_states(runs)


def flag_cache_hits(runs: List[MeasuredRun]) -> List[MeasuredRun]:
    """在同一配置组内识别缓存命中。就地打标并返回新列表。

    判据：组内耗时 < 组内最大耗时 × CACHE_HIT_RATIO。
    为什么用组内相对比较而不是绝对阈值：不同分辨率/步数的真实耗时差异本来就大，
    绝对阈值会把「512×512 / 4 步」这种正常的小图快跑误判成缓存命中。
    """
    groups: Dict[str, List[MeasuredRun]] = {}
    for r in runs:
        key = f"{r.checkpoint}|{r.size}|{r.steps}"
        groups.setdefault(key, []).append(r)

    out: List[MeasuredRun] = []
    for key, members in groups.items():
        ok_members = [m for m in members if m.ok and m.elapsed_s > 0]
        if len(ok_members) < 2:
            out.extend(members)
            continue
        slowest = max(m.elapsed_s for m in ok_members)
        for m in members:
            if m.ok and m.elapsed_s > 0 and m.elapsed_s < slowest * CACHE_HIT_RATIO:
                m.cache_suspect = True
                m.cache_note = (
                    f"同配置组（{key}）内其余运行耗时 {slowest:.2f}s，"
                    f"本次仅 {m.elapsed_s:.2f}s（{m.elapsed_s / slowest * 100:.1f}%）。"
                    "同提示词 + 同 seed 重复提交时 ComfyUI 会命中节点缓存直接返回，"
                    "本次不含真实推理，已从耗时统计中剔除。"
                )
            out.append(m)
    return out


def _config_key(r: MeasuredRun) -> str:
    return f"{r.checkpoint}|{r.size}|{r.steps}steps"


def _work_load(r: MeasuredRun) -> int:
    """标称计算量代理：宽 × 高 × 步数。用于挑「代表配置」。"""
    w = r.width or 0
    h = r.height or 0
    return int(w) * int(h) * max(int(r.steps), 0)


def _stats_for(xs: List[float]) -> Dict[str, Any]:
    xs = sorted(xs)
    n = len(xs)
    if not n:
        return {}
    mid = n // 2
    median = xs[mid] if n % 2 else (xs[mid - 1] + xs[mid]) / 2
    out: Dict[str, Any] = {
        "n": n,
        "mean_s": round(sum(xs) / n, 3),
        "median_s": round(median, 3),
        "min_s": round(xs[0], 3),
        "max_s": round(xs[-1], 3),
    }
    if n == 1:
        out["variance_note"] = "单样本，无法给出离散度；结论仅作参考，需补测"
    return out


def image_stage_stats(runs: List[MeasuredRun]) -> Dict[str, Any]:
    """按「配置 × 冷/热」分组统计。

    ## 为什么必须有这两层分组

    **按配置分组**：实测里有 832×1216/28 步 与 512×512/4 步两种配置。
    把两者平均会得到一个 25.71s 的数字 —— 它不对应任何真实工作负载。
    这类「无关数字求平均」的错误很难被听众当场发现，所以在代码层面拦住。

    **按冷/热分组**：模型是否已驻留，单张耗时差一倍以上（本机实测：
    冷启动含 SDXL 加载，稳态不含）。两者混在一起算平均，得到的数字
    既不是冷启动耗时也不是稳态耗时，同样没有意义。

    ## 严格剔除

    两类样本不进入耗时统计，且必须单独列出：

    1. `cache_suspect` —— ComfyUI 节点缓存命中，不含真实推理（实测有一张仅 0.49s）
    2. `load_state == "unknown"` —— 缺少提交前显存记录，无法确定是冷还是热。
       口径不完整的样本宁可不用，也不要混进来假装完整。
    """
    ok = [r for r in runs if r.ok and r.elapsed_s > 0]
    cache_excluded = [r for r in ok if r.cache_suspect]
    after_cache = [r for r in ok if not r.cache_suspect]
    incomplete_excluded = [r for r in after_cache if r.load_state == "unknown"]
    counted = [r for r in after_cache if r.load_state != "unknown"]

    base = {
        "definition": IMAGE_STAGE_DEFINITION,
        "runs_ok": len(ok),
        "runs_excluded_as_cache": len(cache_excluded),
        "runs_excluded_incomplete_protocol": len(incomplete_excluded),
        "excluded_cache": [r.as_dict() for r in cache_excluded],
        "excluded_incomplete": [r.as_dict() for r in incomplete_excluded],
        "excluded_note": (
            f"剔除判据 ①：同配置组内耗时低于组内最大耗时的 {CACHE_HIT_RATIO:.0%}"
            "（ComfyUI 节点缓存命中，不含真实推理）。"
            "判据 ②：result.json 里缺 `vram_free_before_gb`，无法判定冷/热，口径不完整。"
        ),
    }

    if not counted:
        return {
            **base,
            "available": False,
            "reason": (
                "没有可计入耗时统计的实测运行"
                "（可能未跑过出图、全部命中缓存、或全部缺少提交前显存记录）"
            ),
            "configs": [],
        }

    groups: Dict[tuple, List[MeasuredRun]] = {}
    for r in counted:
        groups.setdefault((_config_key(r), r.load_state), []).append(r)

    by_config: Dict[str, Dict[str, Any]] = {}
    for (cfg_key, state), members in sorted(groups.items()):
        sample = members[0]
        entry = by_config.setdefault(
            cfg_key,
            {
                "config": cfg_key,
                "checkpoint": sample.checkpoint,
                "size": sample.size,
                "steps": sample.steps,
                "width": sample.width,
                "height": sample.height,
                "work_load": _work_load(sample),
                "load_states": {},
                "run_ids": [],
            },
        )
        entry["load_states"][state] = {
            "state": state,
            **_stats_for([m.elapsed_s for m in members]),
            "run_ids": [m.run_id for m in members],
            "vram_free_before_gb": [m.vram_free_before_gb for m in members],
        }
        entry["run_ids"] += [m.run_id for m in members]

    configs = sorted(by_config.values(), key=lambda c: c["work_load"])
    # 代表配置 = 标称计算量（宽×高×步数）最大的一组 = 生产档位。
    # 用计算量而不是样本数选，是为了不让低分辨率冒烟测试配置成为代表值。
    representative = configs[-1]

    warm = representative["load_states"].get("warm") or {}
    cold = representative["load_states"].get("cold") or {}

    for c in configs:
        w = (c["load_states"].get("warm") or {}).get("mean_s")
        if w:
            c["s_per_megapixel_step"] = round(w / (c["work_load"] / 1_000_000), 5)

    return {
        **base,
        "available": True,
        "configs": configs,
        "representative_config": representative["config"],
        "representative_note": (
            "代表配置取**标称计算量（宽×高×步数）最大**的一组，即生产档位；"
            "低分辨率/低步数的冒烟测试配置不会成为代表值。"
        ),
        "warm": warm,
        "cold": cold,
        "per_frame_warm_s": warm.get("mean_s"),
        "per_frame_cold_s": cold.get("mean_s"),
        "sample_count": (warm or cold).get("n"),
        "size": representative["size"],
        "steps": representative["steps"],
        "total_samples_counted": len(counted),
        "no_cross_config_mean_note": (
            "本模块**不输出跨配置、跨冷热的平均耗时**。"
            "不同分辨率/步数是不同工作负载，冷启动与稳态也含不同开销；"
            "把它们求平均会得到一个不对应任何真实场景的数字。分组结果见 configs。"
        ),
        "cold_total_note": (
            "冷启动样本用于估算流水线**首张**的加载开销；稳态样本用于估算其余各张。"
            "整条样片的出图耗时 = 冷启动 1 张 + 稳态 (N-1) 张，而不是 N × 稳态。"
        ),
    }


# ------------------------------------------------------------ 对比


def build_comparison(
    baseline: ManualBaseline,
    image_stats: Dict[str, Any],
    stage_log: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """生成对比表。每格都带 source 标记：measured / declared / unavailable。"""
    measured_minutes: Dict[str, float] = {}
    image_model: Dict[str, Any] = {"model": "unavailable"}

    # 出图：实测。整条样片 = 首张冷启动 + 其余 (N-1) 张稳态。
    # 不用 N × 稳态：那会漏掉首张的模型加载开销，低估总时长。
    if image_stats.get("available"):
        frames = baseline.frames_per_sample
        warm = image_stats.get("per_frame_warm_s")
        cold = image_stats.get("per_frame_cold_s")

        if warm is not None and cold is not None and frames >= 1:
            total_s = cold + (frames - 1) * warm
            image_model = {
                "model": "cold_first_then_warm",
                "frames": frames,
                "cold_s": cold,
                "warm_s": warm,
                "formula": f"cold + (frames - 1) × warm = {cold} + {frames - 1} × {warm}",
                "total_s": round(total_s, 3),
                "note": "首张含模型加载开销，其余为稳态；这是流水线的真实模式（模型只加载一次）。",
            }
        elif warm is not None:
            total_s = frames * warm
            image_model = {
                "model": "warm_only",
                "frames": frames,
                "warm_s": warm,
                "formula": f"frames × warm = {frames} × {warm}",
                "total_s": round(total_s, 3),
                "note": "缺少冷启动样本，按稳态 × 张数估算 —— **低估**了首张的加载开销。",
            }
        elif cold is not None:
            total_s = frames * cold
            image_model = {
                "model": "cold_only",
                "frames": frames,
                "cold_s": cold,
                "formula": f"frames × cold = {frames} × {cold}",
                "total_s": round(total_s, 3),
                "note": "只有冷启动样本，按冷启动 × 张数估算 —— **高估**，因为只有首张需要加载。",
            }
        else:
            total_s = None

        if image_model.get("total_s"):
            measured_minutes["image"] = round(image_model["total_s"] / 60.0, 4)

    # 其余阶段：若外部提供了真实埋点日志，则用实测；否则留空
    for rec in stage_log or []:
        name = str(rec.get("stage") or "")
        if name in STAGE_ORDER and rec.get("elapsed_s") is not None:
            measured_minutes[name] = round(float(rec["elapsed_s"]) / 60.0, 4)

    declared = {a.key: a.minutes for a in baseline.assumptions()}

    rows: List[Dict[str, Any]] = []
    for key in STAGE_ORDER:
        rows.append(
            {
                "stage": key,
                "label": STAGE_LABEL[key],
                "declared_minutes": declared.get(key),
                "declared_source": "declared",
                "measured_minutes": measured_minutes.get(key),
                "measured_source": "measured" if key in measured_minutes else "unavailable",
            }
        )

    measured_total = (
        round(sum(measured_minutes.values()), 4) if measured_minutes else None
    )
    declared_total = baseline.total_minutes

    # 只有在两端都实测时才算倍数。当前只有出图阶段实测，因此这里给出的是
    # 「部分覆盖」的倍数，且显式标注覆盖范围。
    partial_speedup: Optional[float] = None
    if "image" in measured_minutes and measured_minutes["image"] > 0:
        partial_speedup = round(declared["image"] / measured_minutes["image"], 3)

    return {
        "rows": rows,
        "measured_minutes": measured_minutes,
        "declared_minutes": declared,
        "measured_total_minutes": measured_total,
        "declared_total_minutes": declared_total,
        "measured_stage_count": len(measured_minutes),
        "stage_count": len(STAGE_ORDER),
        "image_model": image_model,
        "partial_speedup_image_only": partial_speedup,
        "partial_speedup_note": (
            "⚠️ 该倍数**只在出图阶段成立**，且分母（人工单张耗时）来自申报假设而非实测。"
            "它不是「整体效率提升」。整体倍数必须等配音与合成阶段接通后才能给。"
        ),
        "resume_speedup": RESUME_SPEEDUP,
        "resume_speedup_interpretation": (
            "简历「内容处理效率提升 160%」按「同等时间产出 2.6 倍 → 耗时降为 1/2.6 ≈ 38.5%」解释。"
        ),
        "extrapolation_note": (
            f"整条样片按 {baseline.frames_per_sample} 张分镜外推（{baseline.frames_basis}）。"
            "外推模型为「首张冷启动 + 其余稳态」，未计入批内并发、"
            "也未计入显存不足导致的反复卸载重载。"
        ),
    }


# ------------------------------------------------------------ 输出


def _pct(v: Optional[float]) -> str:
    return "n/a" if v is None else f"{v:.2f}x"


def render_markdown(report: Dict[str, Any]) -> str:
    cmp_ = report["comparison"]
    baseline = report["baseline"]
    img = report["image_stage"]

    lines: List[str] = []
    lines.append("# Demo C 指标层报告（效率口径）")
    lines.append("")
    lines.append("> 生成方式：`python -m demo_c_manju.metrics`")
    lines.append("")

    lines.append("## 1. 结论先行")
    lines.append("")
    if img.get("available"):
        rep = img["representative_config"]
        warm = img.get("warm") or {}
        cold = img.get("cold") or {}
        lines.append(f"- **代表配置**：{img['size']} / {img['steps']} 步（{img['representative_config']}）")
        lines.append(f"  - 选取依据：{img['representative_note']}")
        if warm:
            lines.append(
                f"- **稳态单张**（模型常驻）：均值 **{warm['mean_s']}s**"
                f"（中位 {warm['median_s']}s，区间 {warm['min_s']}–{warm['max_s']}s，n={warm['n']}）"
            )
            if warm.get("variance_note"):
                lines.append(f"  - ⚠️ {warm['variance_note']}")
        else:
            lines.append("- **稳态单张**：无样本（本批次未测到模型常驻的运行）")
        if cold:
            lines.append(
                f"- **冷启动单张**（含模型加载）：**{cold['mean_s']}s**（n={cold['n']}）"
            )
        else:
            lines.append("- **冷启动单张**：无样本")
        lines.append(f"- 口径：{img['definition']}")
        im = cmp_.get("image_model", {})
        if im.get("total_s"):
            lines.append(
                f"- 整条样片出图耗时的**计算模型**：`{im['formula']}` = "
                f"**{im['total_s']}s ≈ {round(im['total_s'] / 60, 2)} 分钟**  "
                f"（{im['note']}）"
            )
    else:
        lines.append(f"- 出图阶段**无可用实测**：{img.get('reason')}")
    lines.append(
        f"- 实测覆盖阶段数：**{cmp_['measured_stage_count']}/{cmp_['stage_count']}** "
        "→ 因此**本报告不给出整体效率倍数**。"
    )
    lines.append(
        f"- 人工基线（**申报假设，非实测**）合计 {cmp_['declared_total_minutes']} 分钟/条。"
    )
    lines.append(
        f"- 简历口径核对：{cmp_['resume_speedup_interpretation']} "
        f"本 Demo 无法证实该倍数，仅能证实出图阶段单张耗时。"
    )
    lines.append("")

    if img.get("configs"):
        lines.append("## 1.1 按「配置 × 冷/热」分组的出图实测")
        lines.append("")
        lines.append("| 配置 | 状态 | 样本 | 均值(s) | 中位(s) | 区间(s) | 提交前空闲显存(GB) |")
        lines.append("|---|---|---|---|---|---|---|")
        for c in img["configs"]:
            mark = " ← 代表配置" if c["config"] == img.get("representative_config") else ""
            for state in ("cold", "warm"):
                st = c["load_states"].get(state)
                if not st:
                    continue
                vram = ", ".join(str(v) for v in st["vram_free_before_gb"] if v is not None) or "—"
                lines.append(
                    f"| {c['size']} / {c['steps']} 步{mark} | {state} | {st['n']} | {st['mean_s']} | "
                    f"{st['median_s']} | {st['min_s']}–{st['max_s']} | {vram} |"
                )
        lines.append("")
        lines.append(f"> {img['no_cross_config_mean_note']}")
        lines.append(f"> {img['cold_total_note']}")
        lines.append("")
        lines.append(
            "> 冷/热的判定不是调用方的声明，而是由**提交前空闲显存**推导，"
            "且阈值取自本批次观测基线："
            f"以批次内最大空闲显存为「模型未驻留」基线，距其 {COLD_VRAM_RATIO:.0%} 以内判为 cold。"
        )
        lines.append("")
        lines.append(
            "> ⚠️ 为什么不用「空闲显存 ≥ 总显存的某个百分比」这种固定阈值："
            "本机 `/free` 后空闲 6.70 GB（84.2%），模型常驻时空闲 4.77–4.79 GB"
            "（59.9–60.2%）—— 任何取 60% 附近的固定阈值都会把常驻误判成冷启动。"
        )
        lines.append("")
        lines.append(
            "> ⚠️ 还有一个**未被建模**的状态：进程生命周期内的**首次**出图"
            "（含 CUDA 上下文初始化与 JIT 预热）。本项目实测首次为 46.06s，"
            "远高于卸载后冷启动的 21.11s 与稳态的 14.45s。该状态只有 n=1，"
            "不足以建模，且缺显存记录，因此列入下方「口径不完整」的剔除清单，"
            "不参与统计 —— 在此说明，避免读报告的人以为这条被藏起来了。"
        )
        lines.append("")

    lines.append("## 2. 阶段对比表")
    lines.append("")
    lines.append("| 阶段 | 人工基线（申报） | 来源 | 流水线（实测） | 来源 |")
    lines.append("|---|---|---|---|---|")
    for r in cmp_["rows"]:
        m = "—（未接通，留空）" if r["measured_minutes"] is None else f"{r['measured_minutes']} 分钟"
        lines.append(
            f"| {r['label']} | {r['declared_minutes']} 分钟 | declared | {m} | {r['measured_source']} |"
        )
    lines.append("")
    lines.append("> 留空的格表示**没有实测数据**。此处不填估算值 —— 空着比填错强。")
    lines.append("")

    lines.append("## 3. 人工基线的逐项依据（可当场质疑）")
    lines.append("")
    lines.append("| 阶段 | 分钟 | 可信度 | 依据 |")
    lines.append("|---|---|---|---|")
    for a in baseline["assumptions"]:
        lines.append(f"| {a['label']} | {a['minutes']} | {a['confidence']} | {a['basis']} |")
    lines.append("")
    lines.append(f"- 分镜张数假设：{baseline['frames_per_sample']}。依据：{baseline['frames_basis']}")
    lines.append("")

    lines.append("## 4. 假设敏感性：把争议摊开")
    lines.append("")
    lines.append("出图单张人工耗时是最敏感的参数。改它，人工基线与倍数如下变化：")
    lines.append("")
    has_ratio = any(r.get("image_speedup") for r in report["sensitivity"]["rows"])
    if has_ratio:
        lines.append("| 单张人工耗时（分钟） | 出图阶段合计（分钟） | 人工合计（分钟/条） | 出图倍数 |")
        lines.append("|---|---|---|---|")
    else:
        lines.append("| 单张人工耗时（分钟） | 出图阶段合计（分钟） | 人工合计（分钟/条） |")
        lines.append("|---|---|---|")
    for row in report["sensitivity"]["rows"]:
        if has_ratio:
            lines.append(
                f"| {row['per_frame_minutes']} | {row['image_minutes']} | {row['total_minutes']} | "
                f"{row.get('image_speedup', '—')}x |"
            )
        else:
            lines.append(
                f"| {row['per_frame_minutes']} | {row['image_minutes']} | {row['total_minutes']} |"
            )
    lines.append("")
    if has_ratio:
        lines.append(f"> ⚠️ **看这一列「出图倍数」：它随假设线性变化。**")
        lines.append(f"> {report['sensitivity']['assumption_dominance_note']}")
        lines.append(
            "> 这就是本报告**不把倍数当结论**的原因：一个能被假设随意缩放的数字，"
            "不是证据。"
        )
        lines.append("")
    lines.append("> 敏感性表的作用：面试官若不认可某个假设值，可当场替换并重算。")
    lines.append("> 若结论在合理区间内大幅波动，就不该对外引用这个倍数。")
    lines.append("")

    if cmp_["partial_speedup_image_only"] is not None:
        im = cmp_.get("image_model", {})
        lines.append("## 5. 仅出图阶段的部分倍数（**请谨慎引用**）")
        lines.append("")
        lines.append(
            f"- 申报假设：人工 {cmp_['declared_minutes']['image']} 分钟（"
            f"{baseline['per_frame_minutes']} 分钟/张 × {baseline['frames_per_sample']} 张）"
        )
        lines.append(
            f"- 实测：流水线 {cmp_['measured_minutes']['image']} 分钟（{im.get('formula', '—')}）"
        )
        lines.append(f"- 商：**{cmp_['partial_speedup_image_only']}x**")
        lines.append("")
        lines.append(f"> {cmp_['partial_speedup_note']}")
        lines.append(f"> {cmp_['extrapolation_note']}")
        lines.append("")
        lines.append("### 5.1 为什么这个倍数不建议在面试里主动引用")
        lines.append("")
        if report["sensitivity"].get("image_speedup_is_assumption_driven"):
            lines.append("- 它约等于「单张人工假设 ÷ 实测单张耗时」，是**单位换算**，不是工程结果。")
            lines.append("- 出图阶段实测的**原值**已经足够有力，且经得起追问：")
            if img.get("warm"):
                w = img["warm"]
                lines.append(
                    f"  - 稳态单张 **{w['mean_s']}s**，n={w['n']}，"
                    f"极差 {round(w['max_s'] - w['min_s'], 3)}s"
                    f"（{w['min_s']}–{w['max_s']}s）"
                )
            if img.get("cold"):
                lines.append(f"  - 冷启动单张 **{img['cold']['mean_s']}s**（含模型加载）")
            lines.append(f"  - 整条 {baseline['frames_per_sample']} 张样片出图 **{im['total_s']}s**")
            lines.append(
                "- 这些数字全部来自 `outputs/demo_c/*/result.json` 的真实运行记录，"
                "可以现场打开逐条核对。"
            )
        lines.append("")

    if img.get("excluded"):
        lines.append("## 6. 被剔除的运行（缓存命中）")
        lines.append("")
        lines.append(img["excluded_note"])
        lines.append("")
        for e in img["excluded"]:
            lines.append(
                f"- `{e['run_id']}`：{e['elapsed_s']}s（{e['size']} / {e['steps']} 步）"
            )
            lines.append(f"  - {e['cache_note']}")
        lines.append("")

    lines.append("## 7. 口径边界")
    lines.append("")
    lines.append("- 流水线侧：所有数字来自 `outputs/demo_c/*/result.json`，是真实运行的落盘记录。")
    lines.append("- 人侧：全部为申报假设，非实测。依据与可信度逐条列在第 3 节。")
    lines.append("- 分时加载与显存卸载（`/free`）会额外增加耗时，本报告的出图耗时**未含**该开销。")
    lines.append("- 未做批内并发测量：当前是串行出图，并发下的吞吐需要另测。")
    lines.append("")
    return "\n".join(lines)


def write_report(report: Dict[str, Any], out_dir: Optional[Path] = None) -> Dict[str, str]:
    out = Path(out_dir) if out_dir else REPORT_DIR
    out.mkdir(parents=True, exist_ok=True)

    json_path = out / "report.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    md_path = out / "REPORT.md"
    md_path.write_text(render_markdown(report), encoding="utf-8")

    csv_path = out / "comparison.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["阶段", "人工基线(分钟)", "来源", "流水线实测(分钟)", "来源"])
        for r in report["comparison"]["rows"]:
            writer.writerow(
                [
                    r["label"],
                    r["declared_minutes"],
                    r["declared_source"],
                    "" if r["measured_minutes"] is None else r["measured_minutes"],
                    r["measured_source"],
                ]
            )
        writer.writerow([])
        writer.writerow(["出图单张实测(s)", report["image_stage"].get("mean_s", "")])
        writer.writerow(
            ["实测覆盖阶段数", f"{report['comparison']['measured_stage_count']}/{report['comparison']['stage_count']}"]
        )

    return {"json": str(json_path), "markdown": str(md_path), "csv": str(csv_path)}


def publish_metrics(report: Dict[str, Any]) -> None:
    """把实测值回填到指标注册表（禁止手写数字）。"""
    img = report["image_stage"]
    cmp_ = report["comparison"]
    warm = img.get("warm") or {}
    cold = img.get("cold") or {}
    im = cmp_.get("image_model", {})

    if img.get("available") and (warm or cold):
        parts = []
        if warm:
            parts.append(f"稳态单张 {warm['mean_s']}s（n={warm['n']}，区间 {warm['min_s']}–{warm['max_s']}s）")
        if cold:
            parts.append(f"冷启动单张 {cold['mean_s']}s（n={cold['n']}，含模型加载）")
        if im.get("total_s"):
            parts.append(
                f"整条 {im.get('frames')} 张样片出图 {im['total_s']}s"
                f"（模型：{im.get('formula')}）"
            )
        parts.append(
            f"代表配置 {img.get('size')}/{img.get('steps')} 步；"
            f"口径含提交+排队+推理+下载+写盘；"
            f"实测覆盖阶段 {cmp_['measured_stage_count']}/{cmp_['stage_count']}，整体倍数不成立；"
            f"人工基线为申报假设（合计 {cmp_['declared_total_minutes']} 分钟/条），非实测"
        )
        value = "；".join(parts)
    else:
        value = f"无可用实测：{img.get('reason')}"

    registry.set_measured("c_pipeline_throughput", value)
    logger.info("已回填 c_pipeline_throughput")
