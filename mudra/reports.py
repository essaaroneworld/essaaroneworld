"""Financial and inventory reports.

Everything is derived on the fly from masters + voucher entries (Tally's
"no posting, no closing entries" model): a report is simply an aggregation of
voucher lines over a period, rolled up through the group hierarchy.

Sign convention: paise, Dr positive / Cr negative. Values are converted to
rupees only at the edge (``_r``).
"""
import datetime as dt
from collections import defaultdict

from .chart import Chart
from .errors import NotFound, ValidationError
from .masters import get_company, parse_date
from .money import to_rupees as _r
from .seed import CASH_GROUPS, PL_LEDGER

NON_BOOK_TYPES = ("Memorandum",)


# ---------------------------------------------------------------- period helpers

def fy_end(fy_start):
    d = dt.date.fromisoformat(fy_start)
    return dt.date(d.year + 1, d.month, d.day) - dt.timedelta(days=1)


def period(company, date_from=None, date_to=None):
    start = max(company["fy_start"], company["books_from"])
    frm = parse_date(date_from, "from date") if date_from else start
    to = parse_date(date_to, "to date") if date_to else fy_end(company["fy_start"]).isoformat()
    if frm < company["books_from"]:
        frm = company["books_from"]
    if to < frm:
        raise ValidationError("'To' date must be on or after 'From' date")
    return frm, to


def _day_before(iso):
    return (dt.date.fromisoformat(iso) - dt.timedelta(days=1)).isoformat()


# ---------------------------------------------------------------- ledger balances

def ledger_balances(conn, company_id, frm, to, chart=None):
    """Per-ledger opening (as at frm), period Dr, period Cr and closing (as at to)."""
    chart = chart or Chart(conn, company_id)
    rows = conn.execute(
        """SELECT e.ledger_id,
                  SUM(CASE WHEN v.date < :frm THEN e.amount ELSE 0 END) AS pre,
                  SUM(CASE WHEN v.date >= :frm AND e.amount > 0 THEN e.amount ELSE 0 END) AS dr,
                  SUM(CASE WHEN v.date >= :frm AND e.amount < 0 THEN -e.amount ELSE 0 END) AS cr
           FROM voucher_entries e
           JOIN vouchers v ON v.id = e.voucher_id
           JOIN voucher_types t ON t.id = v.voucher_type_id
           WHERE v.company_id = :cid AND t.base_type NOT IN ('Memorandum') AND v.date <= :to
           GROUP BY e.ledger_id""",
        {"frm": frm, "to": to, "cid": company_id},
    ).fetchall()
    moves = {r["ledger_id"]: r for r in rows}
    out = {}
    for lid, led in chart.ledgers.items():
        m = moves.get(lid)
        pre, dr, cr = (m["pre"], m["dr"], m["cr"]) if m else (0, 0, 0)
        opening = led["opening_balance"] + pre
        out[lid] = {"opening": opening, "dr": dr, "cr": cr, "closing": opening + dr - cr}
    return out


def _ledger_node(led, b):
    return {"type": "ledger", "id": led["id"], "name": led["name"], **b}


def _group_node(chart, gid, bal, extra=None, include_zero=False):
    """Recursive group node with rolled-up opening/dr/cr/closing."""
    g = chart.groups[gid]
    node = {"type": "group", "id": gid, "name": g["name"], "nature": g["nature"],
            "opening": 0, "dr": 0, "cr": 0, "closing": 0, "children": []}
    for cid in sorted(chart.children[gid], key=lambda c: chart.groups[c]["name"].lower()):
        child = _group_node(chart, cid, bal, extra, include_zero)
        if include_zero or child["opening"] or child["dr"] or child["cr"] or child["closing"]:
            node["children"].append(child)
        for k in ("opening", "dr", "cr", "closing"):
            node[k] += child[k]
    for lid in chart.ledgers_by_group.get(gid, []):
        b = bal[lid]
        if include_zero or b["opening"] or b["dr"] or b["cr"] or b["closing"]:
            node["children"].append(_ledger_node(chart.ledgers[lid], b))
        for k in ("opening", "dr", "cr", "closing"):
            node[k] += b[k]
    if extra and gid in extra:
        for label, amount in extra[gid]:
            node["children"].append({"type": "stock", "id": None, "name": label, "opening": 0,
                                     "dr": 0, "cr": 0, "closing": amount})
            node["closing"] += amount
    return node


def _to_rupees_tree(node):
    out = {k: v for k, v in node.items() if k != "children"}
    for k in ("opening", "dr", "cr", "closing"):
        if k in out:
            out[k] = _r(out[k])
    if "children" in node:
        out["children"] = [_to_rupees_tree(c) for c in node["children"]]
    return out


# ---------------------------------------------------------------- stock valuation

def inventory_rows(conn, company_id, frm, to):
    """Inventory lines in a period, flagging pure godown transfers.

    A Stock Journal line whose item nets to zero quantity within the same
    voucher only moves goods between godowns: it must not count as an inward
    purchase (which would distort average cost) nor as consumption.
    """
    rows = [dict(r) for r in conn.execute(
        """SELECT ie.voucher_id, ie.item_id, ie.qty, ie.amount, t.base_type FROM inventory_entries ie
           JOIN vouchers v ON v.id = ie.voucher_id JOIN voucher_types t ON t.id = v.voucher_type_id
           WHERE v.company_id = ? AND v.date >= ? AND v.date <= ? AND t.base_type NOT IN ('Memorandum')""",
        (company_id, frm or "0000-00-00", to))]
    net = defaultdict(float)
    for r in rows:
        if r["base_type"] == "Stock Journal":
            net[(r["voucher_id"], r["item_id"])] += r["qty"]
    for r in rows:
        r["transfer"] = r["base_type"] == "Stock Journal" and abs(net[(r["voucher_id"], r["item_id"])]) < 1e-9
    return rows

