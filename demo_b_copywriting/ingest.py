"""语料清洗层：raw → clean。

为什么单独一层（而不是在读语料的地方顺手 strip 一下）：
  1. 清洗规则需要**可审计**。面试会问「你的数据怎么处理的」，必须能指着代码说。
  2. raw 是人工撰写的原始文件，clean 是索引输入。两者混在一起，一旦指标异常就无法定位。
  3. 清洗报告要落盘。指标口径页要引用「评测集规模」，那个数字必须来自报告而不是印象。

清洗规则（全部为可机械判定项，**不做任何改写**）：

| 编号 | 规则 | 理由 |
|---|---|---|
| C1 | 去除零宽字符（\\u200b-\\u200f、\\ufeff） | 复制粘贴的常见污染，会污染分词边界 |
| C2 | 换行统一为 \\n，删除每行首尾空白 | 排版噪声 |
| C3 | 全角空格 \\u3000 折叠为半角空格 | 中文正文中全角空格多为排版残留 |
| C4 | 连续空白折叠为单个空格 | 同上 |
| C5 | 丢弃清洗后长度 < min_chars 的记录 | 过短文本无法承载风格信息，且会稀释检索 |
| C6 | 按清洗后文本去重（保留首次出现） | 重复条目会使检索 top-k 被同质内容占满 |

⚠️ **刻意不做的事**：
  - 不改标点（顿号与逗号的混用是中文写作特征，不是噪声）
  - 不做繁简转换（会改变原文的用词与语感）
  - 不做同义替换、不做扩写或压缩

任何改写都会污染「原文 vs 改写」的对照基线。清洗层只负责去掉**机器产生的**污染，
不负责让文本变得更好。

用法：
    python demo_b_copywriting/ingest.py
    python demo_b_copywriting/ingest.py --min-chars 15
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Tuple

from loguru import logger

HERE = Path(__file__).resolve().parent
RAW_DIR = HERE / "data" / "raw"
CLEAN_DIR = HERE / "data" / "clean"

MIN_CHARS = 20
DEFAULT_MAX_CHARS = 400

# C1 零宽与方向控制字符
_ZERO_WIDTH_RE = re.compile("[\u200b\u200c\u200d\u200e\u200f\ufeff]")
# C3/C4 空白折叠：全角空格与各类空白统一成一个半角空格
_SPACE_RE = re.compile(r"[ \t\u3000\u00a0]+")
# 换行折叠
_NEWLINE_RE = re.compile(r"\s*\n\s*")

# 中文句末标点，用于长文切块
_SENT_SPLIT_RE = re.compile(r"(?<=[。！？；!?;])")


@dataclass
class CleanReport:
    """清洗报告。落盘供口径页引用，避免「评测集规模」靠记忆。"""

    source: str
    read: int = 0
    kept: int = 0
    dropped_empty: int = 0
    dropped_short: int = 0
    dropped_dup: int = 0
    removed_chars: int = 0
    notes: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "read": self.read,
            "kept": self.kept,
            "dropped_empty": self.dropped_empty,
            "dropped_short": self.dropped_short,
            "dropped_dup": self.dropped_dup,
            "removed_chars": self.removed_chars,
            "notes": self.notes,
        }


def clean_text(raw: str) -> str:
    """按 C1–C4 清洗单条文本。纯函数，无副作用。"""
    if not raw:
        return ""
    text = _ZERO_WIDTH_RE.sub("", raw)          # C1
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _NEWLINE_RE.sub("\n", text)           # C2
    text = _SPACE_RE.sub(" ", text)              # C3 + C4
    return text.strip()


def split_sentences(text: str) -> List[str]:
    """按中文句末标点切句。保留标点在句尾。"""
    parts = [p.strip() for p in _SENT_SPLIT_RE.split(text or "")]
    return [p for p in parts if p]


def chunk_text(text: str, max_chars: int = DEFAULT_MAX_CHARS) -> List[str]:
    """把长文本按句打包成不超过 max_chars 的块。

    Demo B 的语料每条都在 max_chars 以内，因此实际不会切分。
    保留这段逻辑是为了语料扩充到 5k+ 条时不必重写索引管线 —— 逻辑现在就有，也现在就被测到。
    """
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    chunks: List[str] = []
    buf = ""
    for sent in split_sentences(text):
        if buf and len(buf) + len(sent) > max_chars:
            chunks.append(buf)
            buf = sent
        else:
            buf += sent
    if buf:
        chunks.append(buf)
    return chunks


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def load_raw(path: Path) -> List[Dict[str, Any]]:
    """读 JSONL。空行跳过；坏行报错并指出行号（不静默吞掉）。"""
    rows: List[Dict[str, Any]] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path.name} 第 {lineno} 行不是合法 JSON：{exc}") from exc
    return rows


def clean_records(
    records: List[Dict[str, Any]],
    *,
    source: str,
    min_chars: int = MIN_CHARS,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> Tuple[List[Dict[str, Any]], CleanReport]:
    """清洗一批记录，返回 (clean 记录, 报告)。"""
    report = CleanReport(source=source, read=len(records))
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()

    for rec in records:
        rich_text = rec.get("text") or rec.get("ai_text") or ""
        cleaned = clean_text(rich_text)
        report.removed_chars += max(0, len(rich_text) - len(cleaned))

        if not cleaned:
            report.dropped_empty += 1
            continue
        if len(cleaned) < min_chars:
            report.dropped_short += 1
            continue

        digest = text_hash(cleaned)
        if digest in seen:
            report.dropped_dup += 1
            continue
        seen.add(digest)

        item: Dict[str, Any] = {
            **{k: v for k, v in rec.items() if k not in {"text", "ai_text"}},
            "text": cleaned,
            "text_raw": rich_text,
            "char_count": len(cleaned),
            "text_hash": digest,
        }
        # pair 类记录的对照文本同样清洗，保持两侧口径一致
        if "ai_text" in rec:
            item["ai_text"] = clean_text(rec["ai_text"])
        if "human_text" in rec:
            item["human_text"] = clean_text(rec["human_text"])
        # 长文切块：单条超长时才产生多块，块 id 加序号后缀
        chunks = chunk_text(cleaned, max_chars=max_chars)
        if len(chunks) > 1:
            report.notes.append(f"{rec.get('id')} 被切成 {len(chunks)} 块（>{max_chars} 字）")
            item["chunks"] = chunks
        item["chunk_count"] = len(chunks)
        out.append(item)

    report.kept = len(out)
    return out, report


def write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def run(raw_dir: Path = RAW_DIR, clean_dir: Path = CLEAN_DIR, *, min_chars: int = MIN_CHARS) -> Dict[str, Any]:
    """清洗 raw_dir 下全部 .jsonl，输出到 clean_dir，并写清洗报告。"""
    raw_files = sorted(raw_dir.glob("*.jsonl"))
    if not raw_files:
        raise FileNotFoundError(f"{raw_dir} 下没有 .jsonl 语料文件")

    reports: List[CleanReport] = []
    for src in raw_files:
        records = load_raw(src)
        rows, report = clean_records(records, source=src.name, min_chars=min_chars)
        write_jsonl(clean_dir / src.name, rows)
        reports.append(report)
        logger.info(
            f"{src.name}: 读入 {report.read} → 保留 {report.kept}"
            f"（短 {report.dropped_short} / 重 {report.dropped_dup} / 空 {report.dropped_empty}）"
        )

    payload = {
        "generated_by": "demo_b_copywriting/ingest.py",
        "rules": ["C1_zero_width", "C2_newline", "C3_fullwidth_space", "C4_space_collapse",
                  "C5_min_chars", "C6_dedup"],
        "min_chars": min_chars,
        "files": [r.as_dict() for r in reports],
        "total_kept": sum(r.kept for r in reports),
    }
    (clean_dir / "ingest_report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Demo B 语料清洗 raw → clean")
    parser.add_argument("--raw-dir", type=Path, default=RAW_DIR)
    parser.add_argument("--clean-dir", type=Path, default=CLEAN_DIR)
    parser.add_argument("--min-chars", type=int, default=MIN_CHARS)
    args = parser.parse_args()

    payload = run(args.raw_dir, args.clean_dir, min_chars=args.min_chars)
    logger.info(f"清洗完成，共保留 {payload['total_kept']} 条 → {args.clean_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
