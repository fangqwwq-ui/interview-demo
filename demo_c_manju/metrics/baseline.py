r"""人工基线：**申报假设**，不是实测值。

## 为什么要把基线单独建模成「假设 + 依据」

Demo C 要回答简历里的「内容处理效率提升 160%」。这个倍数只有在
「人工方式耗时」有可信基线时才有意义。本项目**不具备**真实人工基线的采集条件
（没有真实剪辑师的操作录屏与工时记录）。因此只有两条路：

1. 编一个看起来合理的人工耗时 —— 这是伪造，本项目拒绝；
2. 把人工耗时**显式建模成可修改、可质疑的假设**，逐条给出依据，
   并让对比表随假设参数联动。

本模块走第 2 条。设计要点：

- 每个假设项都必须带 `basis`（依据来源）与 `confidence`（可信度），
  空着依据的假设不允许写进来（`validate()` 会拒绝）。
- 全部路径都出**参数化区间**而不是单点值，避免「54 分钟」这种假精确。
- 对外表述规则：流水线侧的耗时可以写「实测」，人工侧只能写「按申报假设」。
  **绝不允许把两者混成一个没有限定的分母。**

## 一句话

这份基线的价值不在于数字本身，而在于它**把争议点摊开了**：
面试官不同意 4 分钟/张，可以当场改成 2 分钟，对比表会立即重算 ——
结论是否稳健，一眼可见。这比端出一个无法追问的「+160%」诚实得多。
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List

# 阶段顺序与 aigc-comic 的执行服务一一对应（既有能力，本项目只做编排与计时）
STAGE_ORDER = ("script", "image", "audio", "compose")

STAGE_LABEL = {
    "script": "剧本 / 分镜",
    "image": "出图",
    "audio": "配音",
    "compose": "音画合成",
}


@dataclass
class BaselineAssumption:
    key: str
    label: str
    minutes: float                 # 单条样片在该阶段的人工耗时（分钟）
    basis: str                     # 依据来源，**不允许为空**
    confidence: str                # high | medium | low
    adjustable: bool = True

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ManualBaseline:
    """人工完成一条 30–60s 漫剧样片的耗时假设。"""

    frames_per_sample: int = 6
    frames_basis: str = (
        "30–60s 成片按 6–8 个分镜计，取中位 6。该值与实际剧本有关，可调。"
    )
    per_frame_minutes: float = 4.0
    per_frame_basis: str = (
        "本机实测单张 832×1216 / 28 步出图耗时 46.06s（纯推理 + 下载）。"
        "人工方式还需自行调试提示词、逐张出图并挑选，实测推理之外的操作时间"
        "按每张 4 分钟计（含 2–3 次试错）。该系数主观性最强，故设为可调主参数。"
    )
    per_frame_confidence: str = "low"

    script_minutes: float = 8.0
    script_basis: str = "按 300 字左右的文案撰写 + 分镜切分，取内容岗经验值 8 分钟。"
    script_confidence: str = "medium"

    audio_minutes: float = 10.0
    audio_basis: str = "6 句对白，含试音、重录与音量校准，取 10 分钟。"
    audio_confidence: str = "low"

    compose_minutes: float = 12.0
    compose_basis: str = "手工对齐每句对白的音画时长并导出，取 12 分钟。"
    compose_confidence: str = "low"

    def assumptions(self) -> List[BaselineAssumption]:
        return [
            BaselineAssumption(
                key="script",
                label=STAGE_LABEL["script"],
                minutes=self.script_minutes,
                basis=self.script_basis,
                confidence=self.script_confidence,
            ),
            BaselineAssumption(
                key="image",
                label=STAGE_LABEL["image"],
                minutes=self.per_frame_minutes * self.frames_per_sample,
                basis=(
                    f"{self.per_frame_minutes} 分钟/张 × {self.frames_per_sample} 张。"
                    f"单张系数依据：{self.per_frame_basis}"
                ),
                confidence=self.per_frame_confidence,
            ),
            BaselineAssumption(
                key="audio",
                label=STAGE_LABEL["audio"],
                minutes=self.audio_minutes,
                basis=self.audio_basis,
                confidence=self.audio_confidence,
            ),
            BaselineAssumption(
                key="compose",
                label=STAGE_LABEL["compose"],
                minutes=self.compose_minutes,
                basis=self.compose_basis,
                confidence=self.compose_confidence,
            ),
        ]

    @property
    def total_minutes(self) -> float:
        return round(sum(a.minutes for a in self.assumptions()), 3)

    def validate(self) -> List[str]:
        """依据缺失或数值不合法时返回问题列表。空列表表示通过。"""
        problems: List[str] = []
        for a in self.assumptions():
            if not a.basis.strip():
                problems.append(f"{a.key}: 缺少 basis，不允许无依据的假设")
            if a.minutes <= 0:
                problems.append(f"{a.key}: minutes={a.minutes} 必须为正")
        if self.frames_per_sample <= 0:
            problems.append("frames_per_sample 必须为正")
        if self.per_frame_minutes <= 0:
            problems.append("per_frame_minutes 必须为正")
        return problems

    def as_dict(self) -> Dict[str, Any]:
        return {
            "frames_per_sample": self.frames_per_sample,
            "frames_basis": self.frames_basis,
            "per_frame_minutes": self.per_frame_minutes,
            "per_frame_basis": self.per_frame_basis,
            "per_frame_confidence": self.per_frame_confidence,
            "assumptions": [a.as_dict() for a in self.assumptions()],
            "total_minutes": self.total_minutes,
        }

    def sensitivity(
        self,
        values: List[float],
        measured_warm_s: float | None = None,
        measured_cold_s: float | None = None,
    ) -> Dict[str, Any]:
        """出图单张系数取不同值时，人工基线与倍数怎么变。

        这是本模块最重要的输出：**把结论对假设的敏感度暴露出来**。
        若倍数在合理区间内大幅波动，就不该对外引用这个倍数。

        `measured_warm_s` / `measured_cold_s` 给出实测稳态与冷启动单张耗时后，
        会额外算出流水线侧的整条样片耗时与倍数。

        ⚠️ 有了这几列，一个事实会立刻显形：**出图阶段倍数 ≈ 单张人工假设 ÷ 实测单张**
        （例：4 分钟 ÷ 14.5 秒 ≈ 16.5）。也就是说这个倍数**几乎完全由假设决定**，
        它不是在证明任何工程效果，只是在做单位换算。把 `image_speedup` 和
        `image_speedup_is_assumption_driven` 一起输出，就是为了不让它被当成战果。
        """
        rows = []
        measured_total_s = None
        if measured_warm_s is not None and measured_cold_s is not None:
            measured_total_s = measured_cold_s + (self.frames_per_sample - 1) * measured_warm_s

        for v in values:
            img_min = v * self.frames_per_sample
            total = self.script_minutes + img_min + self.audio_minutes + self.compose_minutes
            row: Dict[str, Any] = {
                "per_frame_minutes": v,
                "image_minutes": round(img_min, 3),
                "total_minutes": round(total, 3),
            }
            if measured_warm_s:
                row["image_speedup"] = round((v * 60.0) / measured_warm_s, 3)
            if measured_total_s:
                row["total_speedup_image_stage_only"] = round(
                    img_min * 60.0 / measured_total_s, 3
                )
            rows.append(row)

        return {
            "parameter": "per_frame_minutes",
            "rows": rows,
            "measured_warm_s": measured_warm_s,
            "measured_cold_s": measured_cold_s,
            "measured_image_total_s": (
                round(measured_total_s, 3) if measured_total_s else None
            ),
            "image_speedup_is_assumption_driven": True,
            "assumption_dominance_note": (
                "出图阶段的倍数 ≈ 单张人工假设 ÷ 实测单张耗时。"
                "它随假设线性变化，**不构成任何工程效果的证据**。"
                "可信的表述是两侧各自的原值："
                "『流水线稳态单张 X 秒（实测）』与『人工单张 Y 分钟（申报假设，来源见 basis）』。"
            ),
        }


def default_baseline() -> ManualBaseline:
    return ManualBaseline()