def stock_position(conn, company_id, as_of=None):
    """Quantity and weighted-average-cost value per item as at a date.

    ``as_of=None`` returns the opening position (item opening balances only).
    """
    items = {r["id"]: dict(r) for r in conn.execute(
        "SELECT i.*, u.symbol AS unit, COALESCE(g.name,'Primary') AS group_name FROM stock_items i "
        "JOIN units u ON u.id=i.unit_id LEFT JOIN stock_groups g ON g.id=i.group_id "
        "WHERE i.company_id=? ORDER BY i.name", (company_id,))}
    agg = defaultdict(lambda: {"in_qty": 0.0, "in_val": 0, "net_qty": 0.0})
    if as_of is not None:
        for r in inventory_rows(conn, company_id, None, as_of):
            a = agg[r["item_id"]]
            a["net_qty"] += r["qty"]
            if r["qty"] > 0 and not r["transfer"]:
                a["in_qty"] += r["qty"]
                a["in_val"] += r["amount"]
    out = {}
    for iid, it in items.items():
        a = agg[iid]
        in_qty = it["opening_qty"] + a["in_qty"]
        in_val = it["opening_value"] + a["in_val"]
        qty = round(it["opening_qty"] + a["net_qty"], 6)
        rate = (in_val / in_qty) if in_qty else 0
        out[iid] = {"item": it, "qty": qty, "rate": rate, "value": round(qty * rate)}
    return out


def stock_value(conn, company_id, as_of=None):
    return sum(p["value"] for p in stock_position(conn, company_id, as_of).values())


def _opening_stock_for(conn, company, frm):
    if frm <= company["books_from"]:
        return stock_value(conn, company["id"], None)
    return stock_value(conn, company["id"], _day_before(frm))


# ---------------------------------------------------------------- trial balance

def trial_balance(conn, company_id, date_from=None, date_to=None):
    company = get_company(conn, company_id)
    frm, to = period(company, date_from, date_to)
    chart = Chart(conn, company_id)
    bal = ledger_balances(conn, company_id, frm, to, chart)
    rows = [_group_node(chart, gid, bal) for gid in sorted(chart.roots, key=lambda g: chart.groups[g]["name"])]
    rows = [r for r in rows if r["closing"] or r["dr"] or r["cr"] or r["opening"]]
    for lid in chart.ledgers_by_group.get(None, []):  # Profit & Loss A/c
        b = bal[lid]
        if b["closing"] or b["dr"] or b["cr"]:
            rows.append(_ledger_node(chart.ledgers[lid], b))
    opening_stock = stock_value(conn, company_id, None) if company["inventory_enabled"] else 0
    if opening_stock:
        rows.append({"type": "stock", "id": None, "name": "Opening Stock", "opening": opening_stock,
                     "dr": 0, "cr": 0, "closing": opening_stock})
    total = sum(r["closing"] for r in rows)
    diff = total  # non-zero only when opening balances do not tally
    if diff:
        rows.append({"type": "diff", "id": None, "name": "Difference in opening balances",
                     "opening": -diff, "dr": 0, "cr": 0, "closing": -diff})
    dr_total = sum(r["closing"] for r in rows if r["closing"] > 0)
    cr_total = -sum(r["closing"] for r in rows if r["closing"] < 0)
    return {"from": frm, "to": to, "rows": [_to_rupees_tree(r) for r in rows],
            "total_dr": _r(dr_total), "total_cr": _r(cr_total),
            "period_dr": _r(sum(r["dr"] for r in rows)), "period_cr": _r(sum(r["cr"] for r in rows)),
            "difference_in_opening": _r(diff)}


# ---------------------------------------------------------------- profit & loss

def _pl_compute(conn, company, frm, to, chart=None):
    chart = chart or Chart(conn, company["id"])
    bal = ledger_balances(conn, company["id"], frm, to, chart)
    include_ob = frm <= company["books_from"]
    # Revenue ledgers show only the period's movement (plus any opening balance at books start).
    rev = {lid: {"opening": 0, "dr": b["dr"], "cr": b["cr"],
                 "closing": (chart.ledgers[lid]["opening_balance"] if include_ob else 0) + b["dr"] - b["cr"]}
           for lid, b in bal.items()}
    trading_dr, trading_cr, pl_dr, pl_cr = [], [], [], []
    for gid in sorted(chart.roots, key=lambda g: chart.groups[g]["name"]):
        g = chart.groups[gid]
        if g["nature"] not in ("Income", "Expenses"):
            continue
        node = _group_node(chart, gid, rev)
        if not (node["closing"] or node["children"]):
            continue
        if g["nature"] == "Expenses":
            node["amount"] = node["closing"]
            (trading_dr if g["affects_gross_profit"] else pl_dr).append(node)
        else:
            node["amount"] = -node["closing"]
            (trading_cr if g["affects_gross_profit"] else pl_cr).append(node)
    inv = bool(company["inventory_enabled"])
    opening_stock = _opening_stock_for(conn, company, frm) if inv else 0
    closing_stock = stock_value(conn, company["id"], to) if inv else 0
    gross = (sum(n["amount"] for n in trading_cr) + closing_stock
             - sum(n["amount"] for n in trading_dr) - opening_stock)
    net = gross + sum(n["amount"] for n in pl_cr) - sum(n["amount"] for n in pl_dr)
    return {"trading_dr": trading_dr, "trading_cr": trading_cr, "pl_dr": pl_dr, "pl_cr": pl_cr,
            "opening_stock": opening_stock, "closing_stock": closing_stock,
            "gross_profit": gross, "net_profit": net}


