// Candidate portal: my exams, instructions, results (flowchart steps 6, 7 and 12).
import { NetworkError, api, auth } from './api.js';
import { go, route } from './main.js';
import { LETTERS, checkbox, clear, fmtDateTime, fmtDuration, fmtMinutes, h, toast, add } from './util.js';
import { cacheAttempt, pendingQueues, syncQueue } from './exam.js';

route('/home', async (root) => {
  // Step 13: push any answers still queued on this device from an earlier offline session.
  for (const aid of pendingQueues()) {
    try {
      await syncQueue(aid);
    } catch {
      /* still offline — retried later */
    }
  }
  let data;
  try {
    data = await api.get('/my/exams');
  } catch (e) {
    if (e instanceof NetworkError) {
      add(root, h('div.card', h('h2', 'You are offline'), h('p', 'Connect to the exam network and try again. Any answers saved on this device will be uploaded automatically.')));
      return;
    }
    throw e;
  }
  const offset = data.server_now - Date.now() / 1000;
  const card = (x) => {
    const actions = [];
    if (x.attempt_status === 'in_progress') {
      actions.push(h('button.btn.primary', { onclick: () => go(`/attempt/${x.attempt_id}`) }, 'Resume exam'));
    } else if (x.attempt_id) {
      actions.push(x.result_ready
        ? h('button.btn.primary', { onclick: () => go(`/result/${x.attempt_id}`) }, 'View result')
        : h('span.badge.ok', 'Submitted — result awaited'));
    } else if (x.status === 'live') {
      actions.push(h('button.btn.primary', { onclick: () => go(`/instructions/${x.schedule_id}`) }, 'Start exam'));
    } else if (x.status === 'upcoming') {
      const cd = h('span.countdown');
      const tick = () => (cd.textContent = `Starts in ${fmtDuration(x.starts_at - (Date.now() / 1000 + offset))}`);
      tick();
      cd.dataset.timer = setInterval(tick, 1000);
      actions.push(cd);
    } else {
      actions.push(h('span.badge.muted', 'Missed — window closed'));
    }
    return h(`article.exam-card.${x.status}`,
      h('div.exam-card-top', h('span.badge', { class: `badge ${x.status === 'live' ? 'live' : x.status === 'upcoming' ? 'info' : 'muted'}` }, x.status), h('span.muted.small', `${x.duration_min} minutes`)),
      h('h3', x.title), x.description ? h('p.muted', x.description) : null,
      h('p.small', `${fmtDateTime(x.starts_at)} — ${fmtDateTime(x.ends_at)}`),
      h('div.exam-card-actions', actions));
  };
  add(root, h('div.page-head', h('div', h('h1', `Welcome, ${auth.user.name}`), h('p.muted', 'Your scheduled examinations'))),
    data.exams.length ? h('div.exam-grid', data.exams.map(card)) : h('div.card', h('p', 'No exams are scheduled for your batch yet.')));
  const refresh = setInterval(() => {
    // reload when an upcoming exam opens
    if (data.exams.some((x) => x.status === 'upcoming' && x.starts_at <= Date.now() / 1000 + offset)) go('/home');
  }, 5000);
  return () => {
    clearInterval(refresh);
    root.querySelectorAll('[data-timer]').forEach((el) => clearInterval(Number(el.dataset.timer)));
  };
}, { role: 'candidate' });

route('/instructions/:sid', async (root, p) => {
  const data = await api.get('/my/exams');
  const x = data.exams.find((e) => e.schedule_id === p.sid);
  if (!x) throw new Error('Exam not found');
  if (x.attempt_id) return go(x.attempt_status === 'in_progress' ? `/attempt/${x.attempt_id}` : '/home');
  const agree = checkbox('I have read and understood the instructions. I will not switch tabs or leave the exam screen.', false);
  const start = h('button.btn.primary.lg', { disabled: true }, 'Start exam');
  agree.input.addEventListener('change', () => (start.disabled = !agree.input.checked));
  start.addEventListener('click', async () => {
    start.disabled = true;
    // Request full screen inside the click (browsers require a user gesture).
    if (x.require_fullscreen && document.documentElement.requestFullscreen) {
      document.documentElement.requestFullscreen().catch(() => {});
    }
    try {
      const a = await api.post(`/my/schedules/${p.sid}/start`, { client_info: `${navigator.userAgent} · ${screen.width}x${screen.height}` });
      cacheAttempt(a);
      go(`/attempt/${a.id}`);
    } catch (e) {
      toast(e.message, 'error');
      start.disabled = false;
      if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    }
  });
  const rules = [
    `Duration: ${x.duration_min} minutes. The timer runs on the server and continues even if you close the browser.`,
    x.negative_marking ? 'Wrong answers carry negative marks as shown on each question.' : 'There is no negative marking.',
    'Your answers are saved automatically every 30 seconds and whenever you change an answer.',
    'If the internet drops, keep answering — answers are stored on this device and uploaded when the connection returns.',
    x.require_fullscreen ? 'The exam runs in full screen. Leaving full screen counts as a warning.' : null,
    x.max_violations ? `Switching tabs or windows counts as a warning. After ${x.max_violations} warnings the exam is submitted automatically.` : 'Switching tabs or windows is recorded and reported to the invigilator.',
    'Use "Mark for review" to flag questions you want to revisit. Flagged questions with an answer are still scored.',
    'When time is up, the exam is submitted automatically.',
  ].filter(Boolean);
  add(root, h('div.card.narrow.instructions',
    h('h1', x.title), h('p.muted', `${fmtDateTime(x.starts_at)} — ${fmtDateTime(x.ends_at)}`),
    h('h3', 'General instructions'), h('ol', rules.map((r) => h('li', r))),
    x.instructions ? [h('h3', 'Instructions from the examiner'), h('p.pre', x.instructions)] : null,
    h('div.legend-demo', h('h3', 'Question palette'), h('div.legend',
      h('span', h('i.pal.answered'), 'Answered'), h('span', h('i.pal.visited'), 'Not answered'), h('span', h('i.pal.flagged'), 'Marked for review'),
      h('span', h('i.pal.flagged.answered'), 'Answered & marked'), h('span', h('i.pal'), 'Not visited'))),
    agree.el, h('div.row-end', h('button.btn.ghost', { onclick: () => go('/home') }, 'Back'), start)));
}, { role: 'candidate' });

