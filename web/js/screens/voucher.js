// Voucher entry: accounting vouchers (Dr/Cr lines), item invoices with live GST,
// stock journals, bill-wise allocation and bank instrument details.
import { app } from '../app.js';
import { attachPicker, confirmBox, openModal, toast, yesNo } from '../ui.js';
import { amt, debounce, drcr, fdate, h, longDate, num, parseDate, round2 } from '../util.js';
import { createInline, groupAncestry, masterSource } from './masters.js';

const FIRST_SIDE = {
  Payment: 'Dr', Receipt: 'Cr', Contra: 'Dr', Journal: 'Dr', Sales: 'Dr', Purchase: 'Cr',
  'Credit Note': 'Cr', 'Debit Note': 'Dr', Memorandum: 'Dr',
};
const INVOICE_TYPES = ['Sales', 'Purchase', 'Credit Note', 'Debit Note'];
const SALES_LIKE = ['Sales', 'Debit Note'];

app.register('voucher', (params) => new VoucherScreen(params));

class VoucherScreen {
  constructor({ type, id }) {
    this.type = type;
    this.id = id;
    this.node = null;
    this.dirty = false;
    this.saved = false;
  }

  get title() {
    const mode = this.mode === 'invoice' ? ' (Item Invoice)' : this.mode === 'inventory' ? ' (Inventory)' : '';
    return `${this.id ? 'Voucher Alteration' : 'Voucher Creation'}${this.vt ? ' — ' + this.vt.name + mode : ''}`;
  }

  buttons() {
    const b = [{ key: 'Ctrl+A', label: 'Accept', action: () => this.accept() }];
    if (INVOICE_TYPES.includes(this.base) && app.company.inventory_enabled) {
      b.push({ key: 'Ctrl+H', label: this.mode === 'invoice' ? 'As Accounting' : 'As Item Invoice', action: () => this.toggleMode() });
    }
    b.push({ key: 'Alt+T', label: 'Other Vouchers', action: () => app.go('pick-vtype') });
    if (this.id) {
      b.push({ key: 'Alt+D', label: 'Delete', action: () => this.remove() });
      b.push({ key: 'Alt+I', label: 'Print View', action: () => app.go('voucher-print', { id: this.id }) });
    }
    return b;
  }

  switchType(type) {
    if (this.id) return toast('Finish or cancel the alteration first', 'error');
    app.replace('voucher', { type });
  }

  async beforeLeave() {
    if (this.dirty && !this.saved) return confirmBox('Quit?', 'The voucher has not been saved.');
    return true;
  }

  focus() {
    const target = this.lastFocus && this.node.contains(this.lastFocus) ? this.lastFocus : this.firstField();
    target?.focus();
  }

  firstField() {
    return this.node.querySelector('[data-nav]:not([disabled])');
  }

  // ------------------------------------------------------------ render

  async render(root) {
    if (this.node) {
      root.append(this.node);
      return;
    }
    const [vtypes, ledgers, groups] = await Promise.all([
      app.masters('voucher-types'), app.masters('ledgers'), app.masters('groups'),
    ]);
    this.groups = groups;
    this.ledgers = ledgers;
    let v = null;
    if (this.id) {
      v = await app.api.get('vouchers', this.id);
      this.vt = vtypes.find((t) => t.id === v.voucher_type_id);
    } else {
      this.vt = vtypes.find((t) => t.name === this.type) || vtypes.find((t) => t.base_type === this.type);
    }
    if (!this.vt) throw new Error(`Voucher type ${this.type} not found`);
    this.base = this.vt.base_type;
    this.voucher = v;
    const inv = app.company.inventory_enabled;
    this.mode = v ? v.mode : this.base === 'Stock Journal' ? 'inventory' : INVOICE_TYPES.includes(this.base) && inv ? 'invoice' : 'accounting';
    if (this.base === 'Stock Journal' && !inv) throw new Error('Inventory is disabled for this company');
    this.date = v ? v.date : app.vdate;
    if (this.mode === 'invoice') {
      [this.items, this.godowns] = await Promise.all([app.masters('stock-items'), app.masters('godowns')]);
    } else if (this.mode === 'inventory') {
      [this.items, this.godowns] = await Promise.all([app.masters('stock-items'), app.masters('godowns')]);
    }
    this.node = h('div.voucher');
    this.node.addEventListener('input', () => (this.dirty = true));
    this.node.addEventListener('focusin', (e) => (this.lastFocus = e.target));
    this.build(v);
    root.append(this.node);
  }

  build(v) {
    this.node.replaceChildren();
    const dayLabel = h('span.vday', longDate(this.date).split(', ')[1] || '');
    this.dateInput = h('input.inp.vdate', { value: fdate(this.date), dataset: { role: 'date' }, title: 'F2 to change date' });
    this.dateInput.addEventListener('change', () => {
      const d = parseDate(this.dateInput.value, this.date);
      if (!d) {
        toast('Invalid date', 'error');
        this.dateInput.value = fdate(this.date);
        return;
      }
      this.date = d;
      this.dateInput.value = fdate(d);
      dayLabel.textContent = longDate(d).split(', ')[1];
    });
    this.numberInput = h('input.inp.vno', { value: v ? v.number : this.vt.next_number, title: 'Voucher number' });
    const refNav = ['Purchase', 'Debit Note'].includes(this.base) ? { nav: '', role: 'ref' } : { role: 'ref' };
    this.refInput = h('input.inp', { value: v?.reference || '', dataset: refNav,
      placeholder: this.base === 'Purchase' ? 'Supplier invoice no.' : 'Reference' });
    this.node.append(h('div.vhead',
      h('div.vhead-left', h(`div.vtype.t-${this.base.replace(/\s/g, '')}`, this.vt.name), h('span.vlabel', 'No.'), this.numberInput),
      h('div.vhead-mid', h('span.vlabel', this.base === 'Purchase' ? 'Supplier Inv No.' : 'Ref.'), this.refInput),
      h('div.vhead-right', this.dateInput, dayLabel)));

    this.body = h('div.vbody');
    this.node.append(this.body);
    if (this.mode === 'invoice') this.buildInvoice(v);
    else if (this.mode === 'inventory') this.buildStockJournal(v);
    else this.buildAccounting(v);

    this.narration = h('textarea.inp.narr', { rows: 2, dataset: { nav: '', role: 'narration' }, placeholder: 'Narration' });
    this.narration.value = v?.narration || '';
    this.node.append(h('div.vnarr', h('span.vlabel', 'Narration:'), this.narration));
  }

