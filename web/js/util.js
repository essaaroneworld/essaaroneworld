// Formatting, dates and DOM helpers.

const INR = new Intl.NumberFormat('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** Indian digit grouping (12,34,567.00). Blank for zero unless keepZero. */
export function amt(v, keepZero = false) {
  const n = Number(v || 0);
  if (!n && !keepZero) return '';
  return INR.format(n);
}

/** Amount with Dr/Cr suffix, Tally style. */
export function drcr(v, keepZero = false) {
  const n = Number(v || 0);
  if (!n) return keepZero ? '0.00' : '';
  return `${INR.format(Math.abs(n))} ${n > 0 ? 'Dr' : 'Cr'}`;
}

export function qty(v, unit = '') {
  const n = Number(v || 0);
  if (!n) return '';
  const s = Number.isInteger(n) ? String(n) : n.toFixed(3).replace(/0+$/, '').replace(/\.$/, '');
  return unit ? `${s} ${unit}` : s;
}

/** ISO date -> "1-Apr-26" */
export function fdate(iso) {
  if (!iso) return '';
  const [y, m, d] = iso.split('-').map(Number);
  return `${d}-${MONTHS[m - 1]}-${String(y).slice(2)}`;
}

export function longDate(iso) {
  if (!iso) return '';
  const [y, m, d] = iso.split('-').map(Number);
  const day = new Date(y, m - 1, d).toLocaleDateString('en-IN', { weekday: 'long' });
  return `${d}-${MONTHS[m - 1]}-${y}, ${day}`;
}

export function todayISO() {
  const t = new Date();
  return `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, '0')}-${String(t.getDate()).padStart(2, '0')}`;
}

/**
 * Parse the forgiving date formats accountants type: "5", "5-4", "5/4/26",
 * "5-Apr-2026", "2026-04-05". Missing parts come from the reference date.
 */
export function parseDate(text, ref = todayISO()) {
  if (!text) return null;
  const s = String(text).trim();
  if (/^\d{4}-\d{2}-\d{2}$/.test(s)) return s;
  const [ry, rm] = ref.split('-').map(Number);
  const parts = s.split(/[-/.\s]+/).filter(Boolean);
  if (!parts.length || parts.length > 3) return null;
  const d = Number(parts[0]);
  let m = rm;
  if (parts[1]) {
    const idx = MONTHS.findIndex((x) => x.toLowerCase() === parts[1].slice(0, 3).toLowerCase());
    m = idx >= 0 ? idx + 1 : Number(parts[1]);
  }
  let y = ry;
  if (parts[2]) {
    y = Number(parts[2]);
    if (y < 100) y += 2000;
  }
  if (!d || !m || !y || m > 12 || d > 31) return null;
  const dt = new Date(y, m - 1, d);
  if (dt.getMonth() !== m - 1) return null;
  return `${y}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
}

export function num(text) {
  if (text === null || text === undefined || text === '') return 0;
  const n = Number(String(text).replace(/,/g, ''));
  return Number.isFinite(n) ? n : NaN;
}

export function round2(n) {
  return Math.round((n + Number.EPSILON) * 100) / 100;
}

/** Tiny hyperscript: h('div.cls#id', {attrs}, children...) */
export function h(tag, attrs, ...children) {
  const [name, ...rest] = tag.split(/(?=[.#])/);
  const node = document.createElement(name || 'div');
  for (const r of rest) {
    if (r[0] === '.') node.classList.add(r.slice(1));
    else if (r[0] === '#') node.id = r.slice(1);
  }
  if (attrs !== null && attrs !== undefined && (typeof attrs !== 'object' || attrs instanceof Node || Array.isArray(attrs))) {
    children.unshift(attrs);
    attrs = null;
  }
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k.startsWith('on')) node.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === 'class') node.className = v;
    else if (k === 'dataset') Object.assign(node.dataset, v);
    else if (k === 'style' && typeof v === 'object') Object.assign(node.style, v);
    else if (k in node && typeof v !== 'string') node[k] = v;
    else node.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

export function debounce(fn, ms = 200) {
  let t;
  return (...a) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...a), ms);
  };
}

export function downloadCSV(filename, rows) {
  const esc = (v) => {
    const s = v === null || v === undefined ? '' : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const blob = new Blob(['﻿' + rows.map((r) => r.map(esc).join(',')).join('\r\n')], { type: 'text/csv' });
  const a = h('a', { href: URL.createObjectURL(blob), download: filename });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

/** Render "Hot key" labels: underline the hotkey letter. */
export function hotLabel(label, key) {
  if (!key) return label;
  const i = label.toLowerCase().indexOf(key.toLowerCase());
  if (i < 0) return label;
  return h('span', label.slice(0, i), h('u.hk', label[i]), label.slice(i + 1));
}

export function keyName(e) {
  const parts = [];
  if (e.ctrlKey || e.metaKey) parts.push('Ctrl');
  if (e.altKey) parts.push('Alt');
  if (e.shiftKey && e.key.length > 1) parts.push('Shift');
  let k = e.key;
  if (k === ' ') k = 'Space';
  if (k.length === 1) k = k.toUpperCase();
  parts.push(k);
  return parts.join('+');
}
