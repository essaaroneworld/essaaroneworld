// Shell, hash router, login and account screens.
import { api, auth } from './api.js';
import { $, clear, field, h, input, toast, add } from './util.js';

const routes = [];
let cleanup = null;

export function route(pattern, handler, { role = 'any', bare = false } = {}) {
  const keys = [];
  const rx = new RegExp('^' + pattern.replace(/:(\w+)/g, (_, k) => (keys.push(k), '(\\d+)')) + '$');
  routes.push({ rx, keys, handler, role, bare });
}

export function go(path) {
  if (location.hash === `#${path}`) render();
  else location.hash = path;
}

export const isStaff = (u) => u && u.role !== 'candidate';

function homeFor(user) {
  if (!user) return '/login';
  if (user.must_change_password) return '/account';
  return isStaff(user) ? '/admin' : '/home';
}

async function render() {
  if (cleanup) {
    try {
      cleanup();
    } catch {
      /* ignore */
    }
    cleanup = null;
  }
  document.querySelectorAll('.modal-back').forEach((m) => m.remove()); // dialogs never outlive their screen
  const path = location.hash.slice(1) || homeFor(auth.user);
  const user = auth.user;
  for (const r of routes) {
    const m = r.rx.exec(path);
    if (!m) continue;
    if (r.role !== 'public' && !user) return go('/login');
    if (r.role === 'staff' && !isStaff(user)) return go(homeFor(user));
    if (r.role === 'candidate' && isStaff(user)) return go(homeFor(user));
    if (user?.must_change_password && path !== '/account' && r.role !== 'public') return go('/account');
    const params = Object.fromEntries(r.keys.map((k, i) => [k, Number(m[i + 1])]));
    const app = $('#app');
    const content = r.bare ? app : shell(user, path);
    if (r.bare) clear(app);
    clear(content, h('div.loading', 'Loading…'));
    try {
      const node = h('div.page');
      const c = await r.handler(node, params);
      if (typeof c === 'function') cleanup = c;
      clear(content, node);
    } catch (e) {
      console.error(e);
      clear(content, h('div.card.error', h('h3', 'Something went wrong'), h('p', e.message),
        h('button.btn', { onclick: () => render() }, 'Retry')));
    }
    window.scrollTo(0, 0);
    return;
  }
  go(homeFor(user));
}

// ---------------------------------------------------------------- layout

const NAV = [
  { path: '/admin', label: 'Dashboard', icon: '▦', perm: 'dashboard' },
  { path: '/admin/questions', label: 'Question Bank', icon: '?', perm: 'questions' },
  { path: '/admin/exams', label: 'Exams', icon: '✎', perm: 'schedules.view' },
  { path: '/admin/candidates', label: 'Batches & Candidates', icon: '☰', perm: 'users' },
  { path: '/admin/schedules', label: 'Schedules & Results', icon: '◷', perm: 'schedules.view' },
  { path: '/admin/notifications', label: 'Notifications', icon: '✉', perm: 'audit' },
  { path: '/admin/audit', label: 'Audit Log', icon: '⎙', perm: 'audit' },
  { path: '/admin/users', label: 'Staff Users', icon: '⚿', perm: 'users' },
];

export function can(perm) {
  const u = auth.user;
  if (!u) return false;
  if (u.role === 'admin') return perm !== 'take_exam';
  const map = {
    examiner: ['questions', 'exams', 'schedules.view', 'results.view', 'dashboard'],
    proctor: ['monitor', 'schedules.view', 'results.view', 'dashboard'],
    candidate: ['take_exam'],
  };
  return (map[u.role] || []).includes(perm);
}

function shell(user, path) {
  const content = h('main.content');
  const staff = isStaff(user);
  const nav = staff
    ? h('nav.sidenav', { 'aria-label': 'Admin' }, NAV.filter((n) => can(n.perm)).map((n) => h(`a.navlink${
      (n.path === '/admin' ? path === '/admin' : path.startsWith(n.path) || (n.path === '/admin/schedules' && /^\/admin\/(monitor|results)/.test(path))) ? '.active' : ''}`,
    { href: `#${n.path}` }, h('span.navicon', n.icon), h('span', n.label))))
    : null;
  const layout = h(`div.layout${staff ? '' : '.candidate'}`,
    h('header.topbar',
      h('a.brand', { href: `#${homeFor(user)}` }, h('span.brand-mark', 'PW'), h('span.brand-text', h('b', 'PW Batch'), h('small', 'Online Examination System'))),
      h('div.topbar-right',
        h('span.whoami', h('b', user.name), h('small', `${user.login_id} · `, h('span.role', user.role))),
        h('a.btn.ghost.sm', { href: '#/account' }, 'Account'),
        h('button.btn.ghost.sm', { onclick: logout }, 'Log out'))),
    nav,
    content,
    h('footer.footer', 'PW Batch Online Examination System · a product of ESS AAR SOFTEK PLC'));
  clear($('#app'), layout);
  return content;
}

async function logout() {
  try {
    await api.post('/auth/logout');
  } catch {
    /* already logged out / offline */
  }
  auth.clear();
  go('/login');
}