  // ------------------------------------------------------------ helpers

  ancestry(ledger) {
    return ledger ? groupAncestry(this.groups, ledger.group_id) : [];
  }

  isBank(ledger) {
    const a = this.ancestry(ledger);
    return a.includes('Bank Accounts') || a.includes('Bank OD A/c');
  }

  ledgerPicker(input, { filter, onPick, title = 'List of Ledger Accounts', presetGroup } = {}) {
    return attachPicker(input, {
      title,
      required: false,
      source: async () => {
        this.ledgers = await app.masters('ledgers');
        return (await masterSource('ledgers', filter)()).map((it) => ({ ...it, sub: it.row.group_name }));
      },
      onPick,
      create: {
        label: 'Create Ledger',
        run: async (text) => {
          const groups = this.groups;
          const g = presetGroup && groups.find((x) => x.name === presetGroup);
          const res = await createInline('ledgers', { name: text, group_id: g?.id });
          this.ledgers = await app.masters('ledgers');
          return res;
        },
      },
    });
  }

  async showBalance(el, ledgerId) {
    el.textContent = '';
    if (!ledgerId) return;
    try {
      const r = await app.api.report('ledger', { ledger_id: ledgerId, from: app.company.books_from, to: this.date });
      el.textContent = `Cur Bal: ${drcr(r.closing, true)}`;
    } catch {
      /* ignore */
    }
  }

  // ------------------------------------------------------------ accounting mode

  buildAccounting(v) {
    this.rows = [];
    this.rowsEl = h('tbody');
    this.totDr = h('td.num.tot');
    this.totCr = h('td.num.tot');
    this.diffEl = h('span.diff');
    this.body.append(h('table.vtable',
      h('thead', h('tr', h('th.c-side', 'Dr/Cr'), h('th', 'Particulars'), h('th.num.c-amt', 'Debit'), h('th.num.c-amt', 'Credit'))),
      this.rowsEl,
      h('tfoot', h('tr', h('td'), h('td', this.diffEl), this.totDr, this.totCr))));
    if (v && v.entries.length) v.entries.forEach((e) => this.addRow(e.side, e));
    else {
      this.addRow(FIRST_SIDE[this.base] || 'Dr');
    }
    this.updateTotals();
  }

  ledgerFilter(r) {
    const cashBank = (l) => l.is_cash_bank;
    if (this.base === 'Contra') return cashBank;
    if (this.base === 'Journal') return (l) => !l.is_cash_bank;
    if (this.base === 'Payment' && r.side === 'Cr') return cashBank;
    if (this.base === 'Receipt' && r.side === 'Dr') return cashBank;
    return null;
  }

  addRow(side, entry) {
    const r = { side, bills: entry?.bills || [], ledger: null };
    r.sideEl = h('input.inp.side', { value: side, readOnly: true, dataset: { nav: '', role: 'side' }, title: 'Type D or C' });
    r.sideEl.addEventListener('keydown', (e) => {
      const k = e.key.toLowerCase();
      if (k === 'd' || k === 'b') this.setSide(r, 'Dr');
      if (k === 'c' || k === 't') this.setSide(r, 'Cr');
      if (k === ' ') this.setSide(r, r.side === 'Dr' ? 'Cr' : 'Dr');
    });
    r.ledgerEl = h('input.inp.ledger', { dataset: { nav: '', role: 'ledger' }, placeholder: 'Select ledger' });
    r.drEl = h('input.inp.num', { dataset: { nav: '', role: 'amount' } });
    r.crEl = h('input.inp.num', { dataset: { nav: '', role: 'amount' } });
    for (const el of [r.drEl, r.crEl]) el.addEventListener('input', () => this.updateTotals());
    r.balEl = h('div.curbal');
    r.billsEl = h('div.billinfo');
    r.instrNo = h('input.inp.sm', { placeholder: 'Cheque/UTR No.', dataset: { nav: '', role: 'instr_no' } });
    r.instrDate = h('input.inp.sm', { placeholder: 'Inst. date', dataset: { nav: '', role: 'instr_date' } });
    r.instrEl = h('div.instr.hidden', h('span.vlabel', 'Bank Allocation:'), r.instrNo, r.instrDate);
    r.tr = h('tr', h('td.c-side', r.sideEl), h('td', r.ledgerEl, r.balEl, r.instrEl, r.billsEl), h('td.num', r.drEl), h('td.num', r.crEl));
    this.ledgerPicker(r.ledgerEl, {
      filter: (l) => {
        const f = this.ledgerFilter(r);
        return f ? f(l) : true;
      },
      onPick: (it) => this.onLedger(r, it),
    });
    this.rows.push(r);
    this.rowsEl.append(r.tr);
    this.setSide(r, side);
    if (entry) {
      const led = this.ledgers.find((l) => l.id === entry.ledger_id);
      r.ledgerEl._picker.setValue({ id: entry.ledger_id, label: entry.ledger_name });
      r.ledger = led;
      (side === 'Dr' ? r.drEl : r.crEl).value = entry.amount;
      r.instrNo.value = entry.instrument_no || '';
      r.instrDate.value = entry.instrument_date ? fdate(entry.instrument_date) : '';
      r.instrEl.classList.toggle('hidden', !this.isBank(led));
      this.renderBills(r);
      this.showBalance(r.balEl, entry.ledger_id);
    }
    return r;
  }