route('/result/:aid', async (root, p) => {
  const r = await api.get(`/my/attempts/${p.aid}/result`);
  if (!r.available) {
    add(root, h('div.card.narrow.center', h('div.big-icon', '✓'), h('h2', 'Exam submitted'), h('p', r.exam_title), h('p.muted', r.message),
      h('button.btn.primary', { onclick: () => go('/home') }, 'Back to my exams')));
    return;
  }
  const tile = (l, v, k = '') => h(`div.kpi${k ? '.' + k : ''}`, h('div.kpi-value', v), h('div.kpi-label', l));
  add(root, 
    h('div.page-head', h('div', h('h1', r.exam_title), h('p.muted', `${r.candidate.name} · ${r.candidate.login_id} · ${r.batch}`)),
      h('div.actions', h('button.btn', { onclick: () => api.download(`/my/attempts/${p.aid}/report.pdf`, `report-card-${r.candidate.login_id}.pdf`).catch((e) => toast(e.message, 'error')) }, 'Download report card (PDF)'),
        h('button.btn.ghost', { onclick: () => go('/home') }, 'My exams'))),
    r.status === 'terminated' ? h('p.notice.danger', 'This exam was submitted automatically because of repeated proctoring warnings.') : null,
    h('div.kpis', tile('Score', `${r.score} / ${r.max_score}`), tile('Percentage', `${r.percent}%`), tile('Result', r.passed ? 'PASS' : 'FAIL', r.passed ? 'ok' : 'danger'),
      r.rank ? tile('Rank', `${r.rank} of ${r.appeared}`) : tile('Rank', 'after publication'), r.percentile !== undefined ? tile('Percentile', r.percentile) : null,
      tile('Correct / Wrong / Skipped', `${r.correct} / ${r.wrong} / ${r.unanswered}`), tile('Time taken', fmtMinutes(r.time_taken))),
    h('section.card', h('h3', 'Subject-wise performance'), h('div.table-wrap', h('table.tbl', h('thead', h('tr', ['Subject', 'Correct', 'Wrong', 'Skipped', 'Score'].map((t) => h('th', t)))),
      h('tbody', r.subjects.map((s) => h('tr', h('td', s.subject), h('td', s.correct), h('td', s.wrong), h('td', s.unanswered), h('td.b', `${s.score} / ${s.max}`))))))),
    r.review ? h('section.card', h('h3', 'Answer review'), r.review.map((q) => h(`div.review.${q.outcome}`,
      h('div.review-head', h('b', `Q${q.number}.`), h('span.badge', { class: `badge ${q.outcome === 'correct' ? 'ok' : q.outcome === 'wrong' ? 'danger' : 'muted'}` }, `${q.outcome} (${q.marks > 0 ? '+' : ''}${q.marks})`)),
      h('p.pre', q.text),
      h('ul.review-opts', q.options.map((o, i) => h(`li${q.correct.includes(i) ? '.is-correct' : ''}${q.selected.includes(i) && !q.correct.includes(i) ? '.picked-wrong' : ''}`,
        h('b', `${LETTERS[i]}. `), o, q.selected.includes(i) ? h('span.small', '  ← your answer') : null))),
      q.explanation ? h('p.small.muted', h('b', 'Explanation: '), q.explanation) : null))) : h('p.muted', 'The answer key will be shown here once results are published.'));
}, { role: 'candidate' });

export { clear };
