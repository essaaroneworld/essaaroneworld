"""Voucher engine: validation and persistence of accounting and inventory vouchers.

Rules follow Tally's behaviour for each base voucher type:

* every accounting voucher must balance (total Dr == total Cr) — double entry;
* Contra: only Cash/Bank ledgers (fund transfers, deposits, withdrawals);
* Payment: money goes out of a Cash/Bank ledger (credited);
* Receipt: money comes into a Cash/Bank ledger (debited);
* Journal: adjustments only — Cash/Bank ledgers are not allowed;
* Sales / Debit Note: goods go out; Purchase / Credit Note: goods come in;
* Stock Journal: inventory transfer / manufacturing, no ledgers;
* Memorandum: recorded but excluded from books and reports.
"""
import datetime as dt
import json

from . import audit
from .chart import Chart
from .errors import NotFound, ValidationError
from .gst import INVOICE_TYPES, PURCHASE_LIKE, SALES_LIKE, build_invoice
from .masters import _next_number, get_company, parse_date
from .money import to_paise, to_qty, to_rupees

REF_TYPES = ("New Ref", "Agst Ref", "Advance", "On Account")


def _voucher_type(conn, company_id, data):
    vt_id = data.get("voucher_type_id")
    if vt_id:
        r = conn.execute("SELECT * FROM voucher_types WHERE id=? AND company_id=?",
                         (int(vt_id), company_id)).fetchone()
    else:
        name = data.get("voucher_type")
        if not name:
            raise ValidationError("Voucher type is required")
        r = conn.execute("SELECT * FROM voucher_types WHERE name=? COLLATE NOCASE AND company_id=?",
                         (name, company_id)).fetchone()
    if not r:
        raise NotFound("Voucher type not found")
    return dict(r)


def _parse_entry(raw, idx):
    lid = raw.get("ledger_id")
    if not lid:
        raise ValidationError(f"Line {idx}: ledger is required")
    if "side" in raw:
        side = str(raw.get("side")).capitalize()
        if side not in ("Dr", "Cr"):
            raise ValidationError(f"Line {idx}: side must be Dr or Cr")
        amt = to_paise(raw.get("amount"))
        if amt <= 0:
            raise ValidationError(f"Line {idx}: amount must be greater than zero")
        signed = amt if side == "Dr" else -amt
    elif raw.get("_signed"):
        signed = int(raw["amount"])  # already signed paise (invoice builder)
    else:
        dr, cr = to_paise(raw.get("dr")), to_paise(raw.get("cr"))
        if dr and cr:
            raise ValidationError(f"Line {idx}: enter either a debit or a credit, not both")
        signed = dr - cr
    if signed == 0:
        raise ValidationError(f"Line {idx}: amount must be greater than zero")
    return {
        "ledger_id": int(lid), "amount": signed, "bills": raw.get("bills") or [],
        "instrument_no": (raw.get("instrument_no") or None),
        "instrument_date": (raw.get("instrument_date") or None),
        "bank_date": raw.get("bank_date") or None,
    }


def _parse_inventory(raw_lines, base_type, main_godown):
    lines = []
    for i, raw in enumerate(raw_lines or [], 1):
        if "qty" in raw and isinstance(raw.get("qty"), (int, float)) and raw.get("_signed"):
            lines.append(raw)
            continue
        item_id = raw.get("item_id")
        if not item_id:
            raise ValidationError(f"Item line {i}: stock item is required")
        qty = to_qty(raw.get("qty"))
        if qty <= 0:
            raise ValidationError(f"Item line {i}: quantity must be positive")
        rate = to_paise(raw.get("rate"))
        amount = to_paise(raw.get("amount")) if raw.get("amount") not in (None, "") else round(qty * rate)
        if base_type in SALES_LIKE:
            sign = -1
        elif base_type in PURCHASE_LIKE:
            sign = 1
        else:
            direction = raw.get("direction")
            if direction not in ("in", "out"):
                raise ValidationError(f"Item line {i}: direction must be 'in' (destination) or 'out' (source)")
            sign = 1 if direction == "in" else -1
        lines.append({"item_id": int(item_id), "godown_id": int(raw.get("godown_id") or main_godown),
                      "qty": sign * qty, "rate": rate, "amount": amount})
    return lines


