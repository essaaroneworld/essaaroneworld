"""In-memory view of a company's group hierarchy, used by validation and reports."""
from .seed import CASH_GROUPS


class Chart:
    def __init__(self, conn, company_id):
        self.company_id = company_id
        self.groups = {r["id"]: dict(r) for r in conn.execute(
            "SELECT * FROM groups WHERE company_id = ? ORDER BY name", (company_id,))}
        self.by_name = {g["name"].lower(): g for g in self.groups.values()}
        self.children = {gid: [] for gid in self.groups}
        self.roots = []
        for g in self.groups.values():
            if g["parent_id"] is None:
                self.roots.append(g["id"])
            else:
                self.children[g["parent_id"]].append(g["id"])
        self.ledgers = {r["id"]: dict(r) for r in conn.execute(
            "SELECT * FROM ledgers WHERE company_id = ? ORDER BY name", (company_id,))}
        self.ledgers_by_group = {}
        for led in self.ledgers.values():
            self.ledgers_by_group.setdefault(led["group_id"], []).append(led["id"])

    def group_id(self, name):
        g = self.by_name.get(name.lower())
        return g["id"] if g else None

    def primary_of(self, group_id):
        g = self.groups[group_id]
        while g["parent_id"] is not None:
            g = self.groups[g["parent_id"]]
        return g

    def ancestors(self, group_id):
        """Group itself and every parent up to the primary group."""
        out = []
        gid = group_id
        while gid is not None:
            out.append(gid)
            gid = self.groups[gid]["parent_id"]
        return out

    def is_under(self, group_id, ancestor_name):
        if group_id is None:
            return False
        target = self.group_id(ancestor_name)
        return target in self.ancestors(group_id)

    def descendants(self, group_id):
        out = [group_id]
        stack = [group_id]
        while stack:
            gid = stack.pop()
            for c in self.children.get(gid, []):
                out.append(c)
                stack.append(c)
        return out

    def ledger_is_under(self, ledger_id, ancestor_name):
        led = self.ledgers.get(ledger_id)
        return bool(led) and self.is_under(led["group_id"], ancestor_name)

    def is_cash_or_bank(self, ledger_id):
        return any(self.ledger_is_under(ledger_id, g) for g in CASH_GROUPS)

    def ledger_nature(self, ledger_id):
        led = self.ledgers[ledger_id]
        if led["group_id"] is None:
            return "Liabilities"  # Profit & Loss A/c
        return self.groups[led["group_id"]]["nature"]

    def is_revenue_ledger(self, ledger_id):
        return self.ledger_nature(ledger_id) in ("Income", "Expenses")
