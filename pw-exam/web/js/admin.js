// Admin console: flowchart steps 1–4 (setup & scheduling) and 11–14 (results, sync, review).
import { api } from './api.js';
import { can, go, route } from './main.js';
import {
  LETTERS, checkbox, clear, confirmDialog, debounce, downloadBlob, field, fmtDateTime, fmtDuration, fmtMinutes, fromLocalInput,
  h, input, modal, readFile, select, toLocalInput, toast, add,
} from './util.js';

// ---------------------------------------------------------------- helpers

function pageHead(title, sub, ...actions) {
  return h('div.page-head', h('div', h('h1', title), sub ? h('p.muted', sub) : null), h('div.actions', actions));
}

function table(headers, rows, { empty = 'Nothing here yet.' } = {}) {
  return h('div.table-wrap', h('table.tbl',
    h('thead', h('tr', headers.map((x) => (typeof x === 'string' ? h('th', x) : h(`th.${x.cls}`, x.label))))),
    h('tbody', rows.length ? rows : h('tr', h('td.empty', { colSpan: headers.length }, empty)))));
}

const badge = (text, kind = '') => h(`span.badge${kind ? '.' + kind : ''}`, text);
const statusBadge = (s) => badge(s.replace('_', ' '), { live: 'live', upcoming: 'info', closed: 'muted', in_progress: 'live', submitted: 'ok', auto_submitted: 'warn', terminated: 'danger' }[s] || '');

async function act(fn, okMsg) {
  try {
    const r = await fn();
    if (okMsg) toast(okMsg, 'ok');
    return r;
  } catch (e) {
    toast(e.message, 'error');
    throw e;
  }
}

function btn(label, onClick, cls = '') {
  return h(`button.btn.sm${cls}`, { type: 'button', onclick: onClick }, label);
}

function credentialsModal(title, rows) {
  const csv = ['login_id,name,password', ...rows.map((r) => [r.login_id, r.name, r.password].map((v) => `"${String(v ?? '').replace(/"/g, '""')}"`).join(','))].join('\r\n');
  modal(title, h('div',
    h('p.notice', 'These passwords are shown only once. Download or distribute them now — candidates will be asked to change them at first login.'),
    table(['Login ID', 'Name', 'Password'], rows.map((r) => h('tr', h('td', r.login_id), h('td', r.name), h('td.mono', r.password))))), {
    wide: true,
    actions: [
      { label: 'Download CSV', onClick: () => downloadBlob(new Blob(['﻿' + csv], { type: 'text/csv' }), 'credentials.csv') },
      { label: 'Done', primary: true, onClick: (close) => close() },
    ],
  });
}

// ---------------------------------------------------------------- dashboard

route('/admin', async (root) => {
  const [d, schedules] = await Promise.all([api.get('/dashboard'), api.get('/schedules')]);
  const tile = (label, value, kind = '') => h(`div.kpi${kind ? '.' + kind : ''}`, h('div.kpi-value', value), h('div.kpi-label', label));
  const live = schedules.filter((s) => s.status === 'live');
  const upcoming = schedules.filter((s) => s.status === 'upcoming').slice(0, 6);
  const pending = schedules.filter((s) => s.status === 'closed' && !s.results_published);
  const srow = (s) => h('tr',
    h('td', h('b', s.exam_title), h('div.muted.small', s.batch_name)),
    h('td', fmtDateTime(s.starts_at), h('div.muted.small', `to ${fmtDateTime(s.ends_at)}`)),
    h('td', `${s.submitted}/${s.candidates} submitted`, s.in_progress ? h('div.small.live-text', `${s.in_progress} writing now`) : null),
    h('td.right', can('monitor') ? btn('Monitor', () => go(`/admin/monitor/${s.id}`)) : null, ' ', btn('Results', () => go(`/admin/results/${s.id}`))));
  add(root, 
    pageHead('Dashboard', 'Overview of your examination centre'),
    h('div.kpis',
      tile('Live exams', d.live_schedules, d.live_schedules ? 'live' : ''), tile('Candidates writing now', d.in_progress, d.in_progress ? 'live' : ''),
      tile('Submitted (24 h)', d.submitted_today), tile('Awaiting result publication', d.pending_publish, d.pending_publish ? 'warn' : ''),
      tile('Active questions', d.questions), tile('Exams', d.exams), tile('Batches', d.batches), tile('Candidates', d.candidates)),
    h('div.grid2',
      h('section.card', h('h3', 'Live now'), table(['Exam', 'Window', 'Progress', ''], live.map(srow), { empty: 'No exam is live right now.' })),
      h('section.card', h('h3', 'Upcoming'), table(['Exam', 'Window', 'Progress', ''], upcoming.map(srow), { empty: 'Nothing scheduled.' }))),
    pending.length ? h('section.card', h('h3', 'Results waiting to be published'), table(['Exam', 'Window', 'Progress', ''], pending.map(srow))) : null,
    h('section.card.flow', h('h3', 'Examination workflow'),
      h('ol.steps', [
        ['Build question bank', '/admin/questions'], ['Create exam', '/admin/exams'], ['Add batch & candidates', '/admin/candidates'],
        ['Schedule & notify', '/admin/schedules'], ['Monitor live', '/admin/schedules'], ['Publish results', '/admin/schedules'],
      ].map(([t, p], i) => h('li', h('a', { href: `#${p}` }, h('span.step-n', i + 1), t))))));
}, { role: 'staff' });

// ---------------------------------------------------------------- question bank

