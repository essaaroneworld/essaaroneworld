// Reports with Tally-style drill-down: Balance Sheet → Group → Ledger → Voucher.
import { app } from '../app.js';
import { toast } from '../ui.js';
import { amt, drcr, fdate, h, parseDate, qty } from '../util.js';

const openVoucher = (id) => () => app.go('voucher', { id });
const openNode = (n) => {
  if (n.type === 'group' && n.name === 'Stock-in-Hand') return () => app.go('report', { name: 'stock-summary' });
  if (n.type === 'group') return () => app.go('report', { name: 'group-summary', group_id: n.id });
  if (n.type === 'ledger') return () => app.go('report', { name: 'ledger', ledger_id: n.id });
  if (n.type === 'stock') return () => app.go('report', { name: 'stock-summary' });
  return null;
};

const dr = (v) => (v > 0 ? amt(v) : '');
const cr = (v) => (v < 0 ? amt(-v) : '');

// ---------------------------------------------------------------- definitions

const DEFS = {
  'balance-sheet': {
    title: () => 'Balance Sheet',
    period: (d) => `as at ${fdate(d.as_of)}`,
    detail: true,
    render(d, s) {
      const side = (nodes, extra) => {
        const t = h('table.rep.fin');
        const tb = h('tbody');
        for (const n of nodes) {
          s.row(tb, [h('td.b', n.name), h('td.num'), h('td.num.b', amt(n.amount, true))], openNode(n));
          if (s.detailed) {
            for (const c of n.children || []) {
              const v = n.nature === 'Liabilities' ? -c.closing : c.closing;
              s.row(tb, [h('td.ind', c.name), h('td.num.i', amt(v, true)), h('td.num')], openNode(c));
            }
          }
        }
        extra(tb);
        t.append(tb);
        return t;
      };
      const pl = d.profit_loss;
      const left = side(d.liabilities, (tb) => {
        s.row(tb, [h('td.b', 'Profit & Loss A/c'), h('td.num'), h('td.num.b', amt(pl.total, true))], () => app.go('report', { name: 'profit-loss' }));
        if (s.detailed) {
          s.row(tb, [h('td.ind', 'Opening Balance'), h('td.num.i', amt(pl.opening, true)), h('td.num')]);
          s.row(tb, [h('td.ind', 'Current Period'), h('td.num.i', amt(pl.current_period, true)), h('td.num')]);
        }
        if (d.difference_in_opening > 0) s.row(tb, [h('td.b.warn', 'Difference in opening balances'), h('td.num'), h('td.num.b', amt(d.difference_in_opening, true))]);
      });
      const right = side(d.assets, (tb) => {
        if (d.difference_in_opening < 0) s.row(tb, [h('td.b.warn', 'Difference in opening balances'), h('td.num'), h('td.num.b', amt(-d.difference_in_opening, true))]);
      });
      return h('div',
        h('div.two-col',
          h('div.col', h('div.col-head', h('span', 'Liabilities'), h('span', `as at ${fdate(d.as_of)}`)), left),
          h('div.col', h('div.col-head', h('span', 'Assets'), h('span', `as at ${fdate(d.as_of)}`)), right)),
        h('div.two-col.totals',
          h('div.col.total-row', h('span', 'Total'), h('span.num', amt(d.total_liabilities, true))),
          h('div.col.total-row', h('span', 'Total'), h('span.num', amt(d.total_assets, true)))));
    },
    export: (d) => [
      ['Liabilities', 'Amount'], ...d.liabilities.map((n) => [n.name, n.amount]), ['Profit & Loss A/c', d.profit_loss.total],
      [], ['Assets', 'Amount'], ...d.assets.map((n) => [n.name, n.amount]),
      [], ['Total Liabilities', d.total_liabilities], ['Total Assets', d.total_assets],
    ],
  },

  'profit-loss': {
    title: () => 'Profit & Loss A/c',
    period: (d) => `${fdate(d.from)} to ${fdate(d.to)}`,
    detail: true,
    render(d, s) {
      const block = (tb, nodes) => {
        for (const n of nodes) {
          s.row(tb, [h('td.b', n.name), h('td.num'), h('td.num.b', amt(n.amount, true))], openNode(n));
          if (s.detailed) {
            for (const c of n.children || []) {
              const v = n.nature === 'Income' ? -c.closing : c.closing;
              s.row(tb, [h('td.ind', c.name), h('td.num.i', amt(v, true)), h('td.num')], openNode(c));
            }
          }
        }
      };
      const line = (tb, label, v, cls = '.b', open = null) => s.row(tb, [h(`td${cls}`, label), h('td.num'), h('td.num.b', amt(v, true))], open);
      const L = h('tbody');
      const R = h('tbody');
      const stock = () => app.go('report', { name: 'stock-summary' });
      if (d.opening_stock) line(L, 'Opening Stock', d.opening_stock, '.b', stock);
      block(L, d.trading_dr);
      block(R, d.trading_cr);
      if (d.closing_stock) line(R, 'Closing Stock', d.closing_stock, '.b', stock);
      if (d.gross_profit >= 0) line(L, 'Gross Profit c/o', d.gross_profit, '.b.gp');
      else line(R, 'Gross Loss c/o', -d.gross_profit, '.b.gl');
      const sub = (tb) => tb.append(h('tr.subtot', h('td'), h('td'), h('td.num.b', amt(d.trading_total, true))));
      sub(L);
      sub(R);
      if (d.gross_profit >= 0) line(R, 'Gross Profit b/f', d.gross_profit, '.b.gp');
      else line(L, 'Gross Loss b/f', -d.gross_profit, '.b.gl');
      block(L, d.pl_dr);
      block(R, d.pl_cr);
      if (d.net_profit >= 0) line(L, 'Net Profit', d.net_profit, '.b.gp');
      else line(R, 'Net Loss', -d.net_profit, '.b.gl');
      return h('div',
        h('div.two-col',
          h('div.col', h('div.col-head', h('span', 'Particulars'), h('span', `${fdate(d.from)} to ${fdate(d.to)}`)), h('table.rep.fin', L)),
          h('div.col', h('div.col-head', h('span', 'Particulars'), h('span', `${fdate(d.from)} to ${fdate(d.to)}`)), h('table.rep.fin', R))),
        h('div.two-col.totals',
          h('div.col.total-row', h('span', 'Total'), h('span.num', amt(d.pl_total, true))),
          h('div.col.total-row', h('span', 'Total'), h('span.num', amt(d.pl_total, true)))));
    },
    export: (d) => [
      ['Particulars', 'Amount'], ['Opening Stock', d.opening_stock], ...d.trading_dr.map((n) => [n.name, n.amount]),
      ...d.trading_cr.map((n) => [n.name, n.amount]), ['Closing Stock', d.closing_stock], ['Gross Profit', d.gross_profit],
      ...d.pl_dr.map((n) => [n.name, n.amount]), ...d.pl_cr.map((n) => [n.name, n.amount]), ['Net Profit', d.net_profit],
    ],
  },

  'trial-balance': {
    title: () => 'Trial Balance',
    period: (d) => `${fdate(d.from)} to ${fdate(d.to)}`,
    detail: true,
    render(d, s) {
      const tb = h('tbody');
      const walk = (n, depth) => {
        const cls = depth ? `td.ind${Math.min(depth, 4)}` : 'td.b';
        s.row(tb, [h(cls, n.name), h('td.num', dr(n.closing)), h('td.num', cr(n.closing))], openNode(n) || null);
        if (s.detailed && n.children) n.children.forEach((c) => walk(c, depth + 1));
      };
      d.rows.forEach((n) => walk(n, 0));
      return h('table.rep',
        h('thead', h('tr', h('th', 'Particulars'), h('th.num', 'Debit'), h('th.num', 'Credit')),
          h('tr.sub', h('th'), h('th.num', { colSpan: 2 }, 'Closing Balance'))),
        tb,
        h('tfoot', h('tr', h('td', 'Grand Total'), h('td.num', amt(d.total_dr, true)), h('td.num', amt(d.total_cr, true)))));
    },
    export: (d) => {
      const out = [['Particulars', 'Debit', 'Credit']];
      const walk = (n, depth) => {
        out.push(['  '.repeat(depth) + n.name, n.closing > 0 ? n.closing : '', n.closing < 0 ? -n.closing : '']);
        (n.children || []).forEach((c) => walk(c, depth + 1));
      };
      d.rows.forEach((n) => walk(n, 0));
      out.push(['Grand Total', d.total_dr, d.total_cr]);
      return out;
    },
  },

  'day-book': {
    title: () => 'Day Book',
    period: (d) => (d.from === d.to ? `for ${fdate(d.from)}` : `${fdate(d.from)} to ${fdate(d.to)}`),
    params: () => ({}),
    render(d, s) {
      const tb = h('tbody');
      for (const r of d.rows) {
        s.row(tb, [h('td.nowrap', fdate(r.date)), h('td', r.particulars, r.narration && s.detailed ? h('div.narr', r.narration) : null),
          h('td', r.voucher_type, r.memorandum ? h('span.tag', 'memo') : null), h('td', r.number), h('td.num', amt(r.debit)), h('td.num', amt(r.credit))],
        openVoucher(r.voucher_id));
      }
      if (!d.rows.length) tb.append(h('tr', h('td.empty', { colSpan: 6 }, 'No vouchers in this period. Alt+F2 to change the period.')));
      return h('table.rep', h('thead', h('tr', h('th', 'Date'), h('th', 'Particulars'), h('th', 'Vch Type'), h('th', 'Vch No.'), h('th.num', 'Debit Amount'), h('th.num', 'Credit Amount'))), tb);
    },
    detail: true,
    export: (d) => [['Date', 'Particulars', 'Voucher Type', 'Voucher No', 'Debit', 'Credit', 'Narration'],
      ...d.rows.map((r) => [r.date, r.particulars, r.voucher_type, r.number, r.debit, r.credit, r.narration || ''])],
  },

  ledger: {
    title: (p, d) => `Ledger: ${d ? d.ledger.name : ''}`,
    period: (d) => `${fdate(d.from)} to ${fdate(d.to)}`,
    detail: true,
    render(d, s) {
      const tb = h('tbody');
      tb.append(h('tr.open', h('td'), h('td.b', 'Opening Balance'), h('td'), h('td'), h('td.num', dr(d.opening)), h('td.num', cr(d.opening)), h('td.num', drcr(d.opening))));
      for (const r of d.rows) {
        s.row(tb, [h('td.nowrap', fdate(r.date)), h('td', r.particulars, s.detailed && r.details.length > 1 ? h('div.narr', r.details.join(', ')) : null,
          s.detailed && r.narration ? h('div.narr', r.narration) : null),
        h('td', r.voucher_type), h('td', r.number), h('td.num', amt(r.debit)), h('td.num', amt(r.credit)), h('td.num', drcr(r.balance))], openVoucher(r.voucher_id));
      }
      return h('table.rep',
        h('thead', h('tr', h('th', 'Date'), h('th', 'Particulars'), h('th', 'Vch Type'), h('th', 'Vch No.'), h('th.num', 'Debit'), h('th.num', 'Credit'), h('th.num', 'Balance'))),
        tb,
        h('tfoot',
          h('tr', h('td'), h('td', 'Current Total'), h('td'), h('td'), h('td.num', amt(d.total_debit, true)), h('td.num', amt(d.total_credit, true)), h('td')),
          h('tr', h('td'), h('td', 'Closing Balance'), h('td'), h('td'), h('td.num', dr(d.closing)), h('td.num', cr(d.closing)), h('td.num', drcr(d.closing, true)))));
    },
    extra: (d) => h('span', `Under: ${d.ledger.group}`),
    export: (d) => [['Date', 'Particulars', 'Voucher Type', 'Voucher No', 'Debit', 'Credit', 'Balance'], ['', 'Opening Balance', '', '', '', '', d.opening],
      ...d.rows.map((r) => [r.date, r.particulars, r.voucher_type, r.number, r.debit, r.credit, r.balance]), ['', 'Closing Balance', '', '', d.total_debit, d.total_credit, d.closing]],
  },

  'group-summary': {
    title: (p, d) => `Group Summary: ${d ? d.group.name : ''}`,
    period: (d) => `${fdate(d.from)} to ${fdate(d.to)}`,
    render(d, s) {
      const tb = h('tbody');
      for (const c of d.group.children) {
        s.row(tb, [h(`td${c.type === 'group' ? '.b' : ''}`, c.name), h('td.num', drcr(c.opening)), h('td.num', amt(c.dr)), h('td.num', amt(c.cr)), h('td.num', drcr(c.closing))], openNode(c));
      }
      if (!d.group.children.length) tb.append(h('tr', h('td.empty', { colSpan: 5 }, 'No ledgers under this group')));
      const g = d.group;
      return h('table.rep',
        h('thead', h('tr', h('th', 'Particulars'), h('th.num', 'Opening Balance'), h('th.num', 'Debit'), h('th.num', 'Credit'), h('th.num', 'Closing Balance'))),
        tb, h('tfoot', h('tr', h('td', 'Grand Total'), h('td.num', drcr(g.opening, true)), h('td.num', amt(g.dr, true)), h('td.num', amt(g.cr, true)), h('td.num', drcr(g.closing, true)))));
    },
    export: (d) => [['Particulars', 'Opening', 'Debit', 'Credit', 'Closing'], ...d.group.children.map((c) => [c.name, c.opening, c.dr, c.cr, c.closing])],
  },

  'cash-bank': {
    title: () => 'Cash/Bank Summary',
    period: (d) => `${fdate(d.from)} to ${fdate(d.to)}`,
    render(d, s) {
      const tb = h('tbody');
      for (const g of d.groups) {
        s.row(tb, [h('td.b', g.name), h('td.num', drcr(g.opening)), h('td.num', amt(g.dr)), h('td.num', amt(g.cr)), h('td.num.b', drcr(g.closing))], openNode(g));
        for (const c of g.children) s.row(tb, [h('td.ind', c.name), h('td.num', drcr(c.opening)), h('td.num', amt(c.dr)), h('td.num', amt(c.cr)), h('td.num', drcr(c.closing))], openNode(c));
      }
      return h('table.rep',
        h('thead', h('tr', h('th', 'Particulars'), h('th.num', 'Opening'), h('th.num', 'Inflow (Dr)'), h('th.num', 'Outflow (Cr)'), h('th.num', 'Closing Balance'))),
        tb, h('tfoot', h('tr', h('td', 'Grand Total'), h('td'), h('td'), h('td'), h('td.num', drcr(d.total, true)))));
    },
    export: (d) => [['Particulars', 'Opening', 'Debit', 'Credit', 'Closing'], ...d.groups.flatMap((g) => [[g.name, g.opening, g.dr, g.cr, g.closing], ...g.children.map((c) => ['  ' + c.name, c.opening, c.dr, c.cr, c.closing])])],
  },

  'stock-summary': {
    title: () => 'Stock Summary',
    period: (d) => `${fdate(d.from)} to ${fdate(d.to)}`,
    detail: true,
    render(d, s) {
      const tb = h('tbody');
      const groups = {};
      d.rows.forEach((r) => (groups[r.group] = groups[r.group] || []).push(r));
      for (const [g, rows] of Object.entries(groups).sort()) {
        const sum = (k) => rows.reduce((a, r) => a + r[k], 0);
        tb.append(h('tr.grp', h('td.b', g), h('td'), h('td.num.b', amt(sum('opening_value'))), h('td'), h('td.num.b', amt(sum('inward_value'))), h('td'), h('td.num.b', amt(sum('outward_value'))), h('td'), h('td'), h('td.num.b', amt(sum('closing_value')))));
        for (const r of rows) {
          s.row(tb, [h('td.ind', r.name), h('td.num', qty(r.opening_qty, r.unit)), h('td.num', amt(r.opening_value)), h('td.num', qty(r.inward_qty, r.unit)), h('td.num', amt(r.inward_value)),
            h('td.num', qty(r.outward_qty, r.unit)), h('td.num', s.detailed ? amt(r.outward_sale_value) : amt(r.outward_value)), h('td.num', qty(r.closing_qty, r.unit)), h('td.num', amt(r.closing_rate)), h('td.num', amt(r.closing_value))],
          () => app.go('report', { name: 'stock-item', item_id: r.item_id }));
        }
      }
      return h('table.rep.wide',
        h('thead',
          h('tr', h('th', 'Particulars'), h('th.c', { colSpan: 2 }, 'Opening Balance'), h('th.c', { colSpan: 2 }, 'Inwards'), h('th.c', { colSpan: 2 }, `Outwards${s.detailed ? ' (sale value)' : ''}`), h('th.c', { colSpan: 3 }, 'Closing Balance')),
          h('tr.sub', h('th'), ...['Qty', 'Value', 'Qty', 'Value', 'Qty', 'Value', 'Qty', 'Rate', 'Value'].map((t) => h('th.num', t)))),
        tb,
        h('tfoot', h('tr', h('td', 'Grand Total'), h('td'), h('td.num', amt(d.total_opening, true)), h('td'), h('td.num', amt(d.rows.reduce((a, r) => a + r.inward_value, 0), true)), h('td'),
          h('td.num', amt(d.rows.reduce((a, r) => a + r.outward_value, 0), true)), h('td'), h('td'), h('td.num', amt(d.total_closing, true)))));
    },
    extra: () => h('span', 'Valuation: Weighted average cost'),
    export: (d) => [['Item', 'Group', 'Opening Qty', 'Opening Value', 'Inward Qty', 'Inward Value', 'Outward Qty', 'Outward Value (cost)', 'Closing Qty', 'Closing Rate', 'Closing Value'],
      ...d.rows.map((r) => [r.name, r.group, r.opening_qty, r.opening_value, r.inward_qty, r.inward_value, r.outward_qty, r.outward_value, r.closing_qty, r.closing_rate, r.closing_value])],
  },

  'stock-item': {
    title: (p, d) => `Stock Item: ${d ? d.item.name : ''}`,
    period: (d) => `${fdate(d.from)} to ${fdate(d.to)}`,
    render(d, s) {
      const tb = h('tbody');
      tb.append(h('tr.open', h('td'), h('td.b', 'Opening Balance'), h('td'), h('td'), h('td'), h('td'), h('td.num', qty(d.opening_qty, d.item.unit))));
      for (const r of d.rows) {
        s.row(tb, [h('td.nowrap', fdate(r.date)), h('td', r.particulars || ''), h('td', r.voucher_type), h('td', r.number),
          h('td.num', qty(r.inward_qty, d.item.unit)), h('td.num', qty(r.outward_qty, d.item.unit)), h('td.num', qty(r.closing_qty, d.item.unit))], openVoucher(r.voucher_id));
      }
      return h('table.rep',
        h('thead', h('tr', h('th', 'Date'), h('th', 'Particulars'), h('th', 'Vch Type'), h('th', 'Vch No.'), h('th.num', 'Inwards'), h('th.num', 'Outwards'), h('th.num', 'Closing Qty'))),
        tb, h('tfoot', h('tr', h('td'), h('td', `Closing Balance (value ${amt(d.closing_value, true)})`), h('td'), h('td'), h('td'), h('td'), h('td.num', qty(d.closing_qty, d.item.unit)))));
    },
    export: (d) => [['Date', 'Particulars', 'Type', 'No', 'In', 'Out', 'Closing'], ...d.rows.map((r) => [r.date, r.particulars, r.voucher_type, r.number, r.inward_qty, r.outward_qty, r.closing_qty])],
  },

  'godown-summary': {
    title: () => 'Godown Summary',
    period: (d) => `as at ${fdate(d.as_of)}`,
    render(d, s) {
      const tb = h('tbody');
      for (const g of d.godowns) {
        tb.append(h('tr.grp', h('td.b', { colSpan: 2 }, g.name)));
        for (const it of g.items) s.row(tb, [h('td.ind', it.item), h('td.num', qty(it.qty, it.unit))]);
      }
      return h('table.rep', h('thead', h('tr', h('th', 'Godown / Item'), h('th.num', 'Quantity'))), tb);
    },
    export: (d) => [['Godown', 'Item', 'Qty'], ...d.godowns.flatMap((g) => g.items.map((i) => [g.name, i.item, i.qty]))],
  },

  outstanding: {
    title: (p) => (p.kind === 'payable' ? 'Bills Payable' : 'Bills Receivable'),
    period: (d) => `as at ${fdate(d.as_of)}`,
    render(d, s) {
      const tb = h('tbody');
      for (const p of d.parties) {
        s.row(tb, [h('td.b', { colSpan: 3 }, p.name), h('td.num.b', drcr(d.kind === 'receivable' ? p.closing : -p.closing)), h('td'), h('td')],
          () => app.go('report', { name: 'ledger', ledger_id: p.ledger_id }));
        for (const b of p.bills) {
          tb.append(h('tr', h('td.ind.nowrap', fdate(b.date)), h('td', b.name), h('td'), h('td.num', amt(b.pending, true)),
            h('td.nowrap', fdate(b.due_date) || '—'), h(`td.num${b.overdue_days > 0 ? '.warn' : ''}`, b.overdue_days ? `${b.overdue_days} days` : '')));
        }
        if (p.on_account) tb.append(h('tr', h('td.ind'), h('td.muted', 'On Account / Opening'), h('td'), h('td.num', amt(p.on_account, true)), h('td'), h('td')));
      }
      if (!d.parties.length) tb.append(h('tr', h('td.empty', { colSpan: 6 }, 'Nothing outstanding')));
      return h('table.rep',
        h('thead', h('tr', h('th', 'Date'), h('th', 'Ref. No.'), h('th'), h('th.num', 'Pending Amount'), h('th', 'Due on'), h('th.num', 'Overdue by'))),
        tb, h('tfoot', h('tr', h('td', 'Grand Total'), h('td'), h('td'), h('td.num', amt(d.total, true)), h('td'), h('td'))));
    },
    export: (d) => [['Party', 'Bill', 'Date', 'Pending', 'Due', 'Overdue days'], ...d.parties.flatMap((p) => [...p.bills.map((b) => [p.name, b.name, b.date, b.pending, b.due_date, b.overdue_days]), ...(p.on_account ? [[p.name, 'On Account', '', p.on_account, '', '']] : [])])],
  },

  gst: {
    title: () => 'GST Returns — GSTR-3B & GSTR-1',
    period: (d) => `${fdate(d.from)} to ${fdate(d.to)}`,
    render(d, s) {
      const sm = d.summary;
      const heads = ['taxable', 'igst', 'cgst', 'sgst', 'cess'];
      const line = (label, o, cls = '') => h(`tr${cls}`, h('td', label), ...heads.map((k) => h('td.num', amt(o[k], true))));
      const summary = h('table.rep', h('thead', h('tr', h('th', 'GSTR-3B Summary'), h('th.num', 'Taxable Value'), h('th.num', 'IGST'), h('th.num', 'CGST'), h('th.num', 'SGST/UTGST'), h('th.num', 'Cess'))),
        h('tbody', line('3.1 Outward taxable supplies (net of credit notes)', sm.outward), line('4. Eligible ITC (net of debit notes)', sm.inward),
          h('tr.b', h('td', 'Net tax payable / (credit carried forward)'), h('td'), ...['igst', 'cgst', 'sgst', 'cess'].map((k) => h(`td.num${sm.net_payable[k] < 0 ? '.pos' : ''}`, amt(sm.net_payable[k], true))))),
        h('tfoot', h('tr', h('td', 'Total net GST liability'), h('td', { colSpan: 5 }, h('span.num', amt(sm.net_payable.total, true))))));
      const reg = (title, rows) => {
        const tb = h('tbody');
        for (const r of rows) {
          s.row(tb, [h('td.nowrap', fdate(r.date)), h('td', r.voucher_type, r.is_return ? h('span.tag', 'return') : null), h('td', r.number), h('td', r.party || ''), h('td', r.gstin || ''), h('td', r.category),
            h('td.num', amt(r.taxable, true)), h('td.num', amt(r.igst)), h('td.num', amt(r.cgst)), h('td.num', amt(r.sgst)), h('td.num', amt(r.total, true))], openVoucher(r.voucher_id));
        }
        if (!rows.length) tb.append(h('tr', h('td.empty', { colSpan: 11 }, 'No vouchers')));
        return h('div', h('div.sec-head', title), h('table.rep.wide', h('thead', h('tr', ...['Date', 'Type', 'No.', 'Party', 'GSTIN', 'B2B/B2C', 'Taxable', 'IGST', 'CGST', 'SGST', 'Invoice Value'].map((t, i) => h(i > 5 ? 'th.num' : 'th', t)))), tb));
      };
      const hsn = h('div', h('div.sec-head', 'HSN/SAC Summary (outward)'), h('table.rep', h('thead', h('tr', h('th', 'HSN/SAC'), h('th', 'Description'), h('th.num', 'Total Qty'), h('th.num', 'Taxable Value'))),
        h('tbody', d.hsn_summary.map((x) => h('tr', h('td', x.hsn), h('td', x.description), h('td.num', qty(x.qty)), h('td.num', amt(x.taxable, true)))))));
      return h('div.stack', summary, reg('GSTR-1 — Outward supplies', d.outward), reg('Inward supplies (Purchase register for ITC)', d.inward), hsn);
    },
    export: (d) => [['Direction', 'Date', 'Type', 'No', 'Party', 'GSTIN', 'Category', 'Taxable', 'IGST', 'CGST', 'SGST', 'Total'],
      ...d.outward.map((r) => ['Outward', r.date, r.voucher_type, r.number, r.party, r.gstin, r.category, r.taxable, r.igst, r.cgst, r.sgst, r.total]),
      ...d.inward.map((r) => ['Inward', r.date, r.voucher_type, r.number, r.party, r.gstin, r.category, r.taxable, r.igst, r.cgst, r.sgst, r.total])],
  },

  'bank-reconciliation': {
    title: (p, d) => `Bank Reconciliation: ${d ? d.ledger.name : ''}`,
    period: (d) => `as at ${fdate(d.as_of)}`,
    render(d, s) {
      const tb = h('tbody');
      for (const r of d.rows) {
        const inp = h('input.inp.sm.bankdate', { value: r.bank_date ? fdate(r.bank_date) : '', placeholder: 'dd-mm-yy', dataset: { entry: r.entry_id } });
        inp.addEventListener('keydown', async (e) => {
          if (e.key !== 'Enter') return;
          e.stopPropagation();
          e.preventDefault();
          const val = inp.value.trim() ? parseDate(inp.value, r.date) : null;
          if (inp.value.trim() && !val) return toast('Invalid bank date', 'error');
          try {
            await app.api.setBankDate(r.entry_id, val);
            s.keepCursor = true;
            s.moveNext = true;
            app.refresh();
          } catch (err) {
            toast(err.message, 'error');
          }
        });
        s.row(tb, [h('td.nowrap', fdate(r.date)), h('td', r.particulars), h('td', r.voucher_type), h('td', r.instrument_no || ''),
          h('td.nowrap', fdate(r.instrument_date)), h('td', inp), h('td.num', amt(r.debit)), h('td.num', amt(r.credit))], () => {
          inp.focus();
          inp.select();
        }, r.reconciled ? '.done' : '');
      }
      const nr = d.amounts_not_reflected_in_bank;
      return h('div.stack',
        h('table.rep', h('thead', h('tr', h('th', 'Date'), h('th', 'Particulars'), h('th', 'Vch Type'), h('th', 'Inst. No.'), h('th', 'Inst. Date'), h('th', 'Bank Date'), h('th.num', 'Debit'), h('th.num', 'Credit'))), tb),
        h('table.rep.brs-sum', h('tbody',
          h('tr', h('td', 'Balance as per Company Books'), h('td.num.b', drcr(d.balance_as_per_books, true))),
          h('tr', h('td.ind', 'Amounts not reflected in Bank (Dr)'), h('td.num', amt(nr.debit, true))),
          h('tr', h('td.ind', 'Amounts not reflected in Bank (Cr)'), h('td.num', amt(nr.credit, true))),
          h('tr.grand', h('td', 'Balance as per Bank'), h('td.num.b', drcr(d.balance_as_per_bank, true))))),
        h('p.muted', 'Enter on a line to type the date the bank cleared it (blank to un-reconcile).'));
    },
    export: (d) => [['Date', 'Particulars', 'Type', 'Inst No', 'Inst Date', 'Bank Date', 'Debit', 'Credit'], ...d.rows.map((r) => [r.date, r.particulars, r.voucher_type, r.instrument_no, r.instrument_date, r.bank_date, r.debit, r.credit]),
      [], ['Balance as per books', d.balance_as_per_books], ['Balance as per bank', d.balance_as_per_bank]],
  },

  'cheque-register': {
    title: () => 'Cheque Register',
    period: (d) => `${fdate(d.from)} to ${fdate(d.to)}`,
    render(d, s) {
      const tb = h('tbody');
      for (const r of d.rows) {
        s.row(tb, [h('td', r.bank), h('td.nowrap', fdate(r.date)), h('td', r.voucher_type), h('td', r.instrument_no), h('td.nowrap', fdate(r.instrument_date)), h('td', r.party),
          h('td.num', amt(r.issued)), h('td.num', amt(r.received)), h('td', h(`span.tag.${r.status === 'Cleared' ? 'create' : 'alter'}`, r.status))], openVoucher(r.voucher_id));
      }
      if (!d.rows.length) tb.append(h('tr', h('td.empty', { colSpan: 9 }, 'No cheques or instruments recorded')));
      return h('table.rep', h('thead', h('tr', ...['Bank', 'Date', 'Vch Type', 'Inst. No.', 'Inst. Date', 'Party', 'Issued', 'Received', 'Status'].map((t, i) => h(i === 6 || i === 7 ? 'th.num' : 'th', t)))), tb);
    },
    export: (d) => [['Bank', 'Date', 'Type', 'Inst No', 'Inst Date', 'Party', 'Issued', 'Received', 'Status'], ...d.rows.map((r) => [r.bank, r.date, r.voucher_type, r.instrument_no, r.instrument_date, r.party, r.issued, r.received, r.status])],
  },

  ratios: {
    title: () => 'Ratio Analysis',
    period: (d) => `${fdate(d.from)} to ${fdate(d.to)}`,
    render(d) {
      const g = d.principal_groups;
      const r = d.principal_ratios;
      const row = (l, v, suffix = '') => h('tr', h('td', l), h('td.num', v === null || v === undefined ? '—' : typeof v === 'number' && suffix === '' ? amt(v, true) : `${v}${suffix}`));
      return h('div.two-col',
        h('div.col', h('div.col-head', h('span', 'Principal Groups')), h('table.rep.fin', h('tbody',
          row('Working Capital', g.working_capital), row('Cash-in-Hand', g.cash_in_hand), row('Bank Accounts', g.bank_accounts),
          row('Sundry Debtors', g.sundry_debtors), row('Sundry Creditors', g.sundry_creditors), row('Sales Accounts', g.sales_accounts),
          row('Purchase Accounts', g.purchase_accounts), row('Stock-in-Hand', g.stock_in_hand), row('Net Profit', g.net_profit),
          row('Wkg. Capital Turnover', g.wkg_capital_turnover, ' : 1'), row('Inventory Turnover', g.inventory_turnover, ' : 1')))),
        h('div.col', h('div.col-head', h('span', 'Principal Ratios')), h('table.rep.fin', h('tbody',
          row('Current Ratio', r.current_ratio, ' : 1'), row('Quick Ratio', r.quick_ratio, ' : 1'), row('Debt/Equity Ratio', r.debt_equity_ratio, ' : 1'),
          row('Gross Profit %', r.gross_profit_pct, ' %'), row('Net Profit %', r.net_profit_pct, ' %'), row('Operating Cost %', r.operating_cost_pct, ' %'),
          row('Recv. Turnover in days', r.recv_turnover_days, ' days'), row('Return on Investment %', r.return_on_investment_pct, ' %')))));
    },
    export: (d) => [['Metric', 'Value'], ...Object.entries(d.principal_groups), ...Object.entries(d.principal_ratios)],
  },
};

