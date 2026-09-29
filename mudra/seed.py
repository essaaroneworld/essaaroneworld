"""Predefined masters created with every company.

Mirrors Tally's out-of-the-box chart of accounts: 15 primary groups and
13 sub-groups, the two predefined ledgers (Cash and Profit & Loss A/c) and
the standard voucher types.
"""

# (name, nature, affects_gross_profit)
PRIMARY_GROUPS = [
    ("Branch / Divisions", "Liabilities", 0),
    ("Capital Account", "Liabilities", 0),
    ("Current Assets", "Assets", 0),
    ("Current Liabilities", "Liabilities", 0),
    ("Direct Expenses", "Expenses", 1),
    ("Direct Incomes", "Income", 1),
    ("Fixed Assets", "Assets", 0),
    ("Indirect Expenses", "Expenses", 0),
    ("Indirect Incomes", "Income", 0),
    ("Investments", "Assets", 0),
    ("Loans (Liability)", "Liabilities", 0),
    ("Misc. Expenses (ASSET)", "Assets", 0),
    ("Purchase Accounts", "Expenses", 1),
    ("Sales Accounts", "Income", 1),
    ("Suspense A/c", "Liabilities", 0),
]

# (name, parent)
SUB_GROUPS = [
    ("Bank Accounts", "Current Assets"),
    ("Bank OD A/c", "Loans (Liability)"),
    ("Cash-in-Hand", "Current Assets"),
    ("Deposits (Asset)", "Current Assets"),
    ("Duties & Taxes", "Current Liabilities"),
    ("Loans & Advances (Asset)", "Current Assets"),
    ("Provisions", "Current Liabilities"),
    ("Reserves & Surplus", "Capital Account"),
    ("Secured Loans", "Loans (Liability)"),
    ("Stock-in-Hand", "Current Assets"),
    ("Sundry Creditors", "Current Liabilities"),
    ("Sundry Debtors", "Current Assets"),
    ("Unsecured Loans", "Loans (Liability)"),
]

# (name, base_type, abbreviation)
VOUCHER_TYPES = [
    ("Contra", "Contra", "Ctra"),
    ("Payment", "Payment", "Pymt"),
    ("Receipt", "Receipt", "Rcpt"),
    ("Journal", "Journal", "Jrnl"),
    ("Sales", "Sales", "Sale"),
    ("Purchase", "Purchase", "Purc"),
    ("Credit Note", "Credit Note", "C/Note"),
    ("Debit Note", "Debit Note", "D/Note"),
    ("Memorandum", "Memorandum", "Memo"),
    ("Stock Journal", "Stock Journal", "Stjr"),
]

BASE_TYPES = [vt[1] for vt in VOUCHER_TYPES]

UNITS = [
    ("Nos", "Numbers", 0),
    ("Pcs", "Pieces", 0),
    ("Kgs", "Kilograms", 3),
    ("Ltr", "Litres", 3),
    ("Mtr", "Metres", 2),
    ("Box", "Boxes", 0),
]

CASH_GROUPS = ("Cash-in-Hand", "Bank Accounts", "Bank OD A/c")
PL_LEDGER = "Profit & Loss A/c"


def seed_company(conn, company_id, gst_enabled=True):
    ids = {}
    for name, nature, gp in PRIMARY_GROUPS:
        cur = conn.execute(
            "INSERT INTO groups(company_id, name, parent_id, nature, affects_gross_profit, is_predefined)"
            " VALUES (?,?,?,?,?,1)",
            (company_id, name, None, nature, gp),
        )
        ids[name] = (cur.lastrowid, nature, gp)
    for name, parent in SUB_GROUPS:
        pid, nature, gp = ids[parent]
        cur = conn.execute(
            "INSERT INTO groups(company_id, name, parent_id, nature, affects_gross_profit, is_predefined)"
            " VALUES (?,?,?,?,?,1)",
            (company_id, name, pid, nature, gp),
        )
        ids[name] = (cur.lastrowid, nature, gp)

    conn.execute(
        "INSERT INTO ledgers(company_id, name, group_id, is_predefined) VALUES (?,?,?,1)",
        (company_id, "Cash", ids["Cash-in-Hand"][0]),
    )
    conn.execute(
        "INSERT INTO ledgers(company_id, name, group_id, is_predefined) VALUES (?,?,NULL,1)",
        (company_id, PL_LEDGER),
    )
    if gst_enabled:
        for tax in ("CGST", "SGST", "IGST"):
            conn.execute(
                "INSERT INTO ledgers(company_id, name, group_id, gst_type) VALUES (?,?,?,?)",
                (company_id, tax, ids["Duties & Taxes"][0], tax),
            )

    for name, base, abbr in VOUCHER_TYPES:
        conn.execute(
            "INSERT INTO voucher_types(company_id, name, base_type, abbreviation, is_predefined)"
            " VALUES (?,?,?,?,1)",
            (company_id, name, base, abbr),
        )
    for symbol, formal, decimals in UNITS:
        conn.execute(
            "INSERT INTO units(company_id, symbol, formal_name, decimals) VALUES (?,?,?,?)",
            (company_id, symbol, formal, decimals),
        )
    conn.execute(
        "INSERT INTO godowns(company_id, name, is_predefined) VALUES (?,?,1)",
        (company_id, "Main Location"),
    )