  setSide(r, side) {
    const val = (r.side === 'Dr' ? r.drEl : r.crEl).value;
    r.side = side;
    r.sideEl.value = side;
    r.drEl.disabled = side !== 'Dr';
    r.crEl.disabled = side !== 'Cr';
    (side === 'Dr' ? r.drEl : r.crEl).value = val;
    (side === 'Dr' ? r.crEl : r.drEl).value = '';
    this.updateTotals();
  }

  amountOf(r) {
    return num((r.side === 'Dr' ? r.drEl : r.crEl).value) || 0;
  }

  totals(except) {
    let dr = 0;
    let cr = 0;
    for (const r of this.rows) {
      if (r === except || !r.ledger) continue;
      if (r.side === 'Dr') dr += this.amountOf(r);
      else cr += this.amountOf(r);
    }
    return { dr: round2(dr), cr: round2(cr) };
  }

  updateTotals() {
    if (!this.rows) return;
    const t = this.totals();
    this.totDr.textContent = amt(t.dr, true);
    this.totCr.textContent = amt(t.cr, true);
    const d = round2(t.dr - t.cr);
    this.diffEl.textContent = d ? `Difference: ${amt(Math.abs(d), true)} ${d > 0 ? 'Dr' : 'Cr'}` : '';
  }

  onLedger(r, it) {
    r.ledger = it ? it.row || this.ledgers.find((l) => l.id === it.id) : null;
    r.instrEl.classList.toggle('hidden', !this.isBank(r.ledger));
    this.showBalance(r.balEl, r.ledger?.id);
    if (r.ledger && !this.amountOf(r)) {
      const t = this.totals(r);
      const d = round2(t.dr - t.cr);
      if (r.side === 'Cr' && d > 0) r.crEl.value = d;
      if (r.side === 'Dr' && d < 0) r.drEl.value = -d;
    }
    this.updateTotals();
  }

  renderBills(r) {
    r.billsEl.replaceChildren(...r.bills.map((b) => h('div', `${b.ref_type}${b.name ? ' ' + b.name : ''}: ${amt(b.amount, true)}`)));
  }

  afterRow(r) {
    this.updateTotals();
    const idx = this.rows.indexOf(r);
    if (idx < this.rows.length - 1) return this.rows[idx + 1].ledgerEl.focus();
    const t = this.totals();
    if (t.dr === t.cr && this.rows.filter((x) => x.ledger).length >= 2) return this.narration.focus();
    const nr = this.addRow(t.dr > t.cr ? 'Cr' : 'Dr');
    nr.ledgerEl.focus();
  }

  // ------------------------------------------------------------ bill-wise allocation

  async billDialog(ledger, amount, side, existing) {
    const ancestry = this.ancestry(ledger);
    const kind = ancestry.includes('Sundry Creditors') ? 'payable' : 'receivable';
    let pending = [];
    try {
      const out = await app.api.report('outstanding', { kind, to: this.date });
      pending = out.parties.find((p) => p.ledger_id === ledger.id)?.bills || [];
    } catch {
      /* ignore */
    }
    if (this.voucher) {
      // Bills this voucher already settles are pending again while altering it.
      const mine = this.voucher.entries.find((e) => e.ledger_id === ledger.id)?.bills || [];
      for (const b of mine) {
        if (b.ref_type !== 'Agst Ref') continue;
        const p = pending.find((x) => x.name === b.name);
        if (p) p.pending = round2(p.pending + b.amount);
        else pending.push({ name: b.name, pending: b.amount, due_date: null });
      }
    }
    // Entry increases the party balance (invoice) or settles it (receipt/payment)?
    const increases = (kind === 'receivable') === (side === 'Dr');
    let rows = existing && existing.length ? existing.map((b) => ({ ...b })) : [];
    if (!rows.length) {
      if (increases) rows = [{ ref_type: 'New Ref', name: this.numberInput.value || '', amount, due_date: '' }];
      else {
        let left = amount;
        for (const p of pending.filter((x) => x.pending > 0)) {
          if (left <= 0) break;
          const a = round2(Math.min(left, p.pending));
          rows.push({ ref_type: 'Agst Ref', name: p.name, amount: a, due_date: '' });
          left = round2(left - a);
        }
        if (left > 0) rows.push({ ref_type: 'On Account', name: '', amount: left, due_date: '' });
      }
    }
    return new Promise((resolve) => {
      let m;
      const tbody = h('tbody');
      const status = h('div.modal-detail');
      const els = [];
      const pendingList = h('div.pending', pending.length ? h('b', 'Pending bills: ') : null,
        ...pending.filter((p) => p.pending > 0).map((p) => h('span.pill', `${p.name} · ${amt(p.pending, true)}${p.due_date ? ' · due ' + fdate(p.due_date) : ''}`)));
      const addLine = (b) => {
        const type = h('select.inp', ['New Ref', 'Agst Ref', 'Advance', 'On Account'].map((t) => h('option', t)));
        type.value = b.ref_type;
        type.addEventListener('keydown', (e) => {
          const map = { n: 'New Ref', a: 'Agst Ref', d: 'Advance', o: 'On Account' };
          if (map[e.key.toLowerCase()]) {
            type.value = map[e.key.toLowerCase()];
            e.preventDefault();
          }
        });
        const name = h('input.inp', { value: b.name || '' });
        const due = h('input.inp', { value: b.due_date ? fdate(b.due_date) : '', placeholder: 'Due date' });
        const a = h('input.inp.num', { value: b.amount });
        a.addEventListener('input', recalc);
        const line = { type, name, due, a };
        els.push(line);
        tbody.append(h('tr', h('td', type), h('td', name), h('td', due), h('td', a)));
        return line;
      };
      const total = () => round2(els.reduce((s, l) => s + (num(l.a.value) || 0), 0));
      function recalc() {
        const t = total();
        status.textContent = t === amount ? 'Allocated in full.' : `Allocated ${amt(t, true)} of ${amt(amount, true)} — remaining ${amt(round2(amount - t), true)}`;
      }
      rows.forEach(addLine);
      recalc();
      const collect = () =>
        els.filter((l) => num(l.a.value) > 0).map((l) => ({
          ref_type: l.type.value, name: l.name.value.trim(), amount: num(l.a.value),
          due_date: l.due.value ? parseDate(l.due.value, this.date) : null,
        }));
      const finish = (ok) => {
        m.close();
        resolve(ok ? collect() : null);
      };
      m = openModal([
        h('div.modal-title', `Bill-wise Details for: ${ledger.name}`),
        h('div.modal-detail', `Up to: ${amt(amount, true)} ${side}`),
        pendingList,
        h('table.vtable.bills', h('thead', h('tr', h('th', 'Type of Ref'), h('th', 'Name'), h('th', 'Due Date'), h('th.num', 'Amount'))), tbody),
        status,
        h('div.modal-detail', 'Enter to move · Ctrl+A to accept · Esc to cancel · N/A/D/O selects the reference type'),
      ], {
        className: 'wide',
        onKey: (e) => {
          if (e.key === 'Escape') return finish(false), true;
          if (e.key.toLowerCase() === 'a' && e.ctrlKey) return finish(true), true;
          if (e.key === 'Enter') {
            const flat = els.flatMap((l) => [l.type, l.name, l.due, l.a]);
            const i = flat.indexOf(document.activeElement);
            if (i < flat.length - 1) {
              flat[i + 1].focus();
              flat[i + 1].select?.();
              return true;
            }
            const left = round2(amount - total());
            if (left > 0) {
              const nl = addLine({ ref_type: increases ? 'New Ref' : 'On Account', name: '', amount: left });
              recalc();
              nl.type.focus();
              return true;
            }
            if (left < 0) {
              toast('Allocated more than the ledger amount', 'error');
              return true;
            }
            return finish(true), true;
          }
          return false;
        },
      });
      els[0]?.a.focus();
      els[0]?.a.select();
    });
  }

