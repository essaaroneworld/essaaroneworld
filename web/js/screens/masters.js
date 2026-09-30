// Masters: create / alter / display for groups, ledgers, voucher types and inventory masters.
import { app } from '../app.js';
import { attachPicker, confirmBox, field, menu, navigator, toast, yesNo } from '../ui.js';
import { amt, drcr, fdate, h, num, qty } from '../util.js';
import { statePicker } from './gateway.js';

export const KINDS = {
  groups: { label: 'Group', plural: 'Groups', key: 'G' },
  ledgers: { label: 'Ledger', plural: 'Ledgers', key: 'L' },
  'voucher-types': { label: 'Voucher Type', plural: 'Voucher Types', key: 'V' },
  'stock-groups': { label: 'Stock Group', plural: 'Stock Groups', key: 'S' },
  'stock-items': { label: 'Stock Item', plural: 'Stock Items', key: 'I' },
  units: { label: 'Unit', plural: 'Units', key: 'U' },
  godowns: { label: 'Godown', plural: 'Godowns', key: 'O' },
};

const BASE_TYPES = ['Contra', 'Payment', 'Receipt', 'Journal', 'Sales', 'Purchase', 'Credit Note', 'Debit Note',
  'Memorandum', 'Stock Journal'];

export function groupAncestry(groups, groupId) {
  const byId = Object.fromEntries(groups.map((g) => [g.id, g]));
  const names = [];
  let g = byId[groupId];
  while (g) {
    names.push(g.name);
    g = byId[g.parent_id];
  }
  return names;
}

export function labelOf(kind, row) {
  if (kind === 'units') return row.symbol;
  return row.name;
}

function subOf(kind, row) {
  if (kind === 'ledgers') return row.group_name;
  if (kind === 'groups') return row.parent_name;
  if (kind === 'stock-items') return row.group_name;
  if (kind === 'voucher-types') return row.base_type;
  return '';
}

/** Master picker source usable from any form. */
export function masterSource(kind, filter) {
  return async () => {
    let rows = await app.masters(kind);
    if (filter) rows = rows.filter(filter);
    return rows.map((r) => ({ id: r.id, label: labelOf(kind, r), sub: subOf(kind, r), row: r }));
  };
}

/** Open a master creation form on top of the current screen; resolves with the created picker item. */
export function createInline(kind, preset = {}) {
  return new Promise((resolve) => {
    app.go('master-form', { kind, preset, onDone: (row) => resolve(row ? { id: row.id, label: labelOf(kind, row), row } : null) });
  });
}

// ---------------------------------------------------------------- menus

app.register('master-menu', ({ mode }) => {
  let m;
  const pick = (kind) => () => (mode === 'create' ? app.go('master-form', { kind }) : app.go('pick-master', { kind, then: 'alter' }));
  return {
    title: mode === 'create' ? 'Master Creation' : 'Master Alteration',
    async render(root) {
      m = menu([
        { title: 'Accounting Masters', items: ['groups', 'ledgers', 'voucher-types'].map((k) => ({ label: KINDS[k].label, key: KINDS[k].key, action: pick(k) })) },
        { title: 'Inventory Masters', items: ['stock-groups', 'stock-items', 'units', 'godowns'].map((k) => ({ label: KINDS[k].label, key: KINDS[k].key, action: pick(k) })) },
        { title: 'Company', items: [{ label: 'Company', key: 'C', action: () => app.go('company-form', { company: app.company }) }] },
      ], { title: mode === 'create' ? 'Create' : 'Alter' });
      root.append(h('div.center-col', m.el));
    },
    onKey: (e) => m?.onKey(e),
  };
});

app.register('pick-master', ({ kind, then, filter }) => {
  let input;
  const bankOnly = (r) => r.is_cash_bank && r.group_name !== 'Cash-in-Hand';
  const title = then === 'alter' ? `Select ${KINDS[kind].label} to alter`
    : then === 'brs' ? 'Select Bank for Reconciliation' : `Select ${KINDS[kind].label}`;
  const openItem = (it) => {
    const id = it.id;
    const routes = {
      alter: () => app.replace('master-form', { kind, id }),
      'ledger-report': () => app.replace('report', { name: 'ledger', ledger_id: id }),
      'group-report': () => app.replace('report', { name: 'group-summary', group_id: id }),
      'item-report': () => app.replace('report', { name: 'stock-item', item_id: id }),
      brs: () => app.replace('report', { name: 'bank-reconciliation', ledger_id: id }),
    };
    routes[then]();
  };
  return {
    title,
    async render(root) {
      input = h('input.inp.big', { placeholder: `Type ${KINDS[kind].label.toLowerCase()} name…`, dataset: { nav: '' } });
      attachPicker(input, {
        title: `List of ${KINDS[kind].plural}`,
        required: true,
        source: masterSource(kind, filter === 'bank' ? bankOnly : null),
        onPick: openItem,
        create: then === 'alter' ? undefined : undefined,
      });
      root.append(h('div.center-col', h('div.form-card', h('div.fsec', title), input)));
    },
    focus: () => input.focus(),
    onKey: (e) => input._picker.onKey(e),
  };
});