// ---------------------------------------------------------------- screen

class ReportScreen {
  constructor(params) {
    this.p = params;
    this.def = DEFS[params.name];
    if (!this.def) throw new Error(`Unknown report ${params.name}`);
    this.detailed = false;
    this.cur = 0;
    this.data = null;
  }

  get title() {
    return this.def.title(this.p, this.data);
  }

  buttons() {
    const b = [];
    if (this.def.detail) b.push({ key: 'Alt+F1', label: this.detailed ? 'Condensed' : 'Detailed', action: () => this.toggle() });
    b.push({ key: 'Alt+E', label: 'Export CSV', action: () => app.exportCurrent() });
    b.push({ key: 'Alt+P', label: 'Print', action: () => window.print() });
    return b;
  }

  toggle() {
    this.detailed = !this.detailed;
    this.keepCursor = false;
    this.cur = 0;
    this.redraw();
    app.renderButtons();
  }

  params() {
    const { name, ...rest } = this.p;
    const period = this.def.params ? this.def.params() : { from: app.period.from, to: app.period.to };
    if (name === 'day-book') {
      // Day Book defaults to the current date, like Tally; Alt+F2 widens it.
      Object.assign(period, this.periodSet ? { from: app.period.from, to: app.period.to } : { from: app.vdate, to: app.vdate });
    }
    return { ...period, ...rest };
  }