  // ------------------------------------------------------------ invoice mode

  buildInvoice(v) {
    const meta = v?.meta?.invoice || {};
    const sales = SALES_LIKE.includes(this.base);
    const isSaleAcct = ['Sales', 'Credit Note'].includes(this.base);
    this.partyEl = h('input.inp', { dataset: { nav: '', role: 'party' } });
    this.accountEl = h('input.inp', { dataset: { nav: '', role: 'account' } });
    this.partyBal = h('div.curbal');
    this.ledgerPicker(this.partyEl, {
      title: 'List of Party Ledgers',
      presetGroup: sales ? 'Sundry Debtors' : 'Sundry Creditors',
      filter: (l) => {
        const a = this.ancestry(l);
        return a.includes('Sundry Debtors') || a.includes('Sundry Creditors') || l.is_cash_bank;
      },
      onPick: (it) => {
        this.party = it ? this.ledgers.find((l) => l.id === it.id) : null;
        this.showBalance(this.partyBal, it?.id);
        this.preview();
      },
    });
    this.ledgerPicker(this.accountEl, {
      title: isSaleAcct ? 'List of Sales Ledgers' : 'List of Purchase Ledgers',
      presetGroup: isSaleAcct ? 'Sales Accounts' : 'Purchase Accounts',
      filter: (l) => this.ancestry(l).includes(isSaleAcct ? 'Sales Accounts' : 'Purchase Accounts'),
      onPick: () => this.preview(),
    });
    const setL = (el, id) => {
      const l = this.ledgers.find((x) => x.id === id);
      if (l) el._picker.setValue({ id: l.id, label: l.name });
      return l;
    };
    this.party = setL(this.partyEl, meta.party_ledger_id);
    setL(this.accountEl, meta.account_ledger_id);
    if (this.party) this.showBalance(this.partyBal, this.party.id);

    this.itemRows = [];
    this.itemsEl = h('tbody');
    this.taxEl = h('tbody.taxlines');
    this.roundEl = yesNo(meta.round_off ?? true);
    this.roundEl.dataset.role = 'roundoff';
    this.roundEl.addEventListener('change', () => this.preview());
    this.gstEl = yesNo(meta.apply_gst ?? app.company.gst_enabled);
    this.gstEl.dataset.role = 'gst';
    this.gstEl.addEventListener('change', () => this.preview());
    this.body.append(
      h('div.inv-head',
        h('label.frow', h('span.flabel', 'Party A/c name'), h('span.fsep', ':'), h('span.fstack', this.partyEl, this.partyBal)),
        h('label.frow', h('span.flabel', isSaleAcct ? 'Sales ledger' : 'Purchase ledger'), h('span.fsep', ':'), this.accountEl)),
      h('table.vtable.items',
        h('thead', h('tr', h('th', 'Name of Item'), this.multiGodown() ? h('th', 'Godown') : null, h('th.num', 'Quantity'),
          h('th.num', 'Rate'), h('th', 'per'), h('th.num', 'Disc %'), h('th.num', 'Amount'))),
        this.itemsEl),
      h('div.inv-foot',
        h('div.inv-opts',
          h('label.frow', h('span.flabel', 'Apply GST'), h('span.fsep', ':'), this.gstEl),
          h('label.frow', h('span.flabel', 'Round off total'), h('span.fsep', ':'), this.roundEl)),
        h('table.vtable.taxes', this.taxEl)));
    (meta.items || []).forEach((it) => this.addItemRow(it));
    this.addItemRow();
    this.preview();
  }

  multiGodown() {
    return (this.godowns || []).length > 1;
  }

  itemPicker(input, onPick) {
    return attachPicker(input, {
      title: 'List of Stock Items',
      source: async () => {
        this.items = await app.masters('stock-items');
        return this.items.map((i) => ({ id: i.id, label: i.name, sub: `${i.unit}${i.gst_rate ? ' · GST ' + i.gst_rate + '%' : ''}`, row: i }));
      },
      onPick,
      create: {
        label: 'Create Stock Item',
        run: async (text) => {
          const res = await createInline('stock-items', { name: text });
          this.items = await app.masters('stock-items');
          return res;
        },
      },
    });
  }

