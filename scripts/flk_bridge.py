# -*- coding: utf-8 -*-
"""flk 匹配工作的本地桥：给浏览器页提供标题清单并从页面接收匹配结果。

浏览器页（https://flk.npc.gov.cn）通过 fetch 访问 127.0.0.1（Chrome 视其为
potentially trustworthy origin，不受 https 页混合内容限制）。仅监听回环地址，
用完即关。

  GET  /titles.json  → data/flk/tmp/zhuma_nosource.json
  POST /save         → 请求体写入 data/flk/tmp/<name>（name 由 ?name= 指定）
"""
import io
import os

try:
    from http.server import BaseHTTPRequestHandler, HTTPServer
except ImportError:  # Python 2 兼容（本仓库目标 3.6+，此分支仅为稳健）
    from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "flk", "tmp")
TITLES = os.path.join(ROOT, "zhuma_nosource.json")


class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_GET(self):
        path, _, query = self.path.partition("?")
        routes = {
            "/titles.json": "zhuma_nosource.json",
            "/retry.json": "flk_retry.json",
            "/matched.json": "flk_matched_for_page.json",
            "/manifest.json": "flk_manifest_a.json",
            "/missed.json": "flk_missed.json",
        }
        if path in routes:
            fpath = os.path.join(ROOT, routes[path])
            body = b"{}"
            if os.path.exists(fpath):
                with io.open(fpath, encoding="utf-8") as f:
                    body = f.read().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self._cors()
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self._cors()
            self.end_headers()

    def do_POST(self):
        path, _, query = self.path.partition("?")
        name = "flk_matches.json"
        for kv in query.split("&"):
            k, _, v = kv.partition("=")
            if k == "name" and v:
                name = os.path.basename(v)
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        if path == "/savebin":
            out_dir = os.path.abspath(os.path.join(ROOT, "..", "docs_v3"))
            if not os.path.isdir(out_dir):
                os.makedirs(out_dir)
            with io.open(os.path.join(out_dir, name), "wb") as f:
                f.write(body)
        elif path == "/save":
            with io.open(os.path.join(ROOT, name), "wb") as f:
                f.write(body)
        else:
            self.send_response(404)
            self._cors()
            self.end_headers()
            return
        self.send_response(200)
        self._cors()
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    # Windows 主机名含非 UTF-8 字节时 socket.getfqdn 会抛 UnicodeDecodeError，
    # 而该调用只为填充 server_name；直接短路掉。
    import socket
    socket.getfqdn = lambda name="": "localhost"
    HTTPServer(("127.0.0.1", 8782), Handler).serve_forever()
