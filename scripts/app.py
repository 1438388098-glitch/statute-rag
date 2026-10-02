# -*- coding: utf-8 -*-
"""法条检索 Web 应用：输入问题 → 条文原文 + 出处，app 样子的本地界面。

设计纪律：
- 零第三方依赖，只用标准库 http.server；前端是 app/index.html 一个文件，
  没有构建步骤、没有 npm、没有外链脚本；
- 与评测共用同一个 HybridRetriever 实例与同一份融合排名——界面里看到的
  顺序就是 docs/eval_report.md 里评测的那个顺序（命中来源轨迹由
  HybridRetriever.recall_with_trace 给出，只读不改分），不存在「演示版
  另一套逻辑」；
- 服务不映射任意文件路径：只回应内嵌的单页应用与两个 JSON 接口，
  因此没有目录穿越面。

安全边界：默认只绑 127.0.0.1（本机自己用）。要对局域网或公网开放须显式
传 --host，并自行置于反向代理之后——本项目默认不做这件事。

用法：
  python scripts/app.py                                  # 读 data/corpus_v3.jsonl
  python scripts/app.py --corpus demo_corpus/corpus.jsonl # 无真实语料时先跑演示语料
  python scripts/app.py --port 9000 --no-browser
"""
import argparse
import io
import json
import os
import socket
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from http.server import BaseHTTPRequestHandler, HTTPServer  # noqa: E402
from urllib.parse import parse_qs, urlparse  # noqa: E402

try:  # ThreadingHTTPServer 是 Python 3.7+，本机 3.6 时补一个等价实现
    from http.server import ThreadingHTTPServer
except ImportError:  # pragma: no cover - 3.7 以下
    import socketserver

    class ThreadingHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
        daemon_threads = True


from statute_rag.importer import load_corpus  # noqa: E402
from statute_rag.query_expansion import load_synonyms  # noqa: E402
from statute_rag.retrieval import HYBRID_FUSION_DEPTH, HybridRetriever  # noqa: E402

APP_HTML = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "index.html")
DEFAULT_CORPUS = os.path.join("data", "corpus_v3.jsonl")
DEFAULT_K = 10
MAX_K = 50
# 超长查询只会稀释检索信号（且会让 URL 过长），截断比报错友好；
# 但截断是静默的，所以界面会显示实际送检的查询串。
MAX_QUERY_CHARS = 200


class AppState(object):
    """一次性建好的运行态：语料统计 + 检索器 + 元信息。"""

    def __init__(self, corpus, corpus_path, retriever, depth, k, build_seconds, synonyms):
        self.corpus = corpus
        self.corpus_path = corpus_path
        self.retriever = retriever
        self.depth = depth
        self.k = k
        self.build_seconds = build_seconds
        self.synonyms = synonyms

    def meta(self):
        return {
            "corpus": {
                "path": self.corpus_path.replace("\\", "/"),
                "articles": len(self.corpus),
                "laws": len(set(x["law"] for x in self.corpus)),
            },
            "retriever": "hybrid（原查询 BM25 + 同义扩展 BM25 + 精确子串，RRF 融合）",
            "fusion_depth": self.depth,
            "default_k": self.k,
            "max_k": MAX_K,
            "query_max_chars": MAX_QUERY_CHARS,
            "synonym_entries": len(self.synonyms or {}),
            "build_seconds": round(self.build_seconds, 1),
        }

    def search(self, query, k):
        q = " ".join((query or "").split())
        if not q:
            raise ValueError("查询为空")
        truncated = len(q) > MAX_QUERY_CHARS
        if truncated:
            q = q[:MAX_QUERY_CHARS]
        started = time.time()
        ranking, trace = self.retriever.recall_with_trace(q, self.depth)
        took_ms = (time.time() - started) * 1000.0
        results = []
        for rank, cite in enumerate(ranking[:k], 1):
            results.append({
                "rank": rank,
                "id": cite["id"],
                "law": cite["law"],
                "num": cite["num"],
                "text": cite["text"],
                "score": cite["score"],
                "channels": trace["hits"].get(cite["id"], []),
            })
        return {
            "query": q,
            "truncated": truncated,
            "expanded": trace["expanded"],
            "channels": trace["channels"],
            # 候选池 = 各路各取前 depth 名后求并集的大小，不是「全库命中条数」
            # （词法检索里后者无意义：单字 gram 能命中半个库）。界面按此说法呈现。
            "candidates": len(ranking),
            "depth": self.depth,
            "took_ms": round(took_ms, 1),
            "k": k,
            "results": results,
        }