def _validate_bills(conn, chart, entry, base_type, number, date, voucher_id):
    led = chart.ledgers[entry["ledger_id"]]
    if not led["bill_wise"]:
        return []
    sign = 1 if entry["amount"] > 0 else -1
    bills = []
    if not entry["bills"]:
        if base_type in INVOICE_TYPES:
            due = None
            if led["credit_days"]:
                due = (dt.date.fromisoformat(date) + dt.timedelta(days=led["credit_days"])).isoformat()
            bills.append({"ref_type": "New Ref", "name": number, "amount": entry["amount"], "due_date": due})
        else:
            bills.append({"ref_type": "On Account", "name": None, "amount": entry["amount"], "due_date": None})
        return bills
    total = 0
    for b in entry["bills"]:
        ref_type = b.get("ref_type") or "New Ref"
        if ref_type not in REF_TYPES:
            raise ValidationError(f"Bill type must be one of {', '.join(REF_TYPES)}")
        name = (b.get("name") or "").strip() or None
        if ref_type != "On Account" and not name:
            raise ValidationError(f"{led['name']}: bill name is required for {ref_type}")
        amt = to_paise(b.get("amount"))
        if amt <= 0:
            raise ValidationError(f"{led['name']}: bill amounts must be positive")
        if ref_type == "Agst Ref":
            exists = conn.execute(
                "SELECT 1 FROM bill_allocations b JOIN voucher_entries e ON e.id=b.entry_id "
                "WHERE b.ledger_id=? AND b.name=? AND b.ref_type IN ('New Ref','Advance') AND e.voucher_id != ?",
                (led["id"], name, voucher_id or 0)).fetchone()
            if not exists:
                raise ValidationError(f"{led['name']}: no pending bill named '{name}'")
        due = parse_date(b["due_date"], "due date") if b.get("due_date") else None
        bills.append({"ref_type": ref_type, "name": name, "amount": sign * amt, "due_date": due})
        total += amt
    if total != abs(entry["amount"]):
        raise ValidationError(f"{led['name']}: bill allocations ({to_rupees(total)}) must equal "
                              f"the ledger amount ({to_rupees(abs(entry['amount']))})")
    return bills


def _check_type_rules(chart, base_type, entries, inventory):
    if base_type == "Stock Journal":
        if entries:
            raise ValidationError("Stock Journal cannot have ledger entries")
        if not inventory:
            raise ValidationError("Stock Journal needs at least one stock item line")
        return
    if len(entries) < 2:
        raise ValidationError("A voucher needs at least one debit and one credit entry")
    dr = sum(e["amount"] for e in entries if e["amount"] > 0)
    cr = -sum(e["amount"] for e in entries if e["amount"] < 0)
    if dr != cr:
        raise ValidationError(f"Voucher does not balance: Dr {to_rupees(dr):.2f} vs Cr {to_rupees(cr):.2f} "
                              f"(difference {to_rupees(abs(dr - cr)):.2f})")
    for e in entries:
        if e["ledger_id"] not in chart.ledgers:
            raise NotFound("Ledger not found")
    cash = [chart.is_cash_or_bank(e["ledger_id"]) for e in entries]
    if base_type == "Contra" and not all(cash):
        raise ValidationError("Contra vouchers can only use Cash and Bank ledgers")
    if base_type == "Payment" and not any(c and e["amount"] < 0 for c, e in zip(cash, entries)):
        raise ValidationError("Payment voucher must credit a Cash or Bank ledger")
    if base_type == "Receipt" and not any(c and e["amount"] > 0 for c, e in zip(cash, entries)):
        raise ValidationError("Receipt voucher must debit a Cash or Bank ledger")
    if base_type == "Journal" and any(cash):
        raise ValidationError("Cash and Bank ledgers are not allowed in a Journal voucher; "
                              "use Payment, Receipt or Contra")


