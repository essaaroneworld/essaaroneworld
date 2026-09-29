"""Masters: companies, account groups, ledgers, voucher types and inventory masters."""
import datetime as dt

from . import audit
from .chart import Chart
from .errors import Conflict, NotFound, ValidationError
from .money import to_paise, to_qty, to_rupees
from .seed import BASE_TYPES, PL_LEDGER, seed_company

GST_TYPES = ("CGST", "SGST", "IGST", "CESS")


# ---------------------------------------------------------------- helpers

def _clean(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _require_name(data, key="name"):
    name = _clean(data.get(key))
    if not name:
        raise ValidationError(f"{key.replace('_', ' ').capitalize()} is required")
    if len(name) > 200:
        raise ValidationError("Name is too long")
    return name


def parse_date(value, field="date"):
    if not value:
        raise ValidationError(f"{field} is required")
    try:
        return dt.date.fromisoformat(str(value)[:10]).isoformat()
    except ValueError:
        raise ValidationError(f"Invalid {field}: {value!r} (expected YYYY-MM-DD)")


def _row(conn, table, company_id, row_id, label):
    r = conn.execute(
        f"SELECT * FROM {table} WHERE id = ? AND company_id = ?", (row_id, company_id)
    ).fetchone()
    if not r:
        raise NotFound(f"{label} not found")
    return r


def _account_name_taken(conn, company_id, name, exclude_table=None, exclude_id=None):
    """Tally requires names to be unique across groups and ledgers."""
    for table in ("groups", "ledgers"):
        sql = f"SELECT id FROM {table} WHERE company_id = ? AND name = ? COLLATE NOCASE"
        args = [company_id, name]
        if table == exclude_table:
            sql += " AND id != ?"
            args.append(exclude_id)
        if conn.execute(sql, args).fetchone():
            return True
    return False


def _side_amount(amount, side, default_side):
    paise = to_paise(amount)
    side = (side or default_side or "Dr").capitalize()
    if side not in ("Dr", "Cr"):
        raise ValidationError("Side must be Dr or Cr")
    if paise < 0:
        paise, side = -paise, ("Cr" if side == "Dr" else "Dr")
    return paise if side == "Dr" else -paise


# ---------------------------------------------------------------- companies

def company_out(r):
    d = dict(r)
    d["inventory_enabled"] = bool(d["inventory_enabled"])
    d["gst_enabled"] = bool(d["gst_enabled"])
    return d


def list_companies(conn):
    return [company_out(r) for r in conn.execute("SELECT * FROM companies ORDER BY name")]


def get_company(conn, company_id):
    r = conn.execute("SELECT * FROM companies WHERE id = ?", (company_id,)).fetchone()
    if not r:
        raise NotFound("Company not found")
    return company_out(r)


def _company_fields(data, existing=None):
    existing = existing or {}
    out = {}
    for key in ("mailing_name", "address", "state", "country", "pincode", "phone",
                "email", "gstin", "pan", "currency_symbol"):
        if key in data:
            out[key] = _clean(data.get(key))
    if "gstin" in out and out["gstin"]:
        out["gstin"] = out["gstin"].upper()
        if len(out["gstin"]) != 15:
            raise ValidationError("GSTIN must be 15 characters")
    for key in ("inventory_enabled", "gst_enabled"):
        if key in data:
            out[key] = 1 if data.get(key) else 0
    if "fy_start" in data or not existing:
        fy = data.get("fy_start")
        if not fy:
            today = dt.date.today()
            year = today.year if today.month >= 4 else today.year - 1
            fy = f"{year}-04-01"  # Indian financial year: 1 April – 31 March
        out["fy_start"] = parse_date(fy, "Financial year start")
    if "books_from" in data or not existing:
        out["books_from"] = parse_date(data.get("books_from") or out.get("fy_start")
                                       or existing.get("fy_start"), "Books beginning from")
    fy_start = out.get("fy_start", existing.get("fy_start"))
    books_from = out.get("books_from", existing.get("books_from"))
    if books_from < fy_start:
        raise ValidationError("Books beginning date cannot be before the financial year start")
    return out


def create_company(conn, data):
    name = _require_name(data)
    if conn.execute("SELECT 1 FROM companies WHERE name = ? COLLATE NOCASE", (name,)).fetchone():
        raise Conflict(f"Company '{name}' already exists")
    fields = _company_fields(data)
    fields.setdefault("mailing_name", name)
    fields.setdefault("country", "India")
    fields.setdefault("currency_symbol", "₹")
    fields.setdefault("inventory_enabled", 1)
    fields.setdefault("gst_enabled", 1)
    cols = ["name"] + list(fields)
    cur = conn.execute(
        f"INSERT INTO companies({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
        [name] + list(fields.values()),
    )
    cid = cur.lastrowid
    seed_company(conn, cid, gst_enabled=bool(fields["gst_enabled"]))
    audit.log(conn, cid, "create", "company", cid, f"Created company {name}", fields)
    return get_company(conn, cid)


def update_company(conn, company_id, data):
    existing = get_company(conn, company_id)
    fields = _company_fields(data, existing)
    if "name" in data:
        name = _require_name(data)
        clash = conn.execute("SELECT 1 FROM companies WHERE name = ? COLLATE NOCASE AND id != ?",
                             (name, company_id)).fetchone()
        if clash:
            raise Conflict(f"Company '{name}' already exists")
        fields["name"] = name
    if "books_from" in fields:
        first = conn.execute("SELECT MIN(date) FROM vouchers WHERE company_id = ?",
                             (company_id,)).fetchone()[0]
        if first and first < fields["books_from"]:
            raise ValidationError("Vouchers exist before the new books beginning date")
    if fields:
        conn.execute(
            f"UPDATE companies SET {', '.join(k + ' = ?' for k in fields)} WHERE id = ?",
            list(fields.values()) + [company_id],
        )
    audit.log(conn, company_id, "alter", "company", company_id, "Altered company", fields)
    return get_company(conn, company_id)


def delete_company(conn, company_id):
    c = get_company(conn, company_id)
    # Explicit cleanup keeps FK ordering deterministic.
    conn.execute("DELETE FROM vouchers WHERE company_id = ?", (company_id,))
    for table in ("stock_items", "ledgers", "voucher_types", "units", "godowns"):
        conn.execute(f"DELETE FROM {table} WHERE company_id = ?", (company_id,))
    for table in ("groups", "stock_groups", "godowns"):
        # children first
        while conn.execute(f"SELECT 1 FROM {table} WHERE company_id = ?", (company_id,)).fetchone():
            conn.execute(
                f"DELETE FROM {table} WHERE company_id = ? AND id NOT IN "
                f"(SELECT parent_id FROM {table} WHERE parent_id IS NOT NULL)",
                (company_id,),
            )
    conn.execute("DELETE FROM companies WHERE id = ?", (company_id,))
    conn.execute("DELETE FROM edit_log WHERE company_id = ?", (company_id,))
    return {"deleted": c["name"]}


# ---------------------------------------------------------------- groups

def group_out(r, chart=None):
    d = dict(r)
    d["affects_gross_profit"] = bool(d["affects_gross_profit"])
    d["is_predefined"] = bool(d["is_predefined"])
    if chart is not None:
        d["parent_name"] = chart.groups[d["parent_id"]]["name"] if d["parent_id"] else "Primary"
    return d


def list_groups(conn, company_id):
    chart = Chart(conn, company_id)
    return [group_out(g, chart) for g in sorted(chart.groups.values(), key=lambda g: g["name"].lower())]


def get_group(conn, company_id, group_id):
    r = _row(conn, "groups", company_id, group_id, "Group")
    return group_out(r, Chart(conn, company_id))


def _group_fields(conn, company_id, data, group_id=None):
    name = _require_name(data)
    if _account_name_taken(conn, company_id, name, "groups", group_id):
        raise Conflict(f"An account named '{name}' already exists")
    parent_id = data.get("parent_id")
    if parent_id in ("", 0):
        parent_id = None
    if parent_id is not None:
        parent = _row(conn, "groups", company_id, int(parent_id), "Parent group")
        if group_id is not None:
            chart = Chart(conn, company_id)
            if int(parent_id) in chart.descendants(group_id):
                raise ValidationError("A group cannot be placed under itself or its sub-group")
        nature = parent["nature"]
        gp = parent["affects_gross_profit"]
    else:
        nature = data.get("nature")
        if nature not in ("Assets", "Liabilities", "Income", "Expenses"):
            raise ValidationError("Nature of group is required for a primary group "
                                  "(Assets, Liabilities, Income or Expenses)")
        gp = 1 if (data.get("affects_gross_profit") and nature in ("Income", "Expenses")) else 0
    return {"name": name, "parent_id": int(parent_id) if parent_id else None,
            "nature": nature, "affects_gross_profit": gp}


def create_group(conn, company_id, data):
    get_company(conn, company_id)
    f = _group_fields(conn, company_id, data)
    cur = conn.execute(
        "INSERT INTO groups(company_id, name, parent_id, nature, affects_gross_profit) VALUES (?,?,?,?,?)",
        (company_id, f["name"], f["parent_id"], f["nature"], f["affects_gross_profit"]),
    )
    audit.log(conn, company_id, "create", "group", cur.lastrowid, f"Created group {f['name']}", f)
    return get_group(conn, company_id, cur.lastrowid)


def update_group(conn, company_id, group_id, data):
    existing = _row(conn, "groups", company_id, group_id, "Group")
    if existing["is_predefined"]:
        # Predefined groups may be renamed only; their position is fixed.
        name = _require_name(data)
        if _account_name_taken(conn, company_id, name, "groups", group_id):
            raise Conflict(f"An account named '{name}' already exists")
        conn.execute("UPDATE groups SET name = ? WHERE id = ?", (name, group_id))
        f = {"name": name}
    else:
        f = _group_fields(conn, company_id, data, group_id)
        conn.execute(
            "UPDATE groups SET name=?, parent_id=?, nature=?, affects_gross_profit=? WHERE id=?",
            (f["name"], f["parent_id"], f["nature"], f["affects_gross_profit"], group_id),
        )
        # Propagate nature to sub-groups.
        chart = Chart(conn, company_id)
        for gid in chart.descendants(group_id)[1:]:
            conn.execute("UPDATE groups SET nature=?, affects_gross_profit=? WHERE id=?",
                         (f["nature"], f["affects_gross_profit"], gid))
    audit.log(conn, company_id, "alter", "group", group_id, f"Altered group {f['name']}", f)
    return get_group(conn, company_id, group_id)


def delete_group(conn, company_id, group_id):
    g = _row(conn, "groups", company_id, group_id, "Group")
    if g["is_predefined"]:
        raise ValidationError("Predefined groups cannot be deleted")
    if conn.execute("SELECT 1 FROM groups WHERE parent_id = ?", (group_id,)).fetchone():
        raise ValidationError("Group has sub-groups; delete or move them first")
    if conn.execute("SELECT 1 FROM ledgers WHERE group_id = ?", (group_id,)).fetchone():
        raise ValidationError("Group has ledgers; delete or move them first")
    conn.execute("DELETE FROM groups WHERE id = ?", (group_id,))
    audit.log(conn, company_id, "delete", "group", group_id, f"Deleted group {g['name']}", dict(g))
    return {"deleted": g["name"]}


# ---------------------------------------------------------------- ledgers

def ledger_out(r, chart=None):
    d = dict(r)
    ob = d["opening_balance"]
    d["opening_balance"] = to_rupees(abs(ob))
    d["opening_side"] = "Cr" if ob < 0 else "Dr"
    d["bill_wise"] = bool(d["bill_wise"])
    d["is_predefined"] = bool(d["is_predefined"])
    if chart is not None:
        d["group_name"] = chart.groups[d["group_id"]]["name"] if d["group_id"] else "Primary"
        d["is_cash_bank"] = chart.is_cash_or_bank(d["id"])
    return d


def list_ledgers(conn, company_id, group_id=None):
    chart = Chart(conn, company_id)
    rows = chart.ledgers.values()
    if group_id:
        allowed = set(chart.descendants(int(group_id)))
        rows = [r for r in rows if r["group_id"] in allowed]
    return [ledger_out(r, chart) for r in sorted(rows, key=lambda r: r["name"].lower())]


def get_ledger(conn, company_id, ledger_id):
    r = _row(conn, "ledgers", company_id, ledger_id, "Ledger")
    return ledger_out(r, Chart(conn, company_id))


def find_ledger(conn, company_id, name):
    r = conn.execute("SELECT id FROM ledgers WHERE company_id=? AND name=? COLLATE NOCASE",
                     (company_id, name)).fetchone()
    return r["id"] if r else None


def _ledger_fields(conn, company_id, data, ledger_id=None, predefined=False):
    name = _require_name(data)
    if _account_name_taken(conn, company_id, name, "ledgers", ledger_id):
        raise Conflict(f"An account named '{name}' already exists")
    chart = Chart(conn, company_id)
    group_id = data.get("group_id")
    if predefined and ledger_id and chart.ledgers[ledger_id]["group_id"] is None:
        group_id = None  # Profit & Loss A/c stays under Primary
    else:
        if not group_id:
            raise ValidationError("Group ('Under') is required for a ledger")
        group_id = int(group_id)
        if group_id not in chart.groups:
            raise NotFound("Group not found")
    nature = chart.groups[group_id]["nature"] if group_id else "Liabilities"
    default_side = "Dr" if nature in ("Assets", "Expenses") else "Cr"
    opening = _side_amount(data.get("opening_balance"), data.get("opening_side"), default_side)
    gst_type = _clean(data.get("gst_type"))
    if gst_type:
        gst_type = gst_type.upper()
        if gst_type not in GST_TYPES:
            raise ValidationError("GST type must be CGST, SGST, IGST or CESS")
        if not chart.is_under(group_id, "Duties & Taxes"):
            raise ValidationError("GST tax ledgers must be under Duties & Taxes")
    party = group_id and (chart.is_under(group_id, "Sundry Debtors")
                          or chart.is_under(group_id, "Sundry Creditors"))
    bill_wise = data.get("bill_wise")
    if bill_wise is None:
        bill_wise = bool(party)
    gstin = _clean(data.get("gstin"))
    if gstin:
        gstin = gstin.upper()
        if len(gstin) != 15:
            raise ValidationError("GSTIN must be 15 characters")
    credit_days = data.get("credit_days")
    credit_days = int(credit_days) if credit_days not in (None, "") else None
    return {
        "name": name, "group_id": group_id, "opening_balance": opening,
        "bill_wise": 1 if bill_wise else 0, "credit_days": credit_days, "gst_type": gst_type,
        "gstin": gstin, "state": _clean(data.get("state")), "address": _clean(data.get("address")),
        "pan": _clean(data.get("pan")), "bank_name": _clean(data.get("bank_name")),
        "account_no": _clean(data.get("account_no")), "ifsc": _clean(data.get("ifsc")),
    }


def create_ledger(conn, company_id, data):
    get_company(conn, company_id)
    f = _ledger_fields(conn, company_id, data)
    cols = list(f)
    cur = conn.execute(
        f"INSERT INTO ledgers(company_id, {','.join(cols)}) VALUES (?, {','.join('?' * len(cols))})",
        [company_id] + list(f.values()),
    )
    audit.log(conn, company_id, "create", "ledger", cur.lastrowid, f"Created ledger {f['name']}", f)
    return get_ledger(conn, company_id, cur.lastrowid)


def update_ledger(conn, company_id, ledger_id, data):
    existing = _row(conn, "ledgers", company_id, ledger_id, "Ledger")
    if existing["is_predefined"] and existing["name"] == PL_LEDGER and data.get("name") != PL_LEDGER:
        raise ValidationError("Profit & Loss A/c cannot be renamed")
    f = _ledger_fields(conn, company_id, data, ledger_id, bool(existing["is_predefined"]))
    conn.execute(f"UPDATE ledgers SET {', '.join(k + ' = ?' for k in f)} WHERE id = ?",
                 list(f.values()) + [ledger_id])
    audit.log(conn, company_id, "alter", "ledger", ledger_id, f"Altered ledger {f['name']}",
              {"before": ledger_out(existing), "after": f})
    return get_ledger(conn, company_id, ledger_id)


def delete_ledger(conn, company_id, ledger_id):
    led = _row(conn, "ledgers", company_id, ledger_id, "Ledger")
    if led["is_predefined"]:
        raise ValidationError("Predefined ledgers cannot be deleted")
    if conn.execute("SELECT 1 FROM voucher_entries WHERE ledger_id = ? LIMIT 1", (ledger_id,)).fetchone():
        raise ValidationError("Ledger has vouchers and cannot be deleted")
    if conn.execute("SELECT 1 FROM vouchers WHERE party_ledger_id = ? LIMIT 1", (ledger_id,)).fetchone():
        raise ValidationError("Ledger is used as a party in vouchers")
    conn.execute("DELETE FROM ledgers WHERE id = ?", (ledger_id,))
    audit.log(conn, company_id, "delete", "ledger", ledger_id, f"Deleted ledger {led['name']}",
              ledger_out(led))
    return {"deleted": led["name"]}


# ---------------------------------------------------------------- voucher types

def list_voucher_types(conn, company_id):
    return [dict(r) | {"is_predefined": bool(r["is_predefined"])} for r in conn.execute(
        "SELECT * FROM voucher_types WHERE company_id = ? ORDER BY id", (company_id,))]


def get_voucher_type(conn, company_id, vt_id):
    r = _row(conn, "voucher_types", company_id, vt_id, "Voucher type")
    return dict(r) | {"is_predefined": bool(r["is_predefined"])}


def create_voucher_type(conn, company_id, data):
    get_company(conn, company_id)
    name = _require_name(data)
    base = data.get("base_type")
    if base not in BASE_TYPES:
        raise ValidationError(f"Type of voucher must be one of: {', '.join(BASE_TYPES)}")
    if conn.execute("SELECT 1 FROM voucher_types WHERE company_id=? AND name=? COLLATE NOCASE",
                    (company_id, name)).fetchone():
        raise Conflict(f"Voucher type '{name}' already exists")
    cur = conn.execute(
        "INSERT INTO voucher_types(company_id, name, base_type, abbreviation) VALUES (?,?,?,?)",
        (company_id, name, base, _clean(data.get("abbreviation")) or name[:4]),
    )
    audit.log(conn, company_id, "create", "voucher_type", cur.lastrowid, f"Created voucher type {name}")
    return get_voucher_type(conn, company_id, cur.lastrowid)


def update_voucher_type(conn, company_id, vt_id, data):
    vt = _row(conn, "voucher_types", company_id, vt_id, "Voucher type")
    name = _require_name(data)
    if conn.execute("SELECT 1 FROM voucher_types WHERE company_id=? AND name=? COLLATE NOCASE AND id!=?",
                    (company_id, name, vt_id)).fetchone():
        raise Conflict(f"Voucher type '{name}' already exists")
    conn.execute("UPDATE voucher_types SET name=?, abbreviation=? WHERE id=?",
                 (name, _clean(data.get("abbreviation")) or vt["abbreviation"], vt_id))
    audit.log(conn, company_id, "alter", "voucher_type", vt_id, f"Altered voucher type {name}")
    return get_voucher_type(conn, company_id, vt_id)


def delete_voucher_type(conn, company_id, vt_id):
    vt = _row(conn, "voucher_types", company_id, vt_id, "Voucher type")
    if vt["is_predefined"]:
        raise ValidationError("Predefined voucher types cannot be deleted")
    if conn.execute("SELECT 1 FROM vouchers WHERE voucher_type_id=? LIMIT 1", (vt_id,)).fetchone():
        raise ValidationError("Voucher type is in use")
    conn.execute("DELETE FROM voucher_types WHERE id=?", (vt_id,))
    audit.log(conn, company_id, "delete", "voucher_type", vt_id, f"Deleted voucher type {vt['name']}")
    return {"deleted": vt["name"]}


# ---------------------------------------------------------------- inventory masters

def _simple_unique(conn, table, company_id, col, value, exclude_id=None):
    sql = f"SELECT 1 FROM {table} WHERE company_id=? AND {col}=? COLLATE NOCASE"
    args = [company_id, value]
    if exclude_id:
        sql += " AND id != ?"
        args.append(exclude_id)
    if conn.execute(sql, args).fetchone():
        raise Conflict(f"'{value}' already exists")


def _tree_parent(conn, table, company_id, data, row_id=None):
    pid = data.get("parent_id") or None
    if pid is None:
        return None
    pid = int(pid)
    _row(conn, table, company_id, pid, "Parent")
    if row_id is not None:
        cur = pid
        while cur is not None:
            if cur == row_id:
                raise ValidationError("Cannot place a master under itself")
            cur = conn.execute(f"SELECT parent_id FROM {table} WHERE id=?", (cur,)).fetchone()[0]
    return pid


def list_stock_groups(conn, company_id):
    rows = conn.execute(
        "SELECT s.*, COALESCE(p.name, 'Primary') AS parent_name FROM stock_groups s "
        "LEFT JOIN stock_groups p ON p.id = s.parent_id WHERE s.company_id=? ORDER BY s.name",
        (company_id,))
    return [dict(r) for r in rows]


def save_stock_group(conn, company_id, data, row_id=None):
    get_company(conn, company_id)
    if row_id:
        _row(conn, "stock_groups", company_id, row_id, "Stock group")
    name = _require_name(data)
    _simple_unique(conn, "stock_groups", company_id, "name", name, row_id)
    pid = _tree_parent(conn, "stock_groups", company_id, data, row_id)
    if row_id:
        conn.execute("UPDATE stock_groups SET name=?, parent_id=? WHERE id=?", (name, pid, row_id))
        audit.log(conn, company_id, "alter", "stock_group", row_id, f"Altered stock group {name}")
    else:
        row_id = conn.execute("INSERT INTO stock_groups(company_id, name, parent_id) VALUES (?,?,?)",
                              (company_id, name, pid)).lastrowid
        audit.log(conn, company_id, "create", "stock_group", row_id, f"Created stock group {name}")
    return dict(_row(conn, "stock_groups", company_id, row_id, "Stock group"))


def delete_stock_group(conn, company_id, row_id):
    g = _row(conn, "stock_groups", company_id, row_id, "Stock group")
    if conn.execute("SELECT 1 FROM stock_groups WHERE parent_id=?", (row_id,)).fetchone() or \
            conn.execute("SELECT 1 FROM stock_items WHERE group_id=?", (row_id,)).fetchone():
        raise ValidationError("Stock group is not empty")
    conn.execute("DELETE FROM stock_groups WHERE id=?", (row_id,))
    audit.log(conn, company_id, "delete", "stock_group", row_id, f"Deleted stock group {g['name']}")
    return {"deleted": g["name"]}


def list_units(conn, company_id):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM units WHERE company_id=? ORDER BY symbol", (company_id,))]


def save_unit(conn, company_id, data, row_id=None):
    get_company(conn, company_id)
    if row_id:
        _row(conn, "units", company_id, row_id, "Unit")
    symbol = _require_name(data, "symbol")
    _simple_unique(conn, "units", company_id, "symbol", symbol, row_id)
    decimals = int(data.get("decimals") or 0)
    if not 0 <= decimals <= 4:
        raise ValidationError("Decimal places must be between 0 and 4")
    formal = _clean(data.get("formal_name")) or symbol
    if row_id:
        conn.execute("UPDATE units SET symbol=?, formal_name=?, decimals=? WHERE id=?",
                     (symbol, formal, decimals, row_id))
    else:
        row_id = conn.execute("INSERT INTO units(company_id, symbol, formal_name, decimals) VALUES (?,?,?,?)",
                              (company_id, symbol, formal, decimals)).lastrowid
    audit.log(conn, company_id, "save", "unit", row_id, f"Saved unit {symbol}")
    return dict(_row(conn, "units", company_id, row_id, "Unit"))


def delete_unit(conn, company_id, row_id):
    u = _row(conn, "units", company_id, row_id, "Unit")
    if conn.execute("SELECT 1 FROM stock_items WHERE unit_id=?", (row_id,)).fetchone():
        raise ValidationError("Unit is used by stock items")
    conn.execute("DELETE FROM units WHERE id=?", (row_id,))
    audit.log(conn, company_id, "delete", "unit", row_id, f"Deleted unit {u['symbol']}")
    return {"deleted": u["symbol"]}


def list_godowns(conn, company_id):
    rows = conn.execute(
        "SELECT g.*, COALESCE(p.name, 'Primary') AS parent_name FROM godowns g "
        "LEFT JOIN godowns p ON p.id = g.parent_id WHERE g.company_id=? ORDER BY g.name",
        (company_id,))
    return [dict(r) | {"is_predefined": bool(r["is_predefined"])} for r in rows]


def save_godown(conn, company_id, data, row_id=None):
    get_company(conn, company_id)
    if row_id:
        _row(conn, "godowns", company_id, row_id, "Godown")
    name = _require_name(data)
    _simple_unique(conn, "godowns", company_id, "name", name, row_id)
    pid = _tree_parent(conn, "godowns", company_id, data, row_id)
    if row_id:
        conn.execute("UPDATE godowns SET name=?, parent_id=? WHERE id=?", (name, pid, row_id))
    else:
        row_id = conn.execute("INSERT INTO godowns(company_id, name, parent_id) VALUES (?,?,?)",
                              (company_id, name, pid)).lastrowid
    audit.log(conn, company_id, "save", "godown", row_id, f"Saved godown {name}")
    return dict(_row(conn, "godowns", company_id, row_id, "Godown"))


def delete_godown(conn, company_id, row_id):
    g = _row(conn, "godowns", company_id, row_id, "Godown")
    if g["is_predefined"]:
        raise ValidationError("Main Location cannot be deleted")
    if conn.execute("SELECT 1 FROM inventory_entries WHERE godown_id=? LIMIT 1", (row_id,)).fetchone() or \
            conn.execute("SELECT 1 FROM godowns WHERE parent_id=?", (row_id,)).fetchone():
        raise ValidationError("Godown is in use")
    conn.execute("DELETE FROM godowns WHERE id=?", (row_id,))
    audit.log(conn, company_id, "delete", "godown", row_id, f"Deleted godown {g['name']}")
    return {"deleted": g["name"]}


def item_out(r):
    d = dict(r)
    for k in ("opening_rate", "opening_value"):
        d[k] = to_rupees(d[k])
    return d


def list_stock_items(conn, company_id):
    rows = conn.execute(
        "SELECT i.*, u.symbol AS unit, COALESCE(g.name, 'Primary') AS group_name FROM stock_items i "
        "JOIN units u ON u.id = i.unit_id LEFT JOIN stock_groups g ON g.id = i.group_id "
        "WHERE i.company_id=? ORDER BY i.name", (company_id,))
    return [item_out(r) for r in rows]


def get_stock_item(conn, company_id, row_id):
    r = conn.execute(
        "SELECT i.*, u.symbol AS unit, COALESCE(g.name, 'Primary') AS group_name FROM stock_items i "
        "JOIN units u ON u.id = i.unit_id LEFT JOIN stock_groups g ON g.id = i.group_id "
        "WHERE i.company_id=? AND i.id=?", (company_id, row_id)).fetchone()
    if not r:
        raise NotFound("Stock item not found")
    return item_out(r)


def save_stock_item(conn, company_id, data, row_id=None):
    get_company(conn, company_id)
    if row_id:
        _row(conn, "stock_items", company_id, row_id, "Stock item")
    name = _require_name(data)
    _simple_unique(conn, "stock_items", company_id, "name", name, row_id)
    group_id = data.get("group_id") or None
    if group_id:
        _row(conn, "stock_groups", company_id, int(group_id), "Stock group")
    unit_id = data.get("unit_id")
    if not unit_id:
        raise ValidationError("Unit is required")
    _row(conn, "units", company_id, int(unit_id), "Unit")
    gst_rate = float(data.get("gst_rate") or 0)
    if gst_rate < 0 or gst_rate > 100:
        raise ValidationError("GST rate must be between 0 and 100")
    qty = to_qty(data.get("opening_qty"))
    rate = to_paise(data.get("opening_rate"))
    value = to_paise(data.get("opening_value")) if data.get("opening_value") not in (None, "") \
        else round(qty * rate)
    if qty < 0 or value < 0:
        raise ValidationError("Opening quantity and value cannot be negative")
    if qty and not rate and value:
        rate = round(value / qty)
    f = {"name": name, "group_id": int(group_id) if group_id else None, "unit_id": int(unit_id),
         "hsn": _clean(data.get("hsn")), "gst_rate": gst_rate, "opening_qty": qty,
         "opening_rate": rate, "opening_value": value}
    if row_id:
        conn.execute(f"UPDATE stock_items SET {', '.join(k + ' = ?' for k in f)} WHERE id = ?",
                     list(f.values()) + [row_id])
        audit.log(conn, company_id, "alter", "stock_item", row_id, f"Altered stock item {name}", f)
    else:
        cols = list(f)
        row_id = conn.execute(
            f"INSERT INTO stock_items(company_id, {','.join(cols)}) VALUES (?, {','.join('?' * len(cols))})",
            [company_id] + list(f.values())).lastrowid
        audit.log(conn, company_id, "create", "stock_item", row_id, f"Created stock item {name}", f)
    return get_stock_item(conn, company_id, row_id)


def delete_stock_item(conn, company_id, row_id):
    it = _row(conn, "stock_items", company_id, row_id, "Stock item")
    if conn.execute("SELECT 1 FROM inventory_entries WHERE item_id=? LIMIT 1", (row_id,)).fetchone():
        raise ValidationError("Stock item has transactions and cannot be deleted")
    conn.execute("DELETE FROM stock_items WHERE id=?", (row_id,))
    audit.log(conn, company_id, "delete", "stock_item", row_id, f"Deleted stock item {it['name']}")
    return {"deleted": it["name"]}