def profit_loss(conn, company_id, date_from=None, date_to=None):
    company = get_company(conn, company_id)
    frm, to = period(company, date_from, date_to)
    p = _pl_compute(conn, company, frm, to)

    def side(nodes):
        return [dict(_to_rupees_tree(n), amount=_r(n["amount"])) for n in nodes]

    gp, np_ = p["gross_profit"], p["net_profit"]
    trading_total = (sum(n["amount"] for n in p["trading_cr"]) + p["closing_stock"]
                     + (-gp if gp < 0 else 0))
    pl_total = (sum(n["amount"] for n in p["pl_cr"]) + max(gp, 0) + (-np_ if np_ < 0 else 0))
    return {
        "from": frm, "to": to,
        "opening_stock": _r(p["opening_stock"]), "closing_stock": _r(p["closing_stock"]),
        "trading_dr": side(p["trading_dr"]), "trading_cr": side(p["trading_cr"]),
        "pl_dr": side(p["pl_dr"]), "pl_cr": side(p["pl_cr"]),
        "gross_profit": _r(gp), "net_profit": _r(np_),
        "trading_total": _r(trading_total), "pl_total": _r(pl_total),
    }


# ---------------------------------------------------------------- balance sheet

def _bs_compute(conn, company, to):
    chart = Chart(conn, company["id"])
    frm = company["books_from"]
    bal = ledger_balances(conn, company["id"], frm, to, chart)
    inv = bool(company["inventory_enabled"])
    closing_stock = stock_value(conn, company["id"], to) if inv else 0
    opening_stock = stock_value(conn, company["id"], None) if inv else 0
    extra = {}
    sih = chart.group_id("Stock-in-Hand")
    if closing_stock and sih:
        extra[sih] = [("Closing Stock", closing_stock)]
    liabilities, assets = [], []
    for gid in sorted(chart.roots, key=lambda g: chart.groups[g]["name"]):
        g = chart.groups[gid]
        if g["nature"] in ("Income", "Expenses"):
            continue
        node = _group_node(chart, gid, bal, extra)
        if not (node["closing"] or node["children"]):
            continue
        if g["nature"] == "Liabilities":
            node["amount"] = -node["closing"]
            liabilities.append(node)
        else:
            node["amount"] = node["closing"]
            assets.append(node)
    pl_led = next((lid for lid in chart.ledgers_by_group.get(None, [])), None)
    pl_opening = -bal[pl_led]["closing"] if pl_led else 0
    pl = _pl_compute(conn, company, frm, to, chart)
    diff = sum(b["closing"] for b in bal.values()) - sum(b["dr"] - b["cr"] for b in bal.values()) \
        + opening_stock  # Σ opening balances + opening stock
    return {"liabilities": liabilities, "assets": assets, "pl_opening": pl_opening,
            "net_profit": pl["net_profit"], "gross_profit": pl["gross_profit"],
            "closing_stock": closing_stock, "diff": diff, "pl": pl, "bal": bal, "chart": chart}


def balance_sheet(conn, company_id, date_to=None):
    company = get_company(conn, company_id)
    _, to = period(company, None, date_to)
    b = _bs_compute(conn, company, to)
    pl_total = b["pl_opening"] + b["net_profit"]
    liab_total = sum(n["amount"] for n in b["liabilities"]) + pl_total
    asset_total = sum(n["amount"] for n in b["assets"])
    diff = b["diff"]
    if diff > 0:
        liab_total += diff
    elif diff < 0:
        asset_total += -diff

    def side(nodes):
        return [dict(_to_rupees_tree(n), amount=_r(n["amount"])) for n in nodes]

    return {
        "as_of": to,
        "liabilities": side(b["liabilities"]),
        "assets": side(b["assets"]),
        "profit_loss": {"opening": _r(b["pl_opening"]), "current_period": _r(b["net_profit"]),
                        "total": _r(pl_total)},
        "difference_in_opening": _r(diff),
        "closing_stock": _r(b["closing_stock"]),
        "total_liabilities": _r(liab_total),
        "total_assets": _r(asset_total),
    }


# ---------------------------------------------------------------- books

def group_summary(conn, company_id, group_id, date_from=None, date_to=None):
    company = get_company(conn, company_id)
    frm, to = period(company, date_from, date_to)
    chart = Chart(conn, company_id)
    if int(group_id) not in chart.groups:
        raise NotFound("Group not found")
    bal = ledger_balances(conn, company_id, frm, to, chart)
    node = _group_node(chart, int(group_id), bal, include_zero=True)
    return {"from": frm, "to": to, "group": _to_rupees_tree(node)}


