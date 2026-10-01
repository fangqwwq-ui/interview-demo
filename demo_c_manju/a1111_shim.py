r"""A1111 兼容 shim —— 把 ComfyUI 包装成 SD WebUI 的 `txt2img` 接口。

## 为什么需要这一层（这是接入既有服务的关键）

现有执行服务 `E:\aigc-comic` 的出图模块写死了 **A1111 / SD WebUI 协议**：

    POST {SD_API_URL}/sdapi/v1/txt2img
    payload  = {prompt, negative_prompt, seed, steps, width, height, ...}
    response = {"images": ["<base64 PNG>", ...]}

而本机**没有独立的 A1111**，只有 `D:\ComfyUI`（协议完全不同：workflow JSON + 轮询历史）。
两者协议不兼容。

本模块提供一个**协议转换层**，把 ComfyUI 包成 A1111：

```
  E:\aigc-comic（不改一行代码）
        │  SD_API_URL=http://127.0.0.1:7860
        ↓  POST /sdapi/v1/txt2img
  a1111_shim.py（本模块，本次新增）
        │  workflow JSON（复用 comfy_adapter 的构造器）
        ↓  HTTP
  D:\ComfyUI  127.0.0.1:8188
```

于是既有流水线**只改一个环境变量**就拿到真实图像，不再产出 Pillow 占位图。
这正好落在 PLAN.md §8.2 对 Demo C 的定位上：**为既有流水线补齐真实出图后端**。
`aigc-comic` 的状态机 / 断点续跑 / 分镜 / 配音 / 合成全部保持既有、未改动。

## 两个必须说清楚的边界

**边界 1：提示词体系不匹配。**
既有服务的提示词是「中文角色描述 + 中文场景 + 中文动作 + 景别 + 英文风格词」，
而 `animagine-xl-4.0` 是 **Danbooru 标签体系**的模型，中文描述基本无效。
本模块带一张**演示级中文→标签映射表**做适配，并**统计映射命中率与未映射词**
（响应体与日志里都给），而不是假装提示词可以直接通用。
映射表覆盖不了的部分会被如实计数上报 —— 覆盖率是测出来的，不是声称的。

**边界 2：串行执行。**
ComfyUI 会排队执行；本 shim 额外加进程内互斥锁，避免并发请求同时占显存
（8 GB 卡上并发出图会把显存顶爆）。代价是吞吐受串行限制，会在响应里如实报出排队等待时间。

## 与既有服务的调用契约

既有服务调用时用的固定参数（`aigc-comic/config.py`）：`seed=42`、
`steps=25`、`768x1024`、`negative_prompt="lowres, bad anatomy, extra fingers, deformed face"`。
因此**它每次出图都是同 seed**，配合 `LORA_MAP` 是为了跨镜头角色一致。
本 shim 尊重这些参数，不做篡改。
"""

from __future__ import annotations

import argparse
import json
import random
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from pydantic import BaseModel, Field

from common import config
from demo_c_manju.comfy_adapter import (
    DEFAULT_CHECKPOINT,
    DEFAULT_NEGATIVE,
    ComfyUIClient,
    ComfyUIError,
    generate,
)

SHIM_DIR = config.paths.outputs / "demo_c_shim"
CALL_LOG = SHIM_DIR / "calls.jsonl"

# A1111 与 ComfyUI 的采样器命名不同，需要一张对照表。
# 只映射本机 ComfyUI 实际存在的采样器（来自 /object_info：euler / euler_ancestral / dpmpp_2m ...）。
# 未列出的名字一律回落到默认采样器，并在响应里说明 —— 不静默换用别的算法。
SAMPLER_MAP: Dict[str, str] = {
    "euler": "euler",
    "euler a": "euler_ancestral",
    "euler_ancestral": "euler_ancestral",
    "dpm++ 2m": "dpmpp_2m",
    "dpm++ 2m karras": "dpmpp_2m",
    "dpm++ sde": "dpmpp_sde",
    "ddim": "ddim",
    "heun": "heun",
    "lms": "lms",
}