function questionEditor(q, onSaved) {
  q = q || { qtype: 'single', difficulty: 'medium', options: ['', '', '', ''], correct: [], marks: 4, negative: 1, active: true };
  const subject = input({ value: q.subject || '', list: 'subject-list' });
  const topic = input({ value: q.topic || '' });
  const qtype = select([{ value: 'single', label: 'Single correct answer' }, { value: 'multi', label: 'Multiple correct answers' }], q.qtype);
  const difficulty = select(['easy', 'medium', 'hard'], q.difficulty);
  const text = h('textarea.input', { rows: 4 }, q.text || '');
  const marks = input({ type: 'number', step: '0.25', min: '0.25', value: q.marks });
  const negative = input({ type: 'number', step: '0.25', min: '0', value: q.negative });
  const explanation = h('textarea.input', { rows: 2 }, q.explanation || '');
  const active = checkbox('Active (available for exams)', q.active !== false);
  const optsBox = h('div.options-edit');
  let opts = q.options.map((t, i) => ({ text: t, correct: q.correct.includes(i) }));
  const renderOpts = () => {
    const multi = qtype.value === 'multi';
    clear(optsBox, opts.map((o, i) => h('div.opt-row',
      h('input', { type: multi ? 'checkbox' : 'radio', name: 'correct', checked: o.correct, title: 'Correct answer',
        onchange: (e) => {
          if (!multi) opts.forEach((x) => (x.correct = false));
          o.correct = e.target.checked;
        } }),
      h('span.opt-letter', LETTERS[i]),
      h('input.input', { value: o.text, placeholder: `Option ${LETTERS[i]}`, oninput: (e) => (o.text = e.target.value) }),
      opts.length > 2 ? h('button.icon-btn', { type: 'button', title: 'Remove option', onclick: () => { opts.splice(i, 1); renderOpts(); } }, '×') : null)),
    opts.length < 8 ? h('button.btn.sm.ghost', { type: 'button', onclick: () => { opts.push({ text: '', correct: false }); renderOpts(); } }, '+ Add option') : null);
  };
  qtype.addEventListener('change', () => {
    if (qtype.value === 'single') {
      const first = opts.findIndex((o) => o.correct);
      opts.forEach((o, i) => (o.correct = i === first));
    }
    renderOpts();
  });
  renderOpts();
  modal(q.id ? `Edit question #${q.id}` : 'Add question', h('div.form-grid',
    field('Subject', subject), field('Topic', topic), field('Type', qtype), field('Difficulty', difficulty),
    h('div.span2', field('Question', text)),
    h('div.span2', h('span.label', 'Options — tick the correct answer(s)'), optsBox),
    field('Marks for correct answer', marks), field('Negative marks for wrong answer', negative),
    h('div.span2', field('Explanation (shown after results are published)', explanation)),
    h('div.span2', active.el)), {
    wide: true,
    actions: [
      { label: 'Cancel', onClick: (c) => c() },
      { label: 'Save question', primary: true, onClick: async (close) => {
        const body = {
          subject: subject.value, topic: topic.value, qtype: qtype.value, difficulty: difficulty.value, text: text.value,
          options: opts.map((o) => o.text), correct: opts.map((o, i) => (o.correct ? i : -1)).filter((i) => i >= 0),
          marks: Number(marks.value), negative: Number(negative.value), explanation: explanation.value, active: active.input.checked,
        };
        await act(() => (q.id ? api.put(`/questions/${q.id}`, body) : api.post('/questions', body)), 'Question saved');
        close();
        onSaved();
      } },
    ],
  });
}

const QUESTION_TEMPLATE = 'subject,topic,type,difficulty,question,option_a,option_b,option_c,option_d,option_e,option_f,correct,marks,negative,explanation\r\n'
  + 'Physics,Kinematics,single,easy,"A body starts from rest with acceleration 2 m/s². Velocity after 5 s?",5 m/s,10 m/s,20 m/s,2.5 m/s,,,B,4,1,v = at\r\n'
  + 'Chemistry,Bonding,multi,medium,Which molecules are non-polar?,CO2,H2O,CH4,NH3,,,"A;C",4,1,\r\n';

function importQuestions(onDone) {
  const file = h('input', { type: 'file', accept: '.csv,text/csv' });
  const text = h('textarea.input.mono', { rows: 8, placeholder: 'Paste CSV here, or choose a file above' });
  const out = h('div');
  file.addEventListener('change', async () => {
    if (file.files[0]) text.value = await readFile(file.files[0]);
  });
  modal('Import questions from CSV', h('div',
    h('p.muted', 'Columns: subject, topic, type (single/multi), difficulty (easy/medium/hard), question, option_a … option_f, correct (e.g. B or A;C), marks, negative, explanation.'),
    h('p', h('button.btn.sm.ghost', { type: 'button', onclick: () => downloadBlob(new Blob(['﻿' + QUESTION_TEMPLATE], { type: 'text/csv' }), 'question-template.csv') }, 'Download template')),
    file, text, out), {
    wide: true,
    actions: [
      { label: 'Close', onClick: (c) => c() },
      { label: 'Import', primary: true, onClick: async () => {
        const r = await act(() => api.post('/questions/import', { csv: text.value }));
        clear(out, h('p.notice', `${r.created} question(s) imported.`),
          r.errors.length ? table(['Line', 'Problem'], r.errors.map((e) => h('tr', h('td', e.line), h('td', e.error)))) : null);
        onDone();
      } },
    ],
  });
}