// ---------------------------------------------------------------- login

route('/login', async (root) => {
  if (auth.user) return go(homeFor(auth.user));
  const loginId = input({ autocomplete: 'username', required: true, placeholder: 'e.g. PW001' });
  const password = input({ type: 'password', autocomplete: 'current-password', required: true });
  const otp = input({ inputmode: 'numeric', autocomplete: 'one-time-code', maxlength: 6, placeholder: '6-digit code' });
  const msg = h('p.form-error', { role: 'alert' });
  const otpBox = h('div.hidden', field('One-time password', otp), h('p.hint.otp-hint'));
  const submit = h('button.btn.primary.block', { type: 'submit' }, 'Log in');
  let otpToken = null;
  const form = h('form.login-form', {
    onsubmit: async (e) => {
      e.preventDefault();
      msg.textContent = '';
      submit.disabled = true;
      try {
        const res = otpToken
          ? await api.post('/auth/verify-otp', { otp_token: otpToken, code: otp.value.trim() })
          : await api.post('/auth/login', { login_id: loginId.value.trim(), password: password.value });
        if (res.otp_required) {
          otpToken = res.otp_token;
          otpBox.classList.remove('hidden');
          otpBox.querySelector('.otp-hint').textContent = `We sent a code to ${res.sent_to}. It is valid for 5 minutes.`;
          loginId.disabled = password.disabled = true;
          submit.textContent = 'Verify & continue';
          otp.focus();
          return;
        }
        auth.set(res);
        go(homeFor(res.user));
      } catch (err) {
        msg.textContent = err.message;
        if (otpToken && /expired|again/i.test(err.message)) {
          otpToken = null;
          otpBox.classList.add('hidden');
          loginId.disabled = password.disabled = false;
          submit.textContent = 'Log in';
        }
      } finally {
        submit.disabled = false;
      }
    },
  }, h('h2', 'Sign in'), h('p.muted', 'Candidates use the roll number / login ID issued by the institute.'),
  field('Login ID', loginId), field('Password', password), otpBox, msg, submit);

  add(root, h('div.login-wrap',
    h('section.login-hero',
      h('div.hero-mark', 'PW'),
      h('h1', 'PW Batch', h('br'), 'Online Examination System'),
      h('p.hero-sub', 'Secure, proctored MCQ examinations — online, in an offline LAN lab, or on mobile.'),
      h('ul.hero-list',
        h('li', 'Question bank with difficulty levels and CSV import'),
        h('li', 'Randomised papers, server-timed, auto-save every 30 seconds'),
        h('li', 'Tab-switch and full-screen anti-cheat monitoring'),
        h('li', 'Works through network drops — answers sync on reconnect'),
        h('li', 'Instant scoring, rank, percentile and PDF report cards')),
      h('p.hero-vendor', 'A product of ', h('b', 'ESS AAR SOFTEK PLC'))),
    h('section.login-card', form)));
  setTimeout(() => loginId.focus(), 0);
}, { role: 'public', bare: true });

// ---------------------------------------------------------------- account

route('/account', async (root) => {
  const u = auth.user;
  const oldPw = input({ type: 'password', autocomplete: 'current-password' });
  const newPw = input({ type: 'password', autocomplete: 'new-password' });
  const newPw2 = input({ type: 'password', autocomplete: 'new-password' });
  const form = h('form.card.narrow', {
    onsubmit: async (e) => {
      e.preventDefault();
      if (newPw.value !== newPw2.value) return toast('New passwords do not match', 'error');
      try {
        const res = await api.post('/auth/change-password', { old_password: oldPw.value, new_password: newPw.value });
        auth.set(res);
        toast('Password changed', 'ok');
        go(homeFor(res.user));
      } catch (err) {
        toast(err.message, 'error');
      }
    },
  },
  h('h2', 'Change password'),
  u.must_change_password ? h('p.notice', 'For your security, please set a new password before continuing.') : null,
  field('Current password', oldPw), field('New password', newPw, 'At least 8 characters with letters and digits'),
  field('Confirm new password', newPw2), h('button.btn.primary', { type: 'submit' }, 'Update password'));
  add(root, h('div.page-head', h('h1', 'My account'), h('p.muted', `${u.name} · ${u.login_id} · ${u.role}`)), form);
  if (u.role === 'admin' && !u.must_change_password) {
    add(root, h('div.card.narrow', h('h3', 'Backup'), h('p.muted', 'Download a consistent copy of the whole database (all exams, answers and results).'),
      h('button.btn', { onclick: () => api.download('/backup', `pwexam-backup-${new Date().toISOString().slice(0, 10)}.db`).catch((e) => toast(e.message, 'error')) }, 'Download backup')));
  }
});

// ---------------------------------------------------------------- boot

export function start() {
  auth.onLost((message) => {
    toast(message || 'Your session has ended. Please log in again.', 'error');
    go('/login');
  });
  window.addEventListener('hashchange', render);
  if ('serviceWorker' in navigator && window.isSecureContext) {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  }
  render();
}