def ledger_vouchers(conn, company_id, ledger_id, date_from=None, date_to=None):
    company = get_company(conn, company_id)
    frm, to = period(company, date_from, date_to)
    chart = Chart(conn, company_id)
    ledger_id = int(ledger_id)
    if ledger_id not in chart.ledgers:
        raise NotFound("Ledger not found")
    bal = ledger_balances(conn, company_id, frm, to, chart)[ledger_id]
    rows = []
    running = bal["opening"]
    q = conn.execute(
        """SELECT e.id AS entry_id, e.amount, e.voucher_id, v.date, v.number, v.narration, t.name AS vtype,
                  t.base_type
           FROM voucher_entries e JOIN vouchers v ON v.id = e.voucher_id
           JOIN voucher_types t ON t.id = v.voucher_type_id
           WHERE e.ledger_id = ? AND v.company_id = ? AND v.date BETWEEN ? AND ?
                 AND t.base_type NOT IN ('Memorandum')
           ORDER BY v.date, v.id, e.line_no""", (ledger_id, company_id, frm, to)).fetchall()
    for r in q:
        others = conn.execute(
            "SELECT l.name, e.amount FROM voucher_entries e JOIN ledgers l ON l.id=e.ledger_id "
            "WHERE e.voucher_id=? AND e.ledger_id != ? ORDER BY e.line_no", (r["voucher_id"], ledger_id)).fetchall()
        opposite = [o["name"] for o in others if (o["amount"] > 0) != (r["amount"] > 0)] or [o["name"] for o in others]
        running += r["amount"]
        rows.append({
            "voucher_id": r["voucher_id"], "entry_id": r["entry_id"], "date": r["date"],
            "particulars": opposite[0] if len(opposite) == 1 else ("(as per details)" if opposite else "—"),
            "details": opposite, "voucher_type": r["vtype"], "number": r["number"], "narration": r["narration"],
            "debit": _r(r["amount"]) if r["amount"] > 0 else 0, "credit": _r(-r["amount"]) if r["amount"] < 0 else 0,
            "balance": _r(running),
        })
    led = chart.ledgers[ledger_id]
    return {"from": frm, "to": to, "ledger": {"id": ledger_id, "name": led["name"],
            "group": chart.groups[led["group_id"]]["name"] if led["group_id"] else "Primary"},
            "opening": _r(bal["opening"]), "total_debit": _r(bal["dr"]), "total_credit": _r(bal["cr"]),
            "closing": _r(bal["closing"]), "rows": rows}


def day_book(conn, company_id, date_from=None, date_to=None, voucher_type_id=None):
    company = get_company(conn, company_id)
    if not date_from and not date_to:
        date_from = date_to = conn.execute("SELECT MAX(date) FROM vouchers WHERE company_id=?",
                                           (company_id,)).fetchone()[0] or dt.date.today().isoformat()
    frm, to = period(company, date_from, date_to or date_from)
    sql = ("SELECT v.id, v.date, v.number, v.narration, t.name AS vtype, t.base_type, p.name AS party "
           "FROM vouchers v JOIN voucher_types t ON t.id=v.voucher_type_id "
           "LEFT JOIN ledgers p ON p.id=v.party_ledger_id WHERE v.company_id=? AND v.date BETWEEN ? AND ?")
    args = [company_id, frm, to]
    if voucher_type_id:
        sql += " AND v.voucher_type_id=?"
        args.append(int(voucher_type_id))
    rows = []
    for v in conn.execute(sql + " ORDER BY v.date, v.id", args).fetchall():
        first = conn.execute("SELECT l.name, e.amount FROM voucher_entries e JOIN ledgers l ON l.id=e.ledger_id "
                             "WHERE e.voucher_id=? ORDER BY e.line_no LIMIT 1", (v["id"],)).fetchone()
        total = conn.execute("SELECT COALESCE(SUM(amount),0) FROM voucher_entries WHERE voucher_id=? AND amount>0",
                             (v["id"],)).fetchone()[0]
        if not first:  # inventory-only voucher
            total = conn.execute("SELECT COALESCE(SUM(amount),0) FROM inventory_entries WHERE voucher_id=? AND qty>0",
                                 (v["id"],)).fetchone()[0]
        particulars = v["party"] or (first["name"] if first else "(stock transfer)")
        is_dr = True
        if first:
            party_amt = conn.execute(
                "SELECT e.amount FROM voucher_entries e JOIN ledgers l ON l.id=e.ledger_id WHERE e.voucher_id=? "
                "AND l.name=? LIMIT 1", (v["id"], particulars)).fetchone()
            is_dr = (party_amt["amount"] if party_amt else first["amount"]) > 0
        rows.append({"voucher_id": v["id"], "date": v["date"], "particulars": particulars,
                     "voucher_type": v["vtype"], "base_type": v["base_type"], "number": v["number"],
                     "narration": v["narration"], "debit": _r(total) if is_dr else 0,
                     "credit": 0 if is_dr else _r(total), "memorandum": v["base_type"] in NON_BOOK_TYPES})
    return {"from": frm, "to": to, "rows": rows}