route('/admin/questions', async (root) => {
  const state = { subject: '', difficulty: '', search: '' };
  const list = h('div');
  const subjectsDl = h('datalist#subject-list');
  const subjectSel = select([{ value: '', label: 'All subjects' }], '');
  const diffSel = select([{ value: '', label: 'All difficulties' }, 'easy', 'medium', 'hard'], '');
  const search = input({ type: 'search', placeholder: 'Search question text…' });
  const load = async () => {
    const qs = new URLSearchParams(Object.entries(state).filter(([, v]) => v)).toString();
    const [data, subs] = await Promise.all([api.get(`/questions?${qs}`), api.get('/questions/subjects')]);
    const names = [...new Set(subs.map((s) => s.subject))];
    const cur = subjectSel.value;
    clear(subjectSel, h('option', { value: '' }, 'All subjects'), names.map((n) => h('option', { value: n }, n)));
    subjectSel.value = cur;
    clear(subjectsDl, names.map((n) => h('option', { value: n })));
    clear(list, h('p.muted', `${data.total} question(s)`), table(
      ['#', 'Subject / topic', 'Question', 'Type', 'Level', 'Marks', 'Status', ''],
      data.rows.map((q) => h('tr',
        h('td.muted', q.id), h('td', h('b', q.subject), h('div.small.muted', q.topic || '')),
        h('td.qtext', q.text.length > 140 ? q.text.slice(0, 140) + '…' : q.text,
          h('div.small.muted', q.options.map((o, i) => `${LETTERS[i]}${q.correct.includes(i) ? '✓' : ''}`).join('  '))),
        h('td', q.qtype === 'multi' ? 'Multiple' : 'Single'), h('td', badge(q.difficulty, { easy: 'ok', medium: 'info', hard: 'warn' }[q.difficulty])),
        h('td.nowrap', `+${q.marks} / −${q.negative}`), h('td', q.active ? badge('active', 'ok') : badge('inactive', 'muted')),
        h('td.right.nowrap', btn('Edit', () => questionEditor(q, load)), ' ',
          btn('Delete', async () => {
            if (await confirmDialog('Delete question?', 'Questions already used in exams are deactivated instead of deleted.', { danger: true, okLabel: 'Delete' })) {
              await act(() => api.del(`/questions/${q.id}`), 'Done');
              load();
            }
          }, '.ghost'))))));
  };
  subjectSel.addEventListener('change', () => { state.subject = subjectSel.value; load(); });
  diffSel.addEventListener('change', () => { state.difficulty = diffSel.value; load(); });
  search.addEventListener('input', debounce(() => { state.search = search.value.trim(); load(); }, 300));
  add(root, pageHead('Question Bank', 'Step 2 — add or import MCQs and set their difficulty',
    btn('Import CSV', () => importQuestions(load), '.ghost'), btn('+ Add question', () => questionEditor(null, load), '.primary')),
  subjectsDl, h('div.filters', subjectSel, diffSel, search), list);
  await load();
}, { role: 'staff' });

// ---------------------------------------------------------------- exams

route('/admin/exams', async (root) => {
  const list = await api.get('/exams');
  const editable = can('exams');
  add(root, pageHead('Exams', 'Step 3 — rules, timer and randomisation', editable ? btn('+ Create exam', () => go('/admin/exams/new'), '.primary') : null),
    table(['Exam', 'Questions', 'Duration', 'Selection', 'Results', 'Anti-cheat', ''],
      list.map((e) => h('tr',
        h('td', h('b', e.title), h('div.small.muted', e.description || '')),
        h('td', e.question_count, e.max_marks ? h('div.small.muted', `${e.max_marks} marks`) : null),
        h('td', `${e.duration_min} min`),
        h('td', e.selection_mode === 'fixed' ? 'Fixed set' : 'Random from bank', e.shuffle_questions ? h('div.small.muted', 'shuffled') : null),
        h('td', e.show_result === 'immediate' ? 'Immediately' : 'On publish'),
        h('td', e.max_violations ? `${e.max_violations} warnings` : 'log only', e.require_fullscreen ? h('div.small.muted', 'full screen') : null),
        h('td.right.nowrap', editable ? [btn(e.has_attempts ? 'View' : 'Edit', () => go(`/admin/exams/${e.id}`)), ' ',
          btn('Duplicate', async () => { await act(() => api.post(`/exams/${e.id}/duplicate`), 'Exam duplicated'); go('/admin/exams'); }, '.ghost'), ' ',
          btn('Delete', async () => {
            if (await confirmDialog('Delete exam?', e.title, { danger: true, okLabel: 'Delete' })) {
              await act(() => api.del(`/exams/${e.id}`), 'Exam deleted');
              go('/admin/exams');
            }
          }, '.ghost')] : btn('View', () => go(`/admin/exams/${e.id}`)))))));
}, { role: 'staff' });