  godownPicker(input) {
    attachPicker(input, { title: 'List of Godowns', source: masterSource('godowns') });
    const main = this.godowns.find((g) => g.is_predefined) || this.godowns[0];
    if (main) input._picker.setValue({ id: main.id, label: main.name });
  }

  addItemRow(data) {
    const r = { item: null };
    r.itemEl = h('input.inp', { dataset: { nav: '', role: 'item' }, placeholder: this.itemRows.length ? '' : 'Select item' });
    r.godownEl = h('input.inp.sm', { dataset: { nav: '', role: 'godown' } });
    r.qtyEl = h('input.inp.num', { dataset: { nav: '', role: 'qty' } });
    r.rateEl = h('input.inp.num', { dataset: { nav: '', role: 'rate' } });
    r.discEl = h('input.inp.num.sm', { dataset: { nav: '', role: 'disc' } });
    r.unitEl = h('span.unit');
    r.amtEl = h('span.num');
    const recalc = () => {
      const a = num(r.qtyEl.value) * num(r.rateEl.value) * (1 - (num(r.discEl.value) || 0) / 100);
      r.amtEl.textContent = a ? amt(round2(a), true) : '';
      this.preview();
    };
    for (const el of [r.qtyEl, r.rateEl, r.discEl]) el.addEventListener('input', recalc);
    this.itemPicker(r.itemEl, (it) => {
      r.item = it ? it.row || this.items.find((x) => x.id === it.id) : null;
      r.unitEl.textContent = r.item?.unit || '';
      if (r.item && !r.rateEl.value && r.item.opening_rate) r.rateEl.value = r.item.opening_rate;
      recalc();
    });
    const multi = this.multiGodown();
    if (multi) this.godownPicker(r.godownEl);
    r.tr = h('tr', h('td', r.itemEl), multi ? h('td', r.godownEl) : null, h('td.num', r.qtyEl), h('td.num', r.rateEl),
      h('td', r.unitEl), h('td.num', r.discEl), h('td.num', r.amtEl));
    this.itemRows.push(r);
    this.itemsEl.append(r.tr);
    if (data) {
      const item = this.items.find((x) => x.id === Number(data.item_id));
      if (item) {
        r.itemEl._picker.setValue({ id: item.id, label: item.name });
        r.item = item;
        r.unitEl.textContent = item.unit;
      }
      if (multi && data.godown_id) {
        const g = this.godowns.find((x) => x.id === Number(data.godown_id));
        if (g) r.godownEl._picker.setValue({ id: g.id, label: g.name });
      }
      r.qtyEl.value = data.qty ?? '';
      r.rateEl.value = data.rate ?? '';
      r.discEl.value = data.discount || '';
      const a = num(r.qtyEl.value) * num(r.rateEl.value) * (1 - (num(r.discEl.value) || 0) / 100);
      r.amtEl.textContent = a ? amt(round2(a), true) : '';
    }
    return r;
  }

  invoicePayload() {
    return {
      party_ledger_id: Number(this.partyEl.dataset.id) || null,
      account_ledger_id: Number(this.accountEl.dataset.id) || null,
      round_off: this.roundEl.value === 'yes',
      apply_gst: this.gstEl.value === 'yes',
      items: this.itemRows.filter((r) => r.item && num(r.qtyEl.value) > 0).map((r) => ({
        item_id: r.item.id, qty: num(r.qtyEl.value), rate: num(r.rateEl.value) || 0,
        discount: num(r.discEl.value) || 0,
        godown_id: this.multiGodown() ? Number(r.godownEl.dataset.id) || undefined : undefined,
      })),
    };
  }

  preview = debounce(async () => {
    if (this.mode !== 'invoice' || !this.taxEl) return;
    const inv = this.invoicePayload();
    if (!inv.party_ledger_id || !inv.account_ledger_id || !inv.items.length) {
      this.taxEl.replaceChildren(h('tr', h('td.muted', 'Select party, ledger and items to compute taxes')));
      this.previewData = null;
      return;
    }
    try {
      const p = await app.api.previewInvoice({ voucher_type_id: this.vt.id, invoice: inv });
      this.previewData = p;
      const s = p.summary;
      const line = (label, v, cls = '') => h(`tr${cls}`, h('td', label), h('td.num', amt(v, true)));
      this.taxEl.replaceChildren(...[
        line('Taxable value', s.taxable),
        s.cgst ? line('CGST', s.cgst) : null,
        s.sgst ? line('SGST', s.sgst) : null,
        s.igst ? line('IGST', s.igst) : null,
        s.round_off ? line('Round Off', s.round_off) : null,
        line('Total', s.total, '.grand'),
        h('tr.muted', h('td', { colSpan: 2 }, s.interstate ? 'Inter-state supply (IGST)' : 'Intra-state supply (CGST + SGST)')),
      ].filter(Boolean));
    } catch (e) {
      this.taxEl.replaceChildren(h('tr', h('td.err', e.message)));
      this.previewData = null;
    }
  }, 250);

  // ------------------------------------------------------------ stock journal

  buildStockJournal(v) {
    this.sjRows = { out: [], in: [] };
    const table = (dir, title) => {
      const tbody = h('tbody');
      this.sjRows[dir].tbody = tbody;
      return h('div.sj-side', h('div.sj-title', title),
        h('table.vtable', h('thead', h('tr', h('th', 'Name of Item'), h('th', 'Godown'), h('th.num', 'Qty'), h('th.num', 'Rate'), h('th.num', 'Amount'))), tbody));
    };
    this.body.append(h('div.sj', table('out', 'Source (Consumption)'), table('in', 'Destination (Production)')));
    for (const line of v?.inventory || []) this.addSjRow(line.direction, line);
    this.addSjRow('out');
    this.addSjRow('in');
  }