def cash_bank_summary(conn, company_id, date_from=None, date_to=None):
    company = get_company(conn, company_id)
    frm, to = period(company, date_from, date_to)
    chart = Chart(conn, company_id)
    bal = ledger_balances(conn, company_id, frm, to, chart)
    groups = []
    for name in CASH_GROUPS:
        gid = chart.group_id(name)
        if gid:
            groups.append(_to_rupees_tree(_group_node(chart, gid, bal, include_zero=True)))
    return {"from": frm, "to": to, "groups": groups,
            "total": round(sum(g["closing"] for g in groups), 2)}


# ---------------------------------------------------------------- inventory reports

def stock_summary(conn, company_id, date_from=None, date_to=None):
    company = get_company(conn, company_id)
    frm, to = period(company, date_from, date_to)
    opening = stock_position(conn, company_id, None if frm <= company["books_from"] else _day_before(frm))
    closing = stock_position(conn, company_id, to)
    moves = defaultdict(lambda: {"in_qty": 0.0, "in_val": 0, "out_qty": 0.0, "out_sale": 0})
    for r in inventory_rows(conn, company_id, frm, to):
        if r["transfer"]:
            continue
        m = moves[r["item_id"]]
        if r["qty"] > 0:
            m["in_qty"] += r["qty"]
            m["in_val"] += r["amount"]
        else:
            m["out_qty"] += -r["qty"]
            m["out_sale"] += r["amount"]
    rows = []
    for iid, c in closing.items():
        it, o, m = c["item"], opening[iid], moves[iid]
        out_cost = o["value"] + m["in_val"] - c["value"]  # consumption at average cost
        rows.append({
            "item_id": iid, "name": it["name"], "group": it["group_name"], "unit": it["unit"],
            "opening_qty": o["qty"], "opening_value": _r(o["value"]),
            "inward_qty": m["in_qty"], "inward_value": _r(m["in_val"]),
            "outward_qty": m["out_qty"], "outward_value": _r(out_cost), "outward_sale_value": _r(m["out_sale"]),
            "closing_qty": c["qty"], "closing_rate": _r(round(c["rate"])), "closing_value": _r(c["value"]),
        })
    return {"from": frm, "to": to, "rows": rows,
            "total_opening": _r(sum(o["value"] for o in opening.values())),
            "total_closing": _r(sum(c["value"] for c in closing.values()))}


def stock_item_vouchers(conn, company_id, item_id, date_from=None, date_to=None):
    company = get_company(conn, company_id)
    frm, to = period(company, date_from, date_to)
    before = None if frm <= company["books_from"] else _day_before(frm)
    pos = stock_position(conn, company_id, before)
    if int(item_id) not in pos:
        raise NotFound("Stock item not found")
    op = pos[int(item_id)]
    running = op["qty"]
    rows = []
    for r in conn.execute(
            """SELECT ie.*, v.date, v.number, v.id AS vid, t.name AS vtype, g.name AS godown, p.name AS party
               FROM inventory_entries ie JOIN vouchers v ON v.id=ie.voucher_id
               JOIN voucher_types t ON t.id=v.voucher_type_id LEFT JOIN godowns g ON g.id=ie.godown_id
               LEFT JOIN ledgers p ON p.id=v.party_ledger_id
               WHERE ie.item_id=? AND v.company_id=? AND v.date BETWEEN ? AND ?
                     AND t.base_type NOT IN ('Memorandum') ORDER BY v.date, v.id""",
            (int(item_id), company_id, frm, to)):
        running += r["qty"]
        rows.append({"voucher_id": r["vid"], "date": r["date"], "voucher_type": r["vtype"], "number": r["number"],
                     "particulars": r["party"] or r["godown"], "godown": r["godown"],
                     "inward_qty": r["qty"] if r["qty"] > 0 else 0, "outward_qty": -r["qty"] if r["qty"] < 0 else 0,
                     "rate": _r(r["rate"]), "value": _r(r["amount"]), "closing_qty": round(running, 6)})
    close = stock_position(conn, company_id, to)[int(item_id)]
    return {"from": frm, "to": to, "item": {"id": op["item"]["id"], "name": op["item"]["name"],
                                             "unit": op["item"]["unit"]},
            "opening_qty": op["qty"], "opening_value": _r(op["value"]), "rows": rows,
            "closing_qty": close["qty"], "closing_value": _r(close["value"])}


def godown_summary(conn, company_id, date_to=None):
    company = get_company(conn, company_id)
    _, to = period(company, None, date_to)
    main = conn.execute("SELECT id FROM godowns WHERE company_id=? AND is_predefined=1", (company_id,)).fetchone()
    qty = defaultdict(float)
    for it in conn.execute("SELECT id, opening_qty FROM stock_items WHERE company_id=?", (company_id,)):
        if it["opening_qty"] and main:
            qty[(main["id"], it["id"])] += it["opening_qty"]
    for r in conn.execute(
            """SELECT ie.godown_id, ie.item_id, SUM(ie.qty) AS q FROM inventory_entries ie
               JOIN vouchers v ON v.id=ie.voucher_id JOIN voucher_types t ON t.id=v.voucher_type_id
               WHERE v.company_id=? AND v.date<=? AND t.base_type NOT IN ('Memorandum')
               GROUP BY ie.godown_id, ie.item_id""", (company_id, to)):
        qty[(r["godown_id"], r["item_id"])] += r["q"]
    names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM godowns WHERE company_id=?", (company_id,))}
    items = {r["id"]: r for r in conn.execute(
        "SELECT i.id, i.name, u.symbol FROM stock_items i JOIN units u ON u.id=i.unit_id WHERE i.company_id=?",
        (company_id,))}
    out = defaultdict(list)
    for (gid, iid), q in sorted(qty.items(), key=lambda kv: (names.get(kv[0][0], ""), items[kv[0][1]]["name"])):
        if round(q, 6):
            out[names.get(gid, "Main Location")].append({"item": items[iid]["name"], "unit": items[iid]["symbol"],
                                                         "qty": round(q, 6)})
    return {"as_of": to, "godowns": [{"name": k, "items": v} for k, v in out.items()]}


