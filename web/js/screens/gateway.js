// Company selection, company creation/alteration, Gateway of Mudra, Go To and Help.
import { api } from '../api.js';
import { app } from '../app.js';
import { attachPicker, confirmBox, field, menu, navigator, toast, yesNo } from '../ui.js';
import { amt, fdate, h, longDate, parseDate } from '../util.js';

export const STATES = [
  'Andaman and Nicobar Islands', 'Andhra Pradesh', 'Arunachal Pradesh', 'Assam', 'Bihar', 'Chandigarh',
  'Chhattisgarh', 'Dadra and Nagar Haveli and Daman and Diu', 'Delhi', 'Goa', 'Gujarat', 'Haryana',
  'Himachal Pradesh', 'Jammu and Kashmir', 'Jharkhand', 'Karnataka', 'Kerala', 'Ladakh', 'Lakshadweep',
  'Madhya Pradesh', 'Maharashtra', 'Manipur', 'Meghalaya', 'Mizoram', 'Nagaland', 'Odisha', 'Puducherry',
  'Punjab', 'Rajasthan', 'Sikkim', 'Tamil Nadu', 'Telangana', 'Tripura', 'Uttar Pradesh', 'Uttarakhand',
  'West Bengal',
];

export function statePicker(input) {
  return attachPicker(input, { title: 'List of States', source: () => STATES.map((s) => ({ id: s, label: s })) });
}

// ---------------------------------------------------------------- companies

app.register('companies', () => {
  let m;
  return {
    title: 'Select Company',
    buttons: () => [
      { key: 'Alt+C', label: 'Create Company', action: () => app.go('company-form') },
      { key: 'Alt+S', label: 'Sample Company', action: loadDemo },
    ],
    async render(root) {
      const companies = await api.get('/companies');
      const items = companies.map((c) => ({
        label: c.name, hint: `FY from ${fdate(c.fy_start)}`, action: () => app.openCompany(c),
      }));
      m = menu([
        { title: companies.length ? 'List of Companies' : '', items },
        { title: 'Actions', items: [
          { label: 'Create Company', key: 'C', action: () => app.go('company-form') },
          { label: 'Load Sample Company (Mudra Traders)', key: 'S', action: loadDemo },
        ] },
      ], { title: 'Select Company' });
      root.append(h('div.center-col',
        h('div.welcome',
          h('div.logo-big', 'MUDRA'),
          h('div.tagline', 'Finance and Banking Software'),
          h('p.muted', 'Double-entry accounting · Inventory · GST · Banking · Statutory audit trail')),
        m.el));
    },
    onKey: (e) => m?.onKey(e),
  };
});

async function loadDemo() {
  try {
    const c = await api.post('/demo', {});
    toast(`Opened sample company ${c.name}`);
    app.openCompany(c);
  } catch (e) {
    toast(e.message, 'error');
  }
}

// ---------------------------------------------------------------- company form