  addSjRow(dir, data) {
    const r = { dir, item: null };
    r.itemEl = h('input.inp', { dataset: { nav: '', role: 'sj_item' } });
    r.godownEl = h('input.inp.sm', { dataset: { nav: '', role: 'godown' } });
    r.qtyEl = h('input.inp.num.sm', { dataset: { nav: '', role: 'sj_qty' } });
    r.rateEl = h('input.inp.num.sm', { dataset: { nav: '', role: 'sj_rate' } });
    r.amtEl = h('span.num');
    const recalc = () => (r.amtEl.textContent = amt(round2(num(r.qtyEl.value) * num(r.rateEl.value))));
    r.qtyEl.addEventListener('input', recalc);
    r.rateEl.addEventListener('input', recalc);
    this.itemPicker(r.itemEl, (it) => {
      r.item = it ? it.row || this.items.find((x) => x.id === it.id) : null;
      if (r.item && !r.rateEl.value) r.rateEl.value = r.item.opening_rate || '';
      recalc();
    });
    this.godownPicker(r.godownEl);
    r.tr = h('tr', h('td', r.itemEl), h('td', r.godownEl), h('td.num', r.qtyEl), h('td.num', r.rateEl), h('td.num', r.amtEl));
    const list = this.sjRows[dir];
    // keep the trailing blank row last
    const blank = list.find((x) => !x.item && !x.itemEl.value);
    if (blank && data) list.tbody.insertBefore(r.tr, blank.tr);
    else list.tbody.append(r.tr);
    list.push(r);
    if (data) {
      const item = this.items.find((x) => x.id === data.item_id);
      if (item) {
        r.itemEl._picker.setValue({ id: item.id, label: item.name });
        r.item = item;
      }
      const g = this.godowns.find((x) => x.id === data.godown_id);
      if (g) r.godownEl._picker.setValue({ id: g.id, label: g.name });
      r.qtyEl.value = data.qty;
      r.rateEl.value = data.rate;
      recalc();
    }
    return r;
  }

  // ------------------------------------------------------------ keyboard flow

  onKey(e, name) {
    if (name === 'Ctrl+A') return this.accept(), true;
    if (name === 'Alt+D' && this.id) return this.remove(), true;
    if (name === 'Ctrl+H') return this.toggleMode(), true;
    if (name === 'Alt+T') return app.go('pick-vtype'), true;
    if (name === 'Alt+I' && this.id) return app.go('voucher-print', { id: this.id }), true;
    if (name === 'F2') {
      this.dateInput.focus();
      this.dateInput.select();
      return true;
    }
    const el = document.activeElement;
    if (!this.node || !this.node.contains(el)) return false;
    if (el._picker && el._picker.onKey(e)) return true;
    if (name === 'Enter') {
      this.enter(el);
      return true;
    }
    return false;
  }

  focusNext(el) {
    const fields = [...this.node.querySelectorAll('[data-nav]')].filter((f) => !f.disabled && f.offsetParent !== null);
    const i = fields.indexOf(el);
    const next = fields[i + 1];
    if (next) {
      next.focus();
      next.select?.();
    } else this.accept();
  }

  async enter(el) {
    const role = el.dataset.role;
    if (role === 'date') {
      el.dispatchEvent(new Event('change'));
      return this.firstField()?.focus();
    }
    if (role === 'narration') return this.accept();
    if (this.mode === 'accounting') return this.enterAccounting(el, role);
    if (this.mode === 'invoice') return this.enterInvoice(el, role);
    return this.enterStockJournal(el, role);
  }

  async enterAccounting(el, role) {
    const r = this.rows.find((x) => x.tr.contains(el));
    if (!r) return this.focusNext(el);
    if (role === 'side') return r.ledgerEl.focus();
    if (role === 'ledger') {
      if (!r.ledger) {
        const filled = this.rows.filter((x) => x.ledger);
        if (filled.length >= 2 && r === this.rows[this.rows.length - 1]) {
          r.tr.remove();
          this.rows.pop();
          this.updateTotals();
          return this.narration.focus();
        }
        return toast('Select a ledger (Alt+C to create)', 'error');
      }
      const a = r.side === 'Dr' ? r.drEl : r.crEl;
      a.focus();
      return a.select();
    }
    if (role === 'amount') {
      const a = this.amountOf(r);
      if (!(a > 0)) return toast('Enter an amount', 'error');
      if (r.ledger?.bill_wise && this.base !== 'Memorandum') {
        const bills = await this.billDialog(r.ledger, a, r.side, r.bills.length && round2(r.bills.reduce((s, b) => s + b.amount, 0)) === a ? r.bills : null);
        if (bills) {
          r.bills = bills;
          this.renderBills(r);
        } else return el.focus();
      }
      if (!r.instrEl.classList.contains('hidden')) return r.instrNo.focus();
      return this.afterRow(r);
    }
    if (role === 'instr_no') return r.instrDate.focus();
    if (role === 'instr_date') return this.afterRow(r);
    return this.focusNext(el);
  }

  enterInvoice(el, role) {
    const r = this.itemRows.find((x) => x.tr.contains(el));
    if (role === 'item' && r) {
      if (!r.item) {
        if (this.itemRows.some((x) => x.item)) {
          return this.gstEl.focus();
        }
        return toast('Select a stock item (Alt+C to create)', 'error');
      }
      return (this.multiGodown() ? r.godownEl : r.qtyEl).focus();
    }
    if (role === 'qty' && r && !(num(r.qtyEl.value) > 0)) return toast('Enter quantity', 'error');
    if (role === 'disc' && r) {
      const last = this.itemRows[this.itemRows.length - 1];
      if (r === last) this.addItemRow();
      const idx = this.itemRows.indexOf(r);
      return this.itemRows[idx + 1].itemEl.focus();
    }
    if (role === 'roundoff') return this.narration.focus();
    return this.focusNext(el);
  }