  async render(root, { resumed } = {}) {
    const name = this.p.name;
    if (resumed) this.keepCursor = true;
    if (name === 'day-book' && this.lastPeriod && (this.lastPeriod.from !== app.period.from || this.lastPeriod.to !== app.period.to)) {
      this.periodSet = true;
    }
    this.lastPeriod = { ...app.period };
    this.data = await app.api.report(name, this.params());
    this.root = root;
    this.draw();
  }

  redraw() {
    if (!this.root) return;
    this.root.replaceChildren();
    this.draw();
    this.select(this.cur);
  }

  draw() {
    this.rows = [];
    const body = this.def.render(this.data, this);
    const c = app.company;
    this.root.append(h('div.rep-wrap',
      h('div.rep-head',
        h('div', h('b', c.name), this.def.extra ? h('div.muted', this.def.extra(this.data)) : null),
        h('div.rep-title', this.title),
        h('div.rep-period', this.def.period(this.data))),
      body));
    const want = this.moveNext ? this.cur + 1 : this.keepCursor ? this.cur : 0;
    this.moveNext = false;
    this.keepCursor = false;
    this.select(want);
  }

  /** Register a selectable report line. */
  row(tbody, cells, open = null, cls = '') {
    const tr = h(`tr.line${cls}${open ? '.drill' : ''}`, cells);
    const i = this.rows.length;
    tr.addEventListener('click', () => this.select(i));
    tr.addEventListener('dblclick', () => open && open());
    this.rows.push({ tr, open });
    tbody.append(tr);
    return tr;
  }

