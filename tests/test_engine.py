import unittest

from mudra import masters, reports, vouchers
from mudra.db import Database
from mudra.demo import create_demo_company
from mudra.errors import Conflict, ValidationError


class Base(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        with self.db.tx() as conn:
            self.company = masters.create_company(conn, {
                "name": "Test Co", "state": "Maharashtra", "fy_start": "2025-04-01"})
        self.cid = self.company["id"]
        self.groups = {g["name"]: g["id"] for g in self.call(masters.list_groups)}
        self.led = {x["name"]: x["id"] for x in self.call(masters.list_ledgers)}

    def call(self, fn, *args, **kw):
        with self.db.tx() as conn:
            return fn(conn, self.cid, *args, **kw)

    def ledger(self, name, group, **kw):
        led = self.call(masters.create_ledger, {"name": name, "group_id": self.groups[group], **kw})
        self.led[name] = led["id"]
        return led["id"]

    def voucher(self, vtype, date, *lines, **kw):
        entries = [{"ledger_id": self.led[n], "side": s, "amount": a} for n, s, a in lines]
        return self.call(vouchers.save_voucher, {"voucher_type": vtype, "date": date, "entries": entries, **kw})


class ChartOfAccountsTest(Base):
    def test_predefined_groups_and_ledgers(self):
        self.assertEqual(len(self.groups), 28)  # 15 primary + 13 sub-groups, like Tally
        self.assertIn("Cash", self.led)
        self.assertIn("Profit & Loss A/c", self.led)
        self.assertIn("CGST", self.led)
        types = [t["name"] for t in self.call(masters.list_voucher_types)]
        for t in ("Contra", "Payment", "Receipt", "Journal", "Sales", "Purchase", "Credit Note", "Debit Note"):
            self.assertIn(t, types)

    def test_sub_group_inherits_nature(self):
        g = self.call(masters.create_group, {"name": "Online Wallets", "parent_id": self.groups["Bank Accounts"]})
        self.assertEqual(g["nature"], "Assets")

    def test_names_unique_across_groups_and_ledgers(self):
        with self.assertRaises(Conflict):
            self.call(masters.create_ledger, {"name": "sundry debtors", "group_id": self.groups["Capital Account"]})

    def test_predefined_cannot_be_deleted(self):
        with self.assertRaises(ValidationError):
            self.call(masters.delete_group, self.groups["Current Assets"])
        with self.assertRaises(ValidationError):
            self.call(masters.delete_ledger, self.led["Cash"])

    def test_group_cycle_rejected(self):
        a = self.call(masters.create_group, {"name": "A", "parent_id": self.groups["Current Assets"]})
        b = self.call(masters.create_group, {"name": "B", "parent_id": a["id"]})
        with self.assertRaises(ValidationError):
            self.call(masters.update_group, a["id"], {"name": "A", "parent_id": b["id"]})

    def test_party_ledger_defaults_to_bill_wise(self):
        lid = self.ledger("Acme", "Sundry Debtors")
        self.assertTrue(self.call(masters.get_ledger, lid)["bill_wise"])

    def test_ledger_with_vouchers_cannot_be_deleted(self):
        self.ledger("Rent", "Indirect Expenses")
        self.voucher("Payment", "2025-04-05", ("Rent", "Dr", 100), ("Cash", "Cr", 100))
        with self.assertRaises(ValidationError):
            self.call(masters.delete_ledger, self.led["Rent"])


class VoucherRulesTest(Base):
    def setUp(self):
        super().setUp()
        self.ledger("Bank", "Bank Accounts")
        self.ledger("Rent", "Indirect Expenses")
        self.ledger("Capital", "Capital Account")
        self.ledger("Sales", "Sales Accounts")

    def test_unbalanced_rejected(self):
        with self.assertRaisesRegex(ValidationError, "does not balance"):
            self.voucher("Journal", "2025-04-02", ("Rent", "Dr", 100), ("Capital", "Cr", 90))

    def test_contra_only_cash_bank(self):
        self.voucher("Contra", "2025-04-02", ("Bank", "Dr", 100), ("Cash", "Cr", 100))
        with self.assertRaises(ValidationError):
            self.voucher("Contra", "2025-04-02", ("Rent", "Dr", 100), ("Cash", "Cr", 100))

    def test_payment_must_credit_cash_or_bank(self):
        with self.assertRaisesRegex(ValidationError, "Payment"):
            self.voucher("Payment", "2025-04-02", ("Rent", "Dr", 100), ("Capital", "Cr", 100))

    def test_receipt_must_debit_cash_or_bank(self):
        with self.assertRaisesRegex(ValidationError, "Receipt"):
            self.voucher("Receipt", "2025-04-02", ("Capital", "Dr", 100), ("Sales", "Cr", 100))

    def test_journal_disallows_cash(self):
        with self.assertRaisesRegex(ValidationError, "Journal"):
            self.voucher("Journal", "2025-04-02", ("Rent", "Dr", 100), ("Cash", "Cr", 100))

    def test_date_before_books_rejected(self):
        with self.assertRaisesRegex(ValidationError, "books beginning"):
            self.voucher("Payment", "2025-03-31", ("Rent", "Dr", 100), ("Cash", "Cr", 100))

    def test_auto_numbering_and_duplicate(self):
        v1 = self.voucher("Payment", "2025-04-02", ("Rent", "Dr", 100), ("Cash", "Cr", 100))
        v2 = self.voucher("Payment", "2025-04-03", ("Rent", "Dr", 100), ("Cash", "Cr", 100))
        self.assertEqual((v1["number"], v2["number"]), ("1", "2"))
        with self.assertRaisesRegex(ValidationError, "already exists"):
            self.voucher("Payment", "2025-04-03", ("Rent", "Dr", 1), ("Cash", "Cr", 1), number="1")

    def test_alter_and_delete_logged(self):
        v = self.voucher("Payment", "2025-04-02", ("Rent", "Dr", 100), ("Cash", "Cr", 100))
        self.call(vouchers.save_voucher, {"date": "2025-04-02", "entries": [
            {"ledger_id": self.led["Rent"], "side": "Dr", "amount": 150},
            {"ledger_id": self.led["Cash"], "side": "Cr", "amount": 150}]}, v["id"])
        self.assertEqual(self.call(vouchers.get_voucher, v["id"])["total"], 150)
        self.call(vouchers.delete_voucher, v["id"])
        from mudra import audit
        actions = [e["action"] for e in self.call(audit.list_log, 10, "voucher")]
        self.assertEqual(actions[:3], ["delete", "alter", "create"])

    def test_memorandum_excluded_from_books(self):
        self.voucher("Memorandum", "2025-04-02", ("Rent", "Dr", 100), ("Cash", "Cr", 100))
        lv = self.call(reports.ledger_vouchers, self.led["Rent"])
        self.assertEqual(lv["closing"], 0)


class BillWiseTest(Base):
    def test_new_ref_then_agst_ref(self):
        self.ledger("Acme", "Sundry Debtors", credit_days=30)
        self.ledger("Sales", "Sales Accounts")
        self.voucher("Sales", "2025-04-02", ("Acme", "Dr", 1000), ("Sales", "Cr", 1000))
        self.call(vouchers.save_voucher, {"voucher_type": "Receipt", "date": "2025-04-10", "entries": [
            {"ledger_id": self.led["Cash"], "side": "Dr", "amount": 400},
            {"ledger_id": self.led["Acme"], "side": "Cr", "amount": 400,
             "bills": [{"ref_type": "Agst Ref", "name": "1", "amount": 400}]}]})
        out = self.call(reports.outstanding, "receivable", "2025-05-31")
        bill = out["parties"][0]["bills"][0]
        self.assertEqual((bill["name"], bill["pending"], bill["due_date"]), ("1", 600, "2025-05-02"))
        self.assertEqual(bill["overdue_days"], 29)

    def test_agst_ref_must_exist_and_amounts_match(self):
        self.ledger("Acme", "Sundry Debtors")
        with self.assertRaisesRegex(ValidationError, "no pending bill"):
            self.call(vouchers.save_voucher, {"voucher_type": "Receipt", "date": "2025-04-10", "entries": [
                {"ledger_id": self.led["Cash"], "side": "Dr", "amount": 400},
                {"ledger_id": self.led["Acme"], "side": "Cr", "amount": 400,
                 "bills": [{"ref_type": "Agst Ref", "name": "X", "amount": 400}]}]})
        with self.assertRaisesRegex(ValidationError, "must equal"):
            self.call(vouchers.save_voucher, {"voucher_type": "Receipt", "date": "2025-04-10", "entries": [
                {"ledger_id": self.led["Cash"], "side": "Dr", "amount": 400},
                {"ledger_id": self.led["Acme"], "side": "Cr", "amount": 400,
                 "bills": [{"ref_type": "Advance", "name": "ADV", "amount": 300}]}]})


class InvoiceAndGstTest(Base):
    def setUp(self):
        super().setUp()
        self.ledger("Local Party", "Sundry Debtors", state="Maharashtra")
        self.ledger("Delhi Party", "Sundry Debtors", state="Delhi")
        self.ledger("Supplier", "Sundry Creditors", state="Maharashtra")
        self.ledger("Sales", "Sales Accounts")
        self.ledger("Purchases", "Purchase Accounts")
        unit = self.call(masters.list_units)[0]["id"]
        self.item = self.call(masters.save_stock_item, {"name": "Widget", "unit_id": unit, "gst_rate": 18,
                                                        "opening_qty": 10, "opening_rate": 100})["id"]

    def invoice(self, vtype, party, account, qty, rate, **kw):
        return self.call(vouchers.save_voucher, {"voucher_type": vtype, "date": "2025-04-05", "mode": "invoice",
                                                 "invoice": {"party_ledger_id": self.led[party],
                                                             "account_ledger_id": self.led[account],
                                                             "items": [{"item_id": self.item, "qty": qty,
                                                                        "rate": rate}], **kw}})

    def test_intra_state_splits_cgst_sgst(self):
        v = self.invoice("Sales", "Local Party", "Sales", 2, 500)
        amounts = {e["ledger_name"]: (e["side"], e["amount"]) for e in v["entries"]}
        self.assertEqual(amounts["Local Party"], ("Dr", 1180))
        self.assertEqual(amounts["Sales"], ("Cr", 1000))
        self.assertEqual(amounts["CGST"], ("Cr", 90))
        self.assertEqual(amounts["SGST"], ("Cr", 90))
        self.assertEqual(v["inventory"][0]["direction"], "out")

    def test_inter_state_uses_igst(self):
        v = self.invoice("Sales", "Delhi Party", "Sales", 1, 1000)
        amounts = {e["ledger_name"]: e["amount"] for e in v["entries"]}
        self.assertEqual(amounts["IGST"], 180)
        self.assertNotIn("CGST", amounts)

    def test_round_off(self):
        v = self.invoice("Sales", "Local Party", "Sales", 1, 99.5, round_off=True)
        amounts = {e["ledger_name"]: (e["side"], e["amount"]) for e in v["entries"]}
        self.assertEqual(amounts["Local Party"], ("Dr", 117))  # 99.50 + 17.92 = 117.42 -> 117
        self.assertEqual(amounts["Round Off"], ("Dr", 0.42))

    def test_gst_report_net_liability(self):
        self.invoice("Purchase", "Supplier", "Purchases", 5, 100)   # input tax 90
        self.invoice("Sales", "Local Party", "Sales", 5, 200)       # output tax 180
        g = self.call(reports.gst_report)
        self.assertEqual(g["summary"]["outward"]["total_tax"], 180)
        self.assertEqual(g["summary"]["inward"]["total_tax"], 90)
        self.assertEqual(g["summary"]["net_payable"]["total"], 90)

    def test_stock_weighted_average(self):
        self.invoice("Purchase", "Supplier", "Purchases", 10, 200)  # 10@100 + 10@200 -> avg 150
        self.invoice("Sales", "Local Party", "Sales", 5, 400)
        s = self.call(reports.stock_summary)["rows"][0]
        self.assertEqual(s["closing_qty"], 15)
        self.assertEqual(s["closing_value"], 2250)
        self.assertEqual(s["outward_value"], 750)

    def test_preview_does_not_persist(self):
        with self.db.read() as conn:
            vouchers.preview_invoice(conn, self.cid, {"voucher_type": "Sales", "invoice": {
                "party_ledger_id": self.led["Local Party"], "account_ledger_id": self.led["Sales"],
                "round_off": True, "items": [{"item_id": self.item, "qty": 1, "rate": 99.5}]}})
        self.assertNotIn("Round Off", [x["name"] for x in self.call(masters.list_ledgers)])


class FinancialStatementsTest(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        with self.db.tx() as conn:
            self.cid = create_demo_company(conn)["id"]

    def test_statements_tally(self):
        with self.db.read() as conn:
            tb = reports.trial_balance(conn, self.cid)
            self.assertEqual(tb["total_dr"], tb["total_cr"])
            self.assertEqual(tb["difference_in_opening"], 0)
            bs = reports.balance_sheet(conn, self.cid)
            self.assertEqual(bs["total_assets"], bs["total_liabilities"])
            pl = reports.profit_loss(conn, self.cid)
            self.assertEqual(bs["profit_loss"]["current_period"], pl["net_profit"])

    def test_every_report_runs(self):
        with self.db.read() as conn:
            bank = next(x for x in masters.list_ledgers(conn, self.cid) if x["name"].startswith("HDFC"))
            item = masters.list_stock_items(conn, self.cid)[0]
            q = {"ledger_id": bank["id"], "group_id": 1, "item_id": item["id"]}
            for name in reports.REPORTS:
                with self.subTest(report=name):
                    self.assertIsInstance(reports.run_report(conn, self.cid, name, q), dict)

    def test_bank_reconciliation(self):
        with self.db.read() as conn:
            bank = next(x for x in masters.list_ledgers(conn, self.cid) if x["name"].startswith("HDFC"))["id"]
            brs = reports.bank_reconciliation(conn, self.cid, bank)
        first = next(r for r in brs["rows"] if r["credit"])
        with self.db.tx() as conn:
            vouchers.set_bank_date(conn, self.cid, first["entry_id"], first["date"])
            after = reports.bank_reconciliation(conn, self.cid, bank)
        self.assertEqual(after["balance_as_per_books"], brs["balance_as_per_books"])
        self.assertAlmostEqual(after["balance_as_per_bank"], brs["balance_as_per_bank"] - first["credit"], 2)

    def test_opening_difference_detected(self):
        with self.db.tx() as conn:
            g = {x["name"]: x["id"] for x in masters.list_groups(conn, self.cid)}
            masters.create_ledger(conn, self.cid, {"name": "Stray", "group_id": g["Fixed Assets"],
                                                   "opening_balance": 500, "opening_side": "Dr"})
            tb = reports.trial_balance(conn, self.cid)
            bs = reports.balance_sheet(conn, self.cid)
        self.assertEqual(tb["difference_in_opening"], 500)
        self.assertEqual(tb["total_dr"], tb["total_cr"])
        self.assertEqual(bs["total_assets"], bs["total_liabilities"])


if __name__ == "__main__":
    unittest.main()