  enterStockJournal(el, role) {
    const all = [...this.sjRows.out, ...this.sjRows.in];
    const r = all.find((x) => x.tr.contains(el));
    if (role === 'sj_item' && r) {
      if (!r.item) {
        if (r.dir === 'out') {
          const firstIn = this.sjRows.in.find((x) => x.tr.parentNode);
          return firstIn.itemEl.focus();
        }
        return this.narration.focus();
      }
      return r.godownEl.focus();
    }
    if (role === 'sj_rate' && r) {
      const list = this.sjRows[r.dir];
      const rowsInDom = [...list.tbody.children];
      const idx = rowsInDom.indexOf(r.tr);
      if (idx === rowsInDom.length - 1) this.addSjRow(r.dir);
      const next = list.find((x) => x.tr === list.tbody.children[idx + 1]);
      return next.itemEl.focus();
    }
    return this.focusNext(el);
  }

  // ------------------------------------------------------------ mode switch

  toggleMode() {
    if (!INVOICE_TYPES.includes(this.base) || !app.company.inventory_enabled) return;
    if (this.id) return toast('Change of invoice mode is available while creating a voucher', 'error');
    this.mode = this.mode === 'invoice' ? 'accounting' : 'invoice';
    const go = async () => {
      if (this.mode === 'invoice') {
        [this.items, this.godowns] = await Promise.all([app.masters('stock-items'), app.masters('godowns')]);
      }
      this.build(null);
      document.getElementById('screen-title').textContent = this.title;
      app.renderButtons();
      this.firstField()?.focus();
    };
    go();
  }

  // ------------------------------------------------------------ save / delete

  collect() {
    const date = parseDate(this.dateInput.value, this.date);
    if (!date) throw new Error('Invalid voucher date');
    const body = {
      voucher_type_id: this.vt.id, date, number: this.numberInput.value.trim(), reference: this.refInput.value.trim(),
      narration: this.narration.value.trim(), mode: this.mode,
    };
    if (this.mode === 'accounting') {
      const rows = this.rows.filter((r) => r.ledger && this.amountOf(r) > 0);
      if (this.rows.some((r) => !r.ledger && this.amountOf(r) > 0)) throw new Error('A line has an amount but no ledger');
      body.entries = rows.map((r) => ({
        ledger_id: r.ledger.id, side: r.side, amount: this.amountOf(r),
        bills: r.ledger.bill_wise ? r.bills : [],
        instrument_no: this.isBank(r.ledger) ? r.instrNo.value.trim() || null : null,
        instrument_date: this.isBank(r.ledger) && r.instrDate.value ? parseDate(r.instrDate.value, date) : null,
      }));
    } else if (this.mode === 'invoice') {
      body.invoice = this.invoicePayload();
      if (!body.invoice.party_ledger_id) throw new Error('Select the party A/c');
      if (!body.invoice.account_ledger_id) throw new Error('Select the sales/purchase ledger');
      if (!body.invoice.items.length) throw new Error('Add at least one item with quantity');
    } else {
      body.inventory = ['out', 'in'].flatMap((dir) => this.sjRows[dir].filter((r) => r.item && num(r.qtyEl.value) > 0).map((r) => ({
        item_id: r.item.id, godown_id: Number(r.godownEl.dataset.id) || undefined, qty: num(r.qtyEl.value),
        rate: num(r.rateEl.value) || 0, direction: dir,
      })));
    }
    return body;
  }

  async accept() {
    if (this.busy) return;
    let body;
    try {
      body = this.collect();
    } catch (e) {
      return toast(e.message, 'error');
    }
    // Returns against a bill: ask for bill-wise details for the party on Credit/Debit Notes.
    if (this.mode === 'invoice' && ['Credit Note', 'Debit Note'].includes(this.base) && this.party?.bill_wise && this.previewData) {
      const bills = await this.billDialog(this.party, this.previewData.summary.total, this.base === 'Credit Note' ? 'Cr' : 'Dr', this.partyBills);
      if (!bills) return;
      this.partyBills = bills;
      body.invoice.bills = bills;
    }
    if (!(await confirmBox('Accept?'))) return;
    this.busy = true;
    try {
      const saved = this.id ? await app.api.update('vouchers', this.id, body) : await app.api.create('vouchers', body);
      this.saved = true;
      app.invalidate('voucher-types', 'ledgers');
      app.vdate = saved.date;
      toast(`${saved.voucher_type} No. ${saved.number} saved — ${amt(saved.total, true)}`, 'ok');
      if (this.id) {
        app.stack.pop();
        app.show(true);
      } else {
        app.replace('voucher', { type: this.vt.name });
      }
    } catch (e) {
      toast(e.message, 'error');
    } finally {
      this.busy = false;
    }
  }

  async remove() {
    if (!this.id) return;
    if (!(await confirmBox('Delete this voucher?', `${this.vt.name} No. ${this.voucher.number} dated ${fdate(this.voucher.date)}`))) return;
    try {
      await app.api.remove('vouchers', this.id);
      this.saved = true;
      app.invalidate('voucher-types');
      toast('Voucher deleted');
      app.stack.pop();
      app.show(true);
    } catch (e) {
      toast(e.message, 'error');
    }
  }
}

// ---------------------------------------------------------------- other voucher types

app.register('pick-vtype', () => {
  let input;
  return {
    title: 'Change Voucher Type',
    async render(root) {
      input = h('input.inp.big', { dataset: { nav: '' }, placeholder: 'Voucher type…' });
      attachPicker(input, {
        title: 'List of Voucher Types', required: true, source: masterSource('voucher-types'),
        onPick: (it) => {
          app.stack.pop();
          app.openVoucher(it.label);
          if (app.stack[app.stack.length - 1]?.name !== 'voucher') app.go('voucher', { type: it.label });
        },
      });
      root.append(h('div.center-col', h('div.form-card', h('div.fsec', 'Select voucher type'), input)));
    },
    focus: () => input.focus(),
    onKey: (e) => input._picker.onKey(e),
  };
});

// ---------------------------------------------------------------- print view

const ONES = ['', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine', 'Ten', 'Eleven', 'Twelve',
  'Thirteen', 'Fourteen', 'Fifteen', 'Sixteen', 'Seventeen', 'Eighteen', 'Nineteen'];
