# Mudra Finance and Banking Software

**Mudra** (मुद्रा, "currency") is keyboard-first accounting, inventory, GST and
banking software for Indian businesses. It is modelled on the architecture
that made Tally the standard for Indian bookkeeping: 28 predefined account
groups, voucher-driven double entry, and reports computed live from vouchers.
It is built on an open, dependency-free stack.

- **Accounting:** double-entry vouchers (Contra, Payment, Receipt, Journal,
  Sales, Purchase, Credit Note, Debit Note, Memorandum), with the same rules
  per voucher type that Tally applies
- **Inventory:** stock groups, items, units and godowns; item invoices; stock
  journals and godown transfers; weighted-average valuation
- **GST:** automatic CGST+SGST for intra-state and IGST for inter-state
  supplies, round-off, HSN, and a GSTR-1 / GSTR-3B summary
- **Banking:** cheque and UTR details on bank entries, bank reconciliation
  (BRS), a cheque register, and cash/bank books
- **Receivables and payables:** bill-wise tracking (New Ref, Agst Ref, Advance,
  On Account), credit periods, due dates and overdue ageing
- **Reports:** Balance Sheet, Profit & Loss (trading and P&L), Trial Balance,
  Day Book, Ledger, Group Summary, Stock Summary, Stock Item movement, Godown
  Summary, Outstandings, GST, BRS, Ratio Analysis and a Dashboard. Every report
  drills down to the voucher.
- **Compliance:** an always-on **Edit Log** (audit trail) of every creation,
  alteration and deletion
- **UX:** a Gateway menu with hot letters, a function-key button bar, "List of
  …" pickers, Alt+C to create masters on the fly, Ctrl+A to accept, Esc to go
  back, Alt+G for Go To, CSV export, print views, and support for dark mode
  and mobile screens

The design rationale, and how each Tally concept maps to Mudra, are in
[docs/TALLY_ARCHITECTURE_ASSESSMENT.md](docs/TALLY_ARCHITECTURE_ASSESSMENT.md).

## Quick start

Requires **Python 3.9+** and nothing else. There is no `pip install` and no
Node build.

```bash
python3 -m mudra --demo          # starts on http://127.0.0.1:9000 with a sample company
```

Open <http://127.0.0.1:9000>. With `--demo`, a sample company called *Mudra
Traders Pvt Ltd* is created with a month of purchases, sales, receipts,
payments, a credit note and a godown transfer. You can also create your own
company from the start screen (**C**).

Options:

| Flag / env | Default | Meaning |
|---|---|---|
| `--data` / `MUDRA_DATA` | `data/mudra.db` | SQLite database file (all companies) |
| `--port` / `MUDRA_PORT` | `9000` | HTTP port |
| `--host` / `MUDRA_HOST` | `127.0.0.1` | Bind address. There is no login yet, so keep it on localhost. |
| `--demo` | off | Create the sample company if it doesn't exist |

**Backup:** press **Alt+Y** in the app, or `GET /api/backup`, to download a
consistent copy of the database.

## Keyboard reference

| Keys | Action |
|---|---|
| Enter / Esc | Select or next field / go back (quit) |
| Hot letter | The underlined letter in any menu |
| Alt+G (Ctrl+G) | Go To any report, voucher or master |
| F2 / Alt+F2 | Change the current date / reporting period |
| F3 · Alt+F3 | Select company · alter company |
| F4 · F5 · F6 · F7 | Contra · Payment · Receipt · Journal |
| F8 · F9 | Sales · Purchase |
| Ctrl+F8 · Ctrl+F9 | Credit Note · Debit Note |
| Alt+F7 · F10 | Stock Journal · Memorandum |
| Ctrl+A | Accept (save) |
| Ctrl+H | Switch between item invoice and accounting invoice |
| Alt+C | Create a master from inside any list |
| Alt+D | Delete a voucher or master |
| D / C | Toggle Dr or Cr on a voucher line |
| Alt+F1 | Detailed / condensed report |
| Alt+E · Alt+P | Export CSV · print |

Browsers reserve a few keys (for example Alt+D and F5 in some browsers). Every
shortcut is also a clickable button in the right-hand bar.

## Project layout