  select(i) {
    if (!this.rows?.length) return;
    this.cur = Math.max(0, Math.min(i, this.rows.length - 1));
    this.rows.forEach((r, j) => r.tr.classList.toggle('sel', j === this.cur));
    this.rows[this.cur].tr.scrollIntoView({ block: 'nearest' });
  }

  focus() {
    document.activeElement?.blur?.();
    if (this.p.name === 'bank-reconciliation' && this.rows?.[this.cur]) {
      const inp = this.rows[this.cur].tr.querySelector('input');
      if (inp && this.cur > 0) inp.focus();
    }
  }

  exportRows() {
    return this.def.export ? this.def.export(this.data) : [];
  }

  onKey(e, name) {
    if (document.activeElement?.classList.contains('bankdate')) {
      if (name === 'Escape') {
        document.activeElement.blur();
        return true;
      }
      if (name === 'ArrowDown' || name === 'ArrowUp') {
        this.select(this.cur + (name === 'ArrowDown' ? 1 : -1));
        this.rows[this.cur].tr.querySelector('input')?.focus();
        return true;
      }
      return false;
    }
    if (name === 'ArrowDown') return this.select(this.cur + 1), true;
    if (name === 'ArrowUp') return this.select(this.cur - 1), true;
    if (name === 'PageDown') return this.select(this.cur + 15), true;
    if (name === 'PageUp') return this.select(this.cur - 15), true;
    if (name === 'Home') return this.select(0), true;
    if (name === 'End') return this.select(this.rows.length - 1), true;
    if (name === 'Enter') {
      const r = this.rows?.[this.cur];
      if (r?.open) r.open();
      return true;
    }
    if (name === 'Alt+F1' && this.def.detail) return this.toggle(), true;
    return false;
  }
}

app.register('report', (params) => new ReportScreen(params));
