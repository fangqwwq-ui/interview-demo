"""统一配置。

设计原则：所有路径、凭据、开关集中在本模块，其他模块一律 `from common import config`，
不直接读环境变量。这样口径与依赖来源只有一个入口，便于审计。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# ---------------------------------------------------------------- 路径

PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT / ".env")


def _path(env_key: str, default: Path) -> Path:
    raw = os.getenv(env_key)
    return Path(raw) if raw else default


def _flag(env_key: str, default: bool) -> bool:
    raw = os.getenv(env_key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Paths:
    root: Path = PROJECT_ROOT
    models: Path = _path("MODELS_DIR", PROJECT_ROOT / "models")
    corpus: Path = _path("CORPUS_DIR", PROJECT_ROOT / "data" / "corpus")
    index: Path = _path("INDEX_DIR", PROJECT_ROOT / "data" / "index")
    outputs: Path = _path("OUTPUT_DIR", PROJECT_ROOT / "outputs")
    third_party: Path = PROJECT_ROOT / "third_party"

    # 既有资产（只读复用，禁止写入）
    aigc_comic: Path = _path("AIGC_COMIC_HOME", Path("E:/aigc-comic"))
    humanize_chinese: Path = _path(
        "HUMANIZE_CHINESE_HOME", PROJECT_ROOT / "third_party" / "humanize-chinese"
    )
    comfyui: Path = _path("COMFYUI_HOME", Path("D:/ComfyUI"))
    gpt_sovits: Path = _path("GPT_SOVITS_HOME", Path("D:/AI/GPT-SoVITS"))

    def ensure(self) -> None:
        for d in (self.models, self.corpus, self.index, self.outputs):
            d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------- LLM

LOCAL_MODELS = frozenset({"ollama", "local"})


@dataclass(frozen=True)
class LLMConfig:
    """混合路由配置。

    mode:
        local  —— 只用 Ollama
        cloud  —— 只用云端
        hybrid —— 优先云端，失败自动降级本地（默认）
    """

    mode: str = os.getenv("LLM_MODE", "hybrid").strip().lower()
    fallback_to_local: bool = _flag("LLM_FALLBACK_TO_LOCAL", True)

    # 本地
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "qwen3:8b")

    # 云端（阿里云百炼，OpenAI 兼容）
    cloud_api_key: str = os.getenv("DASHSCOPE_API_KEY", "").strip()
    cloud_base_url: str = os.getenv(
        "DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    cloud_model: str = os.getenv("DASHSCOPE_MODEL", "qwen-plus")

    request_timeout: int = int(os.getenv("LLM_TIMEOUT", "60"))
    max_retries: int = int(os.getenv("LLM_MAX_RETRIES", "2"))

    @property
    def cloud_ready(self) -> bool:
        return bool(self.cloud_api_key)

    def describe(self) -> str:
        bits = [f"mode={self.mode}"]
        bits.append("cloud=" + ("ready" if self.cloud_ready else "NO-KEY"))
        bits.append(f"local={self.ollama_model}")
        return " | ".join(bits)


# ---------------------------------------------------------------- 外部服务

@dataclass(frozen=True)
class Services:
    comfyui_base_url: str = os.getenv("COMFYUI_BASE_URL", "http://127.0.0.1:8188")
    aigc_comic_base_url: str = os.getenv("AIGC_COMIC_BASE_URL", "http://127.0.0.1:8000")


# ---------------------------------------------------------------- 检索 / 指标

EMBED_MODEL_NAME = os.getenv("EMBED_MODEL", "BAAI/bge-small-zh-v1.5")

# 8 GB 显存无法同时驻留视觉 / 语音 / LLM，必须分阶段卸载
VRAM_STAGED_LOADING = _flag("VRAM_STAGED_LOADING", True)

paths = Paths()
llm = LLMConfig()
services = Services()