async function examEditor(root, id) {
  const [exam, bank, subs] = await Promise.all([
    id ? api.get(`/exams/${id}`) : null, can('questions') ? api.get('/questions?active=1&limit=2000') : { rows: [] }, can('questions') ? api.get('/questions/subjects') : [],
  ]);
  const e = exam || { duration_min: 60, pass_percent: 40, shuffle_questions: true, shuffle_options: true, negative_marking: true, max_violations: 3, require_fullscreen: true, show_result: 'on_publish', selection_mode: 'fixed', grace_seconds: 120, question_ids: [], random_rules: [] };
  const locked = !!exam?.has_attempts || !can('exams');
  const title = input({ value: e.title || '' });
  const description = input({ value: e.description || '' });
  const instructions = h('textarea.input', { rows: 4 }, e.instructions || '');
  const duration = input({ type: 'number', min: 1, max: 720, value: e.duration_min });
  const pass = input({ type: 'number', min: 0, max: 100, value: e.pass_percent });
  const showResult = select([{ value: 'on_publish', label: 'After the examiner publishes' }, { value: 'immediate', label: 'Immediately after submitting' }], e.show_result);
  const shuffleQ = checkbox('Shuffle question order for each candidate', e.shuffle_questions);
  const shuffleO = checkbox('Shuffle answer options', e.shuffle_options);
  const negative = checkbox('Apply negative marking', e.negative_marking);
  const fullscreen = checkbox('Require full screen (exit = warning)', e.require_fullscreen);
  const maxViol = input({ type: 'number', min: 0, max: 100, value: e.max_violations });
  const grace = input({ type: 'number', min: 0, max: 3600, value: e.grace_seconds });
  const mode = select([{ value: 'fixed', label: 'Fixed set of questions' }, { value: 'random', label: 'Random questions drawn from the bank' }], e.selection_mode);
  const chosen = new Set(e.question_ids);
  const pickerBox = h('div');
  const rulesBox = h('div');
  const summary = h('p.muted');
  const subjectNames = [...new Set(subs.map((s) => s.subject))];
  const availability = (subject, diff) => subs.filter((s) => (!subject || s.subject === subject) && (!diff || s.difficulty === diff)).reduce((a, s) => a + s.count, 0);
  let filter = '';
  const renderPicker = () => {
    const rows = bank.rows.filter((q) => !filter || q.subject === filter);
    const marks = bank.rows.filter((q) => chosen.has(q.id)).reduce((a, q) => a + q.marks, 0);
    summary.textContent = `${chosen.size} question(s) selected · ${marks} marks`;
    const fsel = select([{ value: '', label: 'All subjects' }, ...subjectNames], filter);
    fsel.addEventListener('change', () => { filter = fsel.value; renderPicker(); });
    clear(pickerBox, h('div.filters', fsel, btn('Select all shown', () => { rows.forEach((q) => chosen.add(q.id)); renderPicker(); }, '.ghost'),
      btn('Clear', () => { chosen.clear(); renderPicker(); }, '.ghost'), summary),
    table(['', 'Subject', 'Question', 'Level', 'Marks'], rows.map((q) => h('tr',
      h('td', h('input', { type: 'checkbox', checked: chosen.has(q.id), disabled: locked, onchange: (ev) => { ev.target.checked ? chosen.add(q.id) : chosen.delete(q.id); renderPicker(); } })),
      h('td', q.subject), h('td.qtext', q.text.slice(0, 120)), h('td', q.difficulty), h('td', q.marks))), { empty: 'The question bank is empty. Add questions first.' }));
  };
  let rules = e.random_rules.length ? e.random_rules.map((r) => ({ ...r })) : [{ subject: '', difficulty: '', count: 5 }];
  const renderRules = () => {
    const total = rules.reduce((a, r) => a + Number(r.count || 0), 0);
    clear(rulesBox, rules.map((r, i) => {
      const s = select([{ value: '', label: 'Any subject' }, ...subjectNames], r.subject || '', { disabled: locked });
      const d = select([{ value: '', label: 'Any difficulty' }, 'easy', 'medium', 'hard'], r.difficulty || '', { disabled: locked });
      const c = input({ type: 'number', min: 1, value: r.count, disabled: locked });
      const avail = h('span.small.muted', `${availability(r.subject, r.difficulty)} available`);
      s.addEventListener('change', () => { r.subject = s.value; renderRules(); });
      d.addEventListener('change', () => { r.difficulty = d.value; renderRules(); });
      c.addEventListener('input', () => (r.count = Number(c.value)));
      return h('div.rule-row', s, d, c, avail, !locked && rules.length > 1 ? h('button.icon-btn', { type: 'button', onclick: () => { rules.splice(i, 1); renderRules(); } }, '×') : null);
    }), locked ? null : btn('+ Add rule', () => { rules.push({ subject: '', difficulty: '', count: 5 }); renderRules(); }, '.ghost'),
    h('p.muted', `Each candidate gets ${total} different random question(s).`));
  };
  const syncMode = () => {
    pickerBox.classList.toggle('hidden', mode.value !== 'fixed');
    rulesBox.classList.toggle('hidden', mode.value !== 'random');
  };
  mode.addEventListener('change', syncMode);
  renderPicker();
  renderRules();
  syncMode();
  const save = async () => {
    const body = {
      title: title.value, description: description.value, instructions: instructions.value, duration_min: Number(duration.value),
      pass_percent: Number(pass.value), show_result: showResult.value, shuffle_questions: shuffleQ.input.checked, shuffle_options: shuffleO.input.checked,
      negative_marking: negative.input.checked, require_fullscreen: fullscreen.input.checked, max_violations: Number(maxViol.value),
      grace_seconds: Number(grace.value), selection_mode: mode.value, question_ids: [...chosen], random_rules: rules,
    };
    await act(() => (id ? api.put(`/exams/${id}`, body) : api.post('/exams', body)), 'Exam saved');
    go('/admin/exams');
  };
  if (locked) [title, description, instructions, duration, pass, showResult, maxViol, grace, mode, shuffleQ.input, shuffleO.input, negative.input, fullscreen.input].forEach((x) => (x.disabled = true));
  add(root, 
    pageHead(id ? (locked ? 'View exam' : 'Edit exam') : 'Create exam', locked && exam?.has_attempts ? 'Candidates have attempted this exam, so it is read-only. Duplicate it to make changes.' : 'Step 3 — set rules, timer and randomisation',
      btn('Back', () => go('/admin/exams'), '.ghost'), locked ? null : btn('Save exam', save, '.primary')),
    h('section.card', h('h3', 'Basics'), h('div.form-grid',
      h('div.span2', field('Title', title)), h('div.span2', field('Short description', description)),
      h('div.span2', field('Instructions shown to candidates', instructions)),
      field('Duration (minutes)', duration), field('Pass mark (%)', pass), field('Show result to candidate', showResult))),
    h('section.card', h('h3', 'Rules & anti-cheat'), h('div.form-grid',
      shuffleQ.el, shuffleO.el, negative.el, fullscreen.el,
      field('Warnings before auto-submit', maxViol, 'Tab switches / leaving full screen. 0 = only log them.'),
      field('Offline grace period (seconds)', grace, 'Answers saved offline before time-up are still accepted this long after.'))),
    h('section.card', h('h3', 'Questions'), field('Question selection', mode), pickerBox, rulesBox));
}