def save_voucher(conn, company_id, data, voucher_id=None):
    company = get_company(conn, company_id)
    chart = Chart(conn, company_id)
    existing = None
    if voucher_id:
        existing = conn.execute("SELECT * FROM vouchers WHERE id=? AND company_id=?",
                                (voucher_id, company_id)).fetchone()
        if not existing:
            raise NotFound("Voucher not found")
        if not data.get("voucher_type_id") and not data.get("voucher_type"):
            data = dict(data, voucher_type_id=existing["voucher_type_id"])
    vt = _voucher_type(conn, company_id, data)
    base = vt["base_type"]
    date = parse_date(data.get("date"), "Voucher date")
    if date < company["books_from"]:
        raise ValidationError(f"Voucher date is before books beginning date ({company['books_from']})")

    number = (str(data.get("number")).strip() if data.get("number") not in (None, "") else None)
    if existing and not number and existing["voucher_type_id"] == vt["id"]:
        number = existing["number"]
    number = number or _next_number(conn, vt["id"])
    dup = conn.execute("SELECT 1 FROM vouchers WHERE voucher_type_id=? AND number=? AND id != ?",
                       (vt["id"], number, voucher_id or 0)).fetchone()
    if dup:
        raise ValidationError(f"{vt['name']} voucher number {number} already exists")

    main_godown = conn.execute("SELECT id FROM godowns WHERE company_id=? ORDER BY is_predefined DESC, id LIMIT 1",
                               (company_id,)).fetchone()[0]
    mode = data.get("mode") or "accounting"
    meta = None
    party_id = data.get("party_ledger_id")
    if mode == "invoice":
        inv = data.get("invoice") or {}
        entries, inventory, summary = build_invoice(conn, company, base, inv, chart)
        # Bill-wise details supplied for the party line in invoice mode.
        entries[0]["bills"] = inv.get("bills") or []
        entries = [_parse_entry(dict(e, bills=e.get("bills", []), _signed=True), i)
                   for i, e in enumerate(entries, 1)]
        for line in inventory:
            line["_signed"] = True
        inventory = _parse_inventory(inventory, base, main_godown)
        party_id = inv.get("party_ledger_id")
        meta = {"invoice": inv, "summary": summary}
    else:
        if mode not in ("accounting", "inventory"):
            raise ValidationError("Mode must be accounting, invoice or inventory")
        entries = [_parse_entry(e, i) for i, e in enumerate(data.get("entries") or [], 1)]
        inventory = _parse_inventory(data.get("inventory"), base, main_godown)
        if base == "Stock Journal":
            mode = "inventory"

    for line in inventory:
        if not conn.execute("SELECT 1 FROM stock_items WHERE id=? AND company_id=?",
                            (line["item_id"], company_id)).fetchone():
            raise NotFound("Stock item not found")
        if not conn.execute("SELECT 1 FROM godowns WHERE id=? AND company_id=?",
                            (line["godown_id"], company_id)).fetchone():
            raise NotFound("Godown not found")
    if inventory and not company["inventory_enabled"]:
        raise ValidationError("Inventory is disabled for this company")

    _check_type_rules(chart, base, entries, inventory)
    if party_id:
        party_id = int(party_id)
        if party_id not in chart.ledgers:
            raise NotFound("Party ledger not found")
    elif base in INVOICE_TYPES + ("Payment", "Receipt"):
        # Remember the party for listings: first non cash/bank, non-tax ledger.
        for e in entries:
            led = chart.ledgers[e["ledger_id"]]
            if (chart.ledger_is_under(e["ledger_id"], "Sundry Debtors")
                    or chart.ledger_is_under(e["ledger_id"], "Sundry Creditors")):
                party_id = led["id"]
                break

    bills_per_entry = [_validate_bills(conn, chart, e, base, number, date, voucher_id)
                       for e in entries] if base != "Memorandum" else [[] for _ in entries]

    fields = {
        "voucher_type_id": vt["id"], "number": number, "date": date,
        "reference": (data.get("reference") or None), "narration": (data.get("narration") or None),
        "party_ledger_id": party_id, "mode": mode,
        "meta": json.dumps(meta) if meta else None,
    }
    if existing:
        before = get_voucher(conn, company_id, voucher_id)
        conn.execute(f"UPDATE vouchers SET {', '.join(k + ' = ?' for k in fields)}, "
                     f"updated_at = CURRENT_TIMESTAMP WHERE id = ?", list(fields.values()) + [voucher_id])
        conn.execute("DELETE FROM voucher_entries WHERE voucher_id=?", (voucher_id,))
        conn.execute("DELETE FROM inventory_entries WHERE voucher_id=?", (voucher_id,))
    else:
        cols = list(fields)
        voucher_id = conn.execute(
            f"INSERT INTO vouchers(company_id, {','.join(cols)}) VALUES (?, {','.join('?' * len(cols))})",
            [company_id] + list(fields.values())).lastrowid

    for i, (e, bills) in enumerate(zip(entries, bills_per_entry), 1):
        eid = conn.execute(
            "INSERT INTO voucher_entries(voucher_id, line_no, ledger_id, amount, instrument_no, instrument_date, bank_date)"
            " VALUES (?,?,?,?,?,?,?)",
            (voucher_id, i, e["ledger_id"], e["amount"], e["instrument_no"], e["instrument_date"],
             e["bank_date"])).lastrowid
        for b in bills:
            conn.execute("INSERT INTO bill_allocations(entry_id, ledger_id, ref_type, name, amount, due_date)"
                         " VALUES (?,?,?,?,?,?)",
                         (eid, e["ledger_id"], b["ref_type"], b["name"], b["amount"], b["due_date"]))
    for i, line in enumerate(inventory, 1):
        conn.execute("INSERT INTO inventory_entries(voucher_id, line_no, item_id, godown_id, qty, rate, amount)"
                     " VALUES (?,?,?,?,?,?,?)",
                     (voucher_id, i, line["item_id"], line["godown_id"], line["qty"], line["rate"],
                      line["amount"]))

    saved = get_voucher(conn, company_id, voucher_id)
    if existing:
        audit.log(conn, company_id, "alter", "voucher", voucher_id,
                  f"Altered {vt['name']} No. {number} dated {date}", {"before": before, "after": saved})
    else:
        audit.log(conn, company_id, "create", "voucher", voucher_id,
                  f"Created {vt['name']} No. {number} dated {date} for {saved['total']:.2f}", saved)
    return saved