```
mudra/                Python package (standard library only)
  db.py               SQLite schema and connection/transactions
  seed.py             Predefined groups, ledgers, voucher types, units, godown
  chart.py            Group hierarchy helper (nature, cash/bank detection)
  masters.py          Company, groups, ledgers, voucher types, stock masters
  vouchers.py         Voucher engine: validation, numbering, bill-wise, BRS dates
  gst.py              GST invoice builder (CGST/SGST/IGST, round-off)
  reports.py          All financial, inventory, GST, banking reports
  audit.py            Edit log
  api.py, server.py   JSON API + static file server
  demo.py             Sample company
web/                  Single-page client (vanilla JS modules + CSS, no build)
tests/                unittest suite (engine + HTTP API)
docs/                 Architecture assessment
```

## API

Everything the UI does goes through a JSON API. Amounts are in rupees, and
dates are `YYYY-MM-DD`.

```
GET    /api/companies                          POST /api/companies
GET    /api/companies/{cid}                    PUT/DELETE /api/companies/{cid}
GET    /api/companies/{cid}/{kind}             POST /api/companies/{cid}/{kind}
GET/PUT/DELETE /api/companies/{cid}/{kind}/{id}
       kind = groups | ledgers | voucher-types | stock-groups | stock-items | units | godowns | vouchers
POST   /api/companies/{cid}/invoice-preview    compute GST lines without saving
PUT    /api/companies/{cid}/bank-entries/{entry_id}   {"bank_date": "2026-04-20"}
GET    /api/companies/{cid}/reports/{name}?from=&to=&...
       name = trial-balance | profit-loss | balance-sheet | day-book | ledger (ledger_id) |
              group-summary (group_id) | cash-bank | stock-summary | stock-item (item_id) |
              godown-summary | outstanding (kind=receivable|payable) | gst |
              bank-reconciliation (ledger_id) | cheque-register | ratios | dashboard
GET    /api/companies/{cid}/edit-log
GET    /api/backup                              download the SQLite database
POST   /api/demo                                create/open the sample company
```

Example payment voucher:

```json
POST /api/companies/1/vouchers
{
  "voucher_type": "Payment", "date": "2026-04-20", "narration": "Rent for April",
  "entries": [
    {"ledger_id": 12, "side": "Dr", "amount": 45000},
    {"ledger_id": 6,  "side": "Cr", "amount": 45000, "instrument_no": "000453"}
  ]
}
```

Example GST sales invoice. The server builds the party, sales, tax and
round-off lines and moves the stock:

```json
{
  "voucher_type": "Sales", "date": "2026-04-05", "mode": "invoice",
  "invoice": {
    "party_ledger_id": 18, "account_ledger_id": 9, "round_off": true,
    "items": [{"item_id": 1, "qty": 20, "rate": 9500, "discount": 0}]
  }
}
```

## Tests

```bash
python3 -m unittest discover -s tests -v
```

The tests check the chart of accounts, the rule for each voucher type, balance
checks, numbering, bill-wise allocation, the GST split (intra-state,
inter-state and round-off), weighted-average stock, that TB, P&L and BS agree
with each other, detection of opening-balance differences, bank
reconciliation, the audit log, and the HTTP API.

## Accounting notes

- Amounts are stored as integer **paise**, so vouchers always balance exactly.
- Reports are **computed from vouchers** (there is no posting and no closing
  step), so back-dated edits show up everywhere at once.
- Closing stock uses the **weighted average cost** and flows into the P&L
  (trading account) and the Balance Sheet (Stock-in-Hand).
- If opening balances don't agree, the difference appears as **Difference in
  opening balances**, as Tally does.
- Memorandum vouchers are recorded but left out of the books.

## Status and roadmap

Mudra implements the core of an Indian SME accounting package. Not yet built:
users and roles with encryption at rest, cost centres, multi-currency, order
processing, batches, TDS/TCS, e-invoice and e-way bill, payroll, budgets,
cheque printing, bank-statement import, and year-end split. See section 5 of
the architecture document.

*Mudra is an independent project. It is not affiliated with or endorsed by
Tally Solutions Pvt. Ltd. "Tally" and "TallyPrime" are trademarks of their
respective owner.*
