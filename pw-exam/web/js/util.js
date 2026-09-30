// DOM helpers, formatting, toasts and modal dialogs. No innerHTML anywhere (XSS-safe by construction).

export function h(tag, attrs, ...children) {
  const [name, ...rest] = tag.split(/(?=[.#])/);
  const el = document.createElement(name || 'div');
  for (const r of rest) {
    if (r[0] === '.') el.classList.add(r.slice(1));
    else if (r[0] === '#') el.id = r.slice(1);
  }
  if (attrs !== null && attrs !== undefined && (typeof attrs !== 'object' || attrs instanceof Node || Array.isArray(attrs))) {
    children.unshift(attrs);
    attrs = null;
  }
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k.startsWith('on')) el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === 'class') el.className = v;
    else if (k === 'dataset') Object.assign(el.dataset, v);
    else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
    else if (k in el && typeof v !== 'string') el[k] = v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

export const $ = (sel, root = document) => root.querySelector(sel);

/** append() that skips null/false children (Element.append would print "null"). */
export function add(el, ...children) {
  el.append(...children.flat(Infinity).filter((c) => c !== null && c !== undefined && c !== false));
  return el;
}

export function clear(el, ...children) {
  el.replaceChildren(...children.flat(Infinity).filter((c) => c !== null && c !== undefined && c !== false));
  return el;
}

// ---------------------------------------------------------------- formatting

export function fmtDateTime(ts) {
  if (!ts) return '—';
  return new Date(ts * 1000).toLocaleString('en-IN', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

export function fmtDuration(sec) {
  sec = Math.max(0, Math.round(sec || 0));
  const hh = Math.floor(sec / 3600);
  const mm = Math.floor((sec % 3600) / 60);
  const ss = sec % 60;
  const pad = (n) => String(n).padStart(2, '0');
  return hh ? `${hh}:${pad(mm)}:${pad(ss)}` : `${pad(mm)}:${pad(ss)}`;
}

export function fmtMinutes(sec) {
  return `${Math.round((sec || 0) / 60)} min`;
}

/** datetime-local value <-> epoch seconds (browser local time). */
export function toLocalInput(ts) {
  const d = new Date(ts * 1000);
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function fromLocalInput(v) {
  const t = new Date(v).getTime();
  return Number.isFinite(t) ? Math.round(t / 1000) : null;
}

export const LETTERS = 'ABCDEFGH';

// ---------------------------------------------------------------- storage (never throws)

export const store = {
  get(key, fallback = null) {
    try {
      const v = localStorage.getItem(key);
      return v === null ? fallback : JSON.parse(v);
    } catch {
      return fallback;
    }
  },
  set(key, value) {
    try {
      localStorage.setItem(key, JSON.stringify(value));
      return true;
    } catch {
      return false;
    }
  },
  del(key) {
    try {
      localStorage.removeItem(key);
    } catch {
      /* ignore */
    }
  },
};

// ---------------------------------------------------------------- toasts & modals

export function toast(message, kind = 'info') {
  const host = $('#toasts');
  const t = h(`div.toast.${kind}`, { role: kind === 'error' ? 'alert' : 'status' }, message);
  host.append(t);
  setTimeout(() => t.classList.add('out'), kind === 'error' ? 5000 : 3000);
  setTimeout(() => t.remove(), kind === 'error' ? 5500 : 3500);
}

export function modal(title, body, { actions = [], wide = false, onClose } = {}) {
  const back = h('div.modal-back');
  const close = () => {
    back.remove();
    document.removeEventListener('keydown', esc);
    onClose?.();
  };
  const esc = (e) => e.key === 'Escape' && close();
  const box = h(`div.modal${wide ? '.wide' : ''}`, { role: 'dialog', 'aria-modal': 'true', 'aria-label': title },
    h('div.modal-head', h('h3', title), h('button.icon-btn', { onclick: close, 'aria-label': 'Close' }, '×')),
    h('div.modal-body', body),
    actions.length ? h('div.modal-foot', actions.map((a) => h(`button.btn${a.primary ? '.primary' : ''}${a.danger ? '.danger' : ''}`,
      { onclick: () => a.onClick(close), type: 'button' }, a.label))) : null);
  back.append(box);
  back.addEventListener('mousedown', (e) => e.target === back && close());
  document.addEventListener('keydown', esc);
  document.body.append(back);
  box.querySelector('input, select, textarea, button.primary')?.focus();
  return { close, box };
}

export function confirmDialog(title, message, { okLabel = 'Confirm', danger = false } = {}) {
  return new Promise((resolve) => {
    let done = false;
    const m = modal(title, h('p', message), {
      onClose: () => !done && resolve(false),
      actions: [
        { label: 'Cancel', onClick: (close) => close() },
        { label: okLabel, primary: !danger, danger, onClick: (close) => { done = true; close(); resolve(true); } },
      ],
    });
    m.box.querySelector('.modal-foot .btn:last-child')?.focus();
  });
}

/** Simple labelled form field. */
export function field(label, input, hint) {
  return h('label.field', h('span.label', label), input, hint ? h('span.hint', hint) : null);
}

export function input(attrs = {}) {
  return h('input.input', attrs);
}

export function select(options, value, attrs = {}) {
  const s = h('select.input', attrs, options.map((o) => (typeof o === 'object'
    ? h('option', { value: o.value }, o.label) : h('option', { value: o }, o))));
  if (value !== undefined && value !== null) s.value = String(value);
  return s;
}

export function checkbox(label, checked, attrs = {}) {
  const c = h('input', { type: 'checkbox', checked: !!checked, ...attrs });
  return { el: h('label.check', c, h('span', label)), input: c };
}

export function readFile(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(r.result);
    r.onerror = () => reject(r.error);
    r.readAsText(file);
  });
}

export function downloadBlob(blob, filename) {
  const a = h('a', { href: URL.createObjectURL(blob), download: filename });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}

export function debounce(fn, ms) {
  let t;
  return (...a) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...a), ms);
  };
}