app.register('company-form', ({ company } = {}) => {
  let nav;
  let inputs;
  const save = async () => {
    const v = {};
    for (const [k, inp] of Object.entries(inputs)) {
      if (inp.classList.contains('yn')) v[k] = inp.value === 'yes';
      else if (k === 'fy_start' || k === 'books_from') {
        v[k] = parseDate(inp.value);
        if (!v[k]) {
          inp.focus();
          return toast(`Invalid date: ${inp.value || '(blank)'}`, 'error');
        }
      } else v[k] = inp.value.trim();
    }
    if (!v.name) return toast('Company name is required', 'error');
    if (!(await confirmBox('Accept?'))) return;
    try {
      const saved = company ? await api.put(`/companies/${company.id}`, v) : await api.post('/companies', v);
      toast(`Company ${saved.name} ${company ? 'altered' : 'created'}`);
      if (company) {
        app.company = saved;
        app.renderCompanyBar();
        app.back();
      } else app.openCompany(saved);
    } catch (e) {
      toast(e.message, 'error');
    }
  };
  return {
    title: company ? 'Company Alteration' : 'Company Creation',
    buttons: () => [
      { key: 'Ctrl+A', label: 'Accept', action: save },
      ...(company ? [{ key: 'Alt+D', label: 'Delete', action: () => deleteCompany(company) }] : []),
    ],
    async render(root) {
      const c = company || {};
      const t = (k, v = '') => h('input.inp', { value: c[k] ?? v, dataset: { nav: '' } });
      const year = new Date().getMonth() >= 3 ? new Date().getFullYear() : new Date().getFullYear() - 1;
      inputs = {
        name: t('name'), mailing_name: t('mailing_name'), address: t('address'), state: t('state'),
        pincode: t('pincode'), phone: t('phone'), email: t('email'), gstin: t('gstin'), pan: t('pan'),
        fy_start: h('input.inp', { value: fdate(c.fy_start || `${year}-04-01`), dataset: { nav: '' } }),
        books_from: h('input.inp', { value: fdate(c.books_from || c.fy_start || `${year}-04-01`), dataset: { nav: '' } }),
        inventory_enabled: yesNo(c.inventory_enabled ?? true),
        gst_enabled: yesNo(c.gst_enabled ?? true),
      };
      statePicker(inputs.state);
      inputs.name.addEventListener('change', () => {
        if (!inputs.mailing_name.value) inputs.mailing_name.value = inputs.name.value;
      });
      root.append(h('div.form-card',
        h('div.form-grid',
          h('div.fcol',
            field('Company name', inputs.name), field('Mailing name', inputs.mailing_name),
            field('Address', inputs.address), field('State', inputs.state), field('Country', h('span.static', 'India')),
            field('Pincode', inputs.pincode), field('Telephone', inputs.phone), field('E-mail', inputs.email)),
          h('div.fcol',
            h('div.fsec', 'Books & Features'),
            field('Financial year beginning from', inputs.fy_start),
            field('Books beginning from', inputs.books_from),
            field('Base currency', h('span.static', '₹ Indian Rupees')),
            field('Maintain inventory', inputs.inventory_enabled),
            field('Enable GST', inputs.gst_enabled),
            h('div.fsec', 'Statutory'),
            field('GSTIN/UIN', inputs.gstin), field('PAN', inputs.pan)))));
      nav = navigator(root, save);
    },
    onKey(e, name) {
      if (name === 'Ctrl+A') return save(), true;
      return nav?.(e);
    },
  };
});

async function deleteCompany(company) {
  if (!(await confirmBox(`Delete company ${company.name}?`, 'All masters and vouchers will be permanently removed.'))) return;
  try {
    await api.del(`/companies/${company.id}`);
    toast(`Deleted ${company.name}`);
    app.closeCompany();
  } catch (e) {
    toast(e.message, 'error');
  }
}

// ---------------------------------------------------------------- gateway

app.register('gateway', () => {
  let m;
  return {
    title: 'Gateway of Mudra',
    buttons: () => [
      { key: 'Alt+F3', label: 'Alter Company', action: () => app.go('company-form', { company: app.company }) },
    ],
    async render(root) {
      const d = await app.api.report('dashboard', app.period);
      m = menu([
        { title: 'Masters', items: [
          { label: 'Create', key: 'C', action: () => app.go('master-menu', { mode: 'create' }) },
          { label: 'Alter', key: 'A', action: () => app.go('master-menu', { mode: 'alter' }) },
          { label: 'Chart of Accounts', key: 'H', action: () => app.go('chart') },
        ] },
        { title: 'Transactions', items: [
          { label: 'Vouchers', key: 'V', action: () => app.go('voucher', { type: 'Sales' }) },
          { label: 'Day Book', key: 'K', action: () => app.go('report', { name: 'day-book' }) },
        ] },
        { title: 'Utilities', items: [
          { label: 'Banking', key: 'N', action: () => app.go('banking-menu') },
        ] },
        { title: 'Reports', items: [
          { label: 'Balance Sheet', key: 'B', action: () => app.go('report', { name: 'balance-sheet' }) },
          { label: 'Profit & Loss A/c', key: 'P', action: () => app.go('report', { name: 'profit-loss' }) },
          { label: 'Stock Summary', key: 'S', action: () => app.go('report', { name: 'stock-summary' }) },
          { label: 'Ratio Analysis', key: 'R', action: () => app.go('report', { name: 'ratios' }) },
          { label: 'Display More Reports', key: 'D', action: () => app.go('more-reports') },
          { label: 'Dashboard', key: 'Z', action: () => app.go('dashboard') },
        ] },
        { items: [{ label: 'Quit', key: 'Q', action: () => app.back() }] },
      ], { title: 'Gateway of Mudra' });

      const info = (label, value) => h('div.info-row', h('span.info-label', label), h('span.info-value', value));
      const tile = (label, value, cls = '') => h(`div.kpi${cls}`, h('div.kpi-label', label), h('div.kpi-value', value));
      root.append(h('div.gateway',
        h('div.gw-left',
          h('div.gw-panel',
            info('Current Period', `${fdate(app.period.from)} to ${fdate(app.period.to)}`),
            info('Current Date', longDate(app.vdate))),
          h('div.gw-panel',
            h('div.gw-head', h('span', 'Name of Company'), h('span', 'Date of Last Entry')),
            h('div.gw-company', h('b', app.company.name), h('span', d.last_voucher_date ? fdate(d.last_voucher_date) : 'No Vouchers Entered'))),
          h('div.kpis',
            tile('Cash-in-Hand', amt(d.cash, true)),
            tile('Bank Balance', amt(d.bank, true)),
            tile('Receivables', amt(d.receivables, true)),
            tile('Payables', amt(d.payables, true)),
            tile('Sales', amt(d.sales, true)),
            tile('Purchases', amt(d.purchases, true)),
            tile(d.net_profit >= 0 ? 'Net Profit' : 'Net Loss', amt(Math.abs(d.net_profit), true), d.net_profit >= 0 ? '.pos' : '.neg'),
            tile('Closing Stock', amt(d.closing_stock, true)))),
        h('div.gw-right', m.el)));
    },
    onKey: (e) => m?.onKey(e),
  };
});