// ---------------------------------------------------------------- master form

app.register('master-form', ({ kind, id, preset = {}, onDone }) => {
  const spec = KINDS[kind];
  let nav;
  let collect;
  let done = false;
  let dirty = false;
  const finish = (row) => {
    done = true;
    app.stack.pop();
    if (onDone) onDone(row);
    app.show(true);
  };
  const save = async () => {
    let body;
    try {
      body = collect();
    } catch (e) {
      return toast(e.message, 'error');
    }
    if (!(await confirmBox('Accept?'))) return;
    try {
      const row = id ? await app.api.update(kind, id, body) : await app.api.create(kind, body);
      app.invalidate(kind, 'ledgers', 'groups');
      toast(`${spec.label} '${labelOf(kind, row)}' ${id ? 'altered' : 'created'}`);
      if (id || onDone) finish(row);
      else app.refresh(); // Tally keeps the creation screen open for the next master
    } catch (e) {
      toast(e.message, 'error');
    }
  };
  const remove = async () => {
    if (!id) return;
    if (!(await confirmBox(`Delete ${spec.label}?`))) return;
    try {
      await app.api.remove(kind, id);
      app.invalidate(kind, 'ledgers', 'groups');
      toast(`${spec.label} deleted`);
      finish(null);
    } catch (e) {
      toast(e.message, 'error');
    }
  };
  return {
    title: `${spec.label} ${id ? 'Alteration' : 'Creation'}`,
    beforeLeave: async () => {
      if (!done && dirty && !(await confirmBox('Quit?', 'The master has not been saved.'))) return false;
      if (!done && onDone) onDone(null);
      return true;
    },
    buttons: () => [
      { key: 'Ctrl+A', label: 'Accept', action: save },
      ...(id ? [{ key: 'Alt+D', label: 'Delete', action: remove }] : []),
    ],
    async render(root) {
      dirty = false;
      const row = id ? await app.api.get(kind, id) : { ...preset };
      const builder = FORMS[kind];
      const { el, get } = await builder(row, !!id);
      collect = get;
      root.append(h('div.form-card', el));
      root.addEventListener('input', () => (dirty = true));
      nav = navigator(root, save);
    },
    onKey(e, name) {
      if (name === 'Ctrl+A') return save(), true;
      if (name === 'Alt+D') return remove(), true;
      return nav?.(e);
    },
  };
});

const txt = (value = '') => h('input.inp', { value: value ?? '', dataset: { nav: '' } });

function picked(input, label) {
  if (!input.dataset.id && input.value.trim()) throw new Error(`Select a valid ${label} from the list`);
  return input.dataset.id ? Number(input.dataset.id) || input.dataset.id : null;
}

function presetPicker(input, id, label) {
  if (id !== undefined && id !== null) {
    input.value = label;
    input.dataset.id = id;
    input.dataset.label = label;
  }
}

