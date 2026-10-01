#!/usr/bin/env python3
"""
humanize-chinese HTTP API server (stdlib only, no dependencies)

Endpoints:
  GET  /health              -> {"status": "ok"}
  POST /detect              -> {text, mode?}            AI-pattern detection score
  POST /humanize            -> {text, mode?, aggressive?, scene?, style?}  rewrite only
  POST /full                -> {text, mode?, aggressive?, scene?, style?}  detect + rewrite + re-detect + duplicate check

mode: "general" (default) | "academic"
  general  -> humanize_cn.py
  academic -> academic_cn.py (CNKI/VIP/Wanfang-oriented)

Duplicate check approximates the CNKI rule: any run of >=13 consecutive
identical characters counts as duplicated text.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.join(SCRIPT_DIR, "scripts")
PY = sys.executable or "python3"
PORT = int(os.environ.get("HUMANIZE_PORT", "8765"))
MAX_BODY = 10 * 1024 * 1024  # 10MB
SCRIPT_TIMEOUT = 180

_subprocess_lock = threading.Lock()  # keep CPU sane on big concurrent jobs


def run_script(script, args, input_text=None, expect_json=False):
    cmd = [PY, os.path.join(SCRIPTS_DIR, script)] + args
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    with _subprocess_lock:
        result = subprocess.run(
            cmd,
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=SCRIPT_TIMEOUT,
            env=env,
        )
    if result.returncode != 0:
        raise RuntimeError(f"{script} failed: {result.stderr.strip()[:500]}")
    out = result.stdout
    if expect_json:
        # tolerate leading/trailing noise around the JSON object
        start = out.find("{")
        end = out.rfind("}")
        if start == -1 or end == -1:
            raise RuntimeError(f"{script}: no JSON in output")
        return json.loads(out[start : end + 1])
    return out.strip()


def detect(text):
    """Unified detector (detect_cn.py) so before/after scores are comparable."""
    return run_script("detect_cn.py", ["-j"], input_text=text, expect_json=True)


def humanize_general(text, scene="general", aggressive=False, style=None):
    args = ["--no-stats", "--scene", scene]
    if aggressive:
        args.append("-a")
    if style:
        args.extend(["--style", style])
    return run_script("humanize_cn.py", args, input_text=text)


def humanize_academic(text, aggressive=False):
    # academic_cn.py writes the rewritten text to a file
    fd, out_path = tempfile.mkstemp(suffix=".txt", prefix="hz_ac_")
    os.close(fd)
    try:
        args = ["-o", out_path, "--no-stats"]
        if aggressive:
            args.append("-a")
        run_script("academic_cn.py", args, input_text=text)
        with open(out_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    finally:
        try:
            os.remove(out_path)
        except OSError:
            pass


_WS_RE = re.compile(r"\s+")


def _normalize(s):
    return _WS_RE.sub("", s or "")


def dup_check(original, rewritten, n=13):
    """
    CNKI-style duplicate approximation:
    - slide an n-char window (default 13) over both texts
    - overlap_pct  = share of rewritten windows that appear in the original
    - longest_common = longest run of consecutive matching windows
      (exact longest common substring length at window granularity)
    """
    o = _normalize(original)
    w = _normalize(rewritten)
    if not o or not w:
        return {"overlap_pct": 0.0, "longest_common": 0, "risk": "unknown",
                "note": "empty text"}
    if len(o) < n or len(w) < n:
        n = max(2, min(len(o), len(w), 5))
    ograms = {o[i : i + n] for i in range(len(o) - n + 1)}
    hits = [w[i : i + n] in ograms for i in range(len(w) - n + 1)]
    total = len(hits)
    hit_count = sum(hits)
    overlap_pct = round(hit_count / total * 100, 1) if total else 0.0
    best = cur = 0
    for h in hits:
        cur = cur + 1 if h else 0
        if cur > best:
            best = cur
    longest_common = (best + n - 1) if best else 0
    if overlap_pct < 15:
        risk = "low"
    elif overlap_pct < 30:
        risk = "medium"
    else:
        risk = "high"
    return {
        "window_chars": n,
        "overlap_pct": overlap_pct,
        "longest_common": longest_common,
        "risk": risk,
        "note": f"连续{n}字重合率 {overlap_pct}%，最长连续重合 {longest_common} 字",
    }


def handle_full(payload):
    text = (payload.get("text") or "").strip()
    if not text:
        raise ValueError("text is required")
    mode = payload.get("mode") or "general"
    aggressive = bool(payload.get("aggressive"))
    scene = payload.get("scene") or "general"
    style = payload.get("style") or None

    before = detect(text)
    if mode == "academic":
        rewritten = humanize_academic(text, aggressive=aggressive)
    else:
        rewritten = humanize_general(
            text, scene=scene, aggressive=aggressive, style=style
        )
    if not rewritten:
        rewritten = text
        after = before
    else:
        after = detect(rewritten)
    dup = dup_check(text, rewritten)

    return {
        "mode": mode,
        "aggressive": aggressive,
        "text_original": text,
        "text_rewritten": rewritten,
        "before": {
            "score": before.get("score"),
            "level": before.get("level"),
            "total_issues": before.get("total_issues"),
            "metrics": before.get("metrics"),
        },
        "after": {
            "score": after.get("score"),
            "level": after.get("level"),
            "total_issues": after.get("total_issues"),
            "metrics": after.get("metrics"),
        },
        "score_drop": (before.get("score") or 0) - (after.get("score") or 0),
        "issues_before": list((before.get("issues") or {}).keys()),
        "issues_after": list((after.get("issues") or {}).keys()),
        "worst_sentences_before": (before.get("worst_sentences") or [])[:3],
        "worst_sentences_after": (after.get("worst_sentences") or [])[:3],
        "duplicate": dup,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "humanize-cn/2.1"

    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        sys.stderr.write("[humanize-cn] %s\n" % (fmt % args))

    def do_GET(self):
        if self.path.startswith("/health"):
            self._send(200, {"status": "ok", "version": "2.1.0"})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return self._send(413, {"error": "body too large"})
            raw = self.rfile.read(length) if length else b"{}"
            payload = json.loads(raw.decode("utf-8") or "{}")
        except Exception as e:
            return self._send(400, {"error": f"bad request: {e}"})

        path = self.path.split("?")[0]
        try:
            if path == "/detect":
                text = (payload.get("text") or "").strip()
                if not text:
                    raise ValueError("text is required")
                return self._send(200, detect(text))
            if path == "/humanize":
                text = (payload.get("text") or "").strip()
                if not text:
                    raise ValueError("text is required")
                if (payload.get("mode") or "general") == "academic":
                    out = humanize_academic(text, aggressive=bool(payload.get("aggressive")))
                else:
                    out = humanize_general(
                        text,
                        scene=payload.get("scene") or "general",
                        aggressive=bool(payload.get("aggressive")),
                        style=payload.get("style"),
                    )
                return self._send(200, {"text": out})
            if path == "/full":
                return self._send(200, handle_full(payload))
            return self._send(404, {"error": "not found"})
        except ValueError as e:
            return self._send(400, {"error": str(e)})
        except Exception as e:
            return self._send(500, {"error": str(e)})


def main():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"[humanize-cn] listening on 0.0.0.0:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