route('/admin/exams/new', (root) => examEditor(root, null), { role: 'staff' });
route('/admin/exams/:id', (root, p) => examEditor(root, p.id), { role: 'staff' });

// ---------------------------------------------------------------- batches & candidates

route('/admin/candidates', async (root) => {
  let batches = await api.get('/batches');
  let current = batches[0]?.id || null;
  const left = h('section.card.batch-list');
  const right = h('section.card');
  const batchForm = (b) => {
    const name = input({ value: b?.name || '' });
    const desc = input({ value: b?.description || '' });
    modal(b ? 'Rename batch' : 'New batch', h('div', field('Batch name', name), field('Description', desc)), {
      actions: [{ label: 'Cancel', onClick: (c) => c() }, { label: 'Save', primary: true, onClick: async (close) => {
        const r = await act(() => (b ? api.put(`/batches/${b.id}`, { name: name.value, description: desc.value }) : api.post('/batches', { name: name.value, description: desc.value })), 'Batch saved');
        close();
        current = r.id;
        refresh();
      } }],
    });
  };
  const candidateForm = () => {
    const f = { login_id: input(), name: input(), email: input({ type: 'email' }), phone: input({ type: 'tel' }), password: input({ type: 'text', placeholder: 'Leave blank to generate' }) };
    const send = checkbox('Send login details by email/SMS', false);
    modal('Add candidate', h('div.form-grid', field('Roll no. / login ID', f.login_id), field('Full name', f.name), field('Email', f.email), field('Mobile', f.phone),
      h('div.span2', field('Password', f.password)), h('div.span2', send.el)), {
      actions: [{ label: 'Cancel', onClick: (c) => c() }, { label: 'Add', primary: true, onClick: async (close) => {
        const body = Object.fromEntries(Object.entries(f).map(([k, v]) => [k, v.value.trim()]));
        const c = await act(() => api.post('/candidates', { ...body, batch_id: current, send_credentials: send.input.checked }), 'Candidate added');
        close();
        refresh();
        credentialsModal('Candidate login', [{ login_id: c.login_id, name: c.name, password: c.initial_password }]);
      } }],
    });
  };
  const importForm = () => {
    const file = h('input', { type: 'file', accept: '.csv,text/csv' });
    const text = h('textarea.input.mono', { rows: 8, placeholder: 'login_id,name,email,phone\nPW101,Asha Rao,asha@example.com,9876543210' });
    const send = checkbox('Send login details by email/SMS', false);
    file.addEventListener('change', async () => { if (file.files[0]) text.value = await readFile(file.files[0]); });
    modal('Import candidates', h('div', h('p.muted', 'CSV with header: login_id,name,email,phone (password optional — generated if blank).'), file, text, send.el), {
      wide: true,
      actions: [{ label: 'Cancel', onClick: (c) => c() }, { label: 'Import', primary: true, onClick: async (close) => {
        const r = await act(() => api.post('/candidates/import', { batch_id: current, csv: text.value, send_credentials: send.input.checked }));
        close();
        refresh();
        if (r.errors.length) toast(`${r.errors.length} row(s) skipped: ${r.errors.slice(0, 3).map((e) => `line ${e.line}: ${e.error}`).join('; ')}`, 'error');
        if (r.created.length) credentialsModal(`${r.created.length} candidate(s) imported`, r.created);
      } }],
    });
  };
  const refresh = async () => {
    batches = await api.get('/batches');
    if (!batches.find((b) => b.id === current)) current = batches[0]?.id || null;
    clear(left, h('div.card-head', h('h3', 'Batches'), btn('+ New', () => batchForm(null), '.primary')),
      batches.length ? h('ul.batch-ul', batches.map((b) => h(`li${b.id === current ? '.active' : ''}`, { onclick: () => { current = b.id; refresh(); } },
        h('b', b.name), h('span.muted.small', `${b.candidates} candidate(s)`)))) : h('p.muted', 'Create your first batch (e.g. "PW Batch 2026-A").'));
    if (!current) return clear(right, h('p.muted', 'No batch selected.'));
    const b = batches.find((x) => x.id === current);
    const cands = await api.get(`/candidates?batch_id=${current}`);
    clear(right, h('div.card-head', h('div', h('h3', b.name), h('p.muted.small', b.description || '')),
      h('div.actions', btn('Rename', () => batchForm(b), '.ghost'),
        btn('Delete batch', async () => {
          if (await confirmDialog('Delete batch?', b.name, { danger: true, okLabel: 'Delete' })) { await act(() => api.del(`/batches/${b.id}`), 'Batch deleted'); refresh(); }
        }, '.ghost'),
        btn('Import CSV', importForm, '.ghost'), btn('+ Add candidate', candidateForm, '.primary'))),
    table(['Login ID', 'Name', 'Email', 'Mobile', 'Status', ''], cands.map((c) => h('tr',
      h('td.mono', c.login_id), h('td', c.name), h('td', c.email || ''), h('td', c.phone || ''),
      h('td', c.active ? badge('active', 'ok') : badge('disabled', 'muted')),
      h('td.right.nowrap',
        btn('Reset password', async () => {
          if (!(await confirmDialog('Reset password?', `A new password will be generated for ${c.login_id}.`))) return;
          const r = await act(() => api.post(`/users/${c.id}/reset-password`, { send: false }));
          credentialsModal('New password', [{ login_id: r.login_id, name: c.name, password: r.password }]);
        }, '.ghost'), ' ',
        btn(c.active ? 'Disable' : 'Enable', async () => { await act(() => api.put(`/users/${c.id}`, { active: !c.active }), 'Updated'); refresh(); }, '.ghost'), ' ',
        btn('Delete', async () => {
          if (await confirmDialog('Delete candidate?', `${c.login_id} — candidates with exam attempts are disabled instead.`, { danger: true, okLabel: 'Delete' })) {
            await act(() => api.del(`/users/${c.id}`), 'Done');
            refresh();
          }
        }, '.ghost')))), { empty: 'No candidates in this batch yet.' }));
  };
  add(root, pageHead('Batches & Candidates', 'Step 4 — organise candidates into batches'), h('div.split', left, right));
  await refresh();
}, { role: 'staff' });

