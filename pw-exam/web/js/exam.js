// Exam runner — flowchart steps 7–10 and 13.
//  * server-authoritative timer (re-synced by heartbeat)
//  * answers kept in a local queue (localStorage) and auto-saved every 30 s and after each change
//  * works offline: queue survives reloads and is uploaded on reconnect (idempotent, highest seq wins)
//  * anti-cheat: tab switch / blur / full-screen exit / copy-paste are reported to the server
import { NetworkError, api, auth } from './api.js';
import { go, route } from './main.js';
import { LETTERS, clear, confirmDialog, fmtDuration, h, modal, store, toast } from './util.js';

const AUTOSAVE_MS = 30000;
const HEARTBEAT_MS = 30000;
const qKey = (aid) => `pwexam.queue.${aid}`;
const aKey = (aid) => `pwexam.attempt.${aid}`;

export function cacheAttempt(a) {
  store.set(aKey(a.id), { data: a, offset: a.server_now - Date.now() / 1000, user: auth.user?.id });
}

export function pendingQueues() {
  const out = [];
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (k?.startsWith('pwexam.queue.') && Object.keys(store.get(k, {})).length) out.push(Number(k.split('.').pop()));
    }
  } catch {
    /* storage unavailable */
  }
  return out;
}

/** Upload the local queue for an attempt. Resolves with the server response (or null if nothing to send). */
export async function syncQueue(aid) {
  const queue = store.get(qKey(aid), {});
  const items = Object.entries(queue).map(([qid, v]) => ({ question_id: Number(qid), ...v }));
  if (!items.length) return null;
  try {
    const res = await api.post(`/my/attempts/${aid}/answers`, { answers: items });
    const latest = store.get(qKey(aid), {});
    for (const it of items) if (latest[it.question_id]?.seq === it.seq) delete latest[it.question_id];
    store.set(qKey(aid), latest);
    return res;
  } catch (e) {
    if (e.status === 409 || e.status === 403 || e.status === 404) store.del(qKey(aid)); // exam closed: nothing more to sync
    throw e;
  }
}