SCHEDULER_MAP: Dict[str, str] = {
    "karras": "karras",
    "exponential": "exponential",
    "normal": "normal",
    "ddim_uniform": "ddim_uniform",
    "simple": "simple",
}

# ---------------------------------------------------------------- 中文→Danbooru 标签
# ⚠️ **演示级词表**，覆盖既有服务示例分镜里出现的场景/景别/动作词。
#    它不是通用翻译器；覆盖不到的词会被计数上报（unmapped_terms），
#    以便如实说明提示词适配的覆盖率，而不是假装中文提示词可以直接使用。
ZH_TAG_LEXICON: Dict[str, str] = {
    # 场景
    "天台": "rooftop",
    "教室": "classroom",
    "走廊": "school hallway",
    "操场": "school yard",
    "图书馆": "library",
    "食堂": "cafeteria",
    "街道": "city street",
    "城市": "city",
    "黄昏": "sunset",
    "夜晚": "night",
    "教室窗边": "classroom window",
    "窗边": "window",
    "雨中": "rain",
    "天空": "sky",
    "海边": "beach",
    "山顶": "mountain top",
    "房间": "room",
    "厨房": "kitchen",
    # 时间 / 光照（v1.9 新增）
    #   ⚠️ 来源：既有服务的 `scene` 字段固定写成「大学教室，白天」这种「地点 + 时间」结构。
    #      早期词表只收了地点、漏了时间，导致「白天」以中文原样进入提示词变成噪声 token。
    "白天": "day",
    "清晨": "morning",
    "正午": "noon",
    "傍晚": "evening",
    "深夜": "late night",
    # 教室道具（v1.9 新增，同上：服务分镜反复出现但早期词表未覆盖）
    "大学教室": "university classroom",
    "大学": "university",
    "黑板前": "in front of blackboard",
    "黑板": "blackboard",
    "粉笔": "chalk",
    "讲台": "podium",
    "课桌": "desk",
    # 景别
    "特写": "close-up",
    "近景": "upper body",
    "中景": "cowboy shot",
    "远景": "full body",
    "全景": "wide shot",
    # 动作 / 神态
    "回头": "looking back",
    "低头": "looking down",
    "抬头": "looking up",
    "微笑": "smiling",
    "哭": "crying",
    "生气": "angry",
    "惊讶": "surprised",
    "思考": "thinking",
    "奔跑": "running",
    "站立": "standing",
    "坐着": "sitting",
    "抬头看": "looking up",
    "看着镜头": "looking at viewer",
    "看向窗外": "looking out of window",
    # 神态细节（v1.9 新增）
    "眼神认真": "serious expression",
    "露出怀疑的表情": "doubtful expression",
    "怀疑的表情": "doubtful expression",
    "认真": "serious",
    "怀疑": "doubtful",
    "表情": "expression",
    "点头赞许": "nodding approvingly",
    "点头": "nodding",
    "赞许": "approving",
    "鼓掌": "clapping",
    # 动作短语（v1.9 新增）
    "抬头看向黑板": "looking up at blackboard",
    "看着黑板": "looking at blackboard",
    "走向黑板": "walking to blackboard",
    "拿起粉笔": "holding chalk",
    "推了推眼镜": "adjusting glasses",
    "写出解答": "writing the solution",
    "看着": "looking at",
    "走向": "walking towards",
    "举起": "raising arm",
    "解答": "solution",
    "站在": "standing on",
    # ⚠️ 下面两条是**复合词条**，专门用来消除最长匹配的「坏切分」：
    #   「抬头看向窗外」若按最长匹配会切成「抬头看」+「向窗外」→ `looking up向窗外`（残留中文）
    #   「城市街道」会切成「城市」+「街道」→ `citycity street`（语义重复）
    #   在最长匹配算法下这类问题无法靠调顺序解决，只能把实际会出现的组合显式列出。
    #   v1.9 起「大学教室」「抬头看向黑板」等也属此类：词表补充后两个子词都被覆盖，
    #   若不显式列出复合条目，切分点会落在词中间，拼接结果虽然英文正确但语义被拆散。
    "抬头看向窗外": "looking out of window",
    "城市街道": "city street",
    # 角色描述（演示级）
    "十六岁少女": "teenage girl",
    "少女": "girl",
    "少年": "boy",
    "黑色短发": "short black hair",
    "黑发": "black hair",
    "长发": "long hair",
    "短发": "short hair",
    "校服": "school uniform",
    "眼镜": "glasses",
    "戴眼镜": "wearing glasses",
    "马尾": "ponytail",
    "白衬衫": "white shirt",
    # 身份（v1.9 新增）
    "女大学生": "female college student",
    "男大学生": "male college student",
    "大学生": "college student",
    "女教师": "female teacher",
    "男教师": "male teacher",
    "教师": "teacher",
    "老师": "teacher",
    "同学们": "classmates",
    "同学": "classmate",
    # 服装（v1.9 新增）
    "深色西装": "dark suit",
    "西装": "suit",
    "衬衫": "shirt",
    "领带": "necktie",
    # 风格
    "国漫": "chinese comic style",
    "日漫": "anime style",
    "写实": "photorealistic",
}

