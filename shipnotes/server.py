"""ShipNotes HTTP server. Standard library only: no pip install needed.

    python3 -m shipnotes.server            # listens on 127.0.0.1:8411

Endpoints
    GET  /                 single-page UI
    GET  /api/health       model status and queue length
    POST /api/notes        JSON in, NDJSON event stream out
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import gitsource, llm, pipeline
from .rules import parse_text

HOST = os.environ.get("SHIPNOTES_HOST", "127.0.0.1")
PORT = int(os.environ.get("SHIPNOTES_PORT", "8411"))
MAX_BODY = int(os.environ.get("SHIPNOTES_MAX_BODY", str(64 * 1024)))
MAX_QUEUE = int(os.environ.get("SHIPNOTES_MAX_QUEUE", "4"))
STATIC = Path(__file__).resolve().parent.parent / "static"

# One CPU core means one generation at a time. Everyone else waits in line.
_job_lock = threading.Lock()
_queue_lock = threading.Lock()
_waiting = 0
_cache: "OrderedDict[str, list[dict]]" = OrderedDict()
_CACHE_MAX = 64


def _cache_key(items: list[dict], lang: str, title: str) -> str:
    h = hashlib.sha256()
    h.update(json.dumps([items, lang, title], sort_keys=True).encode())
    return h.hexdigest()


class Handler(BaseHTTPRequestHandler):
    server_version = "ShipNotes/0.1"

    def log_message(self, fmt, *args):  # keep logs short, no IPs or bodies
        pass

    def _json(self, code: int, obj: dict) -> None:
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            data = (STATIC / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(data)
        elif self.path == "/api/health":
            self._json(200, {"llm": llm.health(), "model": "gemma-3-1b-it Q4_K_M",
                             "busy": _job_lock.locked(), "waiting": _waiting})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/api/notes":
            return self._json(404, {"error": "not found"})
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > MAX_BODY:
            return self._json(413, {"error": f"Input is limited to {MAX_BODY // 1024} KB."})
        try:
            req = json.loads(self.rfile.read(n))
        except Exception:
            return self._json(400, {"error": "Bad JSON."})

        lang = "zh" if req.get("lang") == "zh" else "en"
        title = str(req.get("title") or "")[:80]
        link_base = ""
        source = {}
        try:
            if req.get("mode") == "repo":
                src = gitsource.fetch_range(str(req.get("repo", "")), str(req.get("from", "")),
                                            str(req.get("to", "")))
                items = list(reversed(src["commits"]))  # newest first
                link_base = src["url"]
                generic = src["repo"].lower() in {"cli", "core", "app", "api", "server", "web", "client", "sdk"}
                name = f"{src['owner']} {src['repo']}" if generic else src["repo"]
                title = title or f"{name} {src['head']}"
                source = {k: src[k] for k in ("owner", "repo", "base", "head", "total", "truncated", "url")}
            else:
                items = parse_text(str(req.get("text", "")))
        except gitsource.SourceError as e:
            return self._json(400, {"error": str(e)})
        if not items:
            return self._json(400, {"error": "No commits found in the input."})

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        def emit(ev: dict) -> None:
            self.wfile.write((json.dumps(ev, ensure_ascii=False) + "\n").encode())
            self.wfile.flush()

        if source:
            emit({"type": "source", **source})
        key = _cache_key(items, lang, title)
        if key in _cache:
            _cache.move_to_end(key)
            for ev in _cache[key]:
                ev = dict(ev)
                if ev["type"] == "done":
                    ev["cached"] = True
                emit(ev)
            return

        global _waiting
        with _queue_lock:
            if _waiting >= MAX_QUEUE:
                emit({"type": "error", "error": "ShipNotes is busy (it runs on one CPU core). Try again in a minute."})
                return
            _waiting += 1
        try:
            if _job_lock.locked():
                emit({"type": "queued", "ahead": _waiting})
            with _job_lock:
                with _queue_lock:
                    _waiting -= 1
                events = []
                for ev in pipeline.run(items, lang=lang, title=title, link_base=link_base):
                    events.append(ev)
                    try:
                        emit(ev)
                    except (BrokenPipeError, ConnectionResetError):
                        pass  # keep going so the result is cached for a retry
                _cache[key] = events
                while len(_cache) > _CACHE_MAX:
                    _cache.popitem(last=False)
        except Exception:
            try:
                emit({"type": "error", "error": "Something went wrong while generating notes."})
            except Exception:
                pass


def main() -> None:
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    srv.daemon_threads = True
    print(f"ShipNotes on http://{HOST}:{PORT}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