// ---------------------------------------------------------------- schedules

async function scheduleForm(s, onDone) {
  const [examsList, batches] = await Promise.all([api.get('/exams'), api.get('/batches')]);
  if (!examsList.length || !batches.length) return toast('Create an exam and a batch first', 'error');
  const now = Math.floor(Date.now() / 1000);
  const exam = select(examsList.map((e) => ({ value: e.id, label: `${e.title} (${e.duration_min} min)` })), s?.exam_id);
  const batch = select(batches.map((b) => ({ value: b.id, label: `${b.name} (${b.candidates})` })), s?.batch_id);
  const starts = input({ type: 'datetime-local', value: toLocalInput(s?.starts_at || Math.ceil((now + 900) / 900) * 900) });
  const ends = input({ type: 'datetime-local', value: toLocalInput(s?.ends_at || Math.ceil((now + 900) / 900) * 900 + 3 * 3600) });
  const notify = checkbox('Notify candidates by email / SMS now', !s);
  modal(s ? 'Edit schedule' : 'Schedule an exam', h('div',
    field('Exam', exam), field('Batch', batch), h('div.form-grid', field('Window opens', starts), field('Window closes', ends)),
    h('p.muted.small', 'Candidates can start any time inside the window. Each gets the full duration, capped at the window end.'), notify.el), {
    actions: [{ label: 'Cancel', onClick: (c) => c() }, { label: s ? 'Save' : 'Schedule', primary: true, onClick: async (close) => {
      const body = { exam_id: Number(exam.value), batch_id: Number(batch.value), starts_at: fromLocalInput(starts.value), ends_at: fromLocalInput(ends.value), notify: notify.input.checked };
      await act(() => (s ? api.put(`/schedules/${s.id}`, body) : api.post('/schedules', body)), 'Schedule saved');
      close();
      onDone();
    } }],
  });
}

route('/admin/schedules', async (root) => {
  const list = h('div');
  const load = async () => {
    const rows = await api.get('/schedules');
    clear(list, table(['Exam', 'Batch', 'Window', 'Status', 'Progress', 'Results', ''], rows.map((s) => h('tr',
      h('td', h('b', s.exam_title), h('div.small.muted', `${s.duration_min} min`)), h('td', s.batch_name),
      h('td.nowrap', fmtDateTime(s.starts_at), h('div.small.muted', `to ${fmtDateTime(s.ends_at)}`)),
      h('td', statusBadge(s.status)), h('td', `${s.submitted}/${s.candidates}`, s.in_progress ? h('div.small.live-text', `${s.in_progress} writing`) : null),
      h('td', s.results_published ? badge('published', 'ok') : badge('not published', 'muted')),
      h('td.right.nowrap',
        can('monitor') ? btn('Monitor', () => go(`/admin/monitor/${s.id}`)) : null, ' ', btn('Results', () => go(`/admin/results/${s.id}`)), ' ',
        can('schedules') ? [btn('Notify', async () => { const r = await act(() => api.post(`/schedules/${s.id}/notify`)); toast(`${r.queued} message(s) queued`, 'ok'); }, '.ghost'), ' ',
          btn('Edit', () => scheduleForm(s, load), '.ghost'), ' ',
          btn('Delete', async () => {
            if (await confirmDialog('Delete schedule?', `${s.exam_title} for ${s.batch_name}`, { danger: true, okLabel: 'Delete' })) { await act(() => api.del(`/schedules/${s.id}`), 'Deleted'); load(); }
          }, '.ghost')] : null))), { empty: 'No exams scheduled yet.' }));
  };
  add(root, pageHead('Schedules & Results', 'Step 4 — assign exams to batches and notify candidates', can('schedules') ? btn('+ Schedule exam', () => scheduleForm(null, load), '.primary') : null), list);
  await load();
}, { role: 'staff' });

// ---------------------------------------------------------------- live monitor

function attemptDetail(aid) {
  api.get(`/attempts/${aid}`).then((d) => {
    modal(`${d.candidate.name} (${d.candidate.login_id})`, h('div',
      h('p', 'Status: ', statusBadge(d.status), ` · Score so far ${d.score}/${d.max_score}`),
      h('p.muted.small', `Started ${fmtDateTime(d.started_at)} · deadline ${fmtDateTime(d.deadline)} · IP ${d.ip || '—'}`),
      h('p.muted.small', d.client_info || ''),
      h('h4', `Proctoring events (${d.violations.length})`),
      table(['Time', 'Event', 'Counted', 'Detail'], d.violations.map((v) => h('tr', h('td.nowrap', fmtDateTime(v.at)), h('td', v.kind.replace('_', ' ')), h('td', v.counted ? 'yes' : 'no'), h('td.small', v.detail || ''))), { empty: 'No events.' })), { wide: true });
  }).catch((e) => toast(e.message, 'error'));
}