# ---------------------------------------------------------------- outstanding

def outstanding(conn, company_id, kind="receivable", date_to=None):
    company = get_company(conn, company_id)
    _, to = period(company, None, date_to)
    chart = Chart(conn, company_id)
    group = "Sundry Debtors" if kind == "receivable" else "Sundry Creditors"
    if kind not in ("receivable", "payable"):
        raise ValidationError("kind must be receivable or payable")
    bal = ledger_balances(conn, company_id, company["books_from"], to, chart)
    sign = 1 if kind == "receivable" else -1
    today = dt.date.fromisoformat(to)
    parties = []
    for lid, led in chart.ledgers.items():
        if not chart.ledger_is_under(lid, group):
            continue
        bills = []
        for b in conn.execute(
                """SELECT b.name, SUM(b.amount) AS pending, MIN(v.date) AS date,
                          MAX(b.due_date) AS due_date
                   FROM bill_allocations b JOIN voucher_entries e ON e.id=b.entry_id
                   JOIN vouchers v ON v.id=e.voucher_id
                   WHERE b.ledger_id=? AND b.name IS NOT NULL AND v.date<=? GROUP BY b.name
                   HAVING SUM(b.amount) != 0 ORDER BY MIN(v.date)""", (lid, to)):
            due = b["due_date"] or b["date"]
            overdue = (today - dt.date.fromisoformat(due)).days
            bills.append({"name": b["name"], "date": b["date"], "due_date": b["due_date"],
                          "pending": _r(sign * b["pending"]), "overdue_days": max(overdue, 0)})
        closing = bal[lid]["closing"]
        billed = sum(round(x["pending"] * 100) for x in bills) * sign
        on_account = closing - billed
        if closing or bills:
            parties.append({"ledger_id": lid, "name": led["name"], "closing": _r(sign * closing),
                            "bills": bills, "on_account": _r(sign * on_account)})
    parties.sort(key=lambda p: p["name"].lower())
    return {"kind": kind, "as_of": to, "parties": parties,
            "total": round(sum(p["closing"] for p in parties), 2)}


# ---------------------------------------------------------------- GST

def gst_report(conn, company_id, date_from=None, date_to=None):
    company = get_company(conn, company_id)
    frm, to = period(company, date_from, date_to)
    chart = Chart(conn, company_id)
    tax_ledgers = {lid: led["gst_type"] for lid, led in chart.ledgers.items() if led["gst_type"]}
    outward, inward = [], []
    heads = ("CGST", "SGST", "IGST", "CESS")
    totals = {"outward": defaultdict(int), "inward": defaultdict(int)}
    hsn = defaultdict(lambda: {"qty": 0.0, "taxable": 0, "description": ""})
    for v in conn.execute(
            """SELECT v.id, v.date, v.number, t.base_type, t.name AS vtype, p.name AS party, p.gstin, p.state
               FROM vouchers v JOIN voucher_types t ON t.id=v.voucher_type_id
               LEFT JOIN ledgers p ON p.id=v.party_ledger_id
               WHERE v.company_id=? AND v.date BETWEEN ? AND ?
                     AND t.base_type IN ('Sales','Credit Note','Purchase','Debit Note')
               ORDER BY v.date, v.id""", (company_id, frm, to)).fetchall():
        entries = conn.execute("SELECT ledger_id, amount FROM voucher_entries WHERE voucher_id=?", (v["id"],)).fetchall()
        tax = defaultdict(int)
        taxable = 0
        for e in entries:
            if e["ledger_id"] in tax_ledgers:
                tax[tax_ledgers[e["ledger_id"]]] += abs(e["amount"])
            elif chart.ledger_is_under(e["ledger_id"], "Sales Accounts") or \
                    chart.ledger_is_under(e["ledger_id"], "Purchase Accounts"):
                taxable += abs(e["amount"])
        is_out = v["base_type"] in ("Sales", "Credit Note")
        reverse = v["base_type"] in ("Credit Note", "Debit Note")  # returns reduce the side
        row = {"voucher_id": v["id"], "date": v["date"], "number": v["number"], "voucher_type": v["vtype"],
               "party": v["party"], "gstin": v["gstin"], "category": "B2B" if v["gstin"] else "B2C",
               "taxable": _r(taxable), **{h.lower(): _r(tax[h]) for h in heads},
               "total": _r(taxable + sum(tax.values())), "is_return": reverse}
        (outward if is_out else inward).append(row)
        bucket = totals["outward" if is_out else "inward"]
        s = -1 if reverse else 1
        bucket["taxable"] += s * taxable
        for h in heads:
            bucket[h] += s * tax[h]
        if v["base_type"] == "Sales":
            for r in conn.execute("SELECT i.hsn, i.name, ie.qty, ie.amount FROM inventory_entries ie "
                                  "JOIN stock_items i ON i.id=ie.item_id WHERE ie.voucher_id=?", (v["id"],)):
                key = r["hsn"] or "(no HSN)"
                hsn[key]["qty"] += abs(r["qty"])
                hsn[key]["taxable"] += r["amount"]
                hsn[key]["description"] = hsn[key]["description"] or r["name"]

    def fmt(b):
        return {"taxable": _r(b["taxable"]), **{h.lower(): _r(b[h]) for h in heads},
                "total_tax": _r(sum(b[h] for h in heads))}

    out_t, in_t = totals["outward"], totals["inward"]
    net = {h.lower(): _r(out_t[h] - in_t[h]) for h in heads}
    net["total"] = _r(sum(out_t[h] - in_t[h] for h in heads))
    return {"from": frm, "to": to, "outward": outward, "inward": inward,
            "summary": {"outward": fmt(out_t), "inward": fmt(in_t), "net_payable": net},
            "hsn_summary": [{"hsn": k, "description": v["description"], "qty": v["qty"], "taxable": _r(v["taxable"])}
                            for k, v in sorted(hsn.items())]}


