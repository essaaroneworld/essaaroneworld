import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from mudra.db import Database
from mudra.server import make_handler


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = Database(":memory:")
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cls.db, quiet=True))
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def req(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(self.base + path, data=data, method=method,
                                   headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_flow(self):
        status, co = self.req("POST", "/api/companies", {"name": "API Co", "fy_start": "2025-04-01"})
        self.assertEqual(status, 201)
        cid = co["id"]
        _, groups = self.req("GET", f"/api/companies/{cid}/groups")
        g = {x["name"]: x["id"] for x in groups}
        _, rent = self.req("POST", f"/api/companies/{cid}/ledgers", {"name": "Rent", "group_id": g["Indirect Expenses"]})
        _, ledgers = self.req("GET", f"/api/companies/{cid}/ledgers")
        cash = next(x for x in ledgers if x["name"] == "Cash")
        status, v = self.req("POST", f"/api/companies/{cid}/vouchers", {
            "voucher_type": "Payment", "date": "2025-04-10",
            "entries": [{"ledger_id": rent["id"], "side": "Dr", "amount": 2500},
                        {"ledger_id": cash["id"], "side": "Cr", "amount": 2500}]})
        self.assertEqual(status, 201, v)
        status, tb = self.req("GET", f"/api/companies/{cid}/reports/trial-balance")
        self.assertEqual(status, 200)
        self.assertEqual(tb["total_dr"], 2500)
        status, err = self.req("POST", f"/api/companies/{cid}/vouchers", {
            "voucher_type": "Payment", "date": "2025-04-10",
            "entries": [{"ledger_id": rent["id"], "side": "Dr", "amount": 1}]})
        self.assertEqual(status, 400)
        self.assertIn("error", err)

    def test_errors_and_static(self):
        self.assertEqual(self.req("GET", "/api/companies/9999")[0], 404)
        self.assertEqual(self.req("GET", "/api/companies/1/reports/nope")[0], 404)
        with urllib.request.urlopen(self.base + "/") as resp:
            self.assertIn(b"Mudra", resp.read())
        with urllib.request.urlopen(self.base + "/../../etc/passwd") as resp:
            self.assertIn(b"Mudra", resp.read())  # traversal falls back to the app shell

    def test_demo(self):
        status, co = self.req("POST", "/api/demo", {})
        self.assertIn(status, (200, 201))
        _, bs = self.req("GET", f"/api/companies/{co['id']}/reports/balance-sheet")
        self.assertEqual(bs["total_assets"], bs["total_liabilities"])


if __name__ == "__main__":
    unittest.main()
