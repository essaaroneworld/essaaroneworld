// Application shell: screen stack, global shortcuts, button bar and company state.
import { api, companyApi } from './api.js';
import { confirmBox, formDialog, modalOpen, pickerActive, toast } from './ui.js';
import { downloadCSV, fdate, h, keyName, longDate, todayISO } from './util.js';

const registry = {};

export const VOUCHER_KEYS = [
  { key: 'F4', label: 'Contra', type: 'Contra' },
  { key: 'F5', label: 'Payment', type: 'Payment' },
  { key: 'F6', label: 'Receipt', type: 'Receipt' },
  { key: 'F7', label: 'Journal', type: 'Journal' },
  { key: 'F8', label: 'Sales', type: 'Sales' },
  { key: 'F9', label: 'Purchase', type: 'Purchase' },
  { key: 'Ctrl+F8', label: 'Credit Note', type: 'Credit Note' },
  { key: 'Ctrl+F9', label: 'Debit Note', type: 'Debit Note' },
  { key: 'Alt+F7', label: 'Stock Jrnl', type: 'Stock Journal' },
  { key: 'F10', label: 'Memorandum', type: 'Memorandum' },
];

class App {
  constructor() {
    this.stack = [];
    this.company = null;
    this.api = null;
    this.period = null;
    this.vdate = todayISO();
    this.cache = {};
  }

  register(name, factory) {
    registry[name] = factory;
  }

  // ------------------------------------------------------------ navigation

  go(name, params = {}) {
    const factory = registry[name];
    if (!factory) throw new Error(`Unknown screen ${name}`);
    if (!this.company && !['companies', 'company-form'].includes(name)) {
      return this.go('companies');
    }
    const screen = factory(params);
    screen.name = name;
    this.stack.push(screen);
    this.show();
    return screen;
  }

  replace(name, params) {
    this.stack.pop();
    return this.go(name, params);
  }

  async back() {
    const top = this.stack[this.stack.length - 1];
    if (top?.beforeLeave && !(await top.beforeLeave())) return;
    if (this.stack.length <= 1) {
      if (this.company && top?.name === 'gateway') {
        if (await confirmBox('Quit?', 'Close the company and return to the company list.')) this.closeCompany();
      }
      return;
    }
    this.stack.pop();
    this.show(true);
  }

  home() {
    this.stack = [];
    this.go(this.company ? 'gateway' : 'companies');
  }

  async show(resumed = false) {
    const screen = this.stack[this.stack.length - 1];
    const main = document.getElementById('main');
    document.getElementById('picker-host').replaceChildren();
    document.getElementById('screen-title').textContent = screen.title || '';
    main.replaceChildren(h('div.loading', 'Loading…'));
    this.renderButtons();
    try {
      const node = h('div.screen');
      await screen.render(node, { resumed });
      if (this.stack[this.stack.length - 1] !== screen) return; // navigated away meanwhile
      main.replaceChildren(node);
      document.getElementById('screen-title').textContent = screen.title || '';
      this.renderButtons();
      screen.focus ? screen.focus() : node.querySelector('[data-nav], [tabindex]')?.focus();
    } catch (e) {
      console.error(e);
      main.replaceChildren(h('div.error-panel', h('b', 'Could not open this screen'), h('div', e.message)));
    }
  }

  refresh() {
    return this.show(true);
  }

  // ------------------------------------------------------------ company

  async openCompany(company) {
    this.company = company;
    this.api = companyApi(company.id);
    this.cache = {};
    const dash = await this.api.report('dashboard');
    this.period = { from: dash.from, to: dash.to };
    const last = dash.last_voucher_date;
    const t = todayISO();
    this.vdate = t >= dash.from && t <= dash.to ? t : last || dash.from;
    try {
      localStorage.setItem('mudra.lastCompany', String(company.id));
    } catch {
      /* storage unavailable */
    }
    this.renderCompanyBar();
    this.stack = [];
    this.go('gateway');
  }

  closeCompany() {
    this.company = null;
    this.api = null;
    this.renderCompanyBar();
    this.stack = [];
    this.go('companies');
  }

  renderCompanyBar() {
    const el = document.getElementById('company-title');
    el.textContent = this.company ? this.company.name : 'No company open';
  }

  // ------------------------------------------------------------ master cache

  async masters(kind) {
    if (!this.cache[kind]) this.cache[kind] = this.api.list(kind);
    try {
      return await this.cache[kind];
    } catch (e) {
      delete this.cache[kind];
      throw e;
    }
  }

  invalidate(...kinds) {
    if (!kinds.length) this.cache = {};
    for (const k of kinds) delete this.cache[k];
  }

  // ------------------------------------------------------------ dialogs

  async changePeriod() {
    if (!this.company) return;
    const v = await formDialog('Change Period', [
      { key: 'from', label: 'From', type: 'date', value: this.period.from },
      { key: 'to', label: 'To', type: 'date', value: this.period.to },
    ], { hint: 'Type dates like 1-4-26 or 1-Apr-2026' });
    if (!v) return;
    if (v.to < v.from) return toast("'To' date must be after 'From' date", 'error');
    this.period = { from: v.from, to: v.to };
    this.refresh();
  }