# ---------------------------------------------------------------- banking

def bank_reconciliation(conn, company_id, ledger_id, date_to=None):
    company = get_company(conn, company_id)
    _, to = period(company, None, date_to)
    chart = Chart(conn, company_id)
    ledger_id = int(ledger_id)
    if ledger_id not in chart.ledgers:
        raise NotFound("Ledger not found")
    if not (chart.ledger_is_under(ledger_id, "Bank Accounts") or chart.ledger_is_under(ledger_id, "Bank OD A/c")):
        raise ValidationError("Select a ledger under Bank Accounts or Bank OD A/c")
    bal = ledger_balances(conn, company_id, company["books_from"], to, chart)[ledger_id]
    rows, unreconciled = [], 0
    for r in conn.execute(
            """SELECT e.id, e.amount, e.instrument_no, e.instrument_date, e.bank_date, v.id AS vid, v.date,
                      v.number, t.name AS vtype
               FROM voucher_entries e JOIN vouchers v ON v.id=e.voucher_id
               JOIN voucher_types t ON t.id=v.voucher_type_id
               WHERE e.ledger_id=? AND v.date<=? AND t.base_type NOT IN ('Memorandum')
               ORDER BY v.date, v.id""", (ledger_id, to)):
        others = conn.execute("SELECT l.name FROM voucher_entries e JOIN ledgers l ON l.id=e.ledger_id "
                              "WHERE e.voucher_id=? AND e.ledger_id != ? ORDER BY e.line_no LIMIT 1",
                              (r["vid"], ledger_id)).fetchone()
        cleared = bool(r["bank_date"]) and r["bank_date"] <= to
        if not cleared:
            unreconciled += r["amount"]
        rows.append({"entry_id": r["id"], "voucher_id": r["vid"], "date": r["date"], "voucher_type": r["vtype"],
                     "number": r["number"], "particulars": others["name"] if others else "",
                     "instrument_no": r["instrument_no"], "instrument_date": r["instrument_date"],
                     "bank_date": r["bank_date"], "debit": _r(r["amount"]) if r["amount"] > 0 else 0,
                     "credit": _r(-r["amount"]) if r["amount"] < 0 else 0, "reconciled": cleared})
    not_in_bank_dr = sum(round(x["debit"] * 100) for x in rows if not x["reconciled"])
    not_in_bank_cr = sum(round(x["credit"] * 100) for x in rows if not x["reconciled"])
    return {"as_of": to, "ledger": {"id": ledger_id, "name": chart.ledgers[ledger_id]["name"]},
            "balance_as_per_books": _r(bal["closing"]),
            "amounts_not_reflected_in_bank": {"debit": _r(not_in_bank_dr), "credit": _r(not_in_bank_cr)},
            "balance_as_per_bank": _r(bal["closing"] - unreconciled), "rows": rows}


# ---------------------------------------------------------------- ratios & dashboard

def _group_closing(b, name):
    chart, bal = b["chart"], b["bal"]
    gid = chart.group_id(name)
    if not gid:
        return 0
    ids = set(chart.descendants(gid))
    return sum(bal[lid]["closing"] for lid, led in chart.ledgers.items() if led["group_id"] in ids)