# 「20岁」这类年龄写法用正则单独处理，不塞进词表：
#   词表键是**定长字符串**，而年龄的数字部分是变量，放进去只能匹配某一个具体岁数。
_AGE_RE = re.compile(r"(\d{1,2})\s*岁")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
# 连续中文「片段」：删除残留时按片段删，而不是按逗号段删（见 adapt_prompt 注释）
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")


# 按长度降序（长词优先），避免「抬头看」把「抬头看向窗外」的前半截吃掉。
# ⚠️ 顺序在这里是**正确性依赖**，不是风格问题：
#    早期版本按 dict 声明顺序做全局 replace，结果
#    「抬头看向窗外」被替换成 `looking uplooking out of window`（子串重叠）。
#    现在改为「长词优先的单次扫描 + 去重」，见 adapt_prompt()。
_LEXICON_ORDERED = sorted(ZH_TAG_LEXICON.items(), key=lambda kv: len(kv[0]), reverse=True)

_ZH_RE = None


def _build_zh_re():
    global _ZH_RE
    if _ZH_RE is None:
        # 交替分支本身也按长度降序，保证最长匹配优先
        pattern = "|".join(re.escape(k) for k, _ in _LEXICON_ORDERED)
        _ZH_RE = re.compile(pattern)
    return _ZH_RE


