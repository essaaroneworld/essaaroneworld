// Reusable keyboard-first widgets: menus, pickers (Tally's "List of ..." panel),
// dialogs and toasts.
import { h, hotLabel, parseDate, fdate } from './util.js';

// ---------------------------------------------------------------- toast

export function toast(message, kind = 'info') {
  const host = document.getElementById('toasts');
  const t = h(`div.toast.${kind}`, { role: kind === 'error' ? 'alert' : 'status' }, message);
  host.append(t);
  setTimeout(() => t.classList.add('out'), kind === 'error' ? 4500 : 2500);
  setTimeout(() => t.remove(), kind === 'error' ? 5000 : 3000);
}

// ---------------------------------------------------------------- modal dialogs

let modalDepth = 0;
export const modalOpen = () => modalDepth > 0;

export function openModal(content, { onKey, className = '' } = {}) {
  const back = h('div.modal-back');
  const box = h(`div.modal.${className || 'plain'}`, { role: 'dialog', 'aria-modal': 'true' }, content);
  back.append(box);
  document.body.append(back);
  modalDepth++;
  const prevFocus = document.activeElement;
  const handler = (e) => {
    if (onKey && onKey(e)) {
      e.preventDefault();
      e.stopPropagation();
    }
  };
  back.addEventListener('keydown', handler);
  return {
    box,
    close() {
      back.remove();
      modalDepth--;
      if (prevFocus && document.contains(prevFocus)) prevFocus.focus();
    },
  };
}

/** Tally-style "Accept? Yes or No" confirmation. Resolves true/false. */
export function confirmBox(question = 'Accept?', detail = '') {
  return new Promise((resolve) => {
    let m;
    const done = (v) => {
      m.close();
      resolve(v);
    };
    const yes = h('button.btn.primary', { onclick: () => done(true) }, hotLabel('Yes', 'Y'));
    const no = h('button.btn', { onclick: () => done(false) }, hotLabel('No', 'N'));
    m = openModal(
      [h('div.modal-title', question), detail ? h('div.modal-detail', detail) : null, h('div.modal-actions', yes, no)],
      {
        className: 'confirm',
        onKey: (e) => {
          const k = e.key.toLowerCase();
          if (k === 'y' || (k === 'enter' && document.activeElement === yes)) return done(true), true;
          if (k === 'n' || k === 'escape') return done(false), true;
          if (k === 'enter' && document.activeElement === no) return done(false), true;
          if (k === 'arrowleft' || k === 'arrowright' || k === 'tab') {
            (document.activeElement === yes ? no : yes).focus();
            return true;
          }
          return false;
        },
      },
    );
    yes.focus();
  });
}

/** Small form dialog. fields: [{key,label,value,type}] -> resolves values or null */
export function formDialog(title, fields, { hint = '' } = {}) {
  return new Promise((resolve) => {
    let m;
    const inputs = {};
    const rows = fields.map((f) => {
      const input =
        f.type === 'select'
          ? h('select.inp', f.options.map((o) => h('option', { value: o.value, selected: o.value === f.value }, o.label)))
          : h('input.inp', { value: f.type === 'date' ? fdate(f.value) : (f.value ?? ''), autocomplete: 'off' });
      inputs[f.key] = input;
      return h('label.frow', h('span.flabel', f.label), h('span.fsep', ':'), input);
    });
    const finish = () => {
      const out = {};
      for (const f of fields) {
        let v = inputs[f.key].value;
        if (f.type === 'date') {
          v = parseDate(v, f.value || undefined);
          if (!v) {
            toast(`Invalid date in ${f.label}`, 'error');
            inputs[f.key].focus();
            return;
          }
        }
        out[f.key] = v;
      }
      m.close();
      resolve(out);
    };
    m = openModal([h('div.modal-title', title), ...rows, hint ? h('div.modal-detail', hint) : null], {
      onKey: (e) => {
        if (e.key === 'Escape') {
          m.close();
          resolve(null);
          return true;
        }
        if (e.key === 'Enter' || (e.key.toLowerCase() === 'a' && e.ctrlKey)) {
          const list = Object.values(inputs);
          const i = list.indexOf(document.activeElement);
          if (e.ctrlKey || i === list.length - 1) finish();
          else {
            list[i + 1].focus();
            list[i + 1].select?.();
          }
          return true;
        }
        return false;
      },
    });
    const first = Object.values(inputs)[0];
    first.focus();
    first.select?.();
  });
}

