import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from pwexam import clock
from pwexam.config import settings
from pwexam.db import Database
from pwexam.demo import DEMO_PASSWORD, load_demo
from pwexam.server import make_handler

settings.secret = "test-secret"


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        clock.reset()
        cls.db = Database(":memory:")
        load_demo(cls.db)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cls.db, quiet=True))
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def req(self, method, path, body=None, token=None, raw=False):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        r = urllib.request.Request(self.base + path, data=json.dumps(body).encode() if body is not None else None,
                                   method=method, headers=headers)
        try:
            with urllib.request.urlopen(r) as resp:
                data = resp.read()
                return resp.status, (data if raw else json.loads(data)), resp.headers
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}"), e.headers

    def login(self, login_id, password):
        status, body, _ = self.req("POST", "/api/auth/login", {"login_id": login_id, "password": password})
        self.assertEqual(status, 201, body)
        return body["token"]

    def test_requires_auth_and_security_headers(self):
        status, body, headers = self.req("GET", "/api/dashboard")
        self.assertEqual(status, 401)
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])
        self.assertEqual(headers["X-Frame-Options"], "DENY")

    def test_roles_enforced(self):
        cand = self.login("PW002", DEMO_PASSWORD)
        proctor = self.login("proctor", "Proctor@2026")
        self.assertEqual(self.req("GET", "/api/dashboard", token=cand)[0], 403)
        self.assertEqual(self.req("POST", "/api/questions", {}, token=proctor)[0], 403)
        self.assertEqual(self.req("GET", "/api/backup", token=proctor)[0], 403)
        self.assertEqual(self.req("GET", "/api/my/exams", token=proctor)[0], 403)

    def test_candidate_exam_over_http(self):
        tok = self.login("PW001", DEMO_PASSWORD)
        status, mine, _ = self.req("GET", "/api/my/exams", token=tok)
        live = [e for e in mine["exams"] if e["status"] == "live"][0]
        status, a, _ = self.req("POST", f"/api/my/schedules/{live['schedule_id']}/start", {}, token=tok)
        self.assertEqual(status, 201, a)
        q = a["questions"][0]
        status, r, _ = self.req("POST", f"/api/my/attempts/{a['id']}/answers",
                                {"answers": [{"question_id": q["id"], "selected": [q["options"][0]["key"]], "seq": 1}]},
                                token=tok)
        self.assertEqual(r["accepted"], 1)
        status, r, _ = self.req("POST", f"/api/my/attempts/{a['id']}/violations", {"kind": "tab_switch"}, token=tok)
        self.assertEqual(r["violations"], 1)
        status, r, _ = self.req("POST", f"/api/my/attempts/{a['id']}/submit", {}, token=tok)
        self.assertEqual(r["status"], "submitted")
        admin = self.login("admin", "Admin@2026")
        status, csv_bytes, headers = self.req("GET", f"/api/schedules/{live['schedule_id']}/results.csv", token=admin, raw=True)
        self.assertEqual(status, 200)
        self.assertIn(b"PW001", csv_bytes)
        status, pdf, headers = self.req("GET", f"/api/attempts/{a['id']}/report.pdf", token=admin, raw=True)
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertIn("attachment", headers["Content-Disposition"])

    def test_bad_login_is_recorded(self):
        for _ in range(5):
            self.assertEqual(self.req("POST", "/api/auth/login", {"login_id": "PW008", "password": "x"})[0], 401)
        status, body, _ = self.req("POST", "/api/auth/login", {"login_id": "PW008", "password": DEMO_PASSWORD})
        self.assertEqual(status, 401)
        self.assertIn("Too many", body["error"])

    def test_static_and_backup(self):
        with urllib.request.urlopen(self.base + "/") as resp:
            self.assertIn(b"PW Batch", resp.read())
        with urllib.request.urlopen(self.base + "/../../etc/passwd") as resp:
            self.assertIn(b"PW Batch", resp.read())
        admin = self.login("admin", "Admin@2026")
        status, data, _ = self.req("GET", "/api/backup", token=admin, raw=True)
        self.assertEqual(status, 200)
        self.assertTrue(data.startswith(b"SQLite format 3"))


if __name__ == "__main__":
    unittest.main()
