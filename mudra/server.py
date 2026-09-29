"""HTTP server: serves the web client and the JSON API (standard library only)."""
import argparse
import json
import mimetypes
import os
import sys
import tempfile
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__
from .api import dispatch, dispatch_report
from .db import Database
from .errors import MudraError

WEB_ROOT = Path(__file__).resolve().parent.parent / "web"
MAX_BODY = 5 * 1024 * 1024


def make_handler(db, web_root=WEB_ROOT, quiet=False):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"Mudra/{__version__}"

        def log_message(self, fmt, *args):
            if not quiet:
                sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

        def _send(self, status, payload, content_type="application/json; charset=utf-8", headers=None):
            body = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _api(self, method):
            url = urlparse(self.path)
            query = {k: v[-1] for k, v in parse_qs(url.query).items()}
            body = None
            if method in ("POST", "PUT"):
                length = int(self.headers.get("Content-Length") or 0)
                if length > MAX_BODY:
                    return self._send(413, {"error": "Request too large"})
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw or b"{}")
                except json.JSONDecodeError:
                    return self._send(400, {"error": "Invalid JSON body"})
                if not isinstance(body, dict):
                    return self._send(400, {"error": "JSON body must be an object"})
            try:
                if url.path == "/api/health":
                    return self._send(200, {"status": "ok", "version": __version__})
                if url.path == "/api/backup" and method == "GET":
                    return self._backup()
                if method == "GET":
                    result = dispatch_report(db, url.path, query)
                    if result is not None:
                        return self._send(200, result)
                result = dispatch(db, method, url.path, query, body)
                self._send(201 if method == "POST" else 200, result)
            except MudraError as e:
                self._send(e.status, {"error": e.message})
            except Exception:  # pragma: no cover - defensive
                traceback.print_exc()
                self._send(500, {"error": "Internal server error"})

        def _backup(self):
            fd, tmp = tempfile.mkstemp(suffix=".db")
            os.close(fd)
            try:
                db.backup_to(tmp)
                data = Path(tmp).read_bytes()
            finally:
                os.unlink(tmp)
            self._send(200, data, "application/octet-stream",
                       {"Content-Disposition": 'attachment; filename="mudra-backup.db"'})

        def _static(self):
            path = urlparse(self.path).path
            if path in ("", "/"):
                path = "/index.html"
            target = (web_root / path.lstrip("/")).resolve()
            if web_root.resolve() not in target.parents or not target.is_file():
                target = web_root / "index.html"  # SPA fallback
            ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript",):
                ctype += "; charset=utf-8"
            self._send(200, target.read_bytes(), ctype, {"Cache-Control": "no-cache"})

        def do_GET(self):
            if self.path.startswith("/api/"):
                return self._api("GET")
            self._static()

        def do_HEAD(self):
            self.do_GET()

        def do_POST(self):
            self._api("POST")

        def do_PUT(self):
            self._api("PUT")

        def do_DELETE(self):
            self._api("DELETE")

    return Handler


def main(argv=None):
    parser = argparse.ArgumentParser(prog="mudra", description="Mudra Finance and Banking Software")
    parser.add_argument("--host", default=os.environ.get("MUDRA_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("MUDRA_PORT", "9000")))
    parser.add_argument("--data", default=os.environ.get("MUDRA_DATA", "data/mudra.db"),
                        help="SQLite database file (default: data/mudra.db)")
    parser.add_argument("--demo", action="store_true", help="create the sample company on start")
    args = parser.parse_args(argv)

    Path(args.data).parent.mkdir(parents=True, exist_ok=True)
    db = Database(args.data)
    if args.demo:
        from .demo import create_demo_company
        with db.tx() as conn:
            if not conn.execute("SELECT 1 FROM companies WHERE name='Mudra Traders Pvt Ltd'").fetchone():
                create_demo_company(conn)
    httpd = ThreadingHTTPServer((args.host, args.port), make_handler(db))
    print(f"Mudra Finance and Banking Software v{__version__}")
    print(f"  data : {Path(args.data).resolve()}")
    print(f"  open : http://{args.host}:{args.port}/")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        db.close()


if __name__ == "__main__":
    main()
