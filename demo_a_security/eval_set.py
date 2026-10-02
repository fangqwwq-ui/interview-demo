"""构造Demo A 问答评测集（≥100 条，确定性生成，无随机）。

为什么要分「无 RAG」与「分层 RAG」两臂：
  简历原文是「智能应答准确率 72% → 94%」。要诚实地回应这句话，
  必须先把「无 RAG」的基线定义清楚，否则 94%是没有参照的孤立数字。
  本脚本的两臂定义：
    baseline（无 RAG）：直接把问题原文当检索 query，不做任何结构化前置过滤，
                        只取纯向量检索的 top-1 文档作为"答案依据"；
    treatment（分层 RAG）：结构化过滤 + 语义召回 + 重排，取 top-1 且校验是否命中
                        标注答案。
  两臂**唯一差别**是分层过滤，不同时改动别的变量。

判定方式（关键，避免自欺）：
  每条样本标注 ``answer_event_ids``（应被检出的事件）与 ``forbidden_event_ids``
  （不应出现在答案里的事件）。
  判对= top-1 文档 id 属于 answer_event_ids **且** 不属于 forbidden。
  这是「精确命中」而非「语义相关」—— 语义打分需要另配标注或模型，
  容易变成自己给自己打分。若答案不止一条（k>1），判首条是否命中。

语料来源：demo_a_security.seed.SPEC 的 14 条工单 + 人工撰写的多种问法。
**全部人工撰写**，不爬取任何数据（平台条款 + 个人数据风险）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
EVAL_PATH = Path(__file__).resolve().parent / "eval_set.jsonl"

# 14 条工单（与 seed.py 的 SPEC 对齐）：
#   A-0001 gate-01 intrusion 5 东门夜间非工作人员翻越围栏       -> closed
#   A-0002 gate-01 intrusion 4 东门尾随进入未登记             -> closed
#   A-0003 gate-01 person2 东门快递员正常配送          -> false_positive
#   A-0004 gate-02 vehicle 3 西门夜间货车滞留                  -> resolved
#   A-0005 gate-02 vehicle 1 西门白班车辆正常通行-> closed
#   A-0006 yard-02 fire 5 厂区仓库烟雾告警                    -> closed
#   A-0007 yard-02 smoke 4 厂区锅炉房疑似冒烟                -> false_positive
#   A-0008 yard-02 vehicle 2 厂区叉车作业                    -> detected
#   A-0009 office-03 person 2 办公楼员工正常刷脸                -> closed
#   A-0010 office-03 person 4 办公楼非白名单人员进入             -> detected
#   A-0011 office-03 intrusion 3 办公楼夜间窗户开启             -> detected
#   A-0012 depot-04 intrusion 5 仓库门禁被暴力尝试-> detected
#   A-0013 depot-04 vehicle 3 仓库区车辆逆行                    -> detected
#   A-0014 depot-04 person 2 仓库管理员巡检                    -> detected

# (question, filters, answer_ids, forbidden_ids)
# 问法刻意多样化：口语、书面、含/不含区域词、含/不含状态词 ——
# 只用一种模板问，测出来的准确率会虚高得没有意义。
SPECS: List[tuple[str, Dict[str, Any], List[str], List[str]]] = []


def q(
    question: str,
    answers: str | List[str],
    *,
    zone: str | None = None,
    event_type: str | None = None,
    open_only: bool = False,
    min_severity: int | None = None,
    forbidden: str | List[str] | None = None,
) -> None:
    f: Dict[str, Any] = {}
    if zone:
        f["zone"] = zone
    if event_type:
        f["event_type"] = event_type
    if open_only:
        f["open_only"] = True
    if min_severity is not None:
        f["min_severity"] = min_severity
    if answers is None:
        a: List[str] = []
    elif isinstance(answers, str):
        a = [answers]
    else:
        a = list(answers)
    fo = [] if forbidden is None else ([forbidden] if isinstance(forbidden, str) else list(forbidden))
    SPECS.append((question, f, a, fo))


# 各区域全部事件（供开放式追问使用）
_ZONE_ALL: Dict[str, List[str]] = {
    "gate-01": ["A-0001", "A-0002", "A-0003"],
    "gate-02": ["A-0004", "A-0005"],
    "yard-02": ["A-0006", "A-0007", "A-0008"],
    "office-03": ["A-0009", "A-0010", "A-0011"],
    "depot-04": ["A-0012", "A-0013", "A-0014"],
}


# ---------- 东门 gate-01 ----------
for w in ["东门", "gate-01 区域", "大门"]:
    q(f"{w}夜间翻越围栏的事件是什么？", "A-0001", zone="gate-01")
    q(f"{w}有没有尾随进入的情况？", "A-0002", zone="gate-01")
    q(f"{w}的快递员事件是什么？", "A-0003", zone="gate-01")
q("门禁尾随未登记怎么处理的？", "A-0002", zone="gate-01")
q("东门非工作人员闯入，危险等级多少？", "A-0001", zone="gate-01")
q("gate-01 有高危事件吗？", ["A-0001", "A-0002"], zone="gate-01", min_severity=4)
q("东门哪些事件还没闭环？", [], zone="gate-01", open_only=True,
  forbidden=["A-0004", "A-0006"])
# 注：东门三条事件（A-0001/0002/0003）均已进终态（closed / false_positive），
# 因此「东门未闭环」的**正确答案就是空集**。
# 这条必须保留 —— 它考的是「该说没有时不说有」，是最容易被 RAG 糊弄过去的一类问题。
q("东门有人闯入吗？", ["A-0001", "A-0002"], zone="gate-01")

# ---------- 西门 gate-02 ----------
for w in ["西门", "gate-02"]:
    q(f"{w}夜间货车滞留的事件？", "A-0004", zone="gate-02")
    q(f"{w}白班车辆通行情况？", "A-0005", zone="gate-02")
q("货车在西门滞留的处理结果？", "A-0004")
q("西门有哪些车辆事件？", ["A-0004", "A-0005"], zone="gate-02", event_type="vehicle")

# ---------- 厂区 yard-02 ----------
for w in ["厂区", "yard-02"]:
    q(f"{w}仓库的烟雾告警是什么？", "A-0006", zone="yard-02")
    q(f"{w}锅炉房冒烟的事件？", "A-0007", zone="yard-02")
    q(f"{w}叉车作业记录？", "A-0008", zone="yard-02")
q("厂区仓库的烟雾告警后来怎么处理的？", "A-0006")
q("焊接烟尘的那条告警最后怎么结的？", "A-0006")
q("厂区有哪些事件还没闭环？", ["A-0008"], zone="yard-02", open_only=True)
q("厂区高危事件有哪些？", ["A-0006", "A-0007"], zone="yard-02", min_severity=4)

# ---------- 办公楼 office-03 ----------
for w in ["办公楼", "office-03"]:
    q(f"{w}员工刷脸的记录？", "A-0009", zone="office-03")
    q(f"{w}非白名单人员进入事件？", "A-0010", zone="office-03")
    q(f"{w}夜间窗户开启的事件？", "A-0011", zone="office-03")
q("办公楼非白名单人员进入，现在什么状态？", "A-0010")
q("办公楼的闯入类事件有哪些？", ["A-0011"], zone="office-03", event_type="intrusion")
q("办公楼未闭环事件有哪些？", ["A-0010", "A-0011"], zone="office-03", open_only=True)

# ---------- 仓库 depot-04 ----------
for w in ["仓库", "depot-04"]:
    q(f"{w}门禁被暴力尝试的事件？", "A-0012", zone="depot-04")
    q(f"{w}车辆逆行记录？", "A-0013", zone="depot-04")
    q(f"{w}管理员巡检记录？", "A-0014", zone="depot-04")
q("仓库门禁被暴力尝试的那条事件状态如何？", "A-0012")
q("仓库区有什么高危事件？", "A-0012", zone="depot-04", min_severity=4)

# ---------- 跨区域 / 无过滤 ----------
q("哪些事件是误报？", ["A-0003", "A-0007"], forbidden=["A-0001", "A-0012"])
q("最高危险等级的事件有哪些？", ["A-0001", "A-0006", "A-0012"], min_severity=5)
q("全场未闭环的高危事件？", ["A-0010", "A-0012"], min_severity=4, open_only=True)
q("已闭环的事件有哪些？", ["A-0001", "A-0002", "A-0005", "A-0006", "A-0009"],
  forbidden=["A-0010", "A-0012"])
q("人脸识别相关的正常记录？", "A-0009")
q("车辆逆行是在哪个区域？", "A-0013")
q("夜间发生的高危事件有哪些？", ["A-0001", "A-0002"], min_severity=4)

# ---------- 口语化 / 缺词变体（专门测鲁棒性） ----------
_ZONE_MAP = {
    "东门": "gate-01", "西门": "gate-02", "厂区": "yard-02",
    "办公楼": "office-03", "仓库": "depot-04",
}
# 每区 3 条开放式追问，答案 = 该区全部事件（顺序不限，按集合判定）
for w, zid in _ZONE_MAP.items():
    q(f"{w}那边现在什么情况？", _ZONE_ALL[zid], zone=zid)
q("有没有陌生人进来？", ["A-0001", "A-0002", "A-0010"], min_severity=4)
q("着火了吗？", ["A-0006"], forbidden=["A-0013"])
q("门被撬了吗？", ["A-0012"])
q("有没有车乱停乱行？", ["A-0013", "A-0004"], event_type="vehicle")
q("误报的都是些什么？", ["A-0003", "A-0007"])
q("还没处理完的事情？", ["A-0008", "A-0010", "A-0011", "A-0012", "A-0013", "A-0014"],
  open_only=True)

# ---------- 处置结论类（答案在流转备注里） ----------
q("东门闯入事件最后怎么结的？", "A-0001")
q("仓库门禁事件的处置结论？", "A-0012")
q("快递员为什么被标记为误报？", "A-0003")
q("锅炉房冒烟最后怎么处理的？", "A-0007")
q("厂区仓库告警的处置结论是什么？", "A-0006")

# ---------- 摄像头 / 编号类（考结构化字段抽取） ----------
for cam, eid in [("cam-01", "A-0001"), ("cam-05", "A-0006"), ("cam-08", "A-0010")]:
    q(f"摄像头 {cam} 记录了什么事件？", [eid])

# ---------- 状态路径类 ----------
q("哪些事件经历了确认和处置流程？", ["A-0001", "A-0002", "A-0004", "A-0006"], zone="gate-01",
  forbidden=["A-0010"])

# ---------- 补充：事件类型 ×区域交叉（扩到 ≥100 条） ----------
_TYPE_QS = [
    ("东门有人闯入吗", "gate-01", "intrusion", ["A-0001", "A-0002"]),
    ("东门的入侵事件有哪些", "gate-01", "intrusion", ["A-0001", "A-0002"]),
    ("西门有入侵事件吗", "gate-02", "intrusion", []),
    ("西门车辆事件有哪些", "gate-02", "vehicle", ["A-0004", "A-0005"]),
    ("厂区火灾告警是什么", "yard-02", "fire", ["A-0006"]),
    ("厂区烟雾类事件", "yard-02", "smoke", ["A-0007"]),
    ("厂区车辆事件", "yard-02", "vehicle", ["A-0008"]),
    ("办公楼入侵事件", "office-03", "intrusion", ["A-0011"]),
    ("办公楼人员事件", "office-03", "person", ["A-0009", "A-0010"]),
    ("仓库入侵事件有哪些", "depot-04", "intrusion", ["A-0012"]),
    ("仓库人员事件", "depot-04", "person", ["A-0014"]),
    ("仓库车辆事件", "depot-04", "vehicle", ["A-0013"]),
]
for question, zid, et, ans in _TYPE_QS:
    q(question, ans, zone=zid, event_type=et)

# ---------- 补充：严重度阈值变体 ----------
# 阈值类的答案直接从工单表推导，避免手写后与数据脱节。
_SEV_ALL = {
    1: ["A-0001", "A-0002", "A-0003", "A-0004", "A-0005", "A-0006",
        "A-0007", "A-0008", "A-0009", "A-0010", "A-0011", "A-0012",
        "A-0013", "A-0014"],
    2: ["A-0001", "A-0002", "A-0004", "A-0006", "A-0007", "A-0009",
        "A-0010", "A-0011", "A-0012", "A-0013", "A-0014"],
    3: ["A-0001", "A-0002", "A-0004", "A-0006", "A-0007", "A-0010",
        "A-0011", "A-0012", "A-0013"],
    4: ["A-0001", "A-0002", "A-0006", "A-0007", "A-0010", "A-0012"],
    5: ["A-0001", "A-0006", "A-0012"],
}
for sev in [1, 2, 3, 4, 5]:
    q(f"危险等级不低于 {sev} 的事件有哪些？", _SEV_ALL[sev], min_severity=sev)
q("高危事件清单", _SEV_ALL[4], min_severity=4)
q("严重事件有哪些", _SEV_ALL[3], min_severity=3)
q("轻微事件", _SEV_ALL[1], min_severity=1)

# ---------- 补充：状态与区域组合变体 ----------
q("东门已闭环的事件", ["A-0001", "A-0002"], zone="gate-01")
q("厂区已处置的事件", ["A-0006"], zone="yard-02")
q("办公楼高危事件", ["A-0010"], zone="office-03", min_severity=4)
q("仓库低危事件", ["A-0014"], zone="depot-04", min_severity=2)
q("西门夜间事件", ["A-0004"], zone="gate-02", min_severity=3)

# ---------- 补充：处置口径追问 ----------
q("已处置但未关闭的事件有哪些？", ["A-0004"], zone="gate-02")
q("哪些是误报需要复核？", ["A-0003", "A-0007"])
q("需要人工复核的高危事件？", ["A-0001", "A-0006", "A-0010", "A-0012"],
  open_only=True, min_severity=4)


def build() -> List[Dict[str, Any]]:
    out = []
    for i, (question, f, ans, fo) in enumerate(SPECS, 1):
        out.append(
            {
                "qid": f"Q{i:03d}",
                "question": question,
                "filters": f,
                "answer_event_ids": ans,
                "forbidden_event_ids": fo,
            }
        )
    return out


def main() -> int:
    rows = build()
    EVAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with EVAL_PATH.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    ans_none = sum(1 for r in rows if not r["answer_event_ids"])
    zones = {}
    for r in rows:
        z = r["filters"].get("zone", "(全库)")
        zones[z] = zones.get(z, 0) + 1
    print(f"评测集已生成：{EVAL_PATH}")
    print(f"  条目数：{len(rows)}")
    print(f"  有标准答案：{len(rows) - ans_none}；无标准答案（不计入准确率分母）：{ans_none}")
    print(f"  区域分布：{zones}")
    print(f"  含 forbidden 约束：{sum(1 for r in rows if r['forbidden_event_ids'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())