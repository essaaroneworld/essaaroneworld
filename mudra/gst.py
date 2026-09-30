"""GST-aware invoice builder.

Turns an item invoice (party + sales/purchase ledger + stock lines) into the
underlying double-entry lines, splitting tax into CGST+SGST for intra-state
supplies and IGST for inter-state supplies, exactly as Tally does when GST
is enabled for the company.
"""
from decimal import Decimal

from .chart import Chart
from .errors import ValidationError
from .money import round_half_up, to_paise, to_qty, to_rupees

SALES_LIKE = ("Sales", "Debit Note")        # party debited, goods go out
PURCHASE_LIKE = ("Purchase", "Credit Note")  # party credited, goods come in
INVOICE_TYPES = SALES_LIKE + PURCHASE_LIKE


def _tax_ledger(chart, gst_type):
    for led in chart.ledgers.values():
        if led["gst_type"] == gst_type:
            return led["id"]
    raise ValidationError(f"No {gst_type} ledger found. Create a ledger under Duties & Taxes "
                          f"with GST type {gst_type}.")


def _round_off_ledger(conn, company_id, chart):
    for led in chart.ledgers.values():
        if led["name"].lower() == "round off":
            return led["id"]
    gid = chart.group_id("Indirect Expenses")
    lid = conn.execute("INSERT INTO ledgers(company_id, name, group_id) VALUES (?,?,?)",
                       (company_id, "Round Off", gid)).lastrowid
    chart.ledgers[lid] = dict(conn.execute("SELECT * FROM ledgers WHERE id=?", (lid,)).fetchone())
    chart.ledgers_by_group.setdefault(gid, []).append(lid)
    return lid


def is_interstate(company, party):
    a = (company.get("state") or "").strip().lower()
    b = (party.get("state") or "").strip().lower()
    if party.get("gstin") and company.get("gstin"):
        return party["gstin"][:2] != company["gstin"][:2]
    return bool(a and b and a != b)


def build_invoice(conn, company, base_type, inv, chart=None):
    """Return (entries, inventory, summary) for an invoice payload.

    entries: [{ledger_id, amount(paise, Dr+)}]; inventory: [{item_id, godown_id, qty(signed), rate, amount}]
    """
    if base_type not in INVOICE_TYPES:
        raise ValidationError(f"Invoice mode is not available for {base_type} vouchers")
    chart = chart or Chart(conn, company["id"])
    party_id = inv.get("party_ledger_id")
    account_id = inv.get("account_ledger_id")
    if not party_id or int(party_id) not in chart.ledgers:
        raise ValidationError("Party A/c name is required")
    if not account_id or int(account_id) not in chart.ledgers:
        raise ValidationError(("Sales" if base_type in ("Sales", "Credit Note") else "Purchase")
                              + " ledger is required")
    party_id, account_id = int(party_id), int(account_id)
    party = chart.ledgers[party_id]
    lines = inv.get("items") or []
    if not lines:
        raise ValidationError("Add at least one stock item to the invoice")

    items = {r["id"]: dict(r) for r in conn.execute(
        "SELECT * FROM stock_items WHERE company_id=?", (company["id"],))}
    main_godown = conn.execute("SELECT id FROM godowns WHERE company_id=? ORDER BY is_predefined DESC, id LIMIT 1",
                               (company["id"],)).fetchone()[0]
    apply_gst = inv.get("apply_gst", company.get("gst_enabled", True))
    interstate = is_interstate(company, party)
    direction = -1 if base_type in SALES_LIKE else 1  # inventory qty sign

    inventory, taxable_total = [], 0
    tax = {"CGST": 0, "SGST": 0, "IGST": 0}
    tax_rows = []
    for i, line in enumerate(lines, 1):
        item_id = line.get("item_id")
        if not item_id or int(item_id) not in items:
            raise ValidationError(f"Line {i}: stock item not found")
        item = items[int(item_id)]
        qty = to_qty(line.get("qty"))
        if qty <= 0:
            raise ValidationError(f"Line {i}: quantity must be positive")
        rate = to_paise(line.get("rate"))
        disc = Decimal(str(line.get("discount") or 0))
        gross = Decimal(str(qty)) * rate
        taxable = round_half_up(gross * (100 - disc) / 100)
        gst_rate = Decimal(str(line.get("gst_rate", item["gst_rate"]) or 0)) if apply_gst else Decimal(0)
        if interstate:
            igst = round_half_up(taxable * gst_rate / 100)
            tax["IGST"] += igst
            line_tax = {"IGST": igst}
        else:
            half = round_half_up(taxable * gst_rate / 200)
            tax["CGST"] += half
            tax["SGST"] += half
            line_tax = {"CGST": half, "SGST": half}
        taxable_total += taxable
        godown_id = int(line.get("godown_id") or main_godown)
        inventory.append({"item_id": item["id"], "godown_id": godown_id, "qty": direction * qty,
                          "rate": rate, "amount": taxable})
        tax_rows.append({"item": item["name"], "hsn": item["hsn"], "taxable": to_rupees(taxable),
                         "gst_rate": float(gst_rate),
                         **{k: to_rupees(v) for k, v in line_tax.items()}})

    # Signs: sales-like invoices debit the party and credit everything else.
    s = 1 if base_type in SALES_LIKE else -1
    entries = [{"ledger_id": account_id, "amount": -s * taxable_total}]
    for tax_type, amt in tax.items():
        if amt:
            entries.append({"ledger_id": _tax_ledger(chart, tax_type), "amount": -s * amt})
    for extra in inv.get("additional") or []:  # freight, packing etc.
        lid = int(extra.get("ledger_id") or 0)
        if lid not in chart.ledgers:
            raise ValidationError("Additional ledger not found")
        amt = to_paise(extra.get("amount"))
        if amt:
            entries.append({"ledger_id": lid, "amount": -s * amt})
    total = -sum(e["amount"] for e in entries) * s
    round_off = 0
    if inv.get("round_off"):
        rounded = round_half_up(Decimal(total) / 100) * 100
        round_off = rounded - total
        if round_off:
            entries.append({"ledger_id": _round_off_ledger(conn, company["id"], chart),
                            "amount": -s * round_off})
            total = rounded
    entries.insert(0, {"ledger_id": party_id, "amount": s * total})

    summary = {
        "interstate": interstate,
        "taxable": to_rupees(taxable_total),
        "cgst": to_rupees(tax["CGST"]), "sgst": to_rupees(tax["SGST"]), "igst": to_rupees(tax["IGST"]),
        "round_off": to_rupees(round_off),
        "total": to_rupees(total),
        "lines": tax_rows,
    }
    return entries, inventory, summary
