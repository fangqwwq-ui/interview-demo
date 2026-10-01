"""AI 味词典 —— 本项目自研的可解释度量基准。

为什么在已有的内核检测器之外，还要自己维护一份词典：

| | 内核检测器（第三方） | 本词典（自研） |
|---|---|---|
| 输出 | 0–100 分为主的综合分 + 困惑度/突发度 | 命中的具体短语与类别计数 |
| 可解释性 | 分数由内部权重决定，无法逐项追问 | 每条命中都能指出原文的哪几个字 |
| 用途 | 「像不像 AI」的总量判断 | 「哪里像 AI」「改完还剩几处」的定位 |

两者互补。面试里的价值在于：**总分下降 30 分**这句话很难被追问，
但**「改写后仍有 4 处『赋能 / 助力 / 不容忽视』残留」**是可以当场指着屏幕逐条核对的。

词典的三条纪律（避免它退化成拍脑袋的清单）：
  1. 每条都必须能举出真实的中文 AI 高频说法，不收录「我认为听起来像 AI」的表达
  2. 模板类条目用正则，字面类条目用子串 —— 不混用，避免匹配范围不可解释
  3. 词典会被注入到改写提示词里当禁用清单，所以**不能收录正常的书面表达**
     （例如「首先 / 其次」在说明文中是合理的，只作为弱信号标注，不纳入硬禁用）

`count()` 的计数口径：按**命中处数**计，同一短语出现 3 次记 3。
模板类正则用非重叠匹配，因此「不仅…更能…」只记 1 次。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Sequence

# 硬禁用：这些表达几乎没有信息量，出现即认为有 AI 味
HARD_TELLS: List[Dict[str, str]] = [
    # ---- 过渡套话 ----
    {"category": "过渡套话", "pattern": "值得注意的是", "note": "填充式过渡，删掉不影响语义"},
    {"category": "过渡套话", "pattern": "综上所述", "note": "议论文套话"},
    {"category": "过渡套话", "pattern": "总而言之", "note": "议论文套话"},
    {"category": "过渡套话", "pattern": "由此可见", "note": "空洞推论引导"},
    {"category": "过渡套话", "pattern": "不难看出", "note": "空洞推论引导"},
    {"category": "过渡套话", "pattern": "换言之", "note": "更常见的说法是直接说"},
    {"category": "过渡套话", "pattern": "我们知道", "note": "教科书口吻"},
    # ---- 万能动词 ----
    {"category": "万能动词", "pattern": "赋能", "note": "语义空洞，说不出具体做了什么"},
    {"category": "万能动词", "pattern": "助力", "note": "同上"},
    {"category": "万能动词", "pattern": "构筑", "note": "搭配常生硬（构筑防线/构筑体系）"},
    {"category": "万能动词", "pattern": "彰显", "note": "评价性动词，无事实"},
    {"category": "万能动词", "pattern": "秉持", "note": "公文腔"},
    {"category": "万能动词", "pattern": "致力于", "note": "宣传腔，无具体动作"},
    # ---- 空洞评价 ----
    {"category": "空洞评价", "pattern": "至关重要", "note": "程度副词代替论证"},
    {"category": "空洞评价", "pattern": "举足轻重", "note": "同上"},
    {"category": "空洞评价", "pattern": "不容忽视", "note": "同上"},
    {"category": "空洞评价", "pattern": "缺一不可", "note": "同上"},
    {"category": "空洞评价", "pattern": "尤为重要", "note": "同上"},
    {"category": "空洞评价", "pattern": "具有重要意义", "note": "评价代替事实"},
    {"category": "空洞评价", "pattern": "具有深远影响", "note": "评价代替事实"},
    {"category": "空洞评价", "pattern": "相得益彰", "note": "成语式拼凑"},
    {"category": "空洞评价", "pattern": "相辅相成", "note": "成语式拼凑"},
    {"category": "空洞评价", "pattern": "可圈可点", "note": "无具体指向的褒扬"},
    {"category": "空洞评价", "pattern": "前所未有", "note": "夸张且无法验证"},
    {"category": "空洞评价", "pattern": "不二之选", "note": "评测模板残留"},
    {"category": "空洞评价", "pattern": "当之无愧", "note": "评测模板残留"},
    {"category": "空洞评价", "pattern": "脱颖而出", "note": "评测模板残留"},
    {"category": "空洞评价", "pattern": "堪称", "note": "强行拔高"},
    {"category": "空洞评价", "pattern": "诚意之作", "note": "评测模板残留"},
    # ---- 营销话术 ----
    {"category": "营销话术", "pattern": "绝绝子", "note": "短视频话术"},
    {"category": "营销话术", "pattern": "闭眼入", "note": "短视频话术"},
    {"category": "营销话术", "pattern": "拉满", "note": "短视频话术"},
    {"category": "营销话术", "pattern": "家人们", "note": "直播话术"},
    {"category": "营销话术", "pattern": "姐妹们", "note": "直播话术"},
    {"category": "营销话术", "pattern": "强行安利", "note": "直播话术"},
    {"category": "营销话术", "pattern": "宝藏", "note": "被过度使用，已失去区分度"},
    {"category": "营销话术", "pattern": "冲就完事", "note": "短视频话术"},
    {"category": "营销话术", "pattern": "国货之光", "note": "情绪标签取代产品信息"},
    # ---- 育儿套话 ----
    {"category": "育儿套话", "pattern": "用爱浇灌", "note": "抒情代替方法"},
    {"category": "育儿套话", "pattern": "奠定坚实基础", "note": "评价代替方法"},
    {"category": "育儿套话", "pattern": "健全人格", "note": "术语空转"},
]

# 模板句式：正则匹配，覆盖「留白填充」型 AI 句式
HARD_TEMPLATES: List[Dict[str, str]] = [
    {"category": "模板句式", "pattern": r"不仅.{0,14}?更[能要会]", "note": "不仅…更… 递进模板"},
    {"category": "模板句式", "pattern": r"无论[^，。]{0,16}?还是", "note": "无论…还是… 铺陈模板"},
    {"category": "模板句式", "pattern": r"随着[^，。]{0,18}?不断", "note": "随着…不断… 万能开头"},
    {"category": "模板句式", "pattern": r"在(当今|现代|当下)[^，。]{0,14}?(环境|社会|时代|背景|生活)", "note": "在…背景下 万能开头"},
    {"category": "模板句式", "pattern": r"为[^，。]{0,14}?(注入|提供|带来)[^，。]{0,10}?(动力|活力|支持|保障)", "note": "为…注入动力 空洞收尾"},
    {"category": "模板句式", "pattern": r"是[^，。]{0,12}?的(桥梁|基石|源泉|关键|核心要义|重要途径|重要一步|重要因素|核心竞争力)", "note": "…是…的基石 判断式套话"},
    {"category": "模板句式", "pattern": r"让我们[^，。]{0,12}?(守护|拥抱|迎接|共同)", "note": "让我们… 号召式收尾"},
    {"category": "模板句式", "pattern": r"实现[^，。]{0,10}?(提升|跃升|最大化|高质量发展|可持续发展)", "note": "实现…提升 虚指标"},
    {"category": "模板句式", "pattern": r"能够[^，。]{0,12}?(有效|更好|轻松|充分)", "note": "能够有效地… 空转"},
    {"category": "模板句式", "pattern": r"值得(每个人|我们|你)拥有", "note": "营销收尾"},
]

# 弱信号：书面语常见，不作为禁用项，仅在报告中提示
SOFT_SIGNALS: List[Dict[str, str]] = [
    {"category": "弱信号-结构词", "pattern": "首先", "note": "说明文中合理，但密集出现是模板痕迹"},
    {"category": "弱信号-结构词", "pattern": "其次", "note": "同上"},
    {"category": "弱信号-结构词", "pattern": "最后", "note": "同上"},
    {"category": "弱信号-结构词", "pattern": "同时", "note": "同上"},
    {"category": "弱信号-结构词", "pattern": "此外", "note": "同上"},
]

_ALL = HARD_TELLS + HARD_TEMPLATES


@dataclass
class TellFindings:
    count: int
    by_category: Dict[str, int] = field(default_factory=dict)
    hits: List[Dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {"count": self.count, "by_category": self.by_category, "hits": self.hits}


def _compile(entries: Sequence[Dict[str, str]]) -> List[re.Pattern[str]]:
    """⚠️ 只用于**字面**条目。

    模板类条目（HARD_TEMPLATES）存的是正则源码，必须原样编译。
    若对它也调 re.escape，`不仅.{0,14}?更[能要会]` 会被当成字面字符串，
    永远匹配不到 —— 而且不会报错，只会让命中数整体偏低。
    这正是本模块初版踩过的坑。
    """
    return [re.compile(re.escape(e["pattern"])) for e in entries]


def _compile_regex(entries: Sequence[Dict[str, str]]) -> List[re.Pattern[str]]:
    """用于模板类条目：pattern 本身就是正则。"""
    return [re.compile(e["pattern"]) for e in entries]


_HARD_RE: List[tuple[Dict[str, str], re.Pattern[str]]] = [
    *zip(HARD_TELLS, _compile(HARD_TELLS)),
    *zip(HARD_TEMPLATES, _compile_regex(HARD_TEMPLATES)),
]
_SOFT_RE = list(zip(SOFT_SIGNALS, _compile(SOFT_SIGNALS)))


def count(text: str, *, include_soft: bool = False, max_hits: int = 20) -> TellFindings:
    """统计 AI 味命中处数。

    口径：按命中处数计（同短语出现 3 次记 3），非重叠匹配。
    """
    src = text or ""
    count_total = 0
    by_cat: Dict[str, int] = {}
    hits: List[Dict[str, Any]] = []

    for entry, rx in _HARD_RE:
        found = rx.findall(src)
        if not found:
            continue
        n = len(found)
        count_total += n
        by_cat[entry["category"]] = by_cat.get(entry["category"], 0) + n
        if len(hits) < max_hits:
            hits.append(
                {
                    "category": entry["category"],
                    "matched": entry["pattern"],
                    "count": n,
                    "note": entry["note"],
                }
            )

    if include_soft:
        for entry, rx in _SOFT_RE:
            n = len(rx.findall(src))
            if not n:
                continue
            by_cat[entry["category"]] = by_cat.get(entry["category"], 0) + n

    hits.sort(key=lambda h: -h["count"])
    return TellFindings(count=count_total, by_category=by_cat, hits=hits)


def banned_phrases(limit: int = 30) -> List[str]:
    """给改写提示词用的禁用清单（只取字面条目，正则不便于自然语言表述）。"""
    return [e["pattern"] for e in HARD_TELLS][:limit]


def template_examples(limit: int = 6) -> List[Dict[str, str]]:
    """给提示词用的模板句式示例（用人类可读的写法描述正则）。"""
    readable = {
        r"不仅.{0,14}?更[能要会]": "不仅……更能……",
        r"无论[^，。]{0,16}?还是": "无论是……还是……",
        r"随着[^，。]{0,18}?不断": "随着……不断……",
        r"在(当今|现代|当下)[^，。]{0,14}?(环境|社会|时代|背景|生活)": "在当今……的环境下",
        r"为[^，。]{0,14}?(注入|提供|带来)[^，。]{0,10}?(动力|活力|支持|保障)": "为……注入……动力",
        r"是[^，。]{0,12}?的(桥梁|基石|源泉|关键|核心要义|重要途径|重要一步|重要因素|核心竞争力)": "……是……的基石",
        r"让我们[^，。]{0,12}?(守护|拥抱|迎接|共同)": "让我们……",
        r"实现[^，。]{0,10}?(提升|跃升|最大化|高质量发展|可持续发展)": "实现……提升",
        r"能够[^，。]{0,12}?(有效|更好|轻松|充分)": "能够有效地……",
        r"值得(每个人|我们|你)拥有": "值得每个人拥有",
    }
    out: List[Dict[str, str]] = []
    for e in HARD_TEMPLATES[:limit]:
        out.append({"category": e["category"], "readable": readable.get(e["pattern"], e["pattern"]), "note": e["note"]})
    return out


def stats() -> Dict[str, Any]:
    cats: Dict[str, int] = {}
    for e in _ALL:
        cats[e["category"]] = cats.get(e["category"], 0) + 1
    return {
        "hard_literal": len(HARD_TELLS),
        "hard_template": len(HARD_TEMPLATES),
        "soft_signals": len(SOFT_SIGNALS),
        "by_category": cats,
    }
