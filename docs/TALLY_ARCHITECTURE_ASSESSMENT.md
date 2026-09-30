# Tally architecture assessment, and how Mudra maps to it

This document looks at how Tally (Tally.ERP 9 / TallyPrime by Tally Solutions,
Bengaluru) is built, judges which parts are worth copying, and explains how
**Mudra Finance and Banking Software** puts those parts into practice.

> **Scope and sources.** Tally is closed-source. This assessment relies on
> Tally's public documentation, its TDL (Tally Definition Language) reference,
> how the product behaves, and standard Indian accounting and GST practice. No
> Tally code, binaries, data formats or artwork were copied or reverse-engineered.
> Mudra is an independent, clean-room system with its own implementation and
> branding.

---

## 1. Tally at a glance

Tally is a desktop accounting, inventory and statutory-compliance product. Most
Indian SMEs use it, and Chartered Accountants often rely on it as their daily
working tool. Its main architectural ideas are:

| Layer | What Tally does |
|---|---|
| **Platform / runtime** | A native C++ engine (the "Tally platform") that runs an interpreted, declarative 4GL called **TDL**. Almost every screen, report, form and menu that ships with Tally is written in TDL, and customers extend the product by loading more `.tdl`/`.tcp` files. |
| **Data layer** | A proprietary embedded **object database** with one data folder per company. The files hold masters, transactions, and the link and transaction managers (Tally.ERP 9 used `*.900` files, and TallyPrime moved to a newer format). The database is hierarchical and object-based: collections of objects with sub-collections, not relational tables. |
| **Object model** | **Masters** (Company, Group, Ledger, Voucher Type, Cost Centre, Currency, Stock Group/Category/Item, Unit, Godown…) and **Vouchers**. A voucher holds sub-collections: *ledger entries*, *inventory entries*, *bill allocations*, *bank allocations*, *batch allocations* and *cost-centre allocations*. |
| **Accounting engine** | Double entry over a fixed chart skeleton of **28 predefined groups** (15 primary and 13 sub-groups). Every group has a *nature* (Assets, Liabilities, Income or Expenses) and a flag for whether it *affects gross profit*. Posting rules are driven by the base voucher type (Contra, Payment, Receipt, Journal, Sales, Purchase, Credit Note, Debit Note, Memorandum, Stock Journal and others). |
| **Reporting** | There is **no posting run and no year-end closing entry**. Every report (Trial Balance, P&L, Balance Sheet, books, stock summary, outstandings, GST returns) is built on the fly from vouchers, using in-memory aggregates. Reports drill down to their source: Balance Sheet → Group → Ledger → Voucher. |
| **UI paradigm** | Keyboard-first. The "Gateway of Tally" menu uses hot-letter selection, a right-hand **button bar** carries the function keys, forms move field by field on Enter, a "List of …" side panel fills every master reference, masters can be created on the fly with Alt+C, and Ctrl+A accepts. |
| **Statutory** | GST (CGST/SGST/IGST, HSN, GSTR-1/3B), TDS/TCS, e-invoice, e-way bill, and an **Edit Log**: TallyPrime 3.0 added an audit trail to meet the MCA rule on Companies (Accounts) Rules 3(1). |
| **Integration** | An XML-over-HTTP server (default port **9000**), an ODBC interface, import and export (XML, Excel, JSON in newer releases), and remote access. |
| **Security** | TallyVault (encryption of company data), user roles and security levels, and a password per company. |
| **Deployment** | A single desktop executable. Multi-user access works over a LAN with the data on a shared path, and licensing is served through Tally's license server. Backup and restore work per company data folder. |

## 2. Assessment

### Strengths worth copying

1. **Computed reports, no posting.** Every report is always current, and an
   edit to a back-dated voucher shows up everywhere at once. This removes a
   whole class of "re-post / re-close" bugs, and it is the biggest reason
   accountants trust Tally.
2. **The fixed group skeleton with nature and gross-profit flags.** Deriving
   P&L and Balance Sheet placement from the group tree means users never map
   accounts to financial-statement lines themselves. Classification *is* the
   report layout.
3. **Base voucher types carry the business rules.** Custom voucher types
   (such as "Sales – Export") inherit behaviour from a base type, so the rules
   live in one place.
4. **Integrated inventory.** Item invoices produce ledger postings and stock
   movements from a single entry, and closing stock comes from inventory
   valuation, not from a manual journal.
5. **A keyboard-first UX with drill-down.** Data entry is very fast for trained
   operators, and any figure can be traced back to its voucher.
6. **Bill-wise tracking inside the voucher.** Receivables and payables ageing
   comes straight from the New Ref, Agst Ref, Advance and On Account
   allocations.

### Weaknesses and risks

1. **A proprietary data store.** It is opaque to other tools, integration has
   to go through the XML port or ODBC, and corruption needs a vendor
   "rewrite/repair" utility.
2. **TDL lock-in.** Customisations need specialist TDL developers and can break
   between releases.
3. **Desktop-centric.** Multi-user access depends on shared folders, and remote
   and mobile access came late and as add-ons.
4. **Weak API.** The XML interface is verbose and has no typed contract.
5. **Discoverability.** The keyboard-heavy UI is fast for experts but hard for
   new users.

### Verdict

Keep the **domain model** and the **computed-report principle**. Replace the
proprietary store with an open, inspectable database, replace TDL with ordinary
code, and add a documented JSON API. That is how Mudra is built.

---

## 3. Mudra architecture

