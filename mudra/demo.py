"""Sample company with a month of realistic transactions, for exploring Mudra."""
import datetime as dt

from . import masters, vouchers


def create_demo_company(conn, name="Mudra Traders Pvt Ltd"):
    today = dt.date.today()
    fy_year = today.year if today.month >= 4 else today.year - 1
    fy = f"{fy_year}-04-01"
    base = dt.date(fy_year, 4, 1)

    def d(n):
        return (base + dt.timedelta(days=n)).isoformat()

    c = masters.create_company(conn, {
        "name": name, "address": "12 MG Road, Fort", "state": "Maharashtra", "pincode": "400001",
        "phone": "022-4000 1234", "email": "accounts@mudratraders.example", "gstin": "27AABCM1234F1Z5",
        "pan": "AABCM1234F", "fy_start": fy, "books_from": fy,
    })
    cid = c["id"]
    g = {x["name"]: x["id"] for x in masters.list_groups(conn, cid)}
    led = {x["name"]: x["id"] for x in masters.list_ledgers(conn, cid)}

    def ledger(name, group, **kw):
        led[name] = masters.create_ledger(conn, cid, {"name": name, "group_id": g[group], **kw})["id"]

    ledger("HDFC Bank Current A/c", "Bank Accounts", opening_balance=850000, opening_side="Dr",
           bank_name="HDFC Bank", account_no="50200012345678", ifsc="HDFC0000060")
    ledger("SBI Cash Credit", "Bank OD A/c", opening_balance=0)
    ledger("Furniture & Fixtures", "Fixed Assets", opening_balance=250000, opening_side="Dr")
    ledger("Sales - GST", "Sales Accounts")
    ledger("Purchase - GST", "Purchase Accounts")
    ledger("Freight Inward", "Direct Expenses")
    ledger("Office Rent", "Indirect Expenses")
    ledger("Salaries", "Indirect Expenses")
    ledger("Electricity Charges", "Indirect Expenses")
    ledger("Bank Charges", "Indirect Expenses")
    ledger("Interest Received", "Indirect Incomes")
    ledger("TDS Payable", "Duties & Taxes")
    ledger("Sharma Electronics", "Sundry Debtors", state="Maharashtra", gstin="27AAKFS5678K1Z2",
           credit_days=30, address="Andheri East, Mumbai")
    ledger("Kaveri Retail", "Sundry Debtors", state="Karnataka", gstin="29AAGCK4321L1Z9", credit_days=45,
           address="Jayanagar, Bengaluru")
    ledger("Walk-in Customer", "Sundry Debtors", state="Maharashtra", bill_wise=False)
    ledger("Bharat Components Ltd", "Sundry Creditors", state="Gujarat", gstin="24AABCB9876M1Z4",
           credit_days=30, address="GIDC, Ahmedabad")
    ledger("Pune Supplies Co", "Sundry Creditors", state="Maharashtra", gstin="27AAHFP1111N1Z7",
           credit_days=15, opening_balance=0)

    sg = masters.save_stock_group(conn, cid, {"name": "Electronics"})["id"]
    sg2 = masters.save_stock_group(conn, cid, {"name": "Accessories"})["id"]
    units = {u["symbol"]: u["id"] for u in masters.list_units(conn, cid)}
    masters.save_godown(conn, cid, {"name": "Pune Warehouse"})
    capital_extra = 0
    items = {}
    for name, grp, unit, hsn, rate, oq, orate in [
        ("LED Monitor 24in", sg, "Nos", "8528", 18, 40, 7200),
        ("Wireless Keyboard", sg2, "Nos", "8471", 18, 120, 650),
        ("Optical Mouse", sg2, "Nos", "8471", 18, 200, 180),
        ("Laptop 14in i5", sg, "Nos", "8471", 18, 15, 42000),
        ("HDMI Cable 2m", sg2, "Pcs", "8544", 18, 300, 90),
    ]:
        it = masters.save_stock_item(conn, cid, {"name": name, "group_id": grp, "unit_id": units[unit],
                                                 "hsn": hsn, "gst_rate": rate, "opening_qty": oq,
                                                 "opening_rate": orate})
        items[name] = it["id"]
        capital_extra += oq * orate
    # Capital funds the opening bank balance, furniture and opening stock so opening balances tally.
    ledger("Capital - Aarav Mehta", "Capital Account", opening_balance=850000 + 250000 + capital_extra,
           opening_side="Cr")
    ledger("Salary Payable", "Provisions")

    def v(vtype, date, **kw):
        return vouchers.save_voucher(conn, cid, {"voucher_type": vtype, "date": date, **kw})

    def inv(vtype, date, party, account, lines, bills=None, **kw):
        return v(vtype, date, mode="invoice", invoice={
            "party_ledger_id": led[party], "account_ledger_id": led[account], "round_off": True,
            "items": [{"item_id": items[i], "qty": q, "rate": r} for i, q, r in lines],
            "bills": bills or []}, **kw)

    v("Contra", d(1), entries=[{"ledger_id": led["Cash"], "side": "Dr", "amount": 100000},
                               {"ledger_id": led["HDFC Bank Current A/c"], "side": "Cr", "amount": 100000,
                                "instrument_no": "000451"}], narration="Cash withdrawn for office use")
    inv("Purchase", d(2), "Bharat Components Ltd", "Purchase - GST",
        [("LED Monitor 24in", 25, 7000), ("Laptop 14in i5", 10, 41500)], reference="BCL/2201",
        narration="Purchase against PO 17")
    inv("Purchase", d(3), "Pune Supplies Co", "Purchase - GST",
        [("Wireless Keyboard", 100, 640), ("Optical Mouse", 150, 175), ("HDMI Cable 2m", 200, 85)],
        reference="PSC/889")
    inv("Sales", d(4), "Sharma Electronics", "Sales - GST",
        [("LED Monitor 24in", 30, 9500), ("Wireless Keyboard", 80, 950)], narration="Tax invoice")
    inv("Sales", d(6), "Kaveri Retail", "Sales - GST",
        [("Laptop 14in i5", 12, 52000), ("Optical Mouse", 90, 320)], narration="Inter-state supply")
    inv("Sales", d(8), "Walk-in Customer", "Sales - GST", [("HDMI Cable 2m", 40, 180)])
    v("Receipt", d(8), entries=[{"ledger_id": led["Cash"], "side": "Dr", "amount": 8496},
                                {"ledger_id": led["Walk-in Customer"], "side": "Cr", "amount": 8496}])
    v("Payment", d(9), entries=[{"ledger_id": led["Freight Inward"], "side": "Dr", "amount": 4500},
                                {"ledger_id": led["Cash"], "side": "Cr", "amount": 4500}],
      narration="Freight on Bharat Components consignment")
    v("Receipt", d(12), entries=[
        {"ledger_id": led["HDFC Bank Current A/c"], "side": "Dr", "amount": 200000, "instrument_no": "334521",
         "instrument_date": d(12)},
        {"ledger_id": led["Sharma Electronics"], "side": "Cr", "amount": 200000,
         "bills": [{"ref_type": "Agst Ref", "name": "1", "amount": 200000}]}],
      narration="Part payment against invoice 1 by cheque")
    v("Payment", d(14), entries=[
        {"ledger_id": led["Bharat Components Ltd"], "side": "Dr", "amount": 400000,
         "bills": [{"ref_type": "Agst Ref", "name": "1", "amount": 400000}]},
        {"ledger_id": led["HDFC Bank Current A/c"], "side": "Cr", "amount": 400000, "instrument_no": "000452",
         "instrument_date": d(14)}], narration="NEFT to Bharat Components")
    v("Payment", d(20), entries=[{"ledger_id": led["Office Rent"], "side": "Dr", "amount": 45000},
                                 {"ledger_id": led["TDS Payable"], "side": "Cr", "amount": 4500},
                                 {"ledger_id": led["HDFC Bank Current A/c"], "side": "Cr", "amount": 40500,
                                  "instrument_no": "000453"}], narration="Rent for April less TDS 10%")
    v("Payment", d(29), entries=[{"ledger_id": led["Salaries"], "side": "Dr", "amount": 120000},
                                 {"ledger_id": led["HDFC Bank Current A/c"], "side": "Cr", "amount": 120000}],
      narration="Salaries for April")
    v("Payment", d(29), entries=[{"ledger_id": led["Electricity Charges"], "side": "Dr", "amount": 6800},
                                 {"ledger_id": led["Cash"], "side": "Cr", "amount": 6800}])
    v("Payment", d(29), entries=[{"ledger_id": led["Bank Charges"], "side": "Dr", "amount": 354},
                                 {"ledger_id": led["HDFC Bank Current A/c"], "side": "Cr", "amount": 354}])
    v("Receipt", d(29), entries=[{"ledger_id": led["HDFC Bank Current A/c"], "side": "Dr", "amount": 2150},
                                 {"ledger_id": led["Interest Received"], "side": "Cr", "amount": 2150}])
    inv("Credit Note", d(15), "Kaveri Retail", "Sales - GST", [("Optical Mouse", 5, 320)],
        bills=[{"ref_type": "Agst Ref", "name": "2", "amount": 1888}],
        narration="Goods returned against invoice 2 - damaged in transit")
    v("Stock Journal", d(18), inventory=[
        {"item_id": items["Laptop 14in i5"], "qty": 5, "rate": 41500, "direction": "out"},
        {"item_id": items["Laptop 14in i5"], "qty": 5, "rate": 41500, "direction": "in",
         "godown_id": masters.list_godowns(conn, cid)[1]["id"]}], narration="Transfer to Pune warehouse")
    v("Journal", d(29), entries=[{"ledger_id": led["Salaries"], "side": "Dr", "amount": 15000},
                                 {"ledger_id": led["Salary Payable"], "side": "Cr", "amount": 15000}],
      narration="Provision for April overtime payable")
    return c