const FORMS = {
  async groups(row, isAlter) {
    const groups = await app.masters('groups');
    const name = txt(row.name);
    const under = txt();
    const nature = h('select.inp', { dataset: { nav: '' } }, ['Assets', 'Liabilities', 'Income', 'Expenses'].map((n) => h('option', n)));
    nature.value = row.nature || 'Assets';
    const gp = yesNo(row.affects_gross_profit);
    attachPicker(under, {
      title: 'List of Groups',
      source: async () => [{ id: '', label: 'Primary', sub: '' }, ...(await masterSource('groups', (g) => g.id !== row.id)())],
    });
    const parent = groups.find((g) => g.id === row.parent_id);
    presetPicker(under, parent ? parent.id : '', parent ? parent.name : 'Primary');
    const primaryBox = h('div', field('Nature of group', nature), field('Affects gross profit', gp));
    const sync = () => primaryBox.classList.toggle('hidden', !!under.dataset.id);
    under.addEventListener('picked', sync);
    sync();
    if (row.is_predefined) under.disabled = true;
    return {
      el: h('div.fcol', field('Name', name), field('Under', under, { hint: row.is_predefined ? 'predefined' : '' }), primaryBox),
      get: () => ({ name: name.value, parent_id: under.dataset.id ? Number(under.dataset.id) : null, nature: nature.value, affects_gross_profit: gp.value === 'yes' }),
    };
  },

  async ledgers(row) {
    const groups = await app.masters('groups');
    const name = txt(row.name);
    const under = txt();
    attachPicker(under, { title: 'List of Groups', source: masterSource('groups'), required: true });
    const g = groups.find((x) => x.id === row.group_id);
    if (g) presetPicker(under, g.id, g.name);
    const ob = h('input.inp.num', { value: row.opening_balance ? row.opening_balance : '', dataset: { nav: '' } });
    const side = h('select.inp.side', { dataset: { nav: '' } }, h('option', 'Dr'), h('option', 'Cr'));
    side.value = row.opening_side || 'Dr';
    const billWise = yesNo(row.bill_wise);
    const creditDays = txt(row.credit_days);
    const address = txt(row.address);
    const state = txt(row.state);
    statePicker(state);
    const gstin = txt(row.gstin);
    const pan = txt(row.pan);
    const gstType = h('select.inp', { dataset: { nav: '' } }, ['', 'CGST', 'SGST', 'IGST', 'CESS'].map((t) => h('option', { value: t }, t || 'Not applicable')));
    gstType.value = row.gst_type || '';
    const bankName = txt(row.bank_name);
    const accountNo = txt(row.account_no);
    const ifsc = txt(row.ifsc);
    const party = h('div', h('div.fsec', 'Mailing & Statutory Details'), field('Maintain balances bill-by-bill', billWise),
      field('Default credit period (days)', creditDays), field('Address', address), field('State', state),
      field('GSTIN/UIN', gstin), field('PAN', pan));
    const bank = h('div', h('div.fsec', 'Bank Account Details'), field('Bank name', bankName), field('A/c No.', accountNo), field('IFS Code', ifsc));
    const tax = h('div', h('div.fsec', 'Statutory'), field('Type of duty/tax', gstType));
    const sync = () => {
      const anc = under.dataset.id ? groupAncestry(groups, Number(under.dataset.id)) : [];
      party.classList.toggle('hidden', !anc.some((n) => n === 'Sundry Debtors' || n === 'Sundry Creditors'));
      bank.classList.toggle('hidden', !anc.some((n) => n === 'Bank Accounts' || n === 'Bank OD A/c'));
      tax.classList.toggle('hidden', !anc.includes('Duties & Taxes'));
      if (!row.id) {
        const nat = groups.find((x) => x.id === Number(under.dataset.id))?.nature;
        side.value = nat === 'Assets' || nat === 'Expenses' ? 'Dr' : 'Cr';
        if (!party.classList.contains('hidden')) billWise.value = 'yes';
      }
    };
    under.addEventListener('picked', sync);
    sync();
    if (row.is_predefined && row.name === 'Profit & Loss A/c') under.disabled = true;
    return {
      el: h('div.form-grid',
        h('div.fcol', field('Name', name), field('Under', under),
          h('div.fsec', 'Opening Balance'), field(`Opening balance (on ${fdate(app.company.books_from)})`, h('span.inline', ob, side))),
        h('div.fcol', party, bank, tax)),
      get: () => {
        if (!under.disabled) picked(under, 'group');
        const n = num(ob.value);
        if (Number.isNaN(n)) throw new Error('Invalid opening balance');
        return {
          name: name.value, group_id: Number(under.dataset.id) || row.group_id, opening_balance: n, opening_side: side.value,
          bill_wise: billWise.value === 'yes', credit_days: creditDays.value, address: address.value, state: state.value,
          gstin: gstin.value, pan: pan.value, gst_type: gstType.value, bank_name: bankName.value,
          account_no: accountNo.value, ifsc: ifsc.value,
        };
      },
    };
  },

  async 'voucher-types'(row, isAlter) {
    const name = txt(row.name);
    const base = h('select.inp', { dataset: { nav: '' }, disabled: isAlter }, BASE_TYPES.map((b) => h('option', b)));
    base.value = row.base_type || 'Payment';
    const abbr = txt(row.abbreviation);
    return {
      el: h('div.fcol', field('Name', name), field('Select type of voucher', base), field('Abbreviation', abbr),
        field('Method of voucher numbering', h('span.static', 'Automatic'))),
      get: () => ({ name: name.value, base_type: base.value, abbreviation: abbr.value }),
    };
  },

  async 'stock-groups'(row) {
    const name = txt(row.name);
    const under = txt();
    attachPicker(under, { title: 'List of Stock Groups', source: async () => [{ id: '', label: 'Primary' }, ...(await masterSource('stock-groups', (g) => g.id !== row.id)())] });
    presetPicker(under, row.parent_id || '', row.parent_name || 'Primary');
    return {
      el: h('div.fcol', field('Name', name), field('Under', under)),
      get: () => ({ name: name.value, parent_id: under.dataset.id ? Number(under.dataset.id) : null }),
    };
  },

  async 'stock-items'(row) {
    const units = await app.masters('units');
    const name = txt(row.name);
    const under = txt();
    attachPicker(under, { title: 'List of Stock Groups', source: async () => [{ id: '', label: 'Primary' }, ...(await masterSource('stock-groups')())] });
    presetPicker(under, row.group_id || '', row.group_name || 'Primary');
    const unit = txt();
    attachPicker(unit, { title: 'List of Units', source: masterSource('units'), required: true,
      create: { label: 'Create Unit', run: () => createInline('units') } });
    const u = units.find((x) => x.id === row.unit_id) || units.find((x) => x.symbol === 'Nos');
    if (u) presetPicker(unit, u.id, u.symbol);
    const hsn = txt(row.hsn);
    const rate = h('input.inp.num', { value: row.gst_rate ?? 18, dataset: { nav: '' } });
    const oq = h('input.inp.num', { value: row.opening_qty || '', dataset: { nav: '' } });
    const orate = h('input.inp.num', { value: row.opening_rate || '', dataset: { nav: '' } });
    const oval = h('span.static.num');
    const upd = () => (oval.textContent = amt(num(oq.value) * num(orate.value), true));
    oq.addEventListener('input', upd);
    orate.addEventListener('input', upd);
    upd();
    return {
      el: h('div.form-grid',
        h('div.fcol', field('Name', name), field('Under', under), field('Units', unit),
          h('div.fsec', 'Statutory Details'), field('HSN/SAC', hsn), field('GST rate (%)', rate)),
        h('div.fcol', h('div.fsec', 'Opening Balance (Main Location)'), field('Quantity', oq), field('Rate', orate), field('Value', oval))),
      get: () => ({
        name: name.value, group_id: under.dataset.id ? Number(under.dataset.id) : null, unit_id: picked(unit, 'unit'),
        hsn: hsn.value, gst_rate: num(rate.value), opening_qty: num(oq.value), opening_rate: num(orate.value),
      }),
    };
  },

  async units(row) {
    const symbol = txt(row.symbol);
    const formal = txt(row.formal_name);
    const dec = h('input.inp.num', { value: row.decimals ?? 0, dataset: { nav: '' } });
    return {
      el: h('div.fcol', field('Type', h('span.static', 'Simple')), field('Symbol', symbol), field('Formal name', formal),
        field('Number of decimal places', dec)),
      get: () => ({ symbol: symbol.value, formal_name: formal.value, decimals: num(dec.value) }),
    };
  },

  async godowns(row) {
    const name = txt(row.name);
    const under = txt();
    attachPicker(under, { title: 'List of Godowns', source: async () => [{ id: '', label: 'Primary' }, ...(await masterSource('godowns', (g) => g.id !== row.id)())] });
    presetPicker(under, row.parent_id || '', row.parent_name || 'Primary');
    return {
      el: h('div.fcol', field('Name', name), field('Under', under)),
      get: () => ({ name: name.value, parent_id: under.dataset.id ? Number(under.dataset.id) : null }),
    };
  },
};

