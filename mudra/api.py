"""JSON HTTP API — a thin router over the service modules."""
import re

from . import audit, masters, reports, vouchers
from .demo import create_demo_company
from .errors import NotFound, ValidationError

ROUTES = []


def route(method, pattern, write=False):
    rx = re.compile("^" + re.sub(r"{(\w+)}", r"(?P<\1>\\d+)", pattern) + "$")

    def deco(fn):
        ROUTES.append((method, rx, fn, write))
        return fn
    return deco


def dispatch(db, method, path, query, body):
    for m, rx, fn, write in ROUTES:
        if m != method:
            continue
        match = rx.match(path)
        if not match:
            continue
        params = {k: int(v) for k, v in match.groupdict().items()}
        ctx = db.tx() if write else db.read()
        with ctx as conn:
            return fn(conn, query=query, body=body or {}, **params)
    if any(rx.match(path) for _, rx, _, _ in ROUTES):
        raise ValidationError(f"Method {method} not allowed", 405)
    raise NotFound(f"No route for {method} {path}")


# ---------------------------------------------------------------- companies

@route("GET", "/api/companies")
def _list_companies(conn, **_):
    return masters.list_companies(conn)


@route("POST", "/api/companies", write=True)
def _create_company(conn, body, **_):
    return masters.create_company(conn, body)


@route("POST", "/api/demo", write=True)
def _demo(conn, body, **_):
    name = body.get("name") or "Mudra Traders Pvt Ltd"
    existing = [c for c in masters.list_companies(conn) if c["name"].lower() == name.lower()]
    if existing:
        return existing[0]
    return create_demo_company(conn, name)


@route("GET", "/api/companies/{cid}")
def _get_company(conn, cid, **_):
    return masters.get_company(conn, cid)


@route("PUT", "/api/companies/{cid}", write=True)
def _update_company(conn, cid, body, **_):
    return masters.update_company(conn, cid, body)


@route("DELETE", "/api/companies/{cid}", write=True)
def _delete_company(conn, cid, **_):
    return masters.delete_company(conn, cid)


# ---------------------------------------------------------------- generic master routes

def _crud(path, lister, getter, creator, updater, deleter):
    @route("GET", f"/api/companies/{{cid}}/{path}")
    def _l(conn, cid, query, **_):
        masters.get_company(conn, cid)
        return lister(conn, cid, query)

    if getter:
        @route("GET", f"/api/companies/{{cid}}/{path}/{{rid}}")
        def _g(conn, cid, rid, **_):
            return getter(conn, cid, rid)

    @route("POST", f"/api/companies/{{cid}}/{path}", write=True)
    def _c(conn, cid, body, **_):
        return creator(conn, cid, body)

    @route("PUT", f"/api/companies/{{cid}}/{path}/{{rid}}", write=True)
    def _u(conn, cid, rid, body, **_):
        return updater(conn, cid, rid, body)

    @route("DELETE", f"/api/companies/{{cid}}/{path}/{{rid}}", write=True)
    def _d(conn, cid, rid, **_):
        return deleter(conn, cid, rid)


_crud("groups", lambda c, cid, q: masters.list_groups(c, cid), masters.get_group,
      masters.create_group, masters.update_group, masters.delete_group)
_crud("ledgers", lambda c, cid, q: masters.list_ledgers(c, cid, q.get("group_id")), masters.get_ledger,
      masters.create_ledger, masters.update_ledger, masters.delete_ledger)
_crud("voucher-types", lambda c, cid, q: masters.list_voucher_types(c, cid), masters.get_voucher_type,
      masters.create_voucher_type, masters.update_voucher_type, masters.delete_voucher_type)
_crud("stock-groups", lambda c, cid, q: masters.list_stock_groups(c, cid), None,
      lambda c, cid, b: masters.save_stock_group(c, cid, b),
      lambda c, cid, rid, b: masters.save_stock_group(c, cid, b, rid), masters.delete_stock_group)
_crud("units", lambda c, cid, q: masters.list_units(c, cid), None,
      lambda c, cid, b: masters.save_unit(c, cid, b),
      lambda c, cid, rid, b: masters.save_unit(c, cid, b, rid), masters.delete_unit)
_crud("godowns", lambda c, cid, q: masters.list_godowns(c, cid), None,
      lambda c, cid, b: masters.save_godown(c, cid, b),
      lambda c, cid, rid, b: masters.save_godown(c, cid, b, rid), masters.delete_godown)
_crud("stock-items", lambda c, cid, q: masters.list_stock_items(c, cid), masters.get_stock_item,
      lambda c, cid, b: masters.save_stock_item(c, cid, b),
      lambda c, cid, rid, b: masters.save_stock_item(c, cid, b, rid), masters.delete_stock_item)
_crud("vouchers",
      lambda c, cid, q: vouchers.list_vouchers(c, cid, q.get("from"), q.get("to"), q.get("voucher_type_id"),
                                               q.get("base_type"), q.get("ledger_id")),
      vouchers.get_voucher,
      lambda c, cid, b: vouchers.save_voucher(c, cid, b),
      lambda c, cid, rid, b: vouchers.save_voucher(c, cid, b, rid), vouchers.delete_voucher)


@route("POST", "/api/companies/{cid}/invoice-preview")
def _invoice_preview(conn, cid, body, **_):
    return vouchers.preview_invoice(conn, cid, body)


@route("PUT", "/api/companies/{cid}/bank-entries/{eid}", write=True)
def _bank_date(conn, cid, eid, body, **_):
    return vouchers.set_bank_date(conn, cid, eid, body.get("bank_date"))


@route("GET", "/api/companies/{cid}/edit-log")
def _edit_log(conn, cid, query, **_):
    masters.get_company(conn, cid)
    return audit.list_log(conn, cid, query.get("limit", 200), query.get("entity"))


REPORT_RX = re.compile(r"^/api/companies/(\d+)/reports/([a-z-]+)$")


def dispatch_report(db, path, query):
    m = REPORT_RX.match(path)
    if not m:
        return None
    with db.read() as conn:
        return reports.run_report(conn, int(m.group(1)), m.group(2), query)
