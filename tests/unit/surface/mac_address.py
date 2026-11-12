"""The Mac's address as Docker forwards it, for a command that asks core's
tools server over HTTP.

    mac = MacAddress()                  # a listener on 127.0.0.1:<mac.port>
    # [core] cards_port = mac.port in the instance's stack.toml, then:
    stack("devices", "pair", "homer")   # reaches the tools server's app
    mac.answer["link"]                  # the code the command was given
    mac.close()

Core publishes the tools server's third listener on `127.0.0.1:42002`,
and Docker hands every request through it to the container's port 8002
(`access.CARDS_PORT`), which is how the server knows a request came from
the Mac. A unit test may not listen on 42002 (the test rig does), so this
listener takes a free port and hands each request to the tools server's
FastAPI app as arriving on `access.CARDS_PORT`. Nothing else is stood in:
the routes, the gate and the pairing codes are the server's own.
"""

from __future__ import annotations

import http.server
import threading

from .conftest import tools_server

__all__ = ["MacAddress"]

access = tools_server("access")
server = tools_server("server")


class MacAddress:
    """A listener on 127.0.0.1 that hands every request to the tools
    server as arriving on the Mac's address. `made` is set once a code
    was made, with the answer in `answer`; `asked` once the command asked
    for the code's state, so it waits."""

    def __init__(self):
        from fastapi.testclient import TestClient

        app_client = TestClient(server.app, base_url=f"http://localhost:{access.CARDS_PORT}")
        self.made = threading.Event()
        self.asked = threading.Event()
        self.answer: dict = {}
        outer = self

        class Forward(http.server.BaseHTTPRequestHandler):
            def _forward(self):
                length = int(self.headers.get("content-length") or 0)
                body = self.rfile.read(length) if length else None
                # The command's cookie goes along: it carries the admin key.
                cookie = {"cookie": self.headers["cookie"]} if self.headers.get("cookie") else {}
                r = app_client.request(self.command, self.path, content=body,
                                       headers={"host": "127.0.0.1:42002",
                                                "content-type": "application/json", **cookie})
                if self.command == "POST" and self.path.endswith("/devices/pairing") \
                        and r.status_code == 200:
                    outer.answer = r.json()
                    outer.made.set()
                if self.command == "GET":
                    outer.asked.set()
                self.send_response(r.status_code)
                self.send_header("content-type", r.headers.get("content-type", "application/json"))
                self.send_header("content-length", str(len(r.content)))
                self.end_headers()
                self.wfile.write(r.content)

            do_GET = do_POST = do_DELETE = _forward

            def log_message(self, *args):
                pass

        self.listener = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Forward)
        self.port = self.listener.server_address[1]
        threading.Thread(target=self.listener.serve_forever, daemon=True).start()

    def close(self) -> None:
        """Stop listening; the port is free again."""
        self.listener.shutdown()
        self.listener.server_close()