// ---------------------------------------------------------------- chart of accounts

app.register('chart', ({ kind = 'groups' } = {}) => {
  let rows = [];
  let cur = 0;
  let table;
  const kinds = Object.keys(KINDS);
  const cols = {
    groups: [['Name', (r) => r.name], ['Under', (r) => r.parent_name], ['Nature', (r) => r.nature]],
    ledgers: [['Name', (r) => r.name], ['Under', (r) => r.group_name], ['Opening Balance', (r) => (r.opening_balance ? `${amt(r.opening_balance)} ${r.opening_side}` : ''), 'num']],
    'voucher-types': [['Name', (r) => r.name], ['Type of Voucher', (r) => r.base_type], ['Abbr.', (r) => r.abbreviation]],
    'stock-groups': [['Name', (r) => r.name], ['Under', (r) => r.parent_name]],
    'stock-items': [['Name', (r) => r.name], ['Under', (r) => r.group_name], ['Unit', (r) => r.unit], ['HSN', (r) => r.hsn || ''],
      ['GST %', (r) => r.gst_rate, 'num'], ['Opening Qty', (r) => qty(r.opening_qty), 'num'], ['Opening Value', (r) => amt(r.opening_value), 'num']],
    units: [['Symbol', (r) => r.symbol], ['Formal Name', (r) => r.formal_name], ['Decimals', (r) => r.decimals, 'num']],
    godowns: [['Name', (r) => r.name], ['Under', (r) => r.parent_name]],
  };
  const sel = (i) => {
    cur = Math.max(0, Math.min(i, rows.length - 1));
    table?.querySelectorAll('tbody tr').forEach((tr, j) => tr.classList.toggle('sel', j === cur));
    table?.querySelectorAll('tbody tr')[cur]?.scrollIntoView({ block: 'nearest' });
  };
  const open = () => rows[cur] && app.go('master-form', { kind, id: rows[cur].id });
  const screen = {
    title: `Chart of Accounts — ${KINDS[kind].plural}`,
    buttons: () => kinds.map((k, i) => ({ key: `Ctrl+${i + 1}`, label: KINDS[k].plural, action: () => app.replace('chart', { kind: k }) }))
      .concat([{ key: 'Alt+C', label: `Create ${KINDS[kind].label}`, action: () => app.go('master-form', { kind }) }]),
    async render(root) {
      app.invalidate(kind);
      rows = await app.masters(kind);
      const c = cols[kind];
      table = h('table.rep',
        h('thead', h('tr', c.map(([t, , cls]) => h(`th${cls ? '.' + cls : ''}`, t)))),
        h('tbody', rows.map((r, i) => h('tr', { onclick: () => sel(i), ondblclick: open }, c.map(([, f, cls]) => h(`td${cls ? '.' + cls : ''}`, f(r)))))));
      root.append(h('div.rep-wrap', h('div.rep-head', h('b', app.company.name), h('span', `${rows.length} ${KINDS[kind].plural}`)), table));
      sel(cur);
    },
    exportRows: () => [cols[kind].map((c) => c[0]), ...rows.map((r) => cols[kind].map(([, f]) => f(r)))],
    onKey(e, name) {
      if (name === 'ArrowDown') return sel(cur + 1), true;
      if (name === 'ArrowUp') return sel(cur - 1), true;
      if (name === 'Enter') return open(), true;
      if (name === 'Alt+C') return app.go('master-form', { kind }), true;
      const m = /^Ctrl\+(\d)$/.exec(name);
      if (m && kinds[m[1] - 1]) return app.replace('chart', { kind: kinds[m[1] - 1] }), true;
      return false;
    },
  };
  return screen;
});

// ---------------------------------------------------------------- edit log

app.register('edit-log', () => {
  let rows = [];
  return {
    title: 'Edit Log (Audit Trail)',
    async render(root) {
      rows = await app.api.editLog({ limit: 500 });
      root.append(h('div.rep-wrap',
        h('div.rep-head', h('b', app.company.name), h('span', 'Every creation, alteration and deletion is recorded and cannot be disabled.')),
        h('table.rep', h('thead', h('tr', h('th', 'Date & Time (UTC)'), h('th', 'Action'), h('th', 'Master / Voucher'), h('th', 'Details'))),
          h('tbody', rows.map((r) => h('tr', h('td.nowrap', r.ts), h('td', h(`span.tag.${r.action}`, r.action)), h('td', r.entity.replace('_', ' ')), h('td', r.summary)))))));
    },
    exportRows: () => [['Timestamp', 'Action', 'Entity', 'Entity ID', 'Summary'], ...rows.map((r) => [r.ts, r.action, r.entity, r.entity_id, r.summary])],
  };
});

export { drcr };