route('/attempt/:aid', async (root, p) => {
  const aid = p.aid;
  let data;
  let offset;
  try {
    data = await api.get(`/my/attempts/${aid}`);
    cacheAttempt(data);
    offset = data.server_now - Date.now() / 1000;
  } catch (e) {
    const cached = store.get(aKey(aid));
    if (e instanceof NetworkError && cached && cached.user === auth.user?.id) {
      data = cached.data; // Step 13: resume offline from the device cache
      offset = cached.offset;
      toast('Offline — resumed from this device. Answers will sync when you reconnect.', 'warn');
    } else throw e;
  }
  if (data.status !== 'in_progress') {
    store.del(qKey(aid));
    return go(`/result/${aid}`);
  }

  // ------------------------------------------------------------ state
  const exam = data.exam;
  const questions = data.questions;
  const answers = {};
  for (const q of questions) answers[q.id] = { selected: [], flagged: false, seq: 0, visited: false };
  for (const [qid, a] of Object.entries(data.answers)) Object.assign(answers[qid] || {}, a, { visited: true });
  for (const [qid, a] of Object.entries(store.get(qKey(aid), {}))) {
    if (answers[qid] && a.seq > (answers[qid].seq || 0)) Object.assign(answers[qid], a, { visited: true }); // local newer than server
  }
  let deadline = data.deadline;
  let current = 0;
  let violations = data.violations;
  let finished = false;
  let lastSeq = 0;
  let online = navigator.onLine;
  let saveState = 'saved';
  const timers = [];
  const listeners = [];
  const on = (target, ev, fn, opts) => {
    target.addEventListener(ev, fn, opts);
    listeners.push(() => target.removeEventListener(ev, fn, opts));
  };
  const now = () => Date.now() / 1000 + offset;
  const nextSeq = () => (lastSeq = Math.max(lastSeq + 1, Date.now()));

  // ------------------------------------------------------------ DOM
  const timerEl = h('div.timer', { role: 'timer', 'aria-live': 'off' });
  const netEl = h('span.net');
  const saveEl = h('span.save');
  const warnEl = h('div.warn-banner.hidden', { role: 'alert' });
  const qPanel = h('section.qpanel', { 'aria-live': 'polite' });
  const palette = h('div.palette');
  const counts = h('div.pal-counts');
  const gate = h('div.gate.hidden');
  const view = h('div.exam-shell',
    h('header.exam-top',
      h('div.exam-title', h('span.brand-mark.sm', 'PW'), h('div', h('b', exam.title), h('small', `${auth.user.name} · ${auth.user.login_id}`))),
      h('div.exam-status', netEl, saveEl),
      timerEl),
    warnEl,
    h('div.exam-body', qPanel,
      h('aside.exam-side', h('h4', 'Question palette'), palette, counts,
        h('div.legend.small', h('span', h('i.pal.answered'), 'Answered'), h('span', h('i.pal.visited'), 'Not answered'),
          h('span', h('i.pal.flagged'), 'Review'), h('span', h('i.pal'), 'Not visited')),
        h('button.btn.primary.block', { onclick: () => confirmSubmit() }, 'Submit exam'))),
    gate);
  root.append(view);

  // ------------------------------------------------------------ rendering
  function isAnswered(a) {
    return a.selected.length > 0;
  }

  function renderPalette() {
    clear(palette, questions.map((q, i) => {
      const a = answers[q.id];
      const cls = ['pal', isAnswered(a) ? 'answered' : a.visited ? 'visited' : '', a.flagged ? 'flagged' : '', i === current ? 'current' : ''].filter(Boolean).join('.');
      return h(`button.${cls}`, { type: 'button', onclick: () => show(i), 'aria-label': `Question ${i + 1}` }, i + 1);
    }));
    const vals = Object.values(answers);
    clear(counts,
      h('span', h('b', vals.filter(isAnswered).length), ' answered'),
      h('span', h('b', vals.filter((a) => !isAnswered(a)).length), ' not answered'),
      h('span', h('b', vals.filter((a) => a.flagged).length), ' marked'));
  }

  function show(i) {
    current = Math.max(0, Math.min(i, questions.length - 1));
    const q = questions[current];
    const a = answers[q.id];
    a.visited = true;
    const multi = q.qtype === 'multi';
    clear(qPanel,
      h('div.q-meta', h('span.q-no', `Question ${current + 1} of ${questions.length}`), h('span.badge.info', q.subject),
        h('span.small.muted', `${multi ? 'One or more correct answers' : 'Single correct answer'} · +${q.marks}${q.negative ? ` / −${q.negative}` : ''}`),
        a.flagged ? h('span.badge.warn', 'Marked for review') : null),
      h('p.q-text', q.text),
      h('div.q-options', { role: multi ? 'group' : 'radiogroup' }, q.options.map((o, idx) => {
        const checked = a.selected.includes(o.key);
        return h(`label.q-opt${checked ? '.checked' : ''}`,
          h('input', { type: multi ? 'checkbox' : 'radio', name: `q${q.id}`, checked, onchange: () => choose(q, o.key) }),
          h('span.q-letter', LETTERS[idx]), h('span.q-opt-text', o.text));
      })),
      h('div.q-actions',
        h('button.btn.ghost', { disabled: current === 0, onclick: () => show(current - 1) }, '← Previous'),
        h('button.btn.ghost', { onclick: () => update(q, { selected: [] }) }, 'Clear response'),
        h('button.btn.warnbtn', { onclick: () => { update(q, { flagged: !a.flagged }); if (!a.flagged) return; show(current + 1); } }, a.flagged ? 'Unmark review' : 'Mark for review & next'),
        h('button.btn.primary', { onclick: () => show(current + 1) }, current === questions.length - 1 ? 'Save' : 'Save & next →')));
    renderPalette();
  }

  function choose(q, key) {
    const a = answers[q.id];
    const selected = q.qtype === 'multi'
      ? (a.selected.includes(key) ? a.selected.filter((k) => k !== key) : [...a.selected, key])
      : [key];
    update(q, { selected });
  }

  function update(q, patch) {
    if (finished) return;
    const a = answers[q.id];
    Object.assign(a, patch);
    a.seq = nextSeq();
    const queue = store.get(qKey(aid), {});
    queue[q.id] = { selected: a.selected, flagged: a.flagged, seq: a.seq, client_ts: Math.floor(now()) };
    if (!store.set(qKey(aid), queue)) pendingMemory[q.id] = queue[q.id];
    persistSnapshot();
    setSave('pending');
    show(current);
    debouncedSave();
  }

  // Fallback when localStorage is unavailable (private mode): keep the queue in memory.
  const pendingMemory = {};

  // Keep the device copy of the paper *and all answers so far* current, so an offline reload
  // shows answers that were already synced as well as the queued ones.
  function persistSnapshot() {
    const snap = {};
    for (const [qid, a] of Object.entries(answers)) {
      if (a.seq || a.selected.length || a.flagged) snap[qid] = { selected: a.selected, flagged: a.flagged, seq: a.seq };
    }
    store.set(aKey(aid), { data: { ...data, answers: snap, deadline }, offset, user: auth.user?.id });
  }

  function setSave(s) {
    saveState = s;
    const label = { saved: '✓ All answers saved', pending: '● Saving…', offline: '⚠ Saved on this device — will sync' }[s];
    saveEl.textContent = label;
    saveEl.className = `save ${s}`;
  }

  function setNet(isOnline) {
    online = isOnline;
    netEl.textContent = isOnline ? 'Online' : 'Offline';
    netEl.className = `net ${isOnline ? 'on' : 'off'}`;
  }

  // ------------------------------------------------------------ saving (steps 9 & 13)
  let saving = null;
  async function flush() {
    if (saving) return saving;
    saving = (async () => {
      try {
        if (Object.keys(pendingMemory).length) {
          const q = store.get(qKey(aid), {});
          store.set(qKey(aid), { ...pendingMemory, ...q });
        }
        const res = await syncQueue(aid);
        setNet(true);
        if (!Object.keys(store.get(qKey(aid), {})).length) setSave('saved');
        if (res?.status && res.status !== 'in_progress') end(res.status);
        return true;
      } catch (e) {
        if (e instanceof NetworkError) {
          setNet(false);
          setSave('offline');
          return false;
        }
        if (e.status === 409) {
          end('submitted');
          return false;
        }
        toast(e.message, 'error');
        return false;
      } finally {
        saving = null;
      }
    })();
    return saving;
  }
  let saveTimer;
  const debouncedSave = () => {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(flush, 2000);
  };
  timers.push(setInterval(flush, AUTOSAVE_MS));

  // Heartbeat: resync clock, detect proctor force-submit.
  timers.push(setInterval(async () => {
    try {
      const s = await api.get(`/my/attempts/${aid}/status`);
      offset = s.server_now - Date.now() / 1000;
      deadline = s.deadline;
      violations = s.violations;
      setNet(true);
      if (s.status !== 'in_progress') end(s.status);
    } catch (e) {
      if (e instanceof NetworkError) setNet(false);
    }
  }, HEARTBEAT_MS));

  on(window, 'online', () => { setNet(true); flush(); });
  on(window, 'offline', () => setNet(false));

  // ------------------------------------------------------------ timer (time up? → step 10)
  function tick() {
    const left = deadline - now();
    timerEl.textContent = fmtDuration(left);
    timerEl.classList.toggle('low', left <= 300);
    timerEl.classList.toggle('critical', left <= 60);
    if (left <= 0 && !finished) timeUp();
  }
  timers.push(setInterval(tick, 500));

  async function timeUp() {
    finished = true;
    lock('Time is up', 'Submitting your answers…');
    await submitNow(true);
  }

  // ------------------------------------------------------------ submit (step 10)
  async function confirmSubmit() {
    await flush();
    const vals = Object.values(answers);
    const ok = await confirmDialog('Submit exam?',
      `Answered ${vals.filter(isAnswered).length} of ${questions.length}. Not answered: ${vals.filter((a) => !isAnswered(a)).length}. Marked for review: ${vals.filter((a) => a.flagged).length}. You cannot change answers after submitting.`,
      { okLabel: 'Submit' });
    if (!ok || finished) return;
    finished = true;
    lock('Submitting', 'Please wait…');
    await submitNow(false);
  }

  async function submitNow(auto) {
    const queue = store.get(qKey(aid), {});
    const items = Object.entries({ ...pendingMemory, ...queue }).map(([qid, v]) => ({ question_id: Number(qid), ...v }));
    for (let attempt = 0; ; attempt++) {
      try {
        const r = await api.post(`/my/attempts/${aid}/submit`, { answers: items });
        store.del(qKey(aid));
        store.del(aKey(aid));
        return end(r.status);
      } catch (e) {
        if (e.status === 409 || e.status === 403) {
          store.del(qKey(aid));
          return end('submitted');
        }
        if (!(e instanceof NetworkError)) {
          toast(e.message, 'error');
          return end('submitted');
        }
        setNet(false);
        lock(auto ? 'Time is up' : 'Waiting for connection',
          'Your answers are saved on this device and will be submitted automatically when the connection returns. Do not close this page.');
        await new Promise((r) => setTimeout(r, Math.min(15000, 3000 * (attempt + 1))));
      }
    }
  }

  function lock(title, message) {
    clear(gate, h('div.gate-box', h('h2', title), h('p', message)));
    gate.classList.remove('hidden');
  }

  function end(status) {
    if (view.dataset.ended) return;
    view.dataset.ended = '1';
    finished = true;
    teardown();
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    const msg = status === 'terminated' ? 'Your exam was submitted automatically because of repeated warnings.' : 'Your answers have been submitted successfully.';
    lock(status === 'terminated' ? 'Exam ended' : 'Exam submitted', msg);
    gate.firstChild.append(h('button.btn.primary', { onclick: () => go(`/result/${aid}`) }, 'Continue'));
  }

  // ------------------------------------------------------------ anti-cheat (step 8)
  const lastLogged = {};
  async function violation(kind, detail = '') {
    if (finished) return;
    const counted = ['tab_switch', 'fullscreen_exit', 'window_blur'].includes(kind);
    if (!counted && lastLogged[kind] && Date.now() - lastLogged[kind] < 10000) return;
    lastLogged[kind] = Date.now();
    try {
      const r = await api.post(`/my/attempts/${aid}/violations`, { kind, detail });
      violations = r.violations;
      if (r.terminated) return end('terminated');
      if (counted && r.max_violations) showWarning(`Warning ${r.violations} of ${r.max_violations}: ${WARN_TEXT[kind]} Your exam will be submitted automatically after ${r.max_violations} warnings.`);
      else if (counted) showWarning(`${WARN_TEXT[kind]} This has been reported to the invigilator.`);
    } catch {
      /* offline: the invigilator sees the gap in the heartbeat instead */
    }
  }
  const WARN_TEXT = {
    tab_switch: 'Switching tabs or apps is not allowed.',
    window_blur: 'Leaving the exam window is not allowed.',
    fullscreen_exit: 'Leaving full screen is not allowed.',
  };
  function showWarning(text) {
    warnEl.textContent = text;
    warnEl.classList.remove('hidden');
    clearTimeout(warnEl._t);
    warnEl._t = setTimeout(() => warnEl.classList.add('hidden'), 8000);
  }
  on(document, 'visibilitychange', () => document.hidden && violation('tab_switch', 'page hidden'));
  on(window, 'blur', () => setTimeout(() => !document.hidden && !document.hasFocus() && violation('window_blur'), 300));
  for (const ev of ['copy', 'cut', 'paste', 'contextmenu']) {
    on(document, ev, (e) => {
      e.preventDefault();
      violation(ev === 'contextmenu' ? 'context_menu' : ev === 'cut' ? 'copy' : ev);
    });
  }
  on(window, 'beforeprint', () => violation('print'));
  on(window, 'beforeunload', (e) => {
    if (!finished) {
      e.preventDefault();
      e.returnValue = '';
    }
  });
  function fullscreenGate() {
    if (!exam.require_fullscreen || finished || !document.documentElement.requestFullscreen) return;
    if (document.fullscreenElement) {
      gate.classList.add('hidden');
      return;
    }
    clear(gate, h('div.gate-box', h('h2', 'Full screen required'), h('p', 'This exam must be taken in full-screen mode. Your timer keeps running.'),
      h('button.btn.primary.lg', { onclick: () => document.documentElement.requestFullscreen().catch(() => toast('Full screen was blocked by the browser', 'error')) }, 'Enter full screen')));
    gate.classList.remove('hidden');
  }
  let wasFull = !!document.fullscreenElement;
  on(document, 'fullscreenchange', () => {
    if (wasFull && !document.fullscreenElement && !finished) violation('fullscreen_exit');
    wasFull = !!document.fullscreenElement;
    fullscreenGate();
  });

  // Keyboard: ← → navigate, A–H choose.
  on(document, 'keydown', (e) => {
    if (finished || !gate.classList.contains('hidden') || e.ctrlKey || e.metaKey || e.altKey) return;
    if (document.querySelector('.modal-back')) return;
    if (e.key === 'ArrowRight') show(current + 1);
    else if (e.key === 'ArrowLeft') show(current - 1);
    else {
      const idx = LETTERS.indexOf(e.key.toUpperCase());
      const q = questions[current];
      if (idx >= 0 && idx < q.options.length) choose(q, q.options[idx].key);
    }
  });

  function teardown() {
    timers.forEach(clearInterval);
    clearTimeout(saveTimer);
    listeners.forEach((off) => off());
    listeners.length = 0;
  }

  // ------------------------------------------------------------ start
  setNet(navigator.onLine);
  setSave(Object.keys(store.get(qKey(aid), {})).length ? 'pending' : 'saved');
  show(0);
  tick();
  fullscreenGate();
  if (Object.keys(store.get(qKey(aid), {})).length) flush();
  if (violations && exam.max_violations) showWarning(`You have ${violations} of ${exam.max_violations} warnings.`);
  return () => {
    teardown();
    if (!finished) flush();
  };
}, { role: 'candidate', bare: true });

export { modal };
