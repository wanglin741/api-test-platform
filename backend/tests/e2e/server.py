"""截断 e2e 与验收场景的真实流量源:stdlib ThreadingHTTPServer,真实 socket 路径。

端点:
- /json    正常 JSON 响应
- /huge    50 MB 流式响应(客户端应在 64KB 处截断并提前断开)
- /slow    2s 后才响应(验证真实读超时)
- /boom    500 错误
- /login   POST JSON {username, password} → {"code":0,"data":{"token":...}}(验收场景①)
- /profile GET,须带 Authorization: Bearer <token> → {"code":0,"data":{...}}(验收场景②)

HTTP/1.1 + Content-Length 保持连接,`server.connections` 计数即 TCP 连接数,
两次请求计数为 1 即证明连接复用。
"""

import json
import socketserver
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HUGE_BODY_BYTES = 50 * 1024 * 1024
CHUNK = 64 * 1024
SLOW_DELAY_SECONDS = 2.0
VALID_USERNAME = "demo_user"
VALID_PASSWORD = "demo_pass"


class E2EServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), E2EHandler)
        self.connections = 0


class E2EHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: E2EServer

    def setup(self) -> None:
        super().setup()
        self.server.connections += 1  # 每条 TCP 连接一个 handler 实例

    def do_GET(self) -> None:  # noqa: N802 - stdlib 命名
        try:
            if self.path == "/json":
                self._send_json(200, {"code": 0, "data": {"hello": "world"}})
            elif self.path == "/huge":
                self._send_huge()
            elif self.path == "/slow":
                import time

                time.sleep(SLOW_DELAY_SECONDS)
                self._send_json(200, {"code": 0})
            elif self.path == "/boom":
                self._send_json(500, {"error": "boom"})
            elif self.path == "/profile":
                self._profile()
            else:
                self._send_json(404, {"error": "not found"})
        except (BrokenPipeError, ConnectionResetError):
            # 客户端截断后提前断开,继续写会遇到对端关闭 —— 预期行为
            pass

    def do_POST(self) -> None:  # noqa: N802 - stdlib 命名
        try:
            if self.path == "/login":
                self._login()
            else:
                self._send_json(404, {"error": "not found"})
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw or b"{}")

    def _login(self) -> None:
        payload = self._read_json()
        if payload.get("username") == VALID_USERNAME and payload.get("password") == VALID_PASSWORD:
            self._send_json(200, {"code": 0, "data": {"token": f"tok-{payload['username']}-123"}})
        else:
            self._send_json(401, {"code": 1001, "message": "账号或密码错误"})

    def _profile(self) -> None:
        auth = self.headers.get("Authorization") or ""
        if not auth.startswith("Bearer "):
            self._send_json(401, {"code": 1001, "message": "缺少凭据"})
            return
        self._send_json(200, {"code": 0, "data": {"username": VALID_USERNAME, "auth": auth}})

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_huge(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(HUGE_BODY_BYTES))
        self.end_headers()
        chunk = b"x" * CHUNK
        for _ in range(HUGE_BODY_BYTES // CHUNK):
            self.wfile.write(chunk)

    def log_message(self, format: str, *args: object) -> None:  # 静默访问日志
        pass