def build_state(corpus_path, depth=HYBRID_FUSION_DEPTH, k=DEFAULT_K):
    """读语料并建索引。语料条数与部数直接取自语料本身，不写死。"""
    corpus = load_corpus(corpus_path)
    if not corpus:
        raise ValueError("语料为空：%s" % corpus_path)
    missing = [i for i, x in enumerate(corpus[:5]) if "law" not in x or "text" not in x]
    if missing:
        raise ValueError("语料格式不合法（缺 law/text 字段），无法建索引：%s" % corpus_path)
    started = time.time()
    retriever = HybridRetriever(corpus)
    build_seconds = time.time() - started
    return AppState(corpus, corpus_path, retriever, depth, k, build_seconds, load_synonyms())


def make_handler(state):
    class Handler(BaseHTTPRequestHandler):
        server_version = "statute-rag-app/0.1"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # 收窄成一行，别刷屏
            sys.stderr.write("%s  %s\n" % (self.log_date_time_string(), fmt % args))

        def _send(self, status, body, content_type):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, obj, status=200):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self._send(status, body, "application/json; charset=utf-8")

        def _send_html(self):
            try:
                with io.open(APP_HTML, "r", encoding="utf-8") as f:
                    body = f.read().encode("utf-8")
            except IOError:
                self._send_json({"error": "找不到界面文件：%s" % APP_HTML}, 500)
                return
            self._send(200, body, "text/html; charset=utf-8")

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path
            if path in ("/", "/index.html"):
                self._send_html()
                return
            if path == "/api/meta":
                self._send_json(state.meta())
                return
            if path == "/api/search":
                params = parse_qs(parsed.query)
                query = (params.get("q") or [""])[0]
                try:
                    k = int((params.get("k") or [state.k])[0])
                except (TypeError, ValueError):
                    k = state.k
                k = max(1, min(MAX_K, k))
                try:
                    self._send_json(state.search(query, k))
                except ValueError as exc:
                    self._send_json({"error": str(exc)}, 400)
                return
            self._send_json({"error": "未知路径：%s" % path}, 404)

    return Handler


def make_server(state, host="127.0.0.1", port=8787):
    _stub_getfqdn_if_broken()
    return ThreadingHTTPServer((host, port), make_handler(state))


def _stub_getfqdn_if_broken():
    """Windows 主机名含非 UTF-8 字节时 getfqdn 会抛 UnicodeDecodeError，
    使 HTTPServer 一绑定就崩（本机实测）；探测一次，坏了就换成常量。
    HTTPServer.server_bind 只为填 server_name 调它，常量不影响服务。"""
    try:
        socket.getfqdn()
    except Exception:
        socket.getfqdn = lambda name="": "localhost"


def main():
    parser = argparse.ArgumentParser(description="法条检索 Web 应用（零依赖，本机运行）")
    parser.add_argument("--corpus", default=DEFAULT_CORPUS,
                        help="语料 JSONL（缺省 %s）" % DEFAULT_CORPUS)
    parser.add_argument("--host", default="127.0.0.1",
                        help="绑定地址，缺省只本机；--host 0.0.0.0 才对局域网开放")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--k", type=int, default=DEFAULT_K, help="默认返回条数")
    parser.add_argument("--depth", type=int, default=HYBRID_FUSION_DEPTH,
                        help="融合通道深度（检索配置，勿轻改）")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()

    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    if not os.path.exists(args.corpus):
        parser.error("语料不存在：%s\n  无语料可先跑 python scripts/make_demo_corpus.py，"
                     "再用 --corpus demo_corpus/corpus.jsonl 起界面" % args.corpus)

    print("读语料：%s" % args.corpus)
    state = build_state(args.corpus, depth=args.depth, k=args.k)
    print("已建索引：%d 条 / %d 部，用时 %.1f 秒"
          % (len(state.corpus), state.meta()["corpus"]["laws"], state.build_seconds))

    _stub_getfqdn_if_broken()
    try:
        server = make_server(state, args.host, args.port)
    except OSError as exc:
        print("端口 %d 绑定失败：%s（换一个 --port，或先关掉占用它的进程）" % (args.port, exc))
        return 1

    shown_host = "127.0.0.1" if args.host in ("0.0.0.0", "") else args.host
    url = "http://%s:%d/" % (shown_host, server.server_address[1])
    print("界面已就绪：%s   （Ctrl+C 停止）" % url)
    if args.host not in ("127.0.0.1", "localhost"):
        print("注意：已绑定 %s，局域网/公网可达；对公网开放请自行加反向代理与鉴权。" % args.host)
    if not args.no_browser:
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