route('/admin/monitor/:sid', async (root, p) => {
  const body = h('div');
  const stamp = h('span.muted.small');
  const load = async () => {
    const m = await api.get(`/schedules/${p.sid}/monitor`);
    const s = m.schedule;
    const online = m.attempts.filter((a) => a.online).length;
    const writing = m.attempts.filter((a) => a.status === 'in_progress').length;
    stamp.textContent = `Auto-refreshing · updated ${new Date().toLocaleTimeString()}`;
    clear(body,
      h('div.kpis', [['Candidates', s.candidates], ['Writing now', writing, writing ? 'live' : ''], ['Online', online], ['Submitted', s.submitted], ['Not started', m.not_started.length],
        ['Window', statusBadge(s.status)]].map(([l, v, k]) => h(`div.kpi${k ? '.' + k : ''}`, h('div.kpi-value', v), h('div.kpi-label', l)))),
      table(['Candidate', 'Status', 'Answered', 'Time left', 'Warnings', 'Connection', ''], m.attempts.map((a) => h(`tr${a.violations ? '.flag' : ''}`,
        h('td', h('b', a.name), h('div.small.muted.mono', a.login_id)), h('td', statusBadge(a.status)),
        h('td', `${a.answered}/${a.total}`, h('div.progress', h('span', { style: { width: `${(100 * a.answered) / Math.max(1, a.total)}%` } }))),
        h('td.mono', a.status === 'in_progress' ? fmtDuration(a.remaining) : '—'),
        h('td', a.violations ? badge(String(a.violations), 'danger') : '0'),
        h('td', a.status !== 'in_progress' ? '—' : a.online ? h('span.dot.on', 'online') : h('span.dot.off', `offline · seen ${fmtDateTime(a.last_seen)}`)),
        h('td.right.nowrap', btn('Details', () => attemptDetail(a.attempt_id), '.ghost'), ' ',
          a.status === 'in_progress' ? btn('Force submit', async () => {
            if (await confirmDialog('Force submit?', `End ${a.name}'s exam now? Answers saved so far will be scored.`, { danger: true, okLabel: 'Force submit' })) {
              await act(() => api.post(`/attempts/${a.attempt_id}/force-submit`), 'Submitted');
              load();
            }
          }, '.danger') : null))), { empty: 'Nobody has started yet.' }),
      m.not_started.length ? h('details.card', h('summary', `Not started (${m.not_started.length})`), h('p', m.not_started.map((c) => `${c.login_id} ${c.name}`).join(' · '))) : null);
    return s;
  };
  const s = await load();
  add(root, pageHead(`Live monitor — ${s.exam_title}`, `${s.batch_name} · ${fmtDateTime(s.starts_at)} to ${fmtDateTime(s.ends_at)}`, stamp,
    btn('Results', () => go(`/admin/results/${p.sid}`), '.ghost')), body);
  const timer = setInterval(() => load().catch(() => {}), 10000);
  return () => clearInterval(timer);
}, { role: 'staff' });

// ---------------------------------------------------------------- results & analytics

route('/admin/results/:sid', async (root, p) => {
  const [r, an] = await Promise.all([api.get(`/schedules/${p.sid}/results`), api.get(`/schedules/${p.sid}/analytics`)]);
  const s = r.schedule;
  const tile = (l, v, k = '') => h(`div.kpi${k ? '.' + k : ''}`, h('div.kpi-value', v ?? '—'), h('div.kpi-label', l));
  const maxBucket = Math.max(1, ...an.distribution.map((b) => b.count));
  const publishBtn = can('results') ? (s.results_published
    ? btn('Unpublish', async () => { await act(() => api.post(`/schedules/${p.sid}/unpublish`), 'Results hidden'); go(`/admin/results/${p.sid}`); }, '.ghost')
    : btn('Publish results', async () => {
      let force = false;
      if (s.status !== 'closed' || s.in_progress) {
        if (!(await confirmDialog('Publish before the window closes?', 'Some candidates may still be writing or yet to start. Publish anyway?', { okLabel: 'Force publish' }))) return;
        force = true;
      }
      const res = await act(() => api.post(`/schedules/${p.sid}/publish`, { force }));
      toast(`Published for ${res.candidates} candidate(s); ${res.messages} notification(s) queued`, 'ok');
      go(`/admin/results/${p.sid}`);
    }, '.primary')) : null;
  add(root, 
    pageHead(`Results — ${s.exam_title}`, `${s.batch_name} · ${fmtDateTime(s.starts_at)} to ${fmtDateTime(s.ends_at)} · ${s.results_published ? 'published' : 'not yet published'}`,
      btn('Export CSV', () => api.download(`/schedules/${p.sid}/results.csv`, `results-${p.sid}.csv`).catch((e) => toast(e.message, 'error')), '.ghost'), publishBtn),
    h('div.kpis', tile('Appeared', an.appeared), tile('Absent', an.absent), tile('Average %', an.average), tile('Median %', an.median), tile('Highest %', an.highest),
      tile('Pass rate', an.pass_rate === null ? null : `${an.pass_rate}%`, 'ok')),
    h('div.grid2',
      h('section.card', h('h3', 'Score distribution'), h('div.bars', an.distribution.map((b) => h('div.bar-row', h('span.bar-label', b.range),
        h('span.bar-track', h('span.bar-fill', { style: { width: `${(100 * b.count) / maxBucket}%` } })), h('span.bar-val', b.count))))),
      h('section.card', h('h3', 'Subject-wise average'), an.subjects.length ? h('div.bars', an.subjects.map((x) => h('div.bar-row', h('span.bar-label', x.subject),
        h('span.bar-track', h('span.bar-fill.alt', { style: { width: `${Math.max(0, x.percent)}%` } })), h('span.bar-val', `${x.percent}%`)))) : h('p.muted', 'No data yet.'),
      an.violations.length ? [h('h4', 'Proctoring events'), h('p.small', an.violations.map((v) => `${v.kind.replace('_', ' ')}: ${v.count}`).join(' · '))] : null)),
    h('section.card', h('h3', 'Merit list'), table(['Rank', 'Candidate', 'Score', '%', 'Result', 'Percentile', 'C / W / U', 'Time', 'Warnings', ''],
      r.rows.map((x) => h('tr',
        h('td.b', x.rank), h('td', h('b', x.name), h('div.small.muted.mono', x.login_id)), h('td.nowrap', `${x.score} / ${x.max_score}`), h('td', x.percent),
        h('td', x.passed ? badge('PASS', 'ok') : badge('FAIL', 'danger')), h('td', x.percentile), h('td.nowrap', `${x.correct} / ${x.wrong} / ${x.unanswered}`),
        h('td', fmtMinutes(x.time_taken)), h('td', x.violations ? badge(String(x.violations), x.status === 'terminated' ? 'danger' : 'warn') : '0'),
        h('td.right.nowrap', btn('PDF', () => api.download(`/attempts/${x.attempt_id}/report.pdf`, `report-${x.login_id}.pdf`).catch((e) => toast(e.message, 'error')), '.ghost'), ' ',
          can('monitor') ? btn('Log', () => attemptDetail(x.attempt_id), '.ghost') : null))), { empty: 'No submissions yet.' }),
    r.absent.length ? h('p.muted', h('b', `Absent (${r.absent.length}): `), r.absent.map((a) => a.login_id).join(', ')) : null),
    h('section.card', h('h3', 'Question analysis'), h('p.muted.small', 'Questions most candidates got wrong are listed first — check them for errors in the answer key.'),
      table(['#', 'Subject', 'Question', 'Level', 'Correct %', 'Wrong', 'Skipped', ''], an.questions.map((q) => h('tr',
        h('td.muted', q.question_id), h('td', q.subject), h('td.qtext', q.text), h('td', q.difficulty), h('td', h('div.progress', h('span', { style: { width: `${q.percent_correct}%` } })), `${q.percent_correct}%`),
        h('td', q.wrong), h('td', q.unanswered), h('td', q.flag ? badge(q.flag, 'warn') : ''))), { empty: 'No data yet.' })));
}, { role: 'staff' });

