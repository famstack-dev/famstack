#!/usr/bin/env python3
"""Logging proxy between the rig agent and oMLX.

The proxy forwards every request to the real endpoint. It records the
byte-exact request body, the usage object, and the duration in a JSONL
file. This shows the full context the agent sends on each request.

Usage: proxy.py [--listen 42012] [--target http://localhost:8888] [--log FILE]
"""

import argparse
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_LOCK = threading.Lock()


def _extract_usage(body: bytes, content_type: str) -> dict | None:
    """Read the usage object from a JSON or SSE response body."""
    text = body.decode("utf-8", "replace")
    if "text/event-stream" in content_type:
        usage = None
        for line in text.splitlines():
            if line.startswith("data:") and '"usage"' in line:
                try:
                    chunk = json.loads(line[5:].strip())
                    usage = chunk.get("usage") or usage
                except json.JSONDecodeError:
                    pass
        return usage
    try:
        return json.loads(text).get("usage")
    except json.JSONDecodeError:
        return None


class Handler(BaseHTTPRequestHandler):
    target = "http://localhost:8888"
    log_path = Path("proxy-log.jsonl")
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _forward(self, body: bytes | None):
        url = self.target + self.path
        req = urllib.request.Request(url, data=body, method=self.command)
        for h in ("Authorization", "Content-Type", "Accept"):
            if self.headers.get(h):
                req.add_header(h, self.headers[h])
        start = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=900) as resp:
                data = resp.read()
                status = resp.status
                ctype = resp.headers.get("Content-Type", "")
        except urllib.error.HTTPError as e:
            data, status, ctype = e.read(), e.code, ""
        except Exception as e:
            data, status, ctype = json.dumps({"error": str(e)}).encode(), 502, "application/json"
        duration = time.monotonic() - start

        self.send_response(status)
        self.send_header("Content-Type", ctype or "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

        record = {
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "path": self.path,
            "status": status,
            "duration_s": round(duration, 3),
        }
        if body and self.path.endswith("/chat/completions"):
            try:
                payload = json.loads(body)
                record["model"] = payload.get("model")
                record["n_messages"] = len(payload.get("messages", []))
                record["messages"] = payload.get("messages")
                record["tools"] = [t.get("function", {}).get("name")
                                   for t in payload.get("tools") or []]
            except json.JSONDecodeError:
                record["raw_request"] = body.decode("utf-8", "replace")[:2000]
            record["usage"] = _extract_usage(data, ctype)
        with _LOCK, self.log_path.open("a") as fh:
            fh.write(json.dumps(record) + "\n")

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        self._forward(self.rfile.read(length))

    def do_GET(self):
        self._forward(None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen", type=int, default=42012)
    parser.add_argument("--target", default="http://localhost:8888")
    parser.add_argument("--log", default=str(Path(__file__).parent / "state" / "proxy-log.jsonl"))
    args = parser.parse_args()
    Handler.target = args.target.rstrip("/")
    Handler.log_path = Path(args.log)
    Handler.log_path.parent.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", args.listen), Handler)
    print(f"proxy: 127.0.0.1:{args.listen} -> {Handler.target}, log {Handler.log_path}")
    server.serve_forever()


if __name__ == "__main__":
    main()