def get_voucher(conn, company_id, voucher_id):
    v = conn.execute(
        "SELECT v.*, t.name AS voucher_type, t.base_type, p.name AS party_name FROM vouchers v "
        "JOIN voucher_types t ON t.id = v.voucher_type_id LEFT JOIN ledgers p ON p.id = v.party_ledger_id "
        "WHERE v.id=? AND v.company_id=?", (voucher_id, company_id)).fetchone()
    if not v:
        raise NotFound("Voucher not found")
    out = dict(v)
    out["meta"] = json.loads(out["meta"]) if out["meta"] else None
    entries = []
    for e in conn.execute("SELECT e.*, l.name AS ledger_name FROM voucher_entries e JOIN ledgers l "
                          "ON l.id = e.ledger_id WHERE e.voucher_id=? ORDER BY e.line_no", (voucher_id,)):
        bills = [{"ref_type": b["ref_type"], "name": b["name"], "amount": to_rupees(abs(b["amount"])),
                  "due_date": b["due_date"]}
                 for b in conn.execute("SELECT * FROM bill_allocations WHERE entry_id=? ORDER BY id", (e["id"],))]
        entries.append({
            "id": e["id"], "ledger_id": e["ledger_id"], "ledger_name": e["ledger_name"],
            "side": "Dr" if e["amount"] > 0 else "Cr", "amount": to_rupees(abs(e["amount"])),
            "instrument_no": e["instrument_no"], "instrument_date": e["instrument_date"],
            "bank_date": e["bank_date"], "bills": bills,
        })
    inventory = []
    for r in conn.execute(
            "SELECT ie.*, i.name AS item_name, u.symbol AS unit, g.name AS godown FROM inventory_entries ie "
            "JOIN stock_items i ON i.id = ie.item_id JOIN units u ON u.id = i.unit_id "
            "LEFT JOIN godowns g ON g.id = ie.godown_id WHERE ie.voucher_id=? ORDER BY ie.line_no", (voucher_id,)):
        inventory.append({"item_id": r["item_id"], "item_name": r["item_name"], "unit": r["unit"],
                          "godown_id": r["godown_id"], "godown": r["godown"],
                          "direction": "in" if r["qty"] > 0 else "out", "qty": abs(r["qty"]),
                          "rate": to_rupees(r["rate"]), "amount": to_rupees(r["amount"])})
    out["entries"] = entries
    out["inventory"] = inventory
    dr_total = sum(e["amount"] for e in entries if e["side"] == "Dr")
    out["total"] = round(dr_total if entries else sum(i["amount"] for i in inventory if i["direction"] == "in"), 2)
    return out


