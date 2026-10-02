"""Demo A 视觉/语音依赖安装（ONNX 全栈，**不需要 torch**）。

为什么不用 torch cu128
--------------------
本机实测阿里云镜像带宽约 343 KB/s，torch 2.7.0+cu128 + torchvision 约 2.7 GB
→ 约 137 分钟。而Demo A 的三个模型都不依赖 torch：

    YOLO11n      -> onnxruntime-gpu（官方 onnx，仅 10.42 MiB）
    InsightFace-> onnxruntime-gpu
    faster-whisper -> CTranslate2（自带 CUDA 支持）

合计约 500 MB。已实测 `CUDAExecutionProvider` 在本机可用（device=GPU）。

⚠️ 本脚本包含三个**必须按顺序执行**的坑（都是本机实测踩出来的）：

坑 1：`insightface` 无条件依赖 CPU 版 `onnxruntime`
    它的 METADATA 里是 `Requires-Dist: onnxruntime`（无环境标记）。
    若按常规 `pip install insightface`，pip 会装CPU 版，
    **覆盖掉 GPU 版的 `onnxruntime.dll`，CUDA provider 直接消失**。
    实测：装完 insightface 后 providers 从
    ['Tensorrt','CUDA','CPU'] 变成 ['Azure','CPU']。
    对策：`--no-deps` 装 insightface，再手动补它真正需要的依赖。

坑 2：`faster-whisper` 同样会拉 CPU 版 `onnxruntime`
    实测装完它 providers 变成 ['Azure','CPU']，CUDA 再次消失。
    对策：同样 `--no-deps`，手动补 huggingface_hub / tokenizers 等。

坑 3：本机沙箱的**批量删除守卫**会拦截 pip 的 uninstall / --force-reinstall
    实测 `pip uninstall onnxruntime`、`--force-reinstall`、`降级安装` 均以
    `SystemExit: 1` + `SAFE_DELETE_BULK_GUARD_ERROR` 失败，
    并留下 `~onnxruntime` 残缺目录（导致后续 import 报 ModuleNotFoundError）。
    含义：**降级 / 修复必须能在普通终端或无沙箱环境完成**。
    若在沙箱内反复重试，会把环境卡在「旧版已删、新版未装」的中间态。
    对策：本脚本从一开始就用 --no-deps 避免覆盖（坑 1、2），
    使正常路径完全不依赖卸载/覆盖操作。

坑 4：ORT 版本与 CUDA 运行时主版本必须匹配
    ORT >= 1.27 是 CUDA 13 构建（要 cublasLt64_13.dll），
    <= 1.26 才是 CUDA 12.8 构建（要 cublasLt64_12.dll）。
    本机只有 ComfyUI 带的 CUDA 12.8，所以必须锁 ORT_PINNED。
    ⚠️ ORT 在 CUDA 缺依赖时**不抛异常，只静默降级到 CPU** ——
    `get_available_providers()` 仍会列出 CUDAExecutionProvider，
    必须以 `Session.get_providers()` 为准。详见 demo_a_security/vision.py。

用法
----
    python scripts/setup_demo_a_vision.py            # 按正确顺序安装
    python scripts/setup_demo_a_vision.py --check    # 只检查，不安装
    python scripts/setup_demo_a_vision.py --reset-ort  # 仅在 providers 丢失时用

顺序很重要：**先装 onnxruntime-gpu，最后装那些会覆盖它的包（用 --no-deps）**。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

ROOT = Path(__file__).resolve().parent.parent
PY = ROOT / ".venv" / "Scripts" / "python.exe"

# GPU 推理运行时的**锁定版本**，不能写 >= 1.27。
#
# 官方文档（onnxruntime.ai CUDA-ExecutionProvider#requirements，已实测核对）：
#   onnxruntime >= 1.27 的 PyPI 默认包构建于 CUDA 13.0，依赖 cublasLt64_13.dll；
#   <= 1.26.x 构建于 CUDA 12.8，依赖 cublasLt64_12.dll。
# 本机没有 CUDA Toolkit（只有驱动），但 D:\ComfyUI 的 torch 2.7.0+cu128
# 自带完整 CUDA 12.8 运行时，因此必须锁 <= 1.26 才能复用那套 DLL。
# 装成 >= 1.27 会静默降级到 CPU（ORT 不抛异常），看起来「能用」但其实没上 GPU。
ORT_PINNED = "onnxruntime-gpu==1.26.0"

# 本机唯一可用的 pip 源（清华源不可用）
INDEX = "https://mirrors.aliyun.com/pypi/simple/"

# 统一加 --no-cache-dir：沙箱的批量删除保护会拦截 pip 的缓存清理
COMMON = ["--no-cache-dir", "-i", INDEX]


def run(cmd: List[str], timeout: int = 1800) -> Tuple[int, str]:
    """执行命令并返回 (returncode, 输出尾部)。

    注意：这里刻意**不**开shell、不拼字符串，避免路径含空格时出错。
    """
    print(f"\n$ {' '.join(cmd)}")
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, "超时"
    out = (p.stdout or "") + (p.stderr or "")
    tail = "\n".join(out.strip().splitlines()[-6:])
    if p.returncode == 0:
        print("  -> 成功")
    else:
        print(f"  -> 失败 rc={p.returncode}")
        if tail:
            print("     " + tail.replace("\n", "\n     "))
    return p.returncode, tail


def pip_install(pkgs: List[str], *, no_deps: bool = False, timeout: int = 1800) -> int:
    cmd = [str(PY), "-m", "pip", "install"] + COMMON
    if no_deps:
        cmd.append("--no-deps")
    cmd += pkgs
    rc, _ = run(cmd, timeout=timeout)
    return rc


# ------------------------------------------------------------------ 检查
def check(verbose: bool = True) -> dict:
    """检查依赖与 GPU provider 状态。这是**唯一的事实来源**。

    不依赖 pip 的「已安装」列表 —— 因为包可能被 --no-deps 装成
    「元数据在但依赖缺失」的状态，只有真实 import 才能发现。
    """
    import importlib

    state: dict = {}
    mods = ["onnxruntime", "cv2", "numpy", "onnx", "skimage",
            "insightface", "faster_whisper", "ctranslate2"]
    for m in mods:
        try:
            importlib.import_module(m)
            state[m] = True
        except Exception:
            state[m] = False

    providers: List[str] = []
    cuda_available = False
    if state.get("onnxruntime"):
        try:
            import onnxruntime as ort

            providers = list(ort.get_available_providers())
            state["ort_version"] = ort.__version__
        except Exception:
            pass

        # ⚠️ 必须用**真实会话**验证，不能只看 get_available_providers()。
        # ORT 在 CUDA 缺依赖（如 cublasLt64_13.dll 缺失）时**不抛异常，
        # 只是静默回退 CPU**，而 get_available_providers() 依然列出
        # CUDAExecutionProvider —— 只看它会报出假绿灯。
        # 本项目已经因此误报过一次「CUDA 可用」。
        try:
            import onnx as _onnx  # noqa: F401

            model = ROOT / "models" / "demo_a" / "yolo11n.onnx"
            if model.exists():
                from demo_a_security.vision import YoloDetector

                det = YoloDetector()
                providers = list(det.info.providers)
                cuda_available = det.info.is_fallback is False
                state["verified_by"] = "真实推理会话"
            else:
                cuda_available = "CUDAExecutionProvider" in providers
                state["verified_by"] = "仅 available（权重缺失，未做会话验证）"
        except Exception as exc:  # noqa: BLE001
            state["verify_error"] = f"{type(exc).__name__}: {exc}"
            cuda_available = "CUDAExecutionProvider" in providers

    state["providers"] = providers
    state["cuda_available"] = cuda_available

    if verbose:
        print("依赖导入状态：")
        for m in mods:
            print(f"  {'OK  ' if state[m] else 'MISS'} {m}")
        print(f"onnxruntime 版本：{state.get('ort_version', '—')}")
        print(f"实际 providers：{providers or '—'}")
        print(f"验证方式：{state.get('verified_by', '—')}")
        print(f"CUDA 可用：{'是' if cuda_available else '否'}")
        if not state.get("onnxruntime"):
            print()
            print("  注：onnxruntime 是insightface 的导入前提，")
            print("      所以它缺失时 insightface 也会显示 MISS —— 真实原因只有一个。")
        elif not cuda_available:
            print()
            print("  注：ORT 在 CUDA 缺依赖时**不抛异常**，只静默降级 CPU。")
            print("      若安装的是 onnxruntime>=1.27（CUDA 13 构建），")
            print("      会一直报缺 cublasLt64_13.dll —— 本机需锁 <= 1.26。")
    return state


# ------------------------------------------------------------------ 安装步骤
def install_all() -> int:
    """按正确顺序安装。

    顺序原则：**先把会被覆盖的包装好（不带依赖），最后装 onnxruntime-gpu**，
    这样 GPU 版一定是最后落盘的那一份。
    """
    steps: List[Tuple[str, List[str], bool]] = [
        # 1. 纯 Python / 无冲突的先行
        ("基础图像与工具", ["opencv-python-headless", "numpy"], False),
        ("人脸识别本体（--no-deps，避坑 1）", ["insightface>=0.7.3"], True),
        ("人脸识别的实际依赖", ["onnx", "scikit-image", "tqdm", "requests", "scipy"], False),
        ("语音识别本体（--no-deps，避坑 2）", ["faster-whisper"], True),
        ("语音识别的实际依赖", ["ctranslate2", "huggingface_hub", "tokenizers", "av"], False),
        # 2. GPU 版最后装，确保不被前面的包覆盖；且必须锁 <= 1.26（避坑 4）
        (f"GPU 推理运行时（最后装，避坑 3/4）", [ORT_PINNED], True),
    ]

    for title, pkgs, no_deps in steps:
        print(f"\n=== {title} ===")
        rc = pip_install(pkgs, no_deps=no_deps)
        if rc != 0:
            print(f"  !! 该步失败，后续步骤可能无意义。停止。")
            return rc

    return 0


def reset_ort() -> int:
    """修复 providers 丢失：重装 GPU 版。

    ⚠️ 本机沙箱的批量删除守卫会拦截 --force-reinstall。
    必须在**普通终端**（非沙箱）执行本步骤。
    """
    print("将重装 onnxruntime-gpu（需要覆盖现有文件）。")
    print("注意：本机沙箱环境下该操作会被 SAFE_DELETE_BULK_GUARD 拦截，")
    print("      请在普通终端运行：")
    print(f"      {PY} -m pip install --no-cache-dir --force-reinstall "
          f"--no-deps -i {INDEX} onnxruntime-gpu")
    return 0


def download_weights() -> None:
    """下载 YOLO11n 官方 ONNX 权重（10.42 MiB）。

    为什么不用 ultralytics 的 .pt：.pt 需要 torch 才能导出/加载，
    而 YOLO11n 官方直接提供 onnx，可被 onnxruntime 直接消费。
    """
    import urllib.request

    url = ("https://github.com/ultralytics/assets/releases/download/"
           "v8.3.0/yolo11n.onnx")
    out_dir = ROOT / "models" / "demo_a"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "yolo11n.onnx"
    if out.exists() and out.stat().st_size > 1_000_000:
        print(f"\n权重已存在：{out}（{out.stat().st_size/1048576:.2f} MiB）")
        return
    print(f"\n下载 YOLO11n ONNX 权重 -> {out}")
    try:
        urllib.request.urlretrieve(url, out)
        print(f"  完成：{out.stat().st_size/1048576:.2f} MiB")
    except Exception as exc:
        print(f"  失败：{type(exc).__name__}: {exc}")
        print(f"  可手动下载后放到该路径：{url}")


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Demo A 视觉/语音依赖安装（ONNX 全栈）")
    ap.add_argument("--check", action="store_true", help="只检查状态，不安装")
    ap.add_argument("--reset-ort", action="store_true", help="重装 onnxruntime-gpu")
    ap.add_argument("--no-weights", action="store_true", help="跳过权重下载")
    args = ap.parse_args(argv)

    print("=" * 66)
    print("Demo A 视觉/语音依赖（ONNX 全栈，无需 torch cu128）")
    print("=" * 66)

    if args.reset_ort:
        return reset_ort()

    if args.check:
        state = check()
        # 只报「根因」缺失项：onnxruntime 缺失时，insightface 的 MISS 是级联，
        # 单独列出它会让人去装一个其实装不上的包。
        missing = [k for k, v in state.items()
                   if isinstance(v, bool) and not v and k != "cuda_available"]
        if not state.get("onnxruntime") and "insightface" in missing:
            missing.remove("insightface")
            print("\n  已省略 insightface：它是 onnxruntime 缺失导致的级联失败。")
        return 0 if not missing and state["cuda_available"] else 1

    rc = install_all()
    print("\n" + "=" * 66)
    state = check()
    ok = all(state.get(m) for m in
             ["onnxruntime", "cv2", "insightface", "faster_whisper"])
    ok = ok and state["cuda_available"]
    if not ok:
        print("\n安装未完全成功。常见原因：")
        if not state["cuda_available"]:
            print("  · CUDA provider 丢失 → 在普通终端执行 --reset-ort")
        print("\n详细状态见上方输出。")
        return 1 or rc

    if not args.no_weights:
        download_weights()

    print("\n全部就绪。")
    return 0


if __name__ == "__main__":
    sys.exit(main())