"""HTTP server: web client + JSON API + background worker. Standard library only.

Deployment modes (same code):
  * Localhost          python -m pwexam
  * Offline LAN lab    python -m pwexam --host 0.0.0.0 --port 8080
  * Online VPS         behind nginx/Caddy with HTTPS (see deploy/)
"""
import argparse
import getpass
import json
import mimetypes
import os
import socket
import sys
import tempfile
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import PRODUCT, VENDOR, __version__, attempts, notify, users
from .api import Raw, dispatch
from .config import settings
from .db import Database
from .errors import AppError, AuthError
from .security import generate_password

WEB_ROOT = Path(__file__).resolve().parent.parent / "web"
MAX_BODY = 8 * 1024 * 1024
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
       "connect-src 'self'; font-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; "
       "form-action 'self'")
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Content-Security-Policy": CSP,
}


def make_handler(db, web_root=WEB_ROOT, quiet=False):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"PWExam/{__version__}"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            if not quiet:
                sys.stderr.write("%s - %s\n" % (self.client_ip(), fmt % args))

        def client_ip(self):
            if settings.trust_proxy:
                fwd = self.headers.get("X-Forwarded-For")
                if fwd:
                    return fwd.split(",")[0].strip()
            return self.client_address[0]

        def send(self, status, payload, content_type="application/json; charset=utf-8", headers=None):
            body = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for k, v in {**SECURITY_HEADERS, **(headers or {})}.items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def api(self, method):
            url = urlparse(self.path)
            query = {k: v[-1] for k, v in parse_qs(url.query).items()}
            body = None
            if method in ("POST", "PUT"):
                length = int(self.headers.get("Content-Length") or 0)
                if length > MAX_BODY:
                    return self.send(413, {"error": "Request too large"})
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw or b"{}")
                except (json.JSONDecodeError, UnicodeDecodeError):
                    return self.send(400, {"error": "Invalid JSON"})
                if not isinstance(body, dict):
                    return self.send(400, {"error": "JSON body must be an object"})
            auth = self.headers.get("Authorization", "")
            token = auth[7:].strip() if auth.lower().startswith("bearer ") else None
            nocache = {"Cache-Control": "no-store"}
            try:
                if url.path == "/api/health":
                    return self.send(200, {"status": "ok", "version": __version__, "product": PRODUCT}, headers=nocache)
                if url.path == "/api/backup" and method == "GET":
                    return self.backup(token)
                result = dispatch(db, method, url.path, query, body, token, self.client_ip())
                if isinstance(result, Raw):
                    h = dict(nocache)
                    if result.filename:
                        h["Content-Disposition"] = f'attachment; filename="{result.filename}"'
                    return self.send(200, result.data, result.content_type, h)
                self.send(201 if method == "POST" else 200, result, headers=nocache)
            except AppError as e:
                self.send(e.status, {"error": e.message}, headers=nocache)
            except Exception:  # pragma: no cover
                traceback.print_exc()
                self.send(500, {"error": "Internal server error"}, headers=nocache)

        def backup(self, token):
            if not token:
                raise AuthError("Login required")
            with db.read() as conn:
                user = users.authenticate(conn, token)
            if user["role"] != "admin":
                return self.send(403, {"error": "Only administrators can download backups"})
            fd, tmp = tempfile.mkstemp(suffix=".db")
            os.close(fd)
            try:
                db.backup_to(tmp)
                data = Path(tmp).read_bytes()
            finally:
                os.unlink(tmp)
            self.send(200, data, "application/octet-stream",
                      {"Content-Disposition": 'attachment; filename="pwexam-backup.db"', "Cache-Control": "no-store"})

        def static(self):
            path = urlparse(self.path).path
            if path in ("", "/"):
                path = "/index.html"
            root = web_root.resolve()
            target = (web_root / path.lstrip("/")).resolve()
            if root not in target.parents or not target.is_file():
                target = web_root / "index.html"  # single-page app fallback
            ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
            if target.suffix == ".webmanifest":
                ctype = "application/manifest+json"
            if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
                ctype += "; charset=utf-8"
            extra = {"Cache-Control": "no-cache"}
            if target.name == "sw.js":
                extra["Service-Worker-Allowed"] = "/"
            self.send(200, target.read_bytes(), ctype, extra)

        def do_GET(self):
            if self.path.startswith("/api/"):
                return self.api("GET")
            self.static()

        def do_HEAD(self):
            self.do_GET()

        def do_POST(self):
            self.api("POST")

        def do_PUT(self):
            self.api("PUT")

        def do_DELETE(self):
            self.api("DELETE")

    return Handler