app.register('more-reports', () => {
  let m;
  const r = (name, params = {}) => () => app.go('report', { name, ...params });
  return {
    title: 'Display More Reports',
    async render(root) {
      m = menu([
        { title: 'Accounting', items: [
          { label: 'Trial Balance', key: 'T', action: r('trial-balance') },
          { label: 'Day Book', key: 'D', action: r('day-book') },
          { label: 'Cash/Bank Book(s)', key: 'C', action: r('cash-bank') },
          { label: 'Ledger', key: 'L', action: () => app.go('pick-master', { kind: 'ledgers', then: 'ledger-report' }) },
          { label: 'Group Summary', key: 'G', action: () => app.go('pick-master', { kind: 'groups', then: 'group-report' }) },
        ] },
        { title: 'Statements of Accounts', items: [
          { label: 'Outstandings — Receivables', key: 'R', action: r('outstanding', { kind: 'receivable' }) },
          { label: 'Outstandings — Payables', key: 'P', action: r('outstanding', { kind: 'payable' }) },
        ] },
        { title: 'Inventory', items: [
          { label: 'Stock Summary', key: 'S', action: r('stock-summary') },
          { label: 'Stock Item Vouchers', key: 'I', action: () => app.go('pick-master', { kind: 'stock-items', then: 'item-report' }) },
          { label: 'Godown Summary', key: 'O', action: r('godown-summary') },
        ] },
        { title: 'Statutory', items: [
          { label: 'GST Returns (GSTR-1 / GSTR-3B)', key: 'X', action: r('gst') },
        ] },
        { title: 'Exception & Audit', items: [
          { label: 'Edit Log (Audit Trail)', key: 'E', action: () => app.go('edit-log') },
        ] },
      ], { title: 'Display More Reports' });
      root.append(h('div.center-col', m.el));
    },
    onKey: (e) => m?.onKey(e),
  };
});

app.register('banking-menu', () => {
  let m;
  return {
    title: 'Banking',
    async render(root) {
      m = menu([{ title: 'Banking', items: [
        { label: 'Bank Reconciliation', key: 'R', action: () => app.go('pick-master', { kind: 'ledgers', then: 'brs', filter: 'bank' }) },
        { label: 'Cash/Bank Book(s)', key: 'C', action: () => app.go('report', { name: 'cash-bank' }) },
        { label: 'Cheque Register', key: 'Q', action: () => app.go('report', { name: 'cheque-register' }) },
      ] }], { title: 'Banking Utilities' });
      root.append(h('div.center-col', m.el));
    },
    onKey: (e) => m?.onKey(e),
  };
});

