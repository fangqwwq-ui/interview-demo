"""审核层的**规则通道**：确定性、可逐条指认、零依赖。

## 为什么规则通道必须存在，而不是全靠 LLM

1. **确定性**：同一条内容跑多少次结论都一样，可复现、可做回归。
   LLM 做不到这一点，所以不能让 LLM 独占审核结论。
2. **可指认**：每条命中都给出「规则 ID + 匹配片段 + 上下文」，
   面试时可当场追问「为什么判它有风险」，而不是只能说「模型觉得」。
3. **成本前置**：明显违规的内容不该消耗一次 LLM 调用。

## 诚实边界（这几条会被追问，必须主动交代）

- 内置词表是**演示级小型词表**（见 `LEXICON`），不是生产合规词库。
- 词表**不包含涉政敏感类**。该类依赖监管机构下发的动态词库，本项目既不具备
  也不应内置；生产环境必须外接词库服务。这一点写进报告，不假装覆盖了。
- C6 是**启发式**，只给警告、不给阻断。依据是实测的字节/像素比分离度（见下）。
- 规则通道测的是「有没有命中已知模式」，**不等于「内容是否合规」**。
  合规结论必须有人工终审；本模块做的是**初筛分流**，不是终审。

## 关于 C6 阈值的实测依据

同一台机器上实测：

| 样本 | 字节/像素 |
|---|---|
| ComfyUI 真实出图 832x1216 | 1.433 |
| ComfyUI 真实出图 512x512 | 1.174 |
| 纯色占位 PNG 512x512（`data/fixtures/placeholder_512.png`） | 0.0057 |

分离度约 250 倍，所以 `PLACEHOLDER_BPP = 0.10` 落在两个量级之间，
不是拍脑袋定的数。夹具文件已入库，可现场重算。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from common import config
from common.metrics import text_metrics
from demo_c_manju.imgsize import bytes_per_pixel, image_size

# ------------------------------------------------------------ 严重度

SEVERITY_BLOCK = "block"  # 直接阻断，必须人工处理
SEVERITY_WARN = "warn"    # 需人工复核

# ------------------------------------------------------------ C6 阈值

# 实测分离度约 250 倍（真实出图 1.17~1.43，纯色占位 0.0057），取中间量级
PLACEHOLDER_BPP = 0.10

# ------------------------------------------------------------ 演示词表

# ⚠️ 演示级词表。**不含涉政敏感类** —— 该类必须外接监管词库，理由见模块文档。
LEXICON: Dict[str, List[str]] = {
    "violence": ["血腥", "残肢", "断肢", "血肉横飞", "砍杀", "虐杀", "自杀", "割腕", "爆头"],
    "adult": ["色情", "裸露", "性暗示", "艳照", "床上戏"],
    "illegal": ["毒品", "冰毒", "枪支", "管制刀具", "赌博", "洗钱", "走私"],
}

# 词表**未覆盖**的类别，必须在报告里显式声明为缺口
LEXICON_GAPS: List[str] = [
    "涉政敏感类（需外接监管机构下发的动态词库，本项目不含）",
    "变体/谐音/拼音/emoji 绕过（需专门的对抗归一化模块，本项目不含）",
]

# ------------------------------------------------------------ 规则清单

RULES: List[Dict[str, str]] = [
    {
        "id": "C1_banned_term",
        "severity": SEVERITY_BLOCK,
        "checks": "脚本与提示词是否命中演示词表",
        "why": "合规兜底。命中即阻断，成本最低的一道闸门。",
    },
    {
        "id": "C2_empty_or_tiny_line",
        "severity": SEVERITY_WARN,
        "checks": "分镜对白是否存在空对白 / 单字对白",
        "why": "空对白会让配音阶段合成出无声片段，属于上游可拦截的缺陷。",
    },
    {
        "id": "C3_grammar_anomaly",
        "severity": SEVERITY_WARN,
        "checks": "脚本是否存在可机械判定的中文语法异常",
        "why": "复用文本质量规则集，拦截被改写或拼接损坏的脚本。",
    },
    {
        "id": "C4_asset_broken",
        "severity": SEVERITY_BLOCK,
        "checks": "出图资产是否存在、是否可解析为合法 PNG/JPEG",
        "why": "损坏资产会一路带到合成阶段才炸，越早拦越省时间。",
    },
    {
        "id": "C5_prompt_script_mismatch",
        "severity": SEVERITY_WARN,
        "checks": "有对白的角色是否进入出图提示词（支持 character_tags 映射；纯标签提示词记为无法校验）",
        "why": "角色没进提示词，图里就不会有这个人 —— 分镜与画面语义脱节。",
    },
    {
        "id": "C6_placeholder_image",
        "severity": SEVERITY_WARN,
        "checks": "资产是否疑似占位图 / 纯色图（字节像素比启发式）",
        "why": "防止 Pillow 占位图混进成片被当成真实出图交付。",
    },
]

_RULE_INDEX = {r["id"]: r for r in RULES}

# 对白行：「角色名：『对白』」/ 「角色名: "对白"」
_LINE_RE = re.compile(r"^\s*([^：:\n]{1,16})[：:]\s*[「\"'『]?(.*?)[」\"'』]?\s*$")


# ------------------------------------------------------------ 数据结构


@dataclass
class RuleFinding:
    rule: str
    severity: str
    matched: str
    context: str
    detail: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "rule": self.rule,
            "severity": self.severity,
            "matched": self.matched,
            "context": self.context,
            "detail": self.detail,
        }


@dataclass
class RuleOutcome:
    item_id: str
    findings: List[RuleFinding] = field(default_factory=list)
    # 「这条规则在本样本上无法给出结论」的记录。
    # 与 findings 严格区分：无结论 != 合格。若把无法校验当成通过，
    # 报告里的通过率会被虚高，这正是本项目要避免的。
    unverifiable: List[Dict[str, str]] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return any(f.severity == SEVERITY_BLOCK for f in self.findings)

    @property
    def flagged(self) -> bool:
        return bool(self.findings)

    @property
    def by_rule(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for f in self.findings:
            out[f.rule] = out.get(f.rule, 0) + 1
        return out

    def as_dict(self) -> Dict[str, Any]:
        return {
            "item_id": self.item_id,
            "flagged": self.flagged,
            "blocked": self.blocked,
            "by_rule": self.by_rule,
            "findings": [f.as_dict() for f in self.findings],
            "unverifiable": self.unverifiable,
        }


# ------------------------------------------------------------ 辅助


def _ctx(text: str, start: int, end: int, pad: int = 8) -> str:
    return text[max(0, start - pad) : min(len(text), end + pad)]


def parse_dialogue_lines(script: str) -> List[Dict[str, str]]:
    """从脚本里抽出「角色名 + 对白」行。

    只认形如 `角色：对白` 的行。这是本项目对分镜脚本的**约定格式**，
    不是通用中文剧本解析器 —— 换句话说，格式不合规的行会被本函数忽略，
    而不是被误判。这一点在报告里作为边界声明。
    """
    rows: List[Dict[str, str]] = []
    for raw in (script or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _LINE_RE.match(line)
        if not m:
            continue
        rows.append({"speaker": m.group(1).strip(), "dialogue": m.group(2).strip()})
    return rows


def resolve_asset_path(rel: Optional[str]) -> Optional[Path]:
    """把批次里记录的相对路径解析成磁盘路径。

    只允许指向**本仓库内部**；绝对路径与上跳路径一律拒绝。
    这条限制和门户的源码白名单是同一个思路：不给越界读取留面。
    """
    if not rel:
        return None
    candidate = Path(rel)
    if candidate.is_absolute():
        return None
    root = config.paths.root.resolve()
    target = (root / candidate).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None
    return target


# ------------------------------------------------------------ 各规则实现


def _c1_banned_term(item_id: str, script: str, prompt: str) -> List[RuleFinding]:
    out: List[RuleFinding] = []
    for category, terms in LEXICON.items():
        for term in terms:
            for label, text in (("script", script or ""), ("prompt", prompt or "")):
                idx = text.find(term)
                if idx < 0:
                    continue
                out.append(
                    RuleFinding(
                        rule="C1_banned_term",
                        severity=SEVERITY_BLOCK,
                        matched=term,
                        context=_ctx(text, idx, idx + len(term)),
                        detail=f"{label} 命中词表类别 {category}",
                    )
                )
    return out


def _c2_empty_or_tiny_line(item_id: str, script: str) -> List[RuleFinding]:
    out: List[RuleFinding] = []
    for row in parse_dialogue_lines(script):
        d = row["dialogue"]
        if not d:
            out.append(
                RuleFinding(
                    rule="C2_empty_or_tiny_line",
                    severity=SEVERITY_WARN,
                    matched=f"{row['speaker']}：（空）",
                    context=f"{row['speaker']}：",
                    detail="空对白，配音阶段会产出无声片段",
                )
            )
        elif len(d) <= 1:
            out.append(
                RuleFinding(
                    rule="C2_empty_or_tiny_line",
                    severity=SEVERITY_WARN,
                    matched=f"{row['speaker']}：{d}",
                    context=f"{row['speaker']}：{d}",
                    detail=f"对白仅 {len(d)} 字，疑似占位或截断",
                )
            )
    return out


def _c3_grammar_anomaly(item_id: str, script: str) -> List[RuleFinding]:
    res = text_metrics.scan_grammar_anomalies(script or "")
    return [
        RuleFinding(
            rule="C3_grammar_anomaly",
            severity=SEVERITY_WARN,
            matched=str(s.get("matched", "")),
            context=str(s.get("context", "")),
            detail=f"命中 {s.get('rule')}",
        )
        for s in res.samples
    ]


def _c4_asset_broken(item_id: str, asset: Optional[str]) -> List[RuleFinding]:
    """校验**已声明的**出图资产是否可用。

    调用方保证只在 `asset is not None` 时进来。理由：
    「本条内容不含出图资产」与「声明了资产但它是坏的」是两件事。
    前者不该被拦，后者必须被拦。若把前者也判为不合格，
    纯文本分镜条目会被大量误报。
    资产**完整性覆盖率**由批次级检查负责，不在这条规则里。
    """
    path = resolve_asset_path(asset)
    if path is None:
        return [
            RuleFinding(
                rule="C4_asset_broken",
                severity=SEVERITY_BLOCK,
                matched=str(asset),
                context=str(asset),
                detail="资产路径越界或为绝对路径，已拒绝读取",
            )
        ]
    if not path.exists():
        return [
            RuleFinding(
                rule="C4_asset_broken",
                severity=SEVERITY_BLOCK,
                matched=str(asset),
                context=str(asset),
                detail="资产文件不存在",
            )
        ]
    blob = path.read_bytes()
    if not blob:
        return [
            RuleFinding(
                rule="C4_asset_broken",
                severity=SEVERITY_BLOCK,
                matched=str(asset),
                context="0 bytes",
                detail="资产为空文件",
            )
        ]
    if image_size(blob) is None:
        return [
            RuleFinding(
                rule="C4_asset_broken",
                severity=SEVERITY_BLOCK,
                matched=str(asset),
                context=f"首 8 字节 {blob[:8]!r}",
                detail="文件头不是合法 PNG/JPEG",
            )
        ]
    return []


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in (text or ""))


def _c5_prompt_script_mismatch(
    item_id: str,
    script: str,
    prompt: str,
    character_tags: Optional[Dict[str, str]] = None,
) -> tuple[List[RuleFinding], List[Dict[str, str]]]:
    """校验「有对白的角色是否进入出图提示词」。

    ⚠️ 本规则有一个真实的适用边界，必须先说清楚再实现：

    真实流水线的出图提示词是 **英文 Danbooru 标签**（`animagine-xl-4.0` 的体系），
    里面**不会**出现中文角色名 —— 例如提示词写 `1girl, school uniform`，
    而脚本写 `小雨：「...」`。此时用字符包含比角色名，**必然误报**。

    因此分三种情形处理，而不是一律判不合格：

    | 情形 | 处理 |
    |---|---|
    | 批次提供了 `character_tags` 映射 | 用映射后的标签比对（可校验） |
    | 提示词里直接出现角色名 | 通过 |
    | 提示词**完全不含中文**（纯标签） | 记为 unverifiable，**不判不合格** |
    | 其余 | 判为命中 |

    把「无法校验」单列出来，是为了让报告能诚实写出
    「C5 在这批样本里有 N 条根本没验得了」，而不是把它伪装成通过。
    """
    if not prompt:
        return [], []

    speakers = {row["speaker"] for row in parse_dialogue_lines(script) if row["speaker"]}
    speakers = {s for s in speakers if s not in {"旁白", "独白", "画外音", "Narrator"}}
    if not speakers:
        return [], []

    mapping = {k: str(v) for k, v in (character_tags or {}).items()}
    low_prompt = prompt.lower()

    missing: List[str] = []
    unverifiable: List[Dict[str, str]] = []

    for name in sorted(speakers):
        if name in prompt:
            continue
        tag = mapping.get(name)
        if tag:
            if tag.lower() in low_prompt:
                continue
            missing.append(name)
            continue
        if not _has_cjk(prompt):
            # 纯标签提示词：字符匹配在此失效，如实记为无法校验
            unverifiable.append(
                {
                    "rule": "C5_prompt_script_mismatch",
                    "speaker": name,
                    "reason": "提示词为纯英文标签，未提供 character_tags 映射，字符匹配不适用",
                    "advice": f"在批次里补 character_tags: {{\"{name}\": \"<对应标签>\"}} 即可校验",
                }
            )
            continue
        missing.append(name)

    findings: List[RuleFinding] = []
    if missing:
        findings.append(
            RuleFinding(
                rule="C5_prompt_script_mismatch",
                severity=SEVERITY_WARN,
                matched=", ".join(missing),
                context=f"prompt={prompt[:80]}",
                detail=f"{len(missing)} 个有对白的角色未出现在出图提示词中",
            )
        )
    return findings, unverifiable


def _c6_placeholder_image(item_id: str, asset: Optional[str]) -> List[RuleFinding]:
    path = resolve_asset_path(asset)
    if path is None or not path.exists():
        return []  # C4 已负责报告缺失/越界，避免重复命中
    bpp = bytes_per_pixel(path.read_bytes())
    if bpp is None or bpp >= PLACEHOLDER_BPP:
        return []
    return [
        RuleFinding(
            rule="C6_placeholder_image",
            severity=SEVERITY_WARN,
            matched=f"{bpp} B/px",
            context=f"阈值 {PLACEHOLDER_BPP}；真实出图实测 1.17~1.43",
            detail="疑似占位图/纯色图（启发式，非判定性证据）",
        )
    ]


# ------------------------------------------------------------ 入口


def scan_item_rules(
    item_id: str,
    *,
    script: str = "",
    prompt: str = "",
    asset: Optional[str] = None,
    character_tags: Optional[Dict[str, str]] = None,
    rules: Optional[Sequence[str]] = None,
) -> RuleOutcome:
    """对单条内容跑规则通道。

    `rules` 可指定只跑其中几条（用于消融实验：证明某条规则是否真的在起作用）。
    """
    enabled = set(rules) if rules else {r["id"] for r in RULES}
    findings: List[RuleFinding] = []
    unverifiable: List[Dict[str, str]] = []

    if "C1_banned_term" in enabled:
        findings += _c1_banned_term(item_id, script, prompt)
    if "C2_empty_or_tiny_line" in enabled:
        findings += _c2_empty_or_tiny_line(item_id, script)
    if "C3_grammar_anomaly" in enabled:
        findings += _c3_grammar_anomaly(item_id, script)
    if "C4_asset_broken" in enabled and asset is not None:
        findings += _c4_asset_broken(item_id, asset)
    if "C5_prompt_script_mismatch" in enabled:
        c5_findings, c5_unverifiable = _c5_prompt_script_mismatch(
            item_id, script, prompt, character_tags
        )
        findings += c5_findings
        unverifiable += c5_unverifiable
    if "C6_placeholder_image" in enabled:
        findings += _c6_placeholder_image(item_id, asset)

    return RuleOutcome(item_id=item_id, findings=findings, unverifiable=unverifiable)


def rule_provenance() -> Dict[str, Any]:
    """规则通道的来源声明，供口径页与报告引用。"""
    return {
        "self_built": True,
        "rules": [{"id": r["id"], "severity": r["severity"], "checks": r["checks"]} for r in RULES],
        "lexicon": {
            "categories": {k: len(v) for k, v in LEXICON.items()},
            "total_terms": sum(len(v) for v in LEXICON.values()),
            "tier": "演示级（非生产合规词库）",
            "gaps": LEXICON_GAPS,
        },
        "placeholder_threshold_bpp": PLACEHOLDER_BPP,
        "unverifiable_semantics": (
            "「无法校验」与「合格」严格区分并分列上报。"
            "C5 在纯英文标签提示词上无法用字符匹配校验角色，此时记为 unverifiable 而非通过；"
            "C4 只对**已声明**的资产生效，未声明资产不计入不合格（覆盖率单独统计）。"
        ),
        "reused": {
            "C3 语法规则集": "common/metrics/text_metrics.py（Demo B 同一套，本项目自建）",
            "资产头解析": "demo_c_manju/imgsize.py（本项目自建，零依赖）",
        },
    }