// ---------------------------------------------------------------- menu

/**
 * Keyboard menu. sections: [{title, items:[{label, key, action, hint}]}]
 * Up/Down move, Enter activates, hotkey letter activates directly.
 */
export function menu(sections, { title, onPick } = {}) {
  const items = [];
  const box = h('div.menu', { tabIndex: -1 });
  if (title) box.append(h('div.menu-head', title));
  for (const sec of sections) {
    if (sec.title) box.append(h('div.menu-sec', sec.title));
    for (const it of sec.items) {
      const row = h('div.menu-item', { onclick: () => activate(items.indexOf(entry)) }, hotLabel(it.label, it.key),
        it.hint ? h('span.menu-hint', it.hint) : null);
      const entry = { ...it, row };
      items.push(entry);
      box.append(row);
      row.addEventListener('mousemove', () => select(items.indexOf(entry)));
    }
  }
  let cur = 0;
  function select(i, scroll = true) {
    if (!items.length) return;
    cur = (i + items.length) % items.length;
    items.forEach((it, j) => it.row.classList.toggle('sel', j === cur));
    if (scroll) items[cur].row.scrollIntoView({ block: 'nearest' });
  }
  function activate(i) {
    select(i);
    const it = items[cur];
    if (it.action) it.action();
    else if (onPick) onPick(it);
  }
  select(0, false);
  return {
    el: box,
    onKey(e) {
      if (e.ctrlKey || e.altKey || e.metaKey) return false;
      if (e.key === 'ArrowDown') return select(cur + 1), true;
      if (e.key === 'ArrowUp') return select(cur - 1), true;
      if (e.key === 'Home') return select(0), true;
      if (e.key === 'End') return select(items.length - 1), true;
      if (e.key === 'Enter') return activate(cur), true;
      if (e.key.length === 1) {
        const i = items.findIndex((it) => it.key && it.key.toLowerCase() === e.key.toLowerCase());
        if (i >= 0) return activate(i), true;
      }
      return false;
    },
    select,
  };
}

// ---------------------------------------------------------------- picker

let activePicker = null;
export const pickerActive = () => activePicker;

function matches(label, q) {
  if (!q) return true;
  const l = label.toLowerCase();
  return q.toLowerCase().split(/\s+/).every((w) => l.includes(w));
}

/**
 * Attach a Tally-like "List of ..." side panel to an input.
 * opts: { title, source: () => items|Promise<items>, onPick(item), create: {label, run()} , required }
 * items: [{id, label, sub}]
 */
