r"""从 HF 镜像直接拉取 embedding 模型权重到本地目录。

## 为什么不用 `SentenceTransformer('BAAI/bge-small-zh-v1.5')` 直接下

实测：本机 `huggingface.co` 被代理拦截（`Tunnel connection failed: 502`），
必须走镜像 `hf-mirror.com`。但装了 `transformers 5.17.0` /
`huggingface_hub 1.33.0` 后，库除了下载文件外还会请求若干 **API 元数据接口**，
镜像对这些接口返回的不是 JSON，于是报：

    json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)

而**单文件下载**（`/resolve/main/<file>`）在镜像上是正常的（实测 200 + 正确 JSON）。
所以这里绕开库的自动下载逻辑，自己按文件清单拉取，
落到本地目录后以**本地路径**加载 —— 顺带符合本项目「模型权重不与产物混放」的约定。

## 用法

    python scripts/fetch_embed_model.py            # 默认拉 bge-small-zh-v1.5
    python scripts/fetch_embed_model.py --check    # 只检查本地是否已完整

产物：`<repo>/models/bge-small-zh-v1.5/`
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

DEFAULT_REPO = "BAAI/bge-small-zh-v1.5"
DEFAULT_DIR_NAME = "bge-small-zh-v1.5"

# sentence-transformers 加载一个 BERT 类句向量模型所需的最小文件集。
# 少了任何一个都会在加载时报错，因此这里显式列出，而不是「下载整仓库」。
FILES = [
    "config.json",
    "config_sentence_transformers.json",
    "model.safetensors",
    "modules.json",
    "sentence_bert_config.json",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "vocab.txt",
    "1_Pooling/config.json",
]

MIRRORS = [
    "https://hf-mirror.com",
    "https://huggingface.co",
]


def fetch_one(repo: str, rel: str, dest: Path, mirror: str, timeout: int = 120) -> int:
    """下载单个文件，返回字节数。已存在且非空则跳过。"""
    if dest.exists() and dest.stat().st_size > 0:
        return dest.stat().st_size
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = f"{mirror}/{repo}/resolve/main/{rel}"
    req = urllib.request.Request(url, headers={"User-Agent": "workbuddy-fetch/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status}")
        blob = resp.read()
    if not blob:
        raise RuntimeError("空响应")
    dest.write_bytes(blob)
    return len(blob)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="从 HF 镜像拉取 embedding 模型")
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--dir-name", default=DEFAULT_DIR_NAME)
    parser.add_argument("--mirror", default=None, help="指定镜像；默认按 MIRRORS 顺序重试")
    parser.add_argument("--check", action="store_true", help="只检查，不下载")
    args = parser.parse_args(argv)

    target = REPO_ROOT / "models" / args.dir_name
    print(f"目标目录：{target}")

    if args.check:
        missing = [f for f in FILES if not (target / f).exists()]
        ok = not missing
        print(json.dumps({"ok": ok, "missing": missing,
                          "size_mb": round(
                              sum((target / f).stat().st_size for f in FILES
                                  if (target / f).exists()) / 1e6, 2)},
                         ensure_ascii=False, indent=2))
        return 0 if ok else 1

    mirrors = [args.mirror] if args.mirror else MIRRORS
    total = 0
    failed: list[str] = []

    for rel in FILES:
        done = False
        for mirror in mirrors:
            started = time.perf_counter()
            try:
                size = fetch_one(args.repo, rel, target / rel, mirror)
                elapsed = time.perf_counter() - started
                kb = size / 1024
                rate = kb / elapsed if elapsed > 0.01 else 0
                flag = "skip" if elapsed < 0.01 else f"{rate:.0f} KB/s"
                print(f"  [ok]   {rel:<40} {size / 1e6:>8.2f} MB  {flag}")
                total += size
                done = True
                break
            except (urllib.error.URLError, RuntimeError, TimeoutError, OSError) as exc:
                print(f"  [fail] {rel:<40} via {mirror}: {type(exc).__name__}: {exc}")
        if not done:
            failed.append(rel)

    print(f"\n合计 {total / 1e6:.2f} MB")
    if failed:
        print(f"未取到的文件：{failed}", file=sys.stderr)
        return 1
    print("完成。加载方式：SentenceTransformer(r'" + str(target) + "')")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