def adapt_prompt(text: str, strip_residual: bool = True) -> tuple[str, Dict[str, Any]]:
    """把中文提示词里的已知词替换为 Danbooru 标签，并统计覆盖情况。

    为什么用「正则一次扫描」而不是「逐词 replace」：
    逐词 replace 会让**重叠词互相污染**（「抬头看」先替换掉，后面再匹配
    「看向窗外」时就拼出 `looking uplooking out of window`）。正则一次扫描
    会按位置最长匹配、且不会回溯进已替换的文本，从根上避免这类重叠。

    为什么替换结果两侧要**补空格**（v1.9 修正）：
      词表在 v1.9 被扩充后，「大学教室」这种两个子词都被覆盖的写法会**连续命中两次**，
      若直接首尾相接就拼成 `universityclassroom` —— 一个 SD 无法识别的伪词。
      早期词表没暴露这个问题，是因为「大学」不在表里，前半截以中文残留，
      反倒"意外地"当了分隔符。词表越全，越必须显式处理拼接边界。

    为什么默认剥离残留中文（strip_residual）：
      1) SDXL 的文本编码器是英文的，中文词元对它基本是噪声；
      2) 残留里最多的是**角色人名**，专有名词有可能被 SD 当作待渲染文字，
         在画面上烧出乱码。
      ⚠️ **收益未经证实，不要对外宣称「剥离提升了画质」**：
         `scripts/ab_prompt_adaptation.py` 做过单变量对照（同 seed / 同参数，n=1）：
           · V0_raw（不适配）→ V1_keep（扩词表）：**差异显著**，出图从
             「金发粉裙抽象背景」变为「教室 + 黑发 + 白衬衫 + 严肃神情」，
             与分镜对得上；
           · V1_keep → V2_strip（再剥离残留）：**肉眼无可辨识差异**。
         即：真正起作用的是**词表覆盖**，剥离本身在图像层面没测出收益。
         保留剥离的理由是「覆盖率可量化 + 少送噪声进编码器 + 便于审计」，
         不是「画质更好」。n=1 的主观比对不足以支撑更强的结论。
      剥离内容全部记在 `dropped_terms` 里，随时可还原。

    返回 (适配后的提示词, 统计)。`cjk_coverage` 是按**中文字符数**算的覆盖率，
    比按「词条数」算更诚实：一个没覆盖的短词和一个没覆盖的长句权重不同。
    """
    if not text:
        return (
            text or "",
            {
                "mapped": 0,
                "mapped_terms": [],
                "unmapped_terms": [],
                "dropped_terms": [],
                "total_cjk_chars": 0,
                "residual_cjk_chars": 0,
                "cjk_coverage": 1.0,
                "lexicon_size": len(ZH_TAG_LEXICON),
            },
        )

    total_cjk = len(_CJK_RE.findall(text))
    mapped_terms: List[str] = []
    age_terms: List[str] = []

    def _age(m):
        age_terms.append(f"{m.group(0)}->{m.group(1)} years old")
        return f" {m.group(1)} years old "

    def _sub(m):
        zh = m.group(0)
        tag = ZH_TAG_LEXICON[zh]
        mapped_terms.append(f"{zh}->{tag}")
        # 两侧补空格：见 docstring「为什么替换结果两侧要补空格」
        return f" {tag} "

    working = _AGE_RE.sub(_age, text)
    working = _build_zh_re().sub(_sub, working)

    # 归一化：先把补空格带来的连续空格压掉，再统一逗号（含中文逗号/顿号）后的空格
    working = re.sub(r"[ \t]{2,}", " ", working)
    working = re.sub(r"[，、]", ",", working)
    working = re.sub(r"\s*,\s*", ", ", working)
    # 去重：源文本里常自带 `中景 shot`，替换后变成 `cowboy shot shot`。
    # 这里只折叠**紧邻重复**的英文词，不做其它改写。
    working = re.sub(r"\b(\w[\w-]*)(\s+\1\b)+", r"\1", working, flags=re.IGNORECASE)

    # 残留中文：按逗号切分，仍含 CJK 的段即为未覆盖
    segments = [s.strip() for s in working.split(",")]
    unmapped = [s for s in segments if s and _CJK_RE.search(s)]

    dropped: List[str] = []
    if strip_residual and unmapped:
        # ⚠️ 只删**残留的中文片段本身**，绝不删整段（v1.9 修正）：
        #    「删整段」的版本会把 `林小满在 blackboard 上流畅地 writing the solution`
        #    整段丢掉，连已经翻译正确的 `blackboard`、`writing the solution` 一起连坐。
        #    已翻译的英文是资产，不能因为同段里有没覆盖的部分就被牵连。
        for seg in unmapped:
            dropped.extend(m.group(0) for m in _CJK_RUN_RE.finditer(seg))
        working = _CJK_RUN_RE.sub(" ", working)

    # 收尾归一化：压空格 → 统一逗号 → 合并空段（纯中文段被删空后会留下空位）
    working = re.sub(r"\s+", " ", working)
    working = re.sub(r"\s*,\s*", ", ", working)
    working = re.sub(r",(?:\s*,)+", ",", working)
    working = ", ".join(seg.strip() for seg in working.split(",") if seg.strip())
    working = re.sub(r"\s+", " ", working).strip().strip(",").strip()

    residual_chars = sum(len(_CJK_RE.findall(t)) for t in dropped)
    coverage = 1.0 if total_cjk == 0 else round(1.0 - residual_chars / total_cjk, 4)

    return working, {
        "mapped": len(mapped_terms) + len(age_terms),
        "mapped_terms": list(dict.fromkeys(mapped_terms + age_terms)),
        "unmapped_terms": unmapped,
        "dropped_terms": dropped,
        "strip_residual": strip_residual,
        "total_cjk_chars": total_cjk,
        "residual_cjk_chars": residual_chars,
        "cjk_coverage": coverage,
        "lexicon_size": len(ZH_TAG_LEXICON),
    }