export function attachPicker(input, opts) {
  input.setAttribute('autocomplete', 'off');
  input.classList.add('pick');
  let items = [];
  let shown = [];
  let cur = 0;
  let panel = null;

  function render() {
    if (!panel) return;
    const q = input.value.trim();
    shown = items.filter((it) => matches(it.label, input.dataset.id && q === input.dataset.label ? '' : q));
    if (cur >= shown.length) cur = 0;
    if (input.dataset.id && q === input.dataset.label) {
      const i = shown.findIndex((s) => String(s.id) === input.dataset.id);
      if (i >= 0) cur = i;
    }
    const list = panel.querySelector('.plist');
    list.replaceChildren(
      ...shown.map((it, i) =>
        h(`div.pitem${i === cur ? '.sel' : ''}`, { onmousedown: (e) => (e.preventDefault(), pick(i)) },
          h('span', it.label), it.sub ? h('span.psub', it.sub) : null),
      ),
    );
    if (!shown.length) list.append(h('div.pempty', 'No match'));
    list.querySelector('.sel')?.scrollIntoView({ block: 'nearest' });
  }

  async function open() {
    close();
    panel = h('div.picker', h('div.picker-head', opts.title || 'List'),
      opts.create ? h('div.picker-create', { onmousedown: (e) => (e.preventDefault(), createNew()) },
        h('kbd', 'Alt+C'), ' ', opts.create.label || 'Create') : null,
      h('div.plist'));
    document.getElementById('picker-host').append(panel);
    activePicker = api;
    input.select();
    try {
      items = await opts.source();
    } catch (e) {
      items = [];
    }
    render();
  }

  function close() {
    panel?.remove();
    panel = null;
    if (activePicker === api) activePicker = null;
  }

  function setValue(item) {
    input.value = item ? item.label : '';
    input.dataset.id = item ? item.id : '';
    input.dataset.label = item ? item.label : '';
  }

  function pick(i) {
    const it = shown[i];
    if (!it) return false;
    setValue(it);
    close();
    opts.onPick?.(it);
    input.dispatchEvent(new CustomEvent('picked', { detail: it, bubbles: true }));
    return true;
  }

  async function createNew() {
    if (!opts.create) return;
    close();
    const created = await opts.create.run(input.value.trim());
    if (created) {
      setValue(created);
      opts.onPick?.(created);
      input.dispatchEvent(new CustomEvent('picked', { detail: created, bubbles: true }));
    }
    input.focus();
  }

  const api = {
    onKey(e) {
      if (!panel) return false;
      if (e.key === 'ArrowDown') return (cur = Math.min(cur + 1, shown.length - 1)), render(), true;
      if (e.key === 'ArrowUp') return (cur = Math.max(cur - 1, 0)), render(), true;
      if (e.key === 'PageDown') return (cur = Math.min(cur + 10, shown.length - 1)), render(), true;
      if (e.key === 'PageUp') return (cur = Math.max(cur - 10, 0)), render(), true;
      if (e.altKey && e.key.toLowerCase() === 'c' && opts.create) return createNew(), true;
      if (e.key === 'Enter' || e.key === 'Tab') {
        if (e.shiftKey) return false;
        if (!input.value.trim() && !opts.required) {
          setValue(null);
          close();
          opts.onPick?.(null);
          return false; // let the form move on
        }
        if (!pick(cur)) {
          toast('Select a value from the list', 'error');
          return true;
        }
        return false; // picked; form navigation moves to the next field
      }
      if (e.key === 'Escape') return close(), false; // let the screen handle Esc (quit)
      return false;
    },
    close,
    setValue,
    refresh: async () => {
      items = await opts.source();
      render();
    },
  };

  input.addEventListener('focus', open);
  input.addEventListener('blur', () => setTimeout(() => activePicker === api && document.activeElement !== input && close(), 0));
  input.addEventListener('input', () => {
    if (!panel && document.activeElement === input) open();
    cur = 0;
    if (input.value !== input.dataset.label) input.dataset.id = '';
    render();
  });
  input._picker = api;
  return api;
}

// ---------------------------------------------------------------- field navigation

/**
 * Enter / Tab move to the next [data-nav] field within root (Tally flow).
 * Returns a key handler. onLast is called when Enter is pressed on the last field.
 */
export function navigator(root, onLast) {
  return (e) => {
    const el = document.activeElement;
    if (!root.contains(el) || !el.matches('[data-nav]')) return false;
    if (el._picker && el._picker.onKey(e)) return true;
    if (e.key === 'Enter' && el.tagName === 'TEXTAREA' && !e.ctrlKey && e.shiftKey) return false;
    if (e.key === 'Enter' && !e.ctrlKey && !e.altKey) {
      const fields = [...root.querySelectorAll('[data-nav]')].filter((f) => !f.disabled && f.offsetParent !== null);
      const i = fields.indexOf(el);
      if (el.dataset.onenter) {
        const r = el.dispatchEvent(new CustomEvent('naventer', { cancelable: true, bubbles: true }));
        if (!r) return true;
      }
      if (i === fields.length - 1) {
        onLast?.();
      } else {
        fields[i + 1].focus();
        fields[i + 1].select?.();
      }
      return true;
    }
    if (e.key === 'Escape' && el._picker && pickerActive()) return el._picker.onKey(e);
    return false;
  };
}

export function field(label, input, { hint } = {}) {
  return h('label.frow', h('span.flabel', label), h('span.fsep', ':'), input, hint ? h('span.fhint', hint) : null);
}

export function yesNo(value) {
  const s = h('select.inp.yn', { dataset: { nav: '' } }, h('option', { value: 'yes' }, 'Yes'), h('option', { value: 'no' }, 'No'));
  s.value = value ? 'yes' : 'no';
  s.addEventListener('keydown', (e) => {
    if (e.key.toLowerCase() === 'y') s.value = 'yes';
    if (e.key.toLowerCase() === 'n') s.value = 'no';
  });
  return s;
}
