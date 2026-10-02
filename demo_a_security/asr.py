r"""Demo A 语音识别：faster-whisper（走 CTranslate2，**不需要 torch**）。

为什么不用 torch 版whisper
-------------------------
同视觉层的理由：本机镜像约 343 KB/s，torch 2.7.0+cu128 约 2.7 GB → 约 137 分钟。
`faster-whisper` 用 CTranslate2 做推理，**自带 CUDA 12 支持**（依赖 cublas64_12.dll），
与 ComfyUI 的 torch lib 那套运行时正好是同一个 CUDA 主版本。

⚠️ 本机的一个关键不确定性（CTranslate2 4.8 要求 CUDA 12.x 且cuDNN 9.x，
   而 ComfyUI torch lib 里有cudnn64_9.dll，理论可用）——
   **但 ctranslate2 与 onnxruntime-gpu 会争用同一套 CUDA DLL**。
   因此两者**不能同时常驻**（这正是 8GB 显存/单进程下必须分时加载的原因），
   `VRAM_STAGED_LOADING` 就是为此存在。`load_asr()` 返回的句柄要在
   用完后显式 `close()`，否则会一直占着 CUDA 上下文。

本模块只做「音频 -> 文本」，不含VAD 与说话人分离（VAD 由 faster-whisper 自带
的 `vad_filter` 参数提供，说话人分离超出 Demo 范围）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL_DIR = ROOT / "models" / "faster-whisper"

# faster-whisper 支持的模型规格（越小的越快但越不准）。
# ⚠️ faster-whisper 1.2.1 顶层**没有** MODEL_SIZES 常量（实测 `getattr` 返回 n/a），
# 所以这里自带一份。若升级 faster-whisper，需核对其真实支持的规格名。
MODEL_SIZES = ("tiny", "base", "small", "medium", "large-v3")

# 本机连不上 huggingface.co（实测 SSL: UNEXPECTED_EOF_WHILE_READING），
# 但 hf-mirror.com 可用（HTTP 200）。**必须在导入 faster_whisper / huggingface_hub
# 之前**设好这个环境变量，否则第一次 snapshot_download 就会失败。
# 权重是几十~几百 MB，不能靠"失败重试"，只能一开始就指对端点。
# 本机网络实测结论（2026-10-02，勿再重复排查）：
#   huggingface.co        -> SSL: UNEXPECTED_EOF_WHILE_READING（连不上）
#   hf-mirror.com         -> API 与小文件(2 KB)可访问，
#                            但 **model.bin 等大文件会 302 到
#                            cas-bridge.xethub.hf.co（海外 xet CDN），
#                            跟随重定向即 SSL 握手超时**。
#                            设 HF_HUB_DISABLE_XET=1 也无效（服务端行为）。
#   modelscope.cn        -> **完全可用**，支持 Range 断点续传
#                            （实测 config.json 200、model.bin 206）。
# 因此权重改从 ModelScope 的 gpustack 镜像仓库下载。
# 若日后HF 官方端点恢复可用，本模块会自动优先用它。
MODELSCOPE_BASE = "https://www.modelscope.cn/models/gpustack/faster-whisper-{size}/resolve/master"
# CTranslate2 需要的文件清单（vocabulary.txt 而非 .json，ModelScope 侧如此）
REQUIRED_FILES = ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt")

HF_ENDPOINT_FALLBACK = "https://hf-mirror.com"


def _configure_hf_endpoint() -> Optional[str]:
    """若未显式设置 HF_ENDPOINT，则指向本机可达的镜像。

    返回实际使用的端点（None 表示沿用环境变量 / 官方端点）。
    除了在 __init__ 里调用，模块导入时也执行一次——
    这样即使调用方直接 `from faster_whisper import WhisperModel` 绕开本模块，
    端点也已经就位。
    """
    import os

    if os.environ.get("HF_ENDPOINT"):
        return os.environ["HF_ENDPOINT"]
    os.environ["HF_ENDPOINT"] = HF_ENDPOINT_FALLBACK
    return HF_ENDPOINT_FALLBACK


# 模块导入时即设一次（必须早于任何 faster_whisper / huggingface_hub 的导入）
_configure_hf_endpoint()


def fetch_weights(model_size: str, dest: Path) -> Path:
    """把 CTranslate2 权重下载到 ``dest``，返回该目录。

    优先 ModelScope（本机实测唯一可用源），逐文件下载并**校验大小非零**。
    不用 huggingface_hub 的 snapshot_download：它会创建 .locks 与
    .incomplete 临时文件，而本机沙箱的批量删除守卫会把清理步骤拦成
    ``SystemExit: 1``（实测HF 路径下失败 634 次）。
    """
    import urllib.request

    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    base = MODELSCOPE_BASE.format(size=model_size)

    for fn in REQUIRED_FILES:
        target = dest / fn
        if target.exists() and target.stat().st_size > 1024:
            print(f"[asr] 已存在 {fn}（{target.stat().st_size:,} B），跳过")
            continue
        url = f"{base}/{fn}"
        print(f"[asr] 下载 {fn} ...", flush=True)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=180) as r, open(target, "wb") as f:
            total = int(r.headers.get("Content-Length", 0) or 0)
            got = 0
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
        if got <= 1024:
            raise RuntimeError(f"{fn} 下载内容异常（仅 {got} 字节），请检查网络或源可用性")
        print(f"[asr]   {fn} 完成 {got:,} B" + (f" / 总 {total:,} B" if total else ""))
    return dest


@dataclass
class ASRInfo:
    """ASR 后端信息。风格与 VisionInfo 一致，便于指标层统一记录。"""

    model: str
    device: str = "cpu"
    compute_type: str = "int8"
    is_fallback: bool = True
    note: str = ""
    ctranslate2_version: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "model": self.model,
            "device": self.device,
            "compute_type": self.compute_type,
            "is_fallback": self.is_fallback,
            "note": self.note,
            "ctranslate2_version": self.ctranslate2_version,
        }


@dataclass
class Transcript:
    """识别结果。保留分段信息以便后续做「语音→事件」的定位。"""

    text: str
    language: str
    segments: List[Dict[str, Any]] = field(default_factory=list)
    latency_ms: float = 0.0
    audio_duration_s: float = 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "language": self.language,
            "segments": self.segments,
            "latency_ms": round(self.latency_ms, 2),
            "audio_duration_s": round(self.audio_duration_s, 2),
        }


def _resolve_cuda_dll_dir() -> Optional[Path]:
    """CTranslate2 4.x 需要 CUDA 12 + cuDNN 9。

    只读复用 ComfyUI 的 torch lib（那套是 CUDA 12.8 + cuDNN 9），不复制不修改。
    """
    import os

    for d in (
        Path("D:/ComfyUI/venv/Lib/site-packages/torch/lib"),
        Path("D:/ComfyUI/venv/Lib/site-packages/torch"),
    ):
        if not d.is_dir():
            continue
        names = {p.name.lower() for p in d.glob("*.dll")}
        if "cublas64_12.dll" in names and "cudnn64_9.dll" in names:
            try:
                os.add_dll_directory(str(d))
            except Exception:
                pass
            os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
            return d
    return None


def load_asr(
    model_size: str = "small",
    *,
    device: str = "cpu",
    compute_type: str = "int8",
    download_root: Path = DEFAULT_MODEL_DIR,
) -> "ASRHandle":
    """加载语音识别模型。

    device='cpu' + compute_type='int8' 是**默认且推荐**组合：
    CTranslate2 的 int8 在 CPU 上通常比 fp16 小模型更快，且不占显存。
    device='cuda' 需要 CUDA 运行时，失败时**自动降级 CPU 并如实上报**，
    绝不假装在用 GPU。
    """
    if model_size not in MODEL_SIZES:
        raise ValueError(
            f"未知模型规格：{model_size}；可选 {MODEL_SIZES}"
        )
    return ASRHandle(
        model_size=model_size,
        device=device,
        compute_type=compute_type,
        download_root=Path(download_root),
    )

class ASRHandle:
    """已加载的 ASR 模型句柄。

    显式提供 ``close()``：CTranslate2 会持有 CUDA 上下文，
    不释放会和 onnxruntime / YOLO 抢显存（8GB 机器上是硬约束）。
    """

    def __init__(
        self,
        model_size: str = "small",
        *,
        device: str = "cpu",
        compute_type: str = "int8",
        download_root: Path = DEFAULT_MODEL_DIR,
    ) -> None:
        # ⚠️ 顺序很重要：HF_ENDPOINT 必须在 huggingface_hub **被导入之前**设置，
        #否则该库会记住官方端点，之后再设环境变量也无效。
        # 早期版本写成「先 import faster_whisper，再设端点」，实际不生效。
        _configure_hf_endpoint()

        import ctranslate2
        from faster_whisper import WhisperModel

        self.model_size = model_size
        self._model = None
        # 是否发生了「想用GPU 却拿到 CPU」的降级。
        # 主动指定 cpu 不算降级 —— 那是合理选择，不是能力缺失。
        self._downgraded = False
        self.info = ASRInfo(
            model=model_size,
            device=device,
            compute_type=compute_type,
            ctranslate2_version=ctranslate2.__version__,
        )

        if device == "cuda":
            dll_dir = _resolve_cuda_dll_dir()
            if dll_dir is None:
                self.info.note = (
                    "未找到 CUDA 12 运行时 DLL 目录，已降级 CPU。"
                    f"（请求 device=cuda，compute_type={compute_type}）"
                )
                device, compute_type = "cpu", "int8"
                self._downgraded = True
            else:
                self.info.note = f"CUDA 运行时来自 {dll_dir}"
            # int8 在 CUDA 上不常用，GPU 场景下更适合 float16
            if device == "cuda" and compute_type == "int8":
                compute_type = "float16"

        download_root.mkdir(parents=True, exist_ok=True)
        # 权重来源：本机实测 huggingface.co 不可达、hf-mirror 的大文件会 302 到
        # 海外 xet CDN，因此**从 ModelScope 手动取**（fetch_weights）。
        local_dir = fetch_weights(model_size, download_root / model_size)

        t0 = time.perf_counter()
        try:
            self._model = WhisperModel(
                str(local_dir),
                device=device,
                compute_type=compute_type,
                local_files_only=True,  # 权重已在本地，禁止再联网
            )
        except Exception as exc:  # noqa: BLE001
            # GPU 路径失败必须降级并上报，而不是抛给上层
            if device == "cuda":
                self.info.note += (
                    f"；CUDA 加载失败（{type(exc).__name__}: {exc}），"
                    "已降级 CPU int8"
                )
                self._model = WhisperModel(
                    str(local_dir), device="cpu", compute_type="int8",
                    local_files_only=True,
                )
                device, compute_type = "cpu", "int8"
                self._downgraded = True
            else:
                raise
        load_ms = (time.perf_counter() - t0) * 1000

        self.info.device = device
        self.info.compute_type = compute_type
        # ⚠️ 只有「本来想要GPU 却没拿到」才算降级。
        # 主动选 CPU int8 是**合理配置**（0 显存占用、8GB 机器上更稳），
        # 若也标 is_fallback=True，会让指标页误报"降级运行"，
        # 也让「is_fallback 必为True」的降级约定失去区分度。
        self.info.is_fallback = bool(self._downgraded)
        if not self.info.note:
            self.info.note = f"CPU {compute_type} 推理（0 显存占用）"
        self.info.note += f"；加载耗时 {load_ms:.0f} ms"

    # ------------------------------------------------------------ 识别
    def transcribe(
        self,
        audio: Union[str, Path, Any],
        *,
        language: Optional[str] = "zh",
        vad_filter: bool = True,
        beam_size: int = 5,
    ) -> Transcript:
        """把音频转成文本。

        audio 可以是文件路径、``bytes``，也可以是 **16kHz 单声道 float32 numpy 数组**。
        language=None 表示自动检测；演示场景固定 zh 更稳定。
        vad_filter=True 会先做语音活动检测，静音段不参与识别，
        既提速也减少幻听。

        ⚠️ 为什么要支持「数组」而不是只支持文件（本机实测踩到两处不兼容）：
        faster-whisper 声明 `av>=11`（**没有上限**），而 PyAV 19 有两处破坏性变更：
          ① 移除了 ``av.open(..., metadata_errors=...)`` → faster-whisper 1.2.1
             读任何文件都会抛 ``TypeError: open() got an unexpected keyword
             argument 'metadata_errors'``；
          ② ``Container.decode()`` 不再接受无参数调用 → 必须写
             ``c.decode(audio=0)``，否则 ``AssertionError: Container is not open``。
        本模块因此：
          - 数组输入：直接可用（faster-whisper 的数组路径不经过 av）；
          - **wav 文件**：用标准库 ``wave`` 自己解码（8/16/32 bit + 多声道混单声道
            + 线性重采样），完全不碰 av，**最稳**；
          - 其他格式：用 PyAV 19 的新签名解码，并对异常类型都给出可操作提示。
        """
        if self._model is None:
            raise RuntimeError("模型未加载或已close()")

        payload = self._to_array(audio)
        t0 = time.perf_counter()
        segments, info = self._model.transcribe(
            payload,
            language=language,
            vad_filter=vad_filter,
            beam_size=beam_size,
        )
        seg_list: List[Dict[str, Any]] = []
        parts: List[str] = []
        for s in segments:
            text = (s.text or "").strip()
            if not text:
                continue
            parts.append(text)
            seg_list.append(
                {
                    "start": round(float(s.start), 2),
                    "end": round(float(s.end), 2),
                    "text": text,
                }
            )
        dt = (time.perf_counter() - t0) * 1000
        return Transcript(
            text="".join(parts),
            language=getattr(info, "language", language or ""),
            segments=seg_list,
            latency_ms=dt,
            audio_duration_s=float(getattr(info, "duration", 0.0) or 0.0),
        )

    @staticmethod
    def _to_array(audio: Union[str, Path, Any]):
        """把各种输入统一成 16kHz 单声道 float32 数组。"""
        import numpy as np

        if isinstance(audio, np.ndarray):
            return audio.astype(np.float32)

        if isinstance(audio, (str, Path)):
            p = Path(audio)
            if not p.exists():
                raise FileNotFoundError(f"音频文件不存在：{p}")
            # wav 用标准库读，避开 av 的版本问题
            if p.suffix.lower() == ".wav":
                return ASRHandle._read_wav(p)
            # 其他格式退回 av。⚠️ PyAV 19 改了容器 API：
            #   旧写法 c.decode() 无参数 -> 抛 AssertionError: Container is not open
            #   新写法必须显式给流参数 c.decode(audio=0)
            # （这是实测出来的，不是照文档猜的：faster-whisper 1.2.1 用的还是旧写法。）
            try:
                import av

                with av.open(str(p)) as c:
                    frames = list(c.decode(audio=0))
                if not frames:
                    raise ValueError(f"{p.name} 中没有可解码的音频流")
                return np.concatenate(
                    [f.to_ndarray().reshape(-1) for f in frames]
                ).astype(np.float32)
            except (TypeError, AssertionError) as exc:
                raise RuntimeError(
                    f"读取 {p.suffix} 音频失败：{exc}\n"
                    "这通常是 PyAV 版本过新导致（faster-whisper 声明 av>=11 但无上限，"
                    "av 19 改了 Container.decode 的签名）。\n"
                    "两个办法：① 转成 wav 再试；② 降级："
                    "pip install 'av<19' --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/"
                ) from exc

        raise TypeError(f"不支持的音频输入类型：{type(audio)}")

    @staticmethod
    def _read_wav(path: Path, target_sr: int = 16000):
        """读 wav -> float32 单声道 16kHz。只用标准库，不碰 av。"""
        import wave

        import numpy as np

        with wave.open(str(path), "rb") as w:
            n_ch = w.getnchannels()
            sw = w.getsampwidth()
            sr = w.getframerate()
            raw = w.readframes(w.getnframes())

        if sw == 2:
            data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        elif sw == 4:
            data = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
        elif sw == 1:
            data = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
        else:
            raise ValueError(f"暂不支持的采样位宽：{sw * 8} bit（支持 8/16/32）")

        if n_ch > 1:
            data = data.reshape(-1, n_ch).mean(axis=1)

        if sr != target_sr:  # 线性重采样，够用于演示
            n_out = int(round(len(data) * target_sr / sr))
            data = np.interp(
                np.linspace(0, len(data) - 1, n_out, dtype=np.float64),
                np.arange(len(data), dtype=np.float64),
                data,
            ).astype(np.float32)
        return np.ascontiguousarray(data)

    # ------------------------------------------------------------ 生命周期
    def close(self) -> None:
        """释放模型与 CUDA 上下文。

        **8GB 机器上这一步是必须的**：不释放的话，
        紧接着加载 YOLO / InsightFace 会因为 CUDA 上下文占位而失败。
        """
        self._model = None
        try:
            import gc

            gc.collect()
        except Exception:
            pass

    def __enter__(self) -> "ASRHandle":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
