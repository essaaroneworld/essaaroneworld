"""SQLite storage for Mudra.

A single database file holds every company (Tally keeps one data folder per
company; we keep one row per company and scope every master/voucher to it).
The connection is shared behind a re-entrant lock: Mudra is a desktop-style
single-user application, so serialising writes keeps things simple and safe.
"""
import sqlite3
import threading
from contextlib import contextmanager

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    mailing_name TEXT,
    address TEXT,
    state TEXT,
    country TEXT DEFAULT 'India',
    pincode TEXT,
    phone TEXT,
    email TEXT,
    gstin TEXT,
    pan TEXT,
    fy_start TEXT NOT NULL,
    books_from TEXT NOT NULL,
    currency_symbol TEXT DEFAULT '₹',
    inventory_enabled INTEGER NOT NULL DEFAULT 1,
    gst_enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Account groups: the chart of accounts skeleton (Tally's 28 predefined groups
-- plus user groups). Nature is denormalised onto every group from its primary.
CREATE TABLE IF NOT EXISTS groups (
    id INTEGER PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    name TEXT NOT NULL COLLATE NOCASE,
    parent_id INTEGER REFERENCES groups(id),
    nature TEXT NOT NULL CHECK (nature IN ('Assets','Liabilities','Income','Expenses')),
    affects_gross_profit INTEGER NOT NULL DEFAULT 0,
    is_predefined INTEGER NOT NULL DEFAULT 0,
    UNIQUE (company_id, name)
);

CREATE TABLE IF NOT EXISTS ledgers (
    id INTEGER PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    name TEXT NOT NULL COLLATE NOCASE,
    group_id INTEGER REFERENCES groups(id),
    opening_balance INTEGER NOT NULL DEFAULT 0,  -- paise, Dr positive / Cr negative
    bill_wise INTEGER NOT NULL DEFAULT 0,
    credit_days INTEGER,
    gst_type TEXT CHECK (gst_type IN ('CGST','SGST','IGST','CESS') OR gst_type IS NULL),
    gstin TEXT,
    state TEXT,
    address TEXT,
    pan TEXT,
    bank_name TEXT,
    account_no TEXT,
    ifsc TEXT,
    is_predefined INTEGER NOT NULL DEFAULT 0,
    UNIQUE (company_id, name)
);

CREATE TABLE IF NOT EXISTS voucher_types (
    id INTEGER PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    name TEXT NOT NULL COLLATE NOCASE,
    base_type TEXT NOT NULL,
    abbreviation TEXT,
    is_predefined INTEGER NOT NULL DEFAULT 0,
    UNIQUE (company_id, name)
);

CREATE TABLE IF NOT EXISTS stock_groups (
    id INTEGER PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    name TEXT NOT NULL COLLATE NOCASE,
    parent_id INTEGER REFERENCES stock_groups(id),
    UNIQUE (company_id, name)
);

CREATE TABLE IF NOT EXISTS units (
    id INTEGER PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    symbol TEXT NOT NULL COLLATE NOCASE,
    formal_name TEXT,
    decimals INTEGER NOT NULL DEFAULT 0,
    UNIQUE (company_id, symbol)
);

CREATE TABLE IF NOT EXISTS godowns (
    id INTEGER PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    name TEXT NOT NULL COLLATE NOCASE,
    parent_id INTEGER REFERENCES godowns(id),
    is_predefined INTEGER NOT NULL DEFAULT 0,
    UNIQUE (company_id, name)
);

CREATE TABLE IF NOT EXISTS stock_items (
    id INTEGER PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    name TEXT NOT NULL COLLATE NOCASE,
    group_id INTEGER REFERENCES stock_groups(id),
    unit_id INTEGER NOT NULL REFERENCES units(id),
    hsn TEXT,
    gst_rate REAL NOT NULL DEFAULT 0,
    opening_qty REAL NOT NULL DEFAULT 0,
    opening_rate INTEGER NOT NULL DEFAULT 0,   -- paise per unit
    opening_value INTEGER NOT NULL DEFAULT 0,  -- paise
    UNIQUE (company_id, name)
);