  async changeDate() {
    if (!this.company) return;
    const v = await formDialog('Change Current Date', [{ key: 'date', label: 'Date', type: 'date', value: this.vdate }]);
    if (!v) return;
    this.vdate = v.date;
    toast(`Current date: ${longDate(v.date)}`);
    this.refresh();
  }

  // ------------------------------------------------------------ button bar

  globalButtons() {
    const c = !!this.company;
    return [
      { key: 'F2', label: 'Date', action: () => this.changeDate(), disabled: !c },
      { key: 'Alt+F2', label: 'Period', action: () => this.changePeriod(), disabled: !c },
      { key: 'F3', label: 'Company', action: () => this.selectCompany() },
      { sep: true },
      ...VOUCHER_KEYS.map((v) => ({
        key: v.key, label: v.label, disabled: !c,
        action: () => this.openVoucher(v.type),
      })),
    ];
  }

  openVoucher(type) {
    const top = this.stack[this.stack.length - 1];
    if (top?.name === 'voucher' && top.switchType) return top.switchType(type);
    this.go('voucher', { type });
  }

  renderButtons() {
    const top = this.stack[this.stack.length - 1];
    const ctx = top?.buttons ? top.buttons() : [];
    const bar = document.getElementById('buttonbar');
    const mk = (b) =>
      b.sep
        ? h('div.bsep')
        : h(`button.bbtn${b.disabled ? '.dis' : ''}`, { onclick: () => !b.disabled && b.action(), title: b.label, disabled: !!b.disabled },
          h('span.bkey', b.key), h('span.blabel', b.label));
    bar.replaceChildren(...ctx.map(mk), ...(ctx.length ? [h('div.bsep')] : []), ...this.globalButtons().map(mk));
    this._buttons = [...ctx, ...this.globalButtons()].filter((b) => !b.sep);
  }

  selectCompany() {
    this.stack = [];
    this.go('companies');
  }

  // ------------------------------------------------------------ keys

  async onKey(e) {
    if (modalOpen()) return;
    const name = keyName(e);
    const top = this.stack[this.stack.length - 1];
    // Screen gets first chance (pickers, forms, report navigation).
    if (top?.onKey && top.onKey(e, name)) {
      e.preventDefault();
      return;
    }
    if (name === 'Escape') {
      if (pickerActive()) return;
      e.preventDefault();
      return this.back();
    }
    const btn = this._buttons?.find((b) => b.key === name && !b.disabled);
    if (btn) {
      e.preventDefault();
      return btn.action();
    }
    const globals = {
      'Ctrl+G': () => this.goTo(), 'Alt+G': () => this.goTo(),
      'Ctrl+E': () => this.exportCurrent(), 'Alt+E': () => this.exportCurrent(),
      'Ctrl+P': () => window.print(), 'Alt+P': () => window.print(),
      F1: () => this.go('help'), 'Ctrl+H': null,
      'Alt+K': () => this.selectCompany(),
      'Ctrl+Q': () => this.home(),
    };
    if (globals[name]) {
      e.preventDefault();
      globals[name]();
    }
  }

  exportCurrent() {
    const top = this.stack[this.stack.length - 1];
    if (!top?.exportRows) return toast('Nothing to export on this screen');
    const rows = top.exportRows();
    const fname = `${(this.company?.name || 'mudra').replace(/\W+/g, '_')}_${(top.title || 'report').replace(/\W+/g, '_')}.csv`;
    downloadCSV(fname, rows);
    toast(`Exported ${fname}`);
  }

  goTo() {
    if (!this.company) return;
    this.go('goto');
  }

  // ------------------------------------------------------------ boot

  async start() {
    document.addEventListener('keydown', (e) => this.onKey(e));
    document.getElementById('brand').addEventListener('click', () => this.home());
    document.getElementById('topmenu').append(
      ...[
        ['Alt+K', 'Company', () => this.selectCompany()],
        ['Alt+G', 'Go To', () => this.goTo()],
        ['Alt+E', 'Export', () => this.exportCurrent()],
        ['Alt+P', 'Print', () => window.print()],
        ['Alt+Y', 'Backup', () => (window.location.href = '/api/backup')],
        ['F1', 'Help', () => this.go('help')],
      ].map(([k, l, fn]) => h('button.tbtn', { onclick: fn }, h('span.bkey', k.replace('Alt+', '')), ' ', l)),
    );
    const tick = () => {
      document.getElementById('statusbar-date').textContent = this.company
        ? `Current Date: ${longDate(this.vdate)}   ·   Period: ${fdate(this.period.from)} to ${fdate(this.period.to)}`
        : '';
    };
    setInterval(tick, 1000);
    tick();
    let companies = [];
    try {
      companies = await api.get('/companies');
    } catch (e) {
      toast(e.message, 'error');
    }
    let last = null;
    try {
      last = Number(localStorage.getItem('mudra.lastCompany'));
    } catch {
      /* storage unavailable */
    }
    const c = companies.find((x) => x.id === last);
    if (c) return this.openCompany(c);
    this.go('companies');
  }
}

export const app = new App();
window.mudra = app; // handy for debugging from the console