// ---------------------------------------------------------------- notifications & audit

route('/admin/notifications', async (root) => {
  const rows = await api.get('/notifications');
  add(root, pageHead('Notifications', 'Email and SMS outbox. Without a gateway configured, messages are printed in the server console.'),
    table(['Time', 'Channel', 'To', 'Message', 'Status'], rows.map((n) => h('tr', h('td.nowrap', fmtDateTime(n.created_at)), h('td', n.channel.toUpperCase()),
      h('td', n.recipient), h('td.small', n.subject ? h('b', `${n.subject}: `) : null, n.body, n.error ? h('div.err', n.error) : null),
      h('td', badge(n.status, { sent: 'ok', logged: 'info', failed: 'danger', queued: 'warn' }[n.status])))), { empty: 'No messages.' }));
}, { role: 'staff' });

route('/admin/audit', async (root) => {
  const rows = await api.get('/audit?limit=500');
  add(root, pageHead('Audit Log', 'Step 14 — every administrative and exam action'),
    table(['Time', 'User', 'Action', 'Entity', 'Detail', 'IP'], rows.map((a) => h('tr', h('td.nowrap', fmtDateTime(a.ts)), h('td.mono', a.actor || 'system'),
      h('td', a.action.replace('_', ' ')), h('td', a.entity ? `${a.entity}${a.entity_id ? ' #' + a.entity_id : ''}` : ''), h('td.small', a.detail || ''), h('td.small', a.ip || '')))));
}, { role: 'staff' });

// ---------------------------------------------------------------- staff users

route('/admin/users', async (root) => {
  const list = h('div');
  const load = async () => {
    const rows = await api.get('/users');
    clear(list, table(['Login ID', 'Name', 'Role', 'Email', 'Status', ''], rows.map((u) => h('tr',
      h('td.mono', u.login_id), h('td', u.name), h('td', badge(u.role, 'info')), h('td', u.email || ''), h('td', u.active ? badge('active', 'ok') : badge('disabled', 'muted')),
      h('td.right.nowrap',
        btn('Reset password', async () => {
          if (!(await confirmDialog('Reset password?', `Generate a new password for ${u.login_id}?`))) return;
          const r = await act(() => api.post(`/users/${u.id}/reset-password`));
          credentialsModal('New password', [{ login_id: r.login_id, name: u.name, password: r.password }]);
        }, '.ghost'), ' ',
        btn(u.active ? 'Disable' : 'Enable', async () => { await act(() => api.put(`/users/${u.id}`, { active: !u.active }), 'Updated'); load(); }, '.ghost'))))));
  };
  const addStaff = () => {
    const role = select([{ value: 'examiner', label: 'Examiner — question bank & exams' }, { value: 'proctor', label: 'Proctor — live monitoring' }, { value: 'admin', label: 'Administrator — everything' }], 'examiner');
    const f = { login_id: input(), name: input(), email: input({ type: 'email' }), phone: input({ type: 'tel' }), password: input({ placeholder: 'Leave blank to generate' }) };
    modal('Add staff user', h('div.form-grid', h('div.span2', field('Role', role)), field('Login ID', f.login_id), field('Name', f.name), field('Email', f.email), field('Mobile', f.phone), h('div.span2', field('Password', f.password))), {
      actions: [{ label: 'Cancel', onClick: (c) => c() }, { label: 'Add', primary: true, onClick: async (close) => {
        const u = await act(() => api.post('/users', { role: role.value, ...Object.fromEntries(Object.entries(f).map(([k, v]) => [k, v.value.trim()])) }), 'User added');
        close();
        load();
        credentialsModal('Staff login', [{ login_id: u.login_id, name: u.name, password: u.initial_password }]);
      } }],
    });
  };
  add(root, pageHead('Staff Users', 'Step 1 — role-based access for administrators, examiners and proctors', btn('+ Add staff', addStaff, '.primary')), list);
  await load();
}, { role: 'staff' });