def start_worker(db, interval=10):
    """Auto-submit expired attempts and deliver queued notifications."""
    stop = threading.Event()

    def loop():
        while not stop.wait(interval):
            try:
                attempts.auto_submit_expired(db)
                notify.dispatch_pending(db)
            except Exception:  # keep the worker alive
                traceback.print_exc()

    threading.Thread(target=loop, name="pwexam-worker", daemon=True).start()
    return stop


def lan_addresses():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))  # no packet is sent; picks the LAN interface
        ip = s.getsockname()[0]
        s.close()
        return [ip]
    except OSError:
        return []


def bootstrap_admin(db, interactive):
    with db.read() as conn:
        if conn.execute("SELECT 1 FROM users WHERE role='admin' LIMIT 1").fetchone():
            return
    login = os.environ.get("PWEXAM_ADMIN_LOGIN", "admin")
    password = os.environ.get("PWEXAM_ADMIN_PASSWORD")
    shown = False
    typed = False
    if not password and interactive and sys.stdin.isatty():
        print("No administrator exists yet. Create one now.")
        while True:
            password = getpass.getpass(f"Password for '{login}' (8+ chars, letters and digits): ")
            if len(password) >= 8 and not password.isdigit() and not password.isalpha():
                typed = True
                break
            print("Too weak, try again.")
    if not password:
        password, shown = generate_password(12), True
    with db.tx() as conn:
        users.ensure_admin(conn, login, password)
        # Typed interactively: keep it. From an env file or generated: must be changed at first login.
        conn.execute("UPDATE users SET must_change_password=? WHERE login_id=?", (0 if typed else 1, login))
    if shown:
        print("=" * 64)
        print(f"  Initial administrator  login: {login}   password: {password}")
        print("  (shown once — you will be asked to change it after logging in)")
        print("=" * 64)


def main(argv=None):
    p = argparse.ArgumentParser(prog="pwexam", description=f"{PRODUCT} — {VENDOR}")
    p.add_argument("--host", default=os.environ.get("PWEXAM_HOST", "127.0.0.1"),
                   help="bind address (0.0.0.0 for LAN / VPS)")
    p.add_argument("--port", type=int, default=int(os.environ.get("PWEXAM_PORT", "8080")))
    p.add_argument("--data", default=os.environ.get("PWEXAM_DATA", "data/pwexam.db"))
    p.add_argument("--demo", action="store_true", help="load sample batch, candidates, questions and a live exam")
    p.add_argument("--no-prompt", action="store_true", help="never prompt for the admin password")
    args = p.parse_args(argv)

    data_path = Path(args.data)
    data_path.parent.mkdir(parents=True, exist_ok=True)
    settings.load_secret(data_path.parent)
    db = Database(str(data_path))
    if args.demo:
        from .demo import load_demo
        info = load_demo(db)
        if info:
            print("Demo data loaded:", info)
    bootstrap_admin(db, interactive=not args.no_prompt)
    start_worker(db)
    httpd = ThreadingHTTPServer((args.host, args.port), make_handler(db))
    httpd.daemon_threads = True
    print(f"{PRODUCT} v{__version__} — {VENDOR}")
    print(f"  data     : {data_path.resolve()}")
    print(f"  local    : http://127.0.0.1:{args.port}/")
    if args.host in ("0.0.0.0", "::"):
        for ip in lan_addresses():
            print(f"  LAN      : http://{ip}:{args.port}/   (candidates in the lab open this)")
    if not (settings.smtp_host or settings.sms_webhook):
        print("  notify   : no SMTP/SMS gateway configured — messages (incl. OTPs) are printed here")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        db.close()


if __name__ == "__main__":
    main()