```
 Browser (keyboard-first SPA, vanilla JS)          web/
   ├─ Gateway, menus, "List of…" pickers, forms     js/ui.js, js/app.js
   ├─ Voucher entry (accounting / item invoice /     js/screens/voucher.js
   │  stock journal, bill-wise, bank allocations)
   └─ Reports with drill-down                        js/screens/reports.js
                │  JSON over HTTP (default port 9000, as a nod to Tally's XML port)
                ▼
 Python standard-library server                     mudra/server.py, mudra/api.py
   ├─ Masters service      (company, groups, ledgers, voucher types, stock masters)  masters.py
   ├─ Voucher engine       (double entry, per-type rules, numbering, bills)          vouchers.py
   ├─ GST invoice builder  (CGST+SGST vs IGST, round-off)                            gst.py
   ├─ Report engine        (TB, P&L, BS, books, stock, GST, BRS, ratios)             reports.py
   ├─ Chart helper         (group hierarchy, nature, cash/bank detection)            chart.py
   └─ Edit log             (audit trail of every create/alter/delete)                audit.py
                │
                ▼
 SQLite (single file, WAL)                          mudra/db.py
   companies · groups · ledgers · voucher_types · vouchers · voucher_entries ·
   bill_allocations · inventory_entries · stock_groups · stock_items · units ·
   godowns · edit_log
```

### Key design decisions

| Decision | Rationale |
|---|---|
| **Money in integer paise** | Every amount is an `INTEGER`, with Dr positive and Cr negative, so vouchers balance exactly and no floating-point drift builds up. The API speaks rupees. |
| **Computed reports** (Tally's model) | No posting tables and no closing entries. Reports aggregate `voucher_entries` over a period and roll up through the group tree. |
| **Predefined chart = Tally's 28 groups** | The same primary and sub-group structure, natures and gross-profit flags, so accountants trained on Tally feel at home. |
| **Base voucher types** | `voucher_types.base_type` drives the rules: Contra uses only cash/bank, Payment credits cash/bank, Receipt debits cash/bank, Journal excludes cash/bank, Sales and Debit Note move goods out, Purchase and Credit Note move goods in, Stock Journal has no ledgers, and Memorandum is kept out of the books. |
| **Weighted-average stock valuation** | Tally's default method. Pure godown transfers (Stock Journal lines that net to zero) are left out of inward cost, so they don't distort the average. |
| **Opening-balance difference** | As in Tally, if opening balances (including opening stock) don't agree, the difference appears as "Difference in opening balances" and the statements still balance. |
| **SQLite + stdlib only** | Nothing to install, a single-file backup (`/api/backup`), and anyone can read the data with standard tools. |
| **Edit log always on** | Meets the MCA audit-trail requirement and cannot be switched off. |

---

## 4. Concept mapping: Tally → Mudra

| Tally concept | Mudra implementation |
|---|---|
| Company (F3 select, Alt+F3 alter) | `companies` table; F3 / Alt+F3 in the UI |
| 28 predefined groups | `seed.PRIMARY_GROUPS`, `seed.SUB_GROUPS` |
| Cash and Profit & Loss A/c ledgers | Seeded per company; P&L A/c sits under *Primary* |
| Ledger opening balance Dr/Cr | `ledgers.opening_balance` (signed paise) |
| Maintain balances bill-by-bill | `ledgers.bill_wise`, `bill_allocations` (New Ref / Agst Ref / Advance / On Account) |
| Voucher types F4–F10 | Same keys: F4 Contra, F5 Payment, F6 Receipt, F7 Journal, F8 Sales, F9 Purchase, Ctrl+F8 Credit Note, Ctrl+F9 Debit Note, Alt+F7 Stock Journal, F10 Memorandum |
| Item invoice / accounting invoice (Ctrl+H) | `mode = invoice` or `accounting`, toggled with Ctrl+H |
| GST: CGST+SGST (intra-state) / IGST (inter-state) | `gst.build_invoice`, decided by the party's state or GSTIN state code versus the company's |
| Bank allocations (instrument no./date) | `voucher_entries.instrument_no`, `instrument_date` |
| Bank Reconciliation (bank date) | `voucher_entries.bank_date`; *Banking → Bank Reconciliation* |
| Stock Summary / godowns | `reports.stock_summary`, `godown_summary` |
| Outstandings (receivables/payables) | `reports.outstanding` with due dates and overdue days |
| GSTR-1 / GSTR-3B | `reports.gst_report` (outward register, ITC, net payable, HSN summary) |
| Ratio Analysis | `reports.ratios` |
| Edit Log | `edit_log` table, *Display More Reports → Edit Log* |
| Gateway of Tally | *Gateway of Mudra* with the same hot letters (C, A, H, V, K, N, B, P, S, R, D, Q) |
| Go To (Alt+G) | Command palette over all reports, vouchers and masters |
| Export (Alt+E) / Print (Alt+P) | CSV export of any report, and a print stylesheet with a tax-invoice print view |
| XML/ODBC integration | A documented JSON REST API (see README) |
| TallyVault / users | *Not yet* (see roadmap); the server binds to localhost by default |

## 5. Gaps and roadmap

These are features Tally has that Mudra does not yet implement, roughly in
order of business value:

1. Users, roles and login; encryption at rest (TallyVault equivalent).
2. Cost categories and cost centres.
3. Multi-currency with forex gain/loss.
4. Order processing (sales/purchase orders, delivery and receipt notes) and batches with expiry.
5. TDS/TCS computation and returns; e-invoice (IRN/QR) and e-way bill integration.
6. Payroll.
7. Budgets and scenarios; interest calculation on bills.
8. Cheque printing and connected-banking statement import (auto-reconciliation).
9. Year-end split, carry-forward and multi-year comparatives.
10. Import from Tally XML masters/vouchers, for migration.