app.register('dashboard', () => ({
  title: 'Dashboard',
  async render(root) {
    const [d, bs, recv, pay] = await Promise.all([
      app.api.report('dashboard', app.period), app.api.report('ratios', app.period),
      app.api.report('outstanding', { kind: 'receivable', to: app.period.to }),
      app.api.report('outstanding', { kind: 'payable', to: app.period.to }),
    ]);
    const tile = (label, value, cls = '') => h(`div.kpi${cls}`, h('div.kpi-label', label), h('div.kpi-value', value));
    const top = (list) => list.parties.slice().sort((a, b) => b.closing - a.closing).slice(0, 5)
      .map((p) => h('div.info-row', h('span', p.name), h('span.num', amt(p.closing, true))));
    const maxv = Math.max(d.sales, d.purchases, 1);
    const bar = (label, v, cls) => h('div.hbar', h('span.hbar-label', label),
      h('span.hbar-track', h(`span.hbar-fill.${cls}`, { style: { width: `${(Math.abs(v) / maxv) * 100}%` } })),
      h('span.num', amt(v, true)));
    const r = bs.principal_ratios;
    root.append(h('div.dash',
      h('div.kpis.wide',
        tile('Sales', amt(d.sales, true)), tile('Purchases', amt(d.purchases, true)),
        tile('Gross Profit', amt(d.gross_profit, true), d.gross_profit >= 0 ? '.pos' : '.neg'),
        tile('Net Profit', amt(d.net_profit, true), d.net_profit >= 0 ? '.pos' : '.neg'),
        tile('Cash + Bank', amt(d.cash + d.bank, true)), tile('Closing Stock', amt(d.closing_stock, true)),
        tile('Receivables', amt(d.receivables, true)), tile('Payables', amt(d.payables, true))),
      h('div.dash-grid',
        h('div.card', h('div.card-head', 'Trading'), bar('Sales', d.sales, 'a'), bar('Purchases', d.purchases, 'b'),
          bar('Gross Profit', d.gross_profit, 'c')),
        h('div.card', h('div.card-head', 'Key Ratios'),
          ...[['Current Ratio', r.current_ratio], ['Quick Ratio', r.quick_ratio], ['Gross Profit %', r.gross_profit_pct],
            ['Net Profit %', r.net_profit_pct], ['Receivable days', r.recv_turnover_days]]
            .map(([l, v]) => h('div.info-row', h('span', l), h('span.num', v ?? '—')))),
        h('div.card', h('div.card-head', 'Top Receivables'), ...top(recv)),
        h('div.card', h('div.card-head', 'Top Payables'), ...top(pay)))));
  },
}));

// ---------------------------------------------------------------- Go To

const DESTINATIONS = [
  ['Balance Sheet', () => app.go('report', { name: 'balance-sheet' })],
  ['Profit & Loss A/c', () => app.go('report', { name: 'profit-loss' })],
  ['Trial Balance', () => app.go('report', { name: 'trial-balance' })],
  ['Day Book', () => app.go('report', { name: 'day-book' })],
  ['Cash/Bank Books', () => app.go('report', { name: 'cash-bank' })],
  ['Ledger Vouchers', () => app.go('pick-master', { kind: 'ledgers', then: 'ledger-report' })],
  ['Group Summary', () => app.go('pick-master', { kind: 'groups', then: 'group-report' })],
  ['Stock Summary', () => app.go('report', { name: 'stock-summary' })],
  ['Stock Item Vouchers', () => app.go('pick-master', { kind: 'stock-items', then: 'item-report' })],
  ['Godown Summary', () => app.go('report', { name: 'godown-summary' })],
  ['Receivables (Bills Outstanding)', () => app.go('report', { name: 'outstanding', kind: 'receivable' })],
  ['Payables (Bills Outstanding)', () => app.go('report', { name: 'outstanding', kind: 'payable' })],
  ['GST Returns — GSTR-1 / GSTR-3B', () => app.go('report', { name: 'gst' })],
  ['Ratio Analysis', () => app.go('report', { name: 'ratios' })],
  ['Bank Reconciliation', () => app.go('pick-master', { kind: 'ledgers', then: 'brs', filter: 'bank' })],
  ['Cheque Register', () => app.go('report', { name: 'cheque-register' })],
  ['Dashboard', () => app.go('dashboard')],
  ['Edit Log (Audit Trail)', () => app.go('edit-log')],
  ['Chart of Accounts', () => app.go('chart')],
  ['Create Master', () => app.go('master-menu', { mode: 'create' })],
  ['Alter Master', () => app.go('master-menu', { mode: 'alter' })],
  ['Create Ledger', () => app.go('master-form', { kind: 'ledgers' })],
  ['Create Group', () => app.go('master-form', { kind: 'groups' })],
  ['Create Stock Item', () => app.go('master-form', { kind: 'stock-items' })],
  ['Alter Company', () => app.go('company-form', { company: app.company })],
  ['Contra Voucher', () => app.go('voucher', { type: 'Contra' })],
  ['Payment Voucher', () => app.go('voucher', { type: 'Payment' })],
  ['Receipt Voucher', () => app.go('voucher', { type: 'Receipt' })],
  ['Journal Voucher', () => app.go('voucher', { type: 'Journal' })],
  ['Sales Voucher (Invoice)', () => app.go('voucher', { type: 'Sales' })],
  ['Purchase Voucher', () => app.go('voucher', { type: 'Purchase' })],
  ['Credit Note (Sales Return)', () => app.go('voucher', { type: 'Credit Note' })],
  ['Debit Note (Purchase Return)', () => app.go('voucher', { type: 'Debit Note' })],
  ['Stock Journal', () => app.go('voucher', { type: 'Stock Journal' })],
  ['Memorandum Voucher', () => app.go('voucher', { type: 'Memorandum' })],
  ['Help — Keyboard Shortcuts', () => app.go('help')],
];