# A1111 的 LoRA 语法：`<lora:权重名:强度>`，权重可省略（默认 1.0）
_LORA_RE = re.compile(r"<\s*lora\s*:\s*([^:>]+?)\s*(?::\s*([\d.]+)\s*)?>", re.I)


def extract_lora_tags(text: str) -> tuple[str, List[Dict[str, str]]]:
    """剥掉 A1111 的 `<lora:name:weight>` 标记，并把它单独报出来。

    为什么必须显式处理，不能放着不管：
      在 A1111 里 `<lora:x:0.8>` 是**真语法**，会被前端解析成
      「加载 LoRA 权重 x，强度 0.8」。但在 ComfyUI 里 LoRA 是**独立节点**
      （LoraLoader），光靠提示词字符串根本没有效果。

      如果什么都不做，这一串会被当成普通文本塞进 ComfyUI 的 prompt，
      于是：① 提示词被无意义的尖括号 token 污染；② 更糟的是，
      调用方以为自己的 LoRA 生效了 —— 这属于**静默假装兑现了不具备的能力**，
      正是本项目口径明令禁止的那类失真。

      本 shim 不实现 LoraLoader（本机也没有任何 LoRA 权重文件），
      因此选择「剥离 + 显式上报」，让调用方一眼看见它没生效。
    """
    found: List[Dict[str, str]] = []

    def _sub(m):
        found.append({"name": m.group(1).strip(), "weight": (m.group(2) or "1.0").strip()})
        return ""

    cleaned = _LORA_RE.sub(_sub, text or "")
    if found:
        cleaned = re.sub(r"\s*,\s*,\s*", ", ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip().strip(",").strip()
    return cleaned, found


# ---------------------------------------------------------------- 请求模型


class Txt2ImgRequest(BaseModel):
    """A1111 `/sdapi/v1/txt2img` 请求体的**子集**（只覆盖既有服务实际用到的字段）。"""

    prompt: str = ""
    negative_prompt: str = ""
    seed: int = -1
    steps: int = 20
    cfg_scale: float = 6.0
    width: int = 832
    height: int = 1216
    sampler_name: str = ""
    scheduler: str = ""
    batch_size: int = 1
    n_iter: int = 1
    # 其余 A1111 字段一律忽略。用 extra 接收以避免客户端因未知字段被拒。
    model_config = {"extra": "ignore"}


class Txt2ImgResponse(BaseModel):
    images: List[str] = Field(default_factory=list)
    info: str = ""
    parameters: Dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------- 状态

_LOCK = threading.Lock()  # 串行化，避免并发出图顶爆 8 GB 显存
_client: Optional[ComfyUIClient] = None


def _get_client() -> ComfyUIClient:
    global _client
    if _client is None:
        _client = ComfyUIClient()
    return _client


def _log_call(record: Dict[str, Any]) -> None:
    SHIM_DIR.mkdir(parents=True, exist_ok=True)
    with CALL_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- 应用

app = FastAPI(
    title="ComfyUI -> A1111 兼容 shim",
    version="0.1.0",
    description=(
        "把 ComfyUI 包装成 SD WebUI 的 txt2img 接口，使既有 aigc-comic 服务"
        "无需改代码即可使用真实出图后端。"
    ),
)


@app.get("/internal/health")
def internal_health() -> Dict[str, Any]:
    """本 shim 自身 + 上游 ComfyUI 的连通状态。"""
    comfy: Dict[str, Any]
    try:
        comfy = _get_client().health()
    except Exception as exc:  # noqa: BLE001
        comfy = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {
        "ok": bool(comfy.get("ok")),
        "role": "A1111-compatible shim over ComfyUI",
        "comfyui": comfy,
        "checkpoint": DEFAULT_CHECKPOINT,
        "serial_execution": True,
        "prompt_lexicon_size": len(ZH_TAG_LEXICON),
        "call_log": str(CALL_LOG),
    }


@app.get("/sdapi/v1/sd-models")
def sd_models() -> List[Dict[str, Any]]:
    """部分 A1111 客户端会先查询模型列表，这里从 ComfyUI 读实际可用的 checkpoint。"""
    try:
        names = list(_get_client().checkpoints())
    except Exception:  # noqa: BLE001
        names = [DEFAULT_CHECKPOINT]
    return [
        {"title": f"{n} [shim]", "model_name": n, "hash": None, "sha256": None, "filename": n}
        for n in names
    ]


@app.get("/sdapi/v1/samplers")
def sd_samplers() -> List[Dict[str, Any]]:
    return [{"name": k, "aliases": [], "options": {}} for k in SAMPLER_MAP]


@app.get("/sdapi/v1/options")
def get_options() -> Dict[str, Any]:
    return {"sd_model_checkpoint": DEFAULT_CHECKPOINT}


@app.post("/sdapi/v1/options")
def set_options(_payload: Dict[str, Any]) -> Dict[str, Any]:
    """A1111 客户端可能推送模型切换。本 shim 不支持热切换，返回现状而不假装成功。"""
    return {"sd_model_checkpoint": DEFAULT_CHECKPOINT, "note": "shim 不支持运行时切换模型"}


@app.post("/sdapi/v1/txt2img", response_model=Txt2ImgResponse)
def txt2img(req: Txt2ImgRequest) -> Txt2ImgResponse:
    started = time.perf_counter()
    queued_s = 0.0

    # 先剥 LoRA 标记，再适配中文 —— 顺序不能反：`<lora:c1:0.85>` 里没有中文，
    # 但先适配会把整串当成一个英文片段留在结果里，剥离时更难判断边界。
    prompt_wo_lora, lora_tags = extract_lora_tags(req.prompt)
    adapted, prompt_stats = adapt_prompt(prompt_wo_lora)
    negative, _ = adapt_prompt(req.negative_prompt)
    prompt_stats["lora_tags"] = lora_tags
    prompt_stats["lora_applied"] = False
    lora_note = ""
    if lora_tags:
        names = ", ".join(f"{t['name']}(w={t['weight']})" for t in lora_tags)
        lora_note = (
            f"请求含 {len(lora_tags)} 个 LoRA 标记（{names}），"
            "已从提示词中剥离但**未生效**：ComfyUI 的 LoRA 是独立节点，"
            "且本机无对应权重文件。角色一致性因此只能靠固定 seed，不能靠 LoRA。"
        )

    sampler_raw = (req.sampler_name or "").strip().lower()
    sampler = SAMPLER_MAP.get(sampler_raw, "")
    sampler_note = ""
    if sampler_raw and not sampler:
        sampler_note = (
            f"未识别的 A1111 采样器 {req.sampler_name!r}，已回落到 ComfyUI 默认 "
            "（未静默改用其它算法但未告知）"
        )
    scheduler = SCHEDULER_MAP.get((req.scheduler or "").strip().lower(), "")

    # 串行执行：8 GB 显存不允许并发出图
    with _LOCK:
        queued_s = time.perf_counter() - started
        images: List[str] = []
        errors: List[str] = []
        per_image: List[Dict[str, Any]] = []

        n = max(1, min(int(req.batch_size) * max(1, int(req.n_iter)), 4))
        base_seed = req.seed
        for i in range(n):
            if base_seed is None or base_seed < 0:
                this_seed = random.randint(0, 2**31 - 1)
            else:
                this_seed = int(base_seed) + i
            kwargs: Dict[str, Any] = {}
            if sampler:
                kwargs["sampler"] = sampler
            if scheduler:
                kwargs["scheduler"] = scheduler
            res = generate(
                adapted,
                negative=negative or DEFAULT_NEGATIVE,
                checkpoint=DEFAULT_CHECKPOINT,
                width=req.width,
                height=req.height,
                steps=req.steps,
                cfg=req.cfg_scale,
                seed=this_seed,
                quality_tags=False,  # 既有服务的提示词已含风格词，不再叠加
                client=_get_client(),
                **kwargs,
            )
            if res.ok and res.images:
                import base64

                blob = Path(res.images[0].local_path).read_bytes()
                images.append(base64.b64encode(blob).decode("ascii"))
                per_image.append(
                    {
                        "seed": res.seed,
                        "size": f"{res.width}x{res.height}",
                        "elapsed_s": res.elapsed_s,
                        "bytes": res.images[0].bytes_size,
                        "sha256_12": __import__("hashlib").sha256(blob).hexdigest()[:12],
                    }
                )
            else:
                errors.append(res.error or "未知错误")
                per_image.append({"seed": this_seed, "ok": False, "error": res.error})

    total_s = time.perf_counter() - started

    record = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "prompt_chars": len(req.prompt or ""),
        "adapted_prompt": adapted,
        "prompt_stats": prompt_stats,
        "a1111_sampler": req.sampler_name,
        "mapped_sampler": sampler,
        "size": f"{req.width}x{req.height}",
        "steps": req.steps,
        "cfg_scale": req.cfg_scale,
        "requested_seed": req.seed,
        "batch": len(per_image),
        "queued_s": round(queued_s, 3),
        "total_s": round(total_s, 3),
        "images": per_image,
        "errors": errors,
        "notes": [n for n in (sampler_note, lora_note) if n],
    }
    _log_call(record)

    info_payload = {
        "shim": "comfyui-over-a1111",
        "checkpoint": DEFAULT_CHECKPOINT,
        "queued_s": round(queued_s, 3),
        "total_s": round(total_s, 3),
        "prompt_adaptation": prompt_stats,
        "sampler_note": sampler_note,
        "lora_note": lora_note,
        "errors": errors,
        "per_image": per_image,
    }
    return Txt2ImgResponse(
        images=images,
        info=json.dumps(info_payload, ensure_ascii=False),
        parameters={
            "prompt": adapted,
            "negative_prompt": negative,
            "steps": req.steps,
            "cfg_scale": req.cfg_scale,
            "width": req.width,
            "height": req.height,
        },
    )


# ---------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m demo_c_manju.a1111_shim",
        description="A1111 兼容 shim：把 ComfyUI 包成 SD WebUI 的 txt2img 接口",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7860, help="A1111 默认端口，既有服务默认也指这里")
    parser.add_argument("--check", action="store_true", help="只打印上游状态后退出")
    args = parser.parse_args(argv)

    if args.check:
        print(json.dumps(internal_health(), ensure_ascii=False, indent=2))
        try:
            return 0 if internal_health().get("ok") else 1
        except Exception:  # noqa: BLE001
            return 1

    import uvicorn

    print(f"shim 监听 http://{args.host}:{args.port}")
    print(f"  -> 上游 ComfyUI：{config.services.comfyui_base_url}")
    print(f"  -> 接入既有服务：设置 SD_API_URL=http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
