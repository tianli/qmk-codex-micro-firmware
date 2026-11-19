"""Local HTTP + Server-Sent Events (SSE) status API for toolbar/menu-bar frontends."""

from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import sys
import threading
from typing import Any


DEFAULT_HTTP_HOST = "127.0.0.1"
DEFAULT_HTTP_PORT = 41718

_SLOT_FOCUS_RE = re.compile(r"^/slots/(\d+)/focus/?$")
_KEY_FOCUS_RE = re.compile(r"^/keys/(\d+)/focus/?$")
_SLOT_CLEAR_RE = re.compile(r"^/slots/(\d+)/clear/?$")


class _ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def handle_error(self, request: Any, client_address: Any) -> None:
        _, exc, _ = sys.exc_info()
        if isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
            return
        super().handle_error(request, client_address)


class StatusAPIServer:
    """Expose Bridge slot snapshots, live SSE updates, and focus/clear actions on localhost."""

    def __init__(
        self,
        bridge: Any,
        host: str = DEFAULT_HTTP_HOST,
        port: int = DEFAULT_HTTP_PORT,
    ) -> None:
        self.bridge = bridge
        self.host = host
        self.port = int(port)
        self._cond = threading.Condition()
        self._version = 1
        self._stopped = False
        self._server: _ReusableThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def bound_port(self) -> int:
        if self._server is not None:
            return int(self._server.server_address[1])
        return self.port

    def notify_change(self) -> None:
        """Broadcast a state change to all connected SSE clients."""
        with self._cond:
            self._version += 1
            self._cond.notify_all()

    def current_snapshot(self) -> tuple[int, dict[str, Any]]:
        with self._cond:
            version = self._version
        return version, self.bridge.get_slots_snapshot(version=version)

    def start(self) -> None:
        if self._server is not None:
            return
        api = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
                # Suppress default stderr access logging
                return

            def _send_json(self, code: int, payload: dict[str, Any]) -> None:
                body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
                self.close_connection = True
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)

            def _handle_sse(self) -> None:
                self.close_connection = True
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()

                last_version = -1
                try:
                    while not api._stopped:
                        version, snapshot = api.current_snapshot()
                        if version != last_version:
                            data = json.dumps(snapshot, separators=(",", ":")).encode("utf-8")
                            self.wfile.write(b"data: " + data + b"\n\n")
                            self.wfile.flush()
                            last_version = version
                        with api._cond:
                            if api._stopped:
                                break
                            if api._version == last_version:
                                notified = api._cond.wait(timeout=15.0)
                                if not notified and not api._stopped and api._version == last_version:
                                    self.wfile.write(b": keepalive\n\n")
                                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return

            def do_GET(self) -> None:  # noqa: N802
                path = self.path.split("?", 1)[0]
                if path in {"/health", "/health/"}:
                    self._send_json(HTTPStatus.OK, {"ok": True, "version": api._version})
                    return
                if path in {"/slots", "/slots/"}:
                    _, snapshot = api.current_snapshot()
                    self._send_json(HTTPStatus.OK, snapshot)
                    return
                if path in {"/events", "/events/"}:
                    self._handle_sse()
                    return
                self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})

            def do_POST(self) -> None:  # noqa: N802
                path = self.path.split("?", 1)[0]
                # Drain any request body so keep-alive connections remain clean
                content_len = int(self.headers.get("Content-Length") or 0)
                if content_len > 0:
                    try:
                        self.rfile.read(min(content_len, 65536))
                    except OSError:
                        pass

                m = _SLOT_FOCUS_RE.match(path)
                if m:
                    slot = int(m.group(1))
                    result = api.bridge.focus_slot(slot)
                    code = HTTPStatus.OK if result.get("ok") else HTTPStatus.NOT_FOUND
                    self._send_json(code, result)
                    return

                m = _KEY_FOCUS_RE.match(path)
                if m:
                    key_num = int(m.group(1))
                    result = api.bridge.focus_key(key_num)
                    code = HTTPStatus.OK if result.get("ok") else HTTPStatus.NOT_FOUND
                    self._send_json(code, result)
                    return

                m = _SLOT_CLEAR_RE.match(path)
                if m:
                    slot = int(m.group(1))
                    cleared = api.bridge.clear_slot(slot)
                    self._send_json(HTTPStatus.OK, {"ok": cleared, "slot": slot})
                    return

                if path in {"/slots/clear-completed", "/slots/clear-completed/"}:
                    count = api.bridge.clear_completed_slots()
                    self._send_json(HTTPStatus.OK, {"ok": True, "cleared": count})
                    return

                if path in {"/slots/clear-all", "/slots/clear-all/"}:
                    count = api.bridge.clear_all_slots()
                    self._send_json(HTTPStatus.OK, {"ok": True, "cleared": count})
                    return

                if path in {"/previous-window", "/previous-window/"}:
                    ok = api.bridge.restore_previous_window()
                    self._send_json(HTTPStatus.OK, {"ok": ok})
                    return

                self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})

        self._stopped = False
        self._server = _ReusableThreadingHTTPServer((self.host, self.port), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.25},
            name="mf17-status-api",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        with self._cond:
            self._stopped = True
            self._cond.notify_all()
        server = self._server
        self._server = None
        if server is not None:
            try:
                server.shutdown()
            except Exception:
                pass
            try:
                server.server_close()
            except Exception:
                pass
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.5)