def ratios(conn, company_id, date_from=None, date_to=None):
    company = get_company(conn, company_id)
    frm, to = period(company, date_from, date_to)
    b = _bs_compute(conn, company, to)
    pl = _pl_compute(conn, company, frm, to)
    ca = _group_closing(b, "Current Assets") + b["closing_stock"]
    cl = -_group_closing(b, "Current Liabilities")
    stock = b["closing_stock"]
    loans = -_group_closing(b, "Loans (Liability)")
    capital = -_group_closing(b, "Capital Account") + b["pl_opening"] + b["net_profit"]
    sales = sum(n["amount"] for n in pl["trading_cr"] if n["name"] == "Sales Accounts")
    purchases = sum(n["amount"] for n in pl["trading_dr"] if n["name"] == "Purchase Accounts")
    debtors = _group_closing(b, "Sundry Debtors")
    creditors = -_group_closing(b, "Sundry Creditors")
    cash = _group_closing(b, "Cash-in-Hand")
    bank = _group_closing(b, "Bank Accounts") + _group_closing(b, "Bank OD A/c")
    days = (dt.date.fromisoformat(to) - dt.date.fromisoformat(frm)).days + 1

    def ratio(a, c):
        return round(a / c, 2) if c else None

    def pct(a, c):
        return round(a * 100 / c, 2) if c else None

    return {
        "from": frm, "to": to,
        "principal_groups": {
            "working_capital": _r(ca - cl), "cash_in_hand": _r(cash), "bank_accounts": _r(bank),
            "sundry_debtors": _r(debtors), "sundry_creditors": _r(creditors), "sales_accounts": _r(sales),
            "purchase_accounts": _r(purchases), "stock_in_hand": _r(stock), "net_profit": _r(pl["net_profit"]),
            "wkg_capital_turnover": ratio(sales, ca - cl), "inventory_turnover": ratio(sales, stock),
        },
        "principal_ratios": {
            "current_ratio": ratio(ca, cl), "quick_ratio": ratio(ca - stock, cl),
            "debt_equity_ratio": ratio(loans, capital),
            "gross_profit_pct": pct(pl["gross_profit"], sales), "net_profit_pct": pct(pl["net_profit"], sales),
            "operating_cost_pct": pct(sales - pl["net_profit"], sales),
            "recv_turnover_days": round(debtors * days / sales, 1) if sales else None,
            "return_on_investment_pct": pct(pl["net_profit"], capital),
        },
    }


def dashboard(conn, company_id, date_from=None, date_to=None):
    company = get_company(conn, company_id)
    frm, to = period(company, date_from, date_to)
    b = _bs_compute(conn, company, to)
    pl = _pl_compute(conn, company, frm, to)
    sales = sum(n["amount"] for n in pl["trading_cr"] if n["name"] == "Sales Accounts")
    purchases = sum(n["amount"] for n in pl["trading_dr"] if n["name"] == "Purchase Accounts")
    counts = conn.execute("SELECT COUNT(*) FROM vouchers WHERE company_id=? AND date BETWEEN ? AND ?",
                          (company_id, frm, to)).fetchone()[0]
    last = conn.execute("SELECT MAX(date) FROM vouchers WHERE company_id=?", (company_id,)).fetchone()[0]
    return {
        "from": frm, "to": to, "last_voucher_date": last, "voucher_count": counts,
        "cash": _r(_group_closing(b, "Cash-in-Hand")),
        "bank": _r(_group_closing(b, "Bank Accounts") + _group_closing(b, "Bank OD A/c")),
        "receivables": _r(_group_closing(b, "Sundry Debtors")),
        "payables": _r(-_group_closing(b, "Sundry Creditors")),
        "sales": _r(sales), "purchases": _r(purchases),
        "gross_profit": _r(pl["gross_profit"]), "net_profit": _r(pl["net_profit"]),
        "closing_stock": _r(b["closing_stock"]),
        "duties_taxes": _r(-_group_closing(b, "Duties & Taxes")),
    }


REPORTS = {
    "trial-balance": lambda c, cid, q: trial_balance(c, cid, q.get("from"), q.get("to")),
    "profit-loss": lambda c, cid, q: profit_loss(c, cid, q.get("from"), q.get("to")),
    "balance-sheet": lambda c, cid, q: balance_sheet(c, cid, q.get("to")),
    "day-book": lambda c, cid, q: day_book(c, cid, q.get("from"), q.get("to"), q.get("voucher_type_id")),
    "ledger": lambda c, cid, q: ledger_vouchers(c, cid, _need(q, "ledger_id"), q.get("from"), q.get("to")),
    "group-summary": lambda c, cid, q: group_summary(c, cid, _need(q, "group_id"), q.get("from"), q.get("to")),
    "cash-bank": lambda c, cid, q: cash_bank_summary(c, cid, q.get("from"), q.get("to")),
    "stock-summary": lambda c, cid, q: stock_summary(c, cid, q.get("from"), q.get("to")),
    "stock-item": lambda c, cid, q: stock_item_vouchers(c, cid, _need(q, "item_id"), q.get("from"), q.get("to")),
    "godown-summary": lambda c, cid, q: godown_summary(c, cid, q.get("to")),
    "outstanding": lambda c, cid, q: outstanding(c, cid, q.get("kind", "receivable"), q.get("to")),
    "gst": lambda c, cid, q: gst_report(c, cid, q.get("from"), q.get("to")),
    "bank-reconciliation": lambda c, cid, q: bank_reconciliation(c, cid, _need(q, "ledger_id"), q.get("to")),
    "ratios": lambda c, cid, q: ratios(c, cid, q.get("from"), q.get("to")),
    "dashboard": lambda c, cid, q: dashboard(c, cid, q.get("from"), q.get("to")),
}


def _need(q, key):
    if not q.get(key):
        raise ValidationError(f"Query parameter '{key}' is required")
    return q[key]


def run_report(conn, company_id, name, query):
    fn = REPORTS.get(name)
    if not fn:
        raise NotFound(f"Unknown report '{name}'")
    return fn(conn, company_id, query)


__all__ = ["run_report", "REPORTS", "PL_LEDGER"]
