"""组件来源声明 —— 三分类账本。

本项目的对外口径建立在一条硬规则上：
**任何一项能力都必须能归入「第三方开源 / 既有自研 / 本次新增」三类之一，且不得越界声称。**

这个文件就是那本账。门户的来源声明区直接读它。

## 三分类的含义

| 类别 | 含义 | 对外能否说「我做的」 |
|---|---|---|
| `third_party` | 外部开源组件 | ❌ 只能说「我评估并选用了它」，必须附 License 与 commit |
| `existing` | 本人既有的其他项目（不在本仓库） | ⚠️ 可以说「我此前实现的」，但**不得说成本次新增** |
| `new` | 本次在本仓库新写的 | ✅ 可以说自研 |

## 关于 ComfyUI 的 GPL-3.0

ComfyUI 是 GPL-3.0。本项目**只把它当独立服务通过 HTTP 调用**
（`127.0.0.1:8188`），不链接其源码、不修改其文件、不再分发其代码。
这一点在演示被问到「GPL 传染性怎么办」时必须能答清楚：
**独立进程 + HTTP 边界 + 非再分发**，因此本仓库代码不因此被要求 GPL。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional

from common import config

KIND_THIRD = "third_party"
KIND_EXISTING = "existing"
KIND_NEW = "new"

KIND_LABEL = {
    KIND_THIRD: "第三方开源",
    KIND_EXISTING: "既有自研（不在本仓库）",
    KIND_NEW: "本次新增",
}


@dataclass
class Component:
    name: str
    kind: str
    role: str
    license: str
    source: str
    boundary: str = ""       # 使用边界 / 不得越界声称的部分
    verify: str = ""         # 怎么当场核实

    def as_dict(self) -> dict:
        d = asdict(self)
        d["kind_label"] = KIND_LABEL.get(self.kind, self.kind)
        return d


COMPONENTS: List[Component] = [
    # ---------------- 第三方开源 ----------------
    Component(
        name="humanize-chinese",
        kind=KIND_THIRD,
        role="Demo B 的中文 AI 痕迹**检测**内核（打分 + 分类明细）",
        license="MIT（由上游 README 徽章声明；⚠️ 仓库未附独立 LICENSE 文件）",
        source="https://github.com/yukinotech/humanize-chinese @ efdefaa0（2026-04-02，声明版本 2.1.0）",
        boundary=(
            "**仅用于检测，不用于改写。** 实测其规则改写会破坏中文语法"
            "（「前所未有的使用体验」→「巨大的用体验」），故改写改走自研 LLM + RAG。"
            "其困惑度 / 突发度 / 段落熵 CV 亦由该内核提供，**不得声称自研**。"
            "其检测灵敏度分语域，20 条评测集中 14 条得 0 分，须与自研词典并报。"
        ),
        verify="third_party/README.md（含两轮实测记录）；demo_b_copywriting/detector.py",
    ),
    Component(
        name="ComfyUI",
        kind=KIND_THIRD,
        role="Demo C 的真实出图后端",
        license="GPL-3.0",
        source="本机 D:\\ComfyUI（v0.28.0，1045 节点）",
        boundary=(
            "**仅作为独立服务通过 HTTP 调用**（127.0.0.1:8188）：不链接其源码、"
            "不修改其文件、不再分发其代码。因此本仓库代码不因 GPL 传染而需以 GPL 发布。"
        ),
        verify="D:\\ComfyUI\\LICENSE（GPL-3.0 全文）；本项目仅通过 common/clients/http.py 调用",
    ),
    Component(
        name="animagine-xl-4.0 / ltx-video",
        kind=KIND_THIRD,
        role="Demo C 使用的生成模型权重",
        license="⚠️ 未在本仓库断言；权重留在 D:\\ 未再分发，许可以上游模型页声明为准",
        source="D:\\ComfyUI\\models\\checkpoints\\",
        boundary="本仓库不打包、不再分发模型权重；演示时如需声明许可，应现场查上游模型页。",
        verify="ls D:\\ComfyUI\\models\\checkpoints\\",
    ),
    Component(
        name="sentence-transformers + BAAI/bge-small-zh-v1.5",
        kind=KIND_THIRD,
        role="Demo A/B 的语义向量化后端（语义保真度指标的仪器）",
        license="sentence-transformers: Apache-2.0；模型 bge-small-zh-v1.5: MIT",
        source="PyPI / HuggingFace",
        boundary=(
            "**未安装时自动降级**为零依赖 hashing-ngram 兜底。"
            "降级状态下余弦相似度**不具语义意义**，指标页必须标出实际后端，不得静默照报。"
        ),
        verify="demo_b_copywriting/quality.py 的 fidelity_reliable 字段；retriever.py 的编码器一致性校验",
    ),
    Component(
        name="FAISS (faiss-cpu)",
        kind=KIND_THIRD,
        role="向量检索后端",
        license="MIT",
        source="PyPI",
        boundary=(
            "使用 IndexFlatIP（精确检索），与兜底 numpy 暴力检索**结果等价**，"
            "因此「换后端不改指标」成立。十万级以上需换 IVF/HNSW 并重新标定召回率。"
        ),
        verify="common/vectorstore/faiss_store.py 与 numpy_store.py 的对照说明",
    ),
    Component(
        name="FastAPI / uvicorn / pydantic / httpx / loguru / numpy",
        kind=KIND_THIRD,
        role="服务与底座运行时依赖",
        license="FastAPI: MIT；uvicorn: BSD-3；pydantic: MIT；httpx: BSD-3；loguru: MIT；numpy: BSD-3",
        source="PyPI（本机经阿里云镜像安装）",
        boundary="均为常规运行时依赖，不涉及能力声称。",
        verify="requirements/*.txt",
    ),
    Component(
        name="FFmpeg",
        kind=KIND_THIRD,
        role="Demo C 音视频合成与时长探测（ffprobe）",
        license="LGPL/GPL（取决于构建选项）",
        source="本机 PATH（8.1.2）",
        boundary="以命令行外部进程调用，未链接其库。",
        verify="ffmpeg -version / ffprobe -version",
    ),

    # ---------------- 既有自研（不在本仓库） ----------------
    Component(
        name="aigc-comic",
        kind=KIND_EXISTING,
        role="Demo C 的**执行服务**（漫剧流水线的实际执行方）",
        license="本人自有的既有项目；不在本仓库内，未再分发",
        source="E:\\aigc-comic（实测 8 个接口：/script /image /audio /compose /health /run /tasks /tasks/{task_id}）",
        boundary=(
            "⚠️ 硬红线：其中 **`/run` 的「pipeline 状态机 + 断点续跑」、`/tasks` 的任务状态查询、"
            "分镜/配音/合成执行、音画同步，均为既有能力，绝不得声称本次自研。**"
            "本次新增只限于：ComfyUI 出图适配器、审核层、指标层。"
            "本项目**不修改其源码**，只通过 HTTP 调用（演示时可当场 `git status` 验证工作区干净）。"
        ),
        verify="curl E:\\aigc-comic 的 /openapi.json；在其目录执行 git status 应为 clean",
    ),
    Component(
        name="D:\\manju / D:\\AI\\GPT-SoVITS / E:\\ai小说工作区",
        kind=KIND_EXISTING,
        role="角色一致性参考 / TTS 备选 / 文本素材",
        license="本人自有的既有项目",
        source="本机其他盘位",
        boundary="**可用性待确认**，本项目当前不依赖它们；若启用需在来源声明中补充。",
        verify="ls 对应目录",
    ),

    # ---------------- 本次新增 ----------------
    Component(
        name="common/（统一底座）",
        kind=KIND_NEW,
        role="配置、日志与阶段计时、混合 LLM 路由、可插拔向量化与向量库、指标注册表",
        license="本仓库自有",
        source="E:\\interview-demo\\common",
        boundary=(
            "自研。设计要点：每个外部依赖都有**零依赖兜底路径**，"
            "且降级时通过 `is_fallback` 如实上报，不允许静默降级。"
        ),
        verify="python scripts/selfcheck.py",
    ),
    Component(
        name="demo_b_copywriting/（Demo B）",
        kind=KIND_NEW,
        role="语料清洗 → 索引 → 垂类检索 → 双路改写 → 质检 → 评测 → 三路对照页",
        license="本仓库自有",
        source="E:\\interview-demo\\demo_b_copywriting",
        boundary=(
            "自研部分：RAG 检索链路、提示词工程、AI 味词典（`tells.py`）、"
            "质检层 5 道闸门、评测框架、对照界面。"
            "**检测打分来自第三方内核，不是自研。**"
        ),
        verify="python -m demo_b_copywriting.evaluate && python -m demo_b_copywriting.app",
    ),
    Component(
        name="Demo B 语料（35 条风格语料 + 20 条评测集）",
        kind=KIND_NEW,
        role="检索知识库与评测基准",
        license="本仓库自有（全部自行撰写）",
        source="demo_b_copywriting/data/raw/",
        boundary=(
            "**不使用爬取数据**（平台条款 + 个人数据风险）。代价是规模小（35 条），"
            "因此检索质量只能作为**链路可行性证据**，不能作为「上线级检索效果」的证据；"
            "简历声称的「10 万+ 语料 / 6 垂类」在本 Demo 中**不具备**，口径页已写明。"
        ),
        verify="demo_b_copywriting/data/SOURCES.md；python -m demo_b_copywriting.ingest",
    ),
]


def export_json(path: Optional[Path] = None) -> Path:
    target = path or (config.paths.root / "portal" / "components.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    counts: dict = {}
    for c in COMPONENTS:
        counts[c.kind] = counts.get(c.kind, 0) + 1
    payload = {
        "generated_by": "portal/provenance.py",
        "note": (
            "三分类账本。任何能力都必须能归入第三方开源 / 既有自研 / 本次新增之一，且不得越界声称。"
        ),
        "kind_labels": KIND_LABEL,
        "counts": counts,
        "components": [c.as_dict() for c in COMPONENTS],
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target