app.register('goto', () => {
  let input;
  return {
    title: 'Go To',
    async render(root) {
      input = h('input.inp.big', { placeholder: 'Type to search reports, vouchers and masters…', dataset: { nav: '' } });
      attachPicker(input, {
        title: 'Go To',
        required: true,
        source: () => DESTINATIONS.map(([label, fn], i) => ({ id: i, label, fn })),
        onPick: (it) => {
          app.stack.pop();
          it.fn();
        },
      });
      root.append(h('div.center-col', h('div.form-card', h('div.fsec', 'Go To'), input,
        h('p.muted', 'Enter to open · Esc to go back'))));
    },
    focus: () => input.focus(),
    onKey(e) {
      if (input._picker.onKey(e)) return true;
      return false;
    },
  };
});

// ---------------------------------------------------------------- help

app.register('help', () => ({
  title: 'Help — Keyboard Shortcuts',
  async render(root) {
    const rows = [
      ['Navigation', [['Enter', 'Select / next field'], ['Esc', 'Back / close'], ['↑ ↓', 'Move in lists and reports'],
        ['Hot letter', 'Underlined letter in any menu'], ['Alt+G / Ctrl+G', 'Go To any report or voucher'],
        ['Ctrl+Q', 'Gateway of Mudra']]],
      ['Company & period', [['F2', 'Change current (voucher) date'], ['Alt+F2', 'Change reporting period'],
        ['F3 / Alt+K', 'Select company'], ['Alt+F3', 'Alter company (from Gateway)']]],
      ['Vouchers', [['F4', 'Contra'], ['F5', 'Payment'], ['F6', 'Receipt'], ['F7', 'Journal'], ['F8', 'Sales'],
        ['F9', 'Purchase'], ['Ctrl+F8', 'Credit Note'], ['Ctrl+F9', 'Debit Note'], ['Alt+F7', 'Stock Journal'],
        ['F10', 'Memorandum'], ['Ctrl+A', 'Accept (save)'], ['Ctrl+H', 'Item / accounting invoice mode'],
        ['Alt+C', 'Create master from a list'], ['Alt+D', 'Delete voucher / master'], ['D / C', 'Toggle Dr / Cr on a line']]],
      ['Reports', [['Enter', 'Drill down'], ['Alt+F1', 'Detailed / condensed'], ['Alt+E / Ctrl+E', 'Export to CSV'],
        ['Alt+P / Ctrl+P', 'Print'], ['Alt+Y', 'Download database backup']]],
    ];
    root.append(h('div.help-grid', ...rows.map(([t, list]) => h('div.card', h('div.card-head', t),
      ...list.map(([k, d]) => h('div.info-row', h('kbd', k), h('span', d)))))),
    h('p.muted.center', 'Tip: browsers reserve a few keys (e.g. Alt+D, F5). Every shortcut is also clickable in the right-hand button bar.'));
  },
}));