CREATE TABLE IF NOT EXISTS vouchers (
    id INTEGER PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    voucher_type_id INTEGER NOT NULL REFERENCES voucher_types(id),
    number TEXT NOT NULL,
    date TEXT NOT NULL,
    reference TEXT,
    narration TEXT,
    party_ledger_id INTEGER REFERENCES ledgers(id),
    mode TEXT NOT NULL DEFAULT 'accounting',   -- accounting | invoice | inventory
    meta TEXT,                                  -- JSON (invoice inputs etc.)
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_vouchers_company_date ON vouchers(company_id, date);

CREATE TABLE IF NOT EXISTS voucher_entries (
    id INTEGER PRIMARY KEY,
    voucher_id INTEGER NOT NULL REFERENCES vouchers(id) ON DELETE CASCADE,
    line_no INTEGER NOT NULL,
    ledger_id INTEGER NOT NULL REFERENCES ledgers(id),
    amount INTEGER NOT NULL,          -- paise, Dr positive / Cr negative
    instrument_no TEXT,
    instrument_date TEXT,
    bank_date TEXT                    -- set during bank reconciliation
);
CREATE INDEX IF NOT EXISTS ix_entries_ledger ON voucher_entries(ledger_id);
CREATE INDEX IF NOT EXISTS ix_entries_voucher ON voucher_entries(voucher_id);

CREATE TABLE IF NOT EXISTS bill_allocations (
    id INTEGER PRIMARY KEY,
    entry_id INTEGER NOT NULL REFERENCES voucher_entries(id) ON DELETE CASCADE,
    ledger_id INTEGER NOT NULL REFERENCES ledgers(id),
    ref_type TEXT NOT NULL CHECK (ref_type IN ('New Ref','Agst Ref','Advance','On Account')),
    name TEXT,
    amount INTEGER NOT NULL,          -- paise, same sign convention as entry
    due_date TEXT
);
CREATE INDEX IF NOT EXISTS ix_bills_ledger ON bill_allocations(ledger_id, name);

CREATE TABLE IF NOT EXISTS inventory_entries (
    id INTEGER PRIMARY KEY,
    voucher_id INTEGER NOT NULL REFERENCES vouchers(id) ON DELETE CASCADE,
    line_no INTEGER NOT NULL,
    item_id INTEGER NOT NULL REFERENCES stock_items(id),
    godown_id INTEGER REFERENCES godowns(id),
    qty REAL NOT NULL,                -- inward positive / outward negative
    rate INTEGER NOT NULL DEFAULT 0,  -- paise per unit
    amount INTEGER NOT NULL DEFAULT 0 -- paise, always positive
);
CREATE INDEX IF NOT EXISTS ix_inventory_item ON inventory_entries(item_id);

-- Audit trail (statutory edit log, cf. MCA rule 3(1) of Companies (Accounts) Rules).
CREATE TABLE IF NOT EXISTS edit_log (
    id INTEGER PRIMARY KEY,
    company_id INTEGER,
    ts TEXT DEFAULT CURRENT_TIMESTAMP,
    action TEXT NOT NULL,
    entity TEXT NOT NULL,
    entity_id INTEGER,
    summary TEXT,
    snapshot TEXT
);
CREATE INDEX IF NOT EXISTS ix_edit_log_company ON edit_log(company_id, id);
"""


class Database:
    def __init__(self, path=":memory:"):
        self.path = path
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        if path != ":memory:":
            self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.executescript(SCHEMA)
        self.conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )

    @contextmanager
    def tx(self):
        """Run a block inside a single write transaction."""
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            else:
                self.conn.execute("COMMIT")

    @contextmanager
    def read(self):
        with self.lock:
            yield self.conn

    def backup_to(self, dest_path):
        with self.lock:
            dest = sqlite3.connect(dest_path)
            try:
                self.conn.backup(dest)
            finally:
                dest.close()

    def close(self):
        self.conn.close()