def list_vouchers(conn, company_id, date_from=None, date_to=None, voucher_type_id=None,
                  base_type=None, ledger_id=None, limit=1000):
    sql = ("SELECT v.id FROM vouchers v JOIN voucher_types t ON t.id = v.voucher_type_id "
           "WHERE v.company_id = ?")
    args = [company_id]
    if date_from:
        sql += " AND v.date >= ?"
        args.append(parse_date(date_from, "from date"))
    if date_to:
        sql += " AND v.date <= ?"
        args.append(parse_date(date_to, "to date"))
    if voucher_type_id:
        sql += " AND v.voucher_type_id = ?"
        args.append(int(voucher_type_id))
    if base_type:
        sql += " AND t.base_type = ?"
        args.append(base_type)
    if ledger_id:
        sql += " AND v.id IN (SELECT voucher_id FROM voucher_entries WHERE ledger_id = ?)"
        args.append(int(ledger_id))
    sql += " ORDER BY v.date, v.id LIMIT ?"
    args.append(int(limit))
    return [get_voucher(conn, company_id, r["id"]) for r in conn.execute(sql, args)]


def delete_voucher(conn, company_id, voucher_id):
    v = get_voucher(conn, company_id, voucher_id)
    conn.execute("DELETE FROM vouchers WHERE id=?", (voucher_id,))
    audit.log(conn, company_id, "delete", "voucher", voucher_id,
              f"Deleted {v['voucher_type']} No. {v['number']} dated {v['date']}", v)
    return {"deleted": f"{v['voucher_type']} {v['number']}"}


def preview_invoice(conn, company_id, data):
    company = get_company(conn, company_id)
    vt = _voucher_type(conn, company_id, data)
    chart = Chart(conn, company_id)
    # Run in a savepoint so an auto-created Round Off ledger is not persisted by a preview.
    conn.execute("SAVEPOINT preview")
    try:
        entries, inventory, summary = build_invoice(conn, company, vt["base_type"], data.get("invoice") or {}, chart)
        names = {lid: chart.ledgers[lid]["name"] for lid in chart.ledgers}
        rows = [{"ledger_id": e["ledger_id"], "ledger_name": names.get(e["ledger_id"], "Round Off"),
                 "side": "Dr" if e["amount"] > 0 else "Cr", "amount": to_rupees(abs(e["amount"]))}
                for e in entries]
    finally:
        conn.execute("ROLLBACK TO preview")
        conn.execute("RELEASE preview")
    return {"entries": rows, "summary": summary}


def set_bank_date(conn, company_id, entry_id, bank_date):
    row = conn.execute("SELECT e.*, v.date FROM voucher_entries e JOIN vouchers v ON v.id=e.voucher_id "
                       "WHERE e.id=? AND v.company_id=?", (entry_id, company_id)).fetchone()
    if not row:
        raise NotFound("Entry not found")
    chart = Chart(conn, company_id)
    if not chart.is_cash_or_bank(row["ledger_id"]) or chart.ledger_is_under(row["ledger_id"], "Cash-in-Hand"):
        raise ValidationError("Bank reconciliation applies to bank ledgers only")
    if bank_date:
        bank_date = parse_date(bank_date, "bank date")
        if bank_date < row["date"]:
            raise ValidationError("Bank date cannot be before the voucher date")
    conn.execute("UPDATE voucher_entries SET bank_date=? WHERE id=?", (bank_date or None, entry_id))
    audit.log(conn, company_id, "reconcile", "voucher_entry", entry_id,
              f"Bank date set to {bank_date or '(cleared)'}")
    return {"entry_id": entry_id, "bank_date": bank_date or None}