const TENS = ['', '', 'Twenty', 'Thirty', 'Forty', 'Fifty', 'Sixty', 'Seventy', 'Eighty', 'Ninety'];

function words99(n) {
  return n < 20 ? ONES[n] : `${TENS[Math.floor(n / 10)]}${n % 10 ? ' ' + ONES[n % 10] : ''}`;
}

/** Indian numbering: crore, lakh, thousand, hundred. */
export function amountInWords(value) {
  const rupees = Math.floor(Math.abs(value));
  const paise = Math.round((Math.abs(value) - rupees) * 100);
  const parts = [];
  let n = rupees;
  const crore = Math.floor(n / 1e7);
  n %= 1e7;
  const lakh = Math.floor(n / 1e5);
  n %= 1e5;
  const thousand = Math.floor(n / 1000);
  n %= 1000;
  const hundred = Math.floor(n / 100);
  n %= 100;
  if (crore) parts.push(`${crore > 99 ? amountInWords(crore).replace(/^INR |\sOnly$/g, '') : words99(crore)} Crore`);
  if (lakh) parts.push(`${words99(lakh)} Lakh`);
  if (thousand) parts.push(`${words99(thousand)} Thousand`);
  if (hundred) parts.push(`${ONES[hundred]} Hundred`);
  if (n) parts.push(words99(n));
  let s = `INR ${parts.join(' ') || 'Zero'}`;
  if (paise) s += ` and ${words99(paise)} paise`;
  return `${s} Only`;
}

app.register('voucher-print', ({ id }) => ({
  title: 'Voucher Print View',
  buttons: () => [{ key: 'Alt+P', label: 'Print', action: () => window.print() }],
  async render(root) {
    const v = await app.api.get('vouchers', id);
    const c = app.company;
    const isInv = v.mode === 'invoice';
    const heading = isInv ? { Sales: 'TAX INVOICE', Purchase: 'PURCHASE VOUCHER', 'Credit Note': 'CREDIT NOTE', 'Debit Note': 'DEBIT NOTE' }[v.base_type] : `${v.voucher_type.toUpperCase()} VOUCHER`;
    const party = v.party_ledger_id ? (await app.masters('ledgers')).find((l) => l.id === v.party_ledger_id) : null;
    const s = v.meta?.summary;
    const doc = h('div.print-doc',
      h('div.pd-head', h('div', h('div.pd-co', c.name), h('div', c.address || ''), h('div', [c.state, c.pincode].filter(Boolean).join(' - ')),
        c.gstin ? h('div', `GSTIN/UIN: ${c.gstin}`) : null), h('div.pd-title', heading)),
      h('div.pd-meta', h('div', h('b', 'No. '), v.number), h('div', h('b', 'Dated '), fdate(v.date)), v.reference ? h('div', h('b', 'Ref. '), v.reference) : null),
      party ? h('div.pd-party', h('div.muted', isInv && SALES_LIKE.includes(v.base_type) ? 'Buyer (Bill to)' : 'Party'), h('b', party.name),
        party.address ? h('div', party.address) : null, party.state ? h('div', `State: ${party.state}`) : null,
        party.gstin ? h('div', `GSTIN/UIN: ${party.gstin}`) : null) : null,
      isInv
        ? h('table.pd-table', h('thead', h('tr', h('th', 'Sl'), h('th', 'Description of Goods'), h('th.num', 'Quantity'), h('th.num', 'Rate'), h('th', 'per'), h('th.num', 'Amount'))),
          h('tbody', v.inventory.map((it, i) => h('tr', h('td', i + 1), h('td', it.item_name), h('td.num', `${it.qty} ${it.unit}`), h('td.num', amt(it.rate, true)), h('td', it.unit), h('td.num', amt(it.amount, true)))),
            s ? [s.cgst ? h('tr', h('td'), h('td.r', 'CGST'), h('td'), h('td'), h('td'), h('td.num', amt(s.cgst, true))) : null,
              s.sgst ? h('tr', h('td'), h('td.r', 'SGST'), h('td'), h('td'), h('td'), h('td.num', amt(s.sgst, true))) : null,
              s.igst ? h('tr', h('td'), h('td.r', 'IGST'), h('td'), h('td'), h('td'), h('td.num', amt(s.igst, true))) : null,
              s.round_off ? h('tr', h('td'), h('td.r', 'Round Off'), h('td'), h('td'), h('td'), h('td.num', amt(s.round_off, true))) : null] : null),
          h('tfoot', h('tr', h('td'), h('td.r', 'Total'), h('td.num', v.inventory.reduce((a, b) => a + b.qty, 0)), h('td'), h('td'), h('td.num', `₹ ${amt(v.total, true)}`))))
        : h('table.pd-table', h('thead', h('tr', h('th', 'Particulars'), h('th.num', 'Debit'), h('th.num', 'Credit'))),
          h('tbody', v.entries.map((e) => h('tr', h('td', e.ledger_name, e.instrument_no ? h('div.muted', `Inst. ${e.instrument_no}`) : null,
            ...e.bills.map((b) => h('div.muted', `${b.ref_type} ${b.name || ''} ${amt(b.amount, true)}`))),
          h('td.num', e.side === 'Dr' ? amt(e.amount, true) : ''), h('td.num', e.side === 'Cr' ? amt(e.amount, true) : '')))),
          h('tfoot', h('tr', h('td.r', 'Total'), h('td.num', amt(v.total, true)), h('td.num', amt(v.total, true))))),
      h('div.pd-words', h('span.muted', 'Amount chargeable (in words): '), h('b', amountInWords(v.total))),
      v.narration ? h('div.pd-narr', h('span.muted', 'Narration: '), v.narration) : null,
      h('div.pd-sign', h('div', 'Receiver\'s Signature'), h('div', `for ${c.name}`, h('div.pd-auth', 'Authorised Signatory'))),
      h('div.pd-foot', 'This is a computer generated document — Mudra Finance and Banking Software'));
    root.append(h('div.print-wrap', doc));
  },
}));
