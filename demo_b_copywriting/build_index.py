"""构建 Demo B 的检索索引。

数据流：
    data/clean/style_corpus.jsonl
        → 展开（长文切块）
        → 向量化（common.embed: bge-small-zh 优先，hashing 兜底）
        → 入库（common.vectorstore: FAISS 优先，numpy 兜底）
        → data/index/demo_b/{index.faiss, vectors.npy, meta.jsonl, info.json, build_info.json}

⚠️ 一个刻意的设计决策：**同时保存两份向量**
  - `index.faiss` —— 后端原生索引，加载快，是**加速副本**
  - `vectors.npy` + `meta.jsonl` —— numpy 布局，任何后端都能读，是**权威副本**

理由：演示机不保证装了 faiss。若只存 FAISS 索引，一旦 faiss 不可用，
索引就完全无法加载，「可降级」这个设计承诺当场失效。
存两份的代价是磁盘（Demo 规模下 < 1 MB），换来的是「换后端不改指标」这句话真正成立。

用法：
    python demo_b_copywriting/build_index.py
    python demo_b_copywriting/build_index.py --encoder hashing --store numpy
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
from loguru import logger

from common.embed import get_encoder
from common.vectorstore import get_store

HERE = Path(__file__).resolve().parent
CLEAN_DIR = HERE / "data" / "clean"
INDEX_DIR = HERE.parent / "data" / "index" / "demo_b"

KB_FILENAME = "style_corpus.jsonl"


# ============================================================ 语料 → 文档


def load_clean(clean_dir: Path = CLEAN_DIR) -> List[Dict[str, Any]]:
    """读取清洗后的知识库语料。"""
    path = clean_dir / KB_FILENAME
    if not path.exists():
        raise FileNotFoundError(f"缺少 {path} —— 请先运行 `python demo_b_copywriting/ingest.py`")
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not rows:
        raise ValueError(f"{path} 为空")
    return rows


def corpus_docs(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把语料记录展开为「检索文档」。

    两类记录都产出**人味文本**作为检索锚点：
      - kind=human_style → 文档 = 范文本身
      - kind=pair        → 文档 = 改写后的 human_text；同时把 ai_text 存进 meta，
                           供提示词构造 few-shot 对照（检索命中时能拿到「改前 / 改后」）

    为什么不把 ai_text 也建成文档：那会把「AI 味文本」当成风格锚点检索出来注入提示词，
    方向正好相反。
    """
    docs: List[Dict[str, Any]] = []
    for rec in records:
        if rec.get("kind") == "pair":
            # ⚠️ 显式优先 human_text：pair 记录里 ai_text 与 human_text 并存，
            # 取错就变成「用 AI 味文本当风格锚点」，方向相反且不报错。
            anchor = rec.get("human_text") or rec.get("text") or ""
            ai_text = rec.get("ai_text") or ""
            if anchor and ai_text and anchor == ai_text:
                raise ValueError(
                    f"{rec.get('id')}: pair 记录的锚点与 ai_text 完全相同，"
                    "说明取错了字段 —— 风格锚点必须是改写后的人味文本。"
                )
        else:
            anchor = rec.get("text") or ""
            ai_text = ""

        chunks = rec.get("chunks") or [anchor]
        for idx, chunk in enumerate(chunks):
            doc_id = rec["id"] if len(chunks) == 1 else f"{rec['id']}#{idx}"
            docs.append(
                {
                    "doc_id": doc_id,
                    "id": rec.get("id"),
                    "vertical": rec.get("vertical") or "未分类",
                    "kind": rec.get("kind") or "unknown",
                    "title": rec.get("title") or "",
                    "text": chunk,
                    "notes": rec.get("notes") or "",
                    "ai_text": ai_text,
                    "char_count": len(chunk),
                }
            )
    return docs


# ============================================================ 构建


def build(
    *,
    clean_dir: Path = CLEAN_DIR,
    out_dir: Path = INDEX_DIR,
    prefer_encoder: str = "auto",
    prefer_store: str = "auto",
) -> Dict[str, Any]:
    """构建并落盘索引。返回 build_info。"""
    records = load_clean(clean_dir)
    docs = corpus_docs(records)
    if not docs:
        raise ValueError("没有可索引的文档")

    encoder = get_encoder(prefer_encoder)
    texts = [d["text"] for d in docs]
    vectors = encoder.encode(texts)
    vectors = np.asarray(vectors, dtype=np.float32)

    if vectors.ndim != 2 or vectors.shape[0] != len(docs):
        raise ValueError(
            f"向量形状异常：{vectors.shape}，期望 ({len(docs)}, dim)"
        )

    dim = int(vectors.shape[1])
    store = get_store(dim=dim, prefer=prefer_store)
    store.add(vectors, docs)

    out_dir.mkdir(parents=True, exist_ok=True)
    store.save(str(out_dir))                      # 后端原生索引（加速副本）
    np.save(out_dir / "vectors.npy", vectors)     # numpy 布局（权威副本，任何后端可读）

    corpus_digest = hashlib.sha256(
        "".join(d["doc_id"] + d["text"] for d in docs).encode("utf-8")
    ).hexdigest()[:16]

    build_info: Dict[str, Any] = {
        "generated_by": "demo_b_copywriting/build_index.py",
        "built_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "corpus_file": str((clean_dir / KB_FILENAME).relative_to(HERE.parent)),
        "corpus_digest": corpus_digest,
        "doc_count": len(docs),
        "source_record_count": len(records),
        "verticals": sorted({d["vertical"] for d in docs}),
        "encoder": encoder.info.as_dict(),
        "store": store.info.as_dict(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "note": (
            "检索指标只在 build_info 记录的 encoder/store 组合下成立。"
            "换了后端必须重建索引并重新评测 —— 这是「可复现」的前提。"
        ),
    }
    (out_dir / "build_info.json").write_text(
        json.dumps(build_info, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    logger.info(
        f"索引构建完成：{len(docs)} 篇文档 / dim={dim} / "
        f"encoder={encoder.info.backend} / store={store.info.backend}"
    )
    if encoder.info.is_fallback:
        logger.warning(
            "当前使用兜底向量化后端（无真语义），检索结果仅可用于跑通链路；"
            "正式评测请安装 torch + sentence-transformers 后重建索引。"
        )
    return build_info


def main() -> int:
    parser = argparse.ArgumentParser(description="构建 Demo B 检索索引")
    parser.add_argument("--clean-dir", type=Path, default=CLEAN_DIR)
    parser.add_argument("--out-dir", type=Path, default=INDEX_DIR)
    parser.add_argument("--encoder", default="auto", choices=["auto", "st", "hashing"])
    parser.add_argument("--store", default="auto", choices=["auto", "faiss", "numpy"])
    args = parser.parse_args()

    info = build(
        clean_dir=args.clean_dir,
        out_dir=args.out_dir,
        prefer_encoder=args.encoder,
        prefer_store=args.store,
    )
    print(json.dumps(info, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
