"""Exam delivery: start/resume, paper generation, autosave + offline sync, anti-cheat, submit, scoring.

Timing is server-authoritative. The deadline is fixed when the attempt starts
(start + duration, capped at the schedule's end). Answers queued offline are
accepted after the deadline only within the exam's grace period and only if
the client saved them before the deadline; the worker then auto-submits.
"""
import json
import random

from . import audit, clock
from .errors import Conflict, Forbidden, NotFound, ValidationError
from .exams import schedule_status

# Violations that count towards the exam's limit. Others are only logged.
COUNTED_VIOLATIONS = {"tab_switch", "fullscreen_exit", "window_blur", "devtools"}
LOGGED_VIOLATIONS = {"copy", "paste", "context_menu", "offline", "print", "resize"}
DUPLICATE_WINDOW = 2  # seconds: a blur and a tab switch fired together count once

_rng = random.SystemRandom()


def _attempt(conn, aid):
    r = conn.execute("SELECT * FROM attempts WHERE id=?", (aid,)).fetchone()
    if not r:
        raise NotFound("Attempt not found")
    return dict(r)


def _own_attempt(conn, user, aid):
    a = _attempt(conn, aid)
    if a["user_id"] != user["id"]:
        raise Forbidden("This is not your exam")
    return a


def _schedule_exam(conn, sid):
    r = conn.execute("SELECT s.*, e.* , s.id AS schedule_id, e.id AS exam_id FROM schedules s "
                     "JOIN exams e ON e.id = s.exam_id WHERE s.id=?", (sid,)).fetchone()
    if not r:
        raise NotFound("Exam schedule not found")
    return dict(r)


# ---------------------------------------------------------------- paper

def build_paper(conn, exam):
    if exam["selection_mode"] == "fixed":
        qids = [r[0] for r in conn.execute(
            "SELECT question_id FROM exam_questions WHERE exam_id=? ORDER BY position", (exam["exam_id"],))]
    else:
        qids, chosen = [], set()
        for rule in json.loads(exam["random_rules"] or "[]"):
            sql, args = "SELECT id FROM questions WHERE active=1", []
            if rule.get("subject"):
                sql += " AND subject=? COLLATE NOCASE"
                args.append(rule["subject"])
            if rule.get("difficulty"):
                sql += " AND difficulty=?"
                args.append(rule["difficulty"])
            pool = [r[0] for r in conn.execute(sql, args) if r[0] not in chosen]
            if len(pool) < rule["count"]:
                raise ValidationError("Not enough questions in the bank for this exam. Contact the administrator.")
            pick = _rng.sample(pool, rule["count"])
            chosen.update(pick)
            qids += pick
    if not qids:
        raise ValidationError("This exam has no questions")
    if exam["shuffle_questions"]:
        _rng.shuffle(qids)
    paper = []
    for qid in qids:
        n = len(json.loads(conn.execute("SELECT options FROM questions WHERE id=?", (qid,)).fetchone()[0]))
        order = list(range(n))
        if exam["shuffle_options"]:
            _rng.shuffle(order)
        paper.append({"q": qid, "o": order})
    return paper


# ---------------------------------------------------------------- views

def my_exams(conn, user):
    now = clock.now()
    rows = conn.execute(
        "SELECT s.*, e.title, e.duration_min, e.description, e.show_result, e.instructions, e.negative_marking, "
        "e.max_violations, e.require_fullscreen FROM schedules s "
        "JOIN exams e ON e.id=s.exam_id WHERE s.batch_id=? AND s.ends_at > ? ORDER BY s.starts_at",
        (user["batch_id"], now - 90 * 86400))
    out = []
    for s in rows:
        s = dict(s)
        a = conn.execute("SELECT id, status, score, max_score, submitted_at FROM attempts "
                         "WHERE schedule_id=? AND user_id=?", (s["id"], user["id"])).fetchone()
        result_ready = bool(a and a["status"] != "in_progress"
                            and (s["results_published"] or s["show_result"] == "immediate"))
        out.append({
            "schedule_id": s["id"], "title": s["title"], "description": s["description"],
            "duration_min": s["duration_min"], "starts_at": s["starts_at"], "ends_at": s["ends_at"],
            "status": schedule_status(s, now), "attempt_id": a["id"] if a else None,
            "attempt_status": a["status"] if a else None, "result_ready": result_ready,
            "results_published": bool(s["results_published"]), "instructions": s["instructions"],
            "negative_marking": bool(s["negative_marking"]), "max_violations": s["max_violations"],
            "require_fullscreen": bool(s["require_fullscreen"]),
        })
    return {"server_now": now, "exams": out}


def attempt_view(conn, a):
    ex = _schedule_exam(conn, a["schedule_id"])
    now = clock.now()
    paper = json.loads(a["paper"])
    qrows = {r["id"]: dict(r) for r in conn.execute(
        f"SELECT id, subject, topic, qtype, text, options, marks, negative FROM questions "
        f"WHERE id IN ({','.join('?' * len(paper))})", [p["q"] for p in paper])}
    questions = []
    for i, p in enumerate(paper):
        q = qrows[p["q"]]
        opts = json.loads(q["options"])
        questions.append({
            "id": q["id"], "number": i + 1, "subject": q["subject"], "qtype": q["qtype"], "text": q["text"],
            "options": [{"key": k, "text": opts[k]} for k in p["o"]],
            "marks": q["marks"], "negative": q["negative"] if ex["negative_marking"] else 0,
        })
    answers = {str(r["question_id"]): {"selected": json.loads(r["selected"]), "flagged": bool(r["flagged"]),
                                       "seq": r["seq"]}
               for r in conn.execute("SELECT * FROM answers WHERE attempt_id=?", (a["id"],))}
    return {
        "id": a["id"], "status": a["status"], "schedule_id": a["schedule_id"],
        "exam": {"title": ex["title"], "instructions": ex["instructions"], "duration_min": ex["duration_min"],
                 "require_fullscreen": bool(ex["require_fullscreen"]), "max_violations": ex["max_violations"],
                 "negative_marking": bool(ex["negative_marking"]), "show_result": ex["show_result"]},
        "started_at": a["started_at"], "deadline": a["deadline"], "server_now": now,
        "remaining": max(0, a["deadline"] - now), "violations": a["violations"],
        "questions": questions, "answers": answers,
    }


# ---------------------------------------------------------------- lifecycle

def start_attempt(conn, user, sid, ip=None, client_info=None):
    ex = _schedule_exam(conn, sid)
    if user["batch_id"] != ex["batch_id"]:
        raise Forbidden("This exam is not scheduled for your batch")
    existing = conn.execute("SELECT * FROM attempts WHERE schedule_id=? AND user_id=?", (sid, user["id"])).fetchone()
    now = clock.now()
    if existing:
        a = dict(existing)
        if a["status"] != "in_progress":
            raise Conflict("You have already submitted this exam")
        maybe_finalize(conn, a)
        a = _attempt(conn, a["id"])
        if a["status"] != "in_progress":
            raise Conflict("Time is over for this exam; it has been submitted")
        audit.log(conn, user, "resume", "attempt", a["id"], None, ip)
        return attempt_view(conn, a)
    if now < ex["starts_at"]:
        raise ValidationError("This exam has not started yet")
    if now > ex["ends_at"]:
        raise ValidationError("The exam window has closed")
    paper = build_paper(conn, ex)
    deadline = min(now + ex["duration_min"] * 60, ex["ends_at"])
    aid = conn.execute(
        "INSERT INTO attempts(schedule_id, user_id, status, started_at, deadline, paper, last_seen, client_info, ip) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (sid, user["id"], "in_progress", now, deadline, json.dumps(paper, separators=(",", ":")), now,
         (client_info or "")[:300], ip)).lastrowid
    audit.log(conn, user, "start", "attempt", aid, {"schedule": sid}, ip)
    return attempt_view(conn, _attempt(conn, aid))


def get_attempt(conn, user, aid):
    a = _own_attempt(conn, user, aid)
    maybe_finalize(conn, a)
    return attempt_view(conn, _attempt(conn, aid))


def heartbeat(conn, user, aid):
    a = _own_attempt(conn, user, aid)
    maybe_finalize(conn, a)
    a = _attempt(conn, aid)
    now = clock.now()
    if a["status"] == "in_progress":
        conn.execute("UPDATE attempts SET last_seen=? WHERE id=?", (now, aid))
    return {"status": a["status"], "server_now": now, "deadline": a["deadline"],
            "remaining": max(0, a["deadline"] - now), "violations": a["violations"]}


def save_answers(conn, user, aid, items, ip=None):
    """Idempotent upsert used by autosave and offline sync. Highest client `seq` wins per question."""
    a = _own_attempt(conn, user, aid)
    maybe_finalize(conn, a)
    a = _attempt(conn, aid)
    if a["status"] != "in_progress":
        raise Conflict("This exam has already been submitted")
    now = clock.now()
    late = now > a["deadline"]
    paper = {p["q"]: p["o"] for p in json.loads(a["paper"])}
    qtypes = {r[0]: r[1] for r in conn.execute(
        f"SELECT id, qtype FROM questions WHERE id IN ({','.join('?' * len(paper))})", list(paper))}
    accepted = rejected = 0
    for it in (items or [])[:1000]:
        try:
            qid = int(it.get("question_id"))
            seq = int(it.get("seq") or 0)
            client_ts = int(it.get("client_ts") or now)
        except (TypeError, ValueError):
            rejected += 1
            continue
        if qid not in paper or (late and client_ts > a["deadline"]):
            rejected += 1
            continue
        try:
            selected = sorted({int(x) for x in (it.get("selected") or [])})
        except (TypeError, ValueError):
            rejected += 1
            continue
        if any(x not in paper[qid] for x in selected) or (qtypes[qid] == "single" and len(selected) > 1):
            rejected += 1
            continue
        conn.execute(
            "INSERT INTO answers(attempt_id, question_id, selected, flagged, seq, saved_at) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(attempt_id, question_id) DO UPDATE SET selected=excluded.selected, "
            "flagged=excluded.flagged, seq=excluded.seq, saved_at=excluded.saved_at WHERE excluded.seq > answers.seq",
            (aid, qid, json.dumps(selected), 1 if it.get("flagged") else 0, seq, now))
        accepted += 1
    conn.execute("UPDATE attempts SET last_seen=? WHERE id=?", (now, aid))
    return {"accepted": accepted, "rejected": rejected, "status": a["status"],
            "remaining": max(0, a["deadline"] - now), "server_now": now}


def record_violation(conn, user, aid, kind, detail=None, ip=None):
    a = _own_attempt(conn, user, aid)
    if a["status"] != "in_progress":
        return {"violations": a["violations"], "terminated": a["status"] == "terminated", "status": a["status"]}
    kind = (kind or "").strip().lower()
    if kind not in COUNTED_VIOLATIONS | LOGGED_VIOLATIONS:
        raise ValidationError("Unknown violation type")
    now = clock.now()
    counted = kind in COUNTED_VIOLATIONS
    if counted:
        recent = conn.execute("SELECT 1 FROM violations WHERE attempt_id=? AND counted=1 AND at >= ?",
                              (aid, now - DUPLICATE_WINDOW)).fetchone()
        counted = not recent
    conn.execute("INSERT INTO violations(attempt_id, kind, detail, counted, at) VALUES (?,?,?,?,?)",
                 (aid, kind, (detail or "")[:200], 1 if counted else 0, now))
    violations = a["violations"] + (1 if counted else 0)
    conn.execute("UPDATE attempts SET violations=?, last_seen=? WHERE id=?", (violations, now, aid))
    ex = _schedule_exam(conn, a["schedule_id"])
    terminated = bool(ex["max_violations"]) and violations > ex["max_violations"]
    if terminated:
        finalize(conn, _attempt(conn, aid), "terminated")
        audit.log(conn, user, "terminated", "attempt", aid, {"violations": violations, "last": kind}, ip)
    return {"violations": violations, "max_violations": ex["max_violations"], "terminated": terminated,
            "status": "terminated" if terminated else "in_progress"}


def submit(conn, user, aid, items=None, ip=None):
    a = _own_attempt(conn, user, aid)
    if a["status"] == "in_progress" and items:
        save_answers(conn, user, aid, items, ip)
    a = _attempt(conn, aid)
    if a["status"] == "in_progress":
        finalize(conn, a, "submitted" if clock.now() <= a["deadline"] + 5 else "auto_submitted")
        audit.log(conn, user, "submit", "attempt", aid, None, ip)
    return {"status": _attempt(conn, aid)["status"], "attempt_id": aid}


def maybe_finalize(conn, a):
    """Auto-submit once the deadline plus the offline grace period has passed."""
    if a["status"] != "in_progress":
        return False
    grace = conn.execute("SELECT e.grace_seconds FROM schedules s JOIN exams e ON e.id=s.exam_id WHERE s.id=?",
                         (a["schedule_id"],)).fetchone()[0]
    if clock.now() > a["deadline"] + grace:
        finalize(conn, a, "auto_submitted")
        audit.log(conn, None, "auto_submit", "attempt", a["id"])
        return True
    return False


def auto_submit_expired(db):
    with db.read() as conn:
        ids = [r[0] for r in conn.execute(
            "SELECT a.id FROM attempts a JOIN schedules s ON s.id=a.schedule_id JOIN exams e ON e.id=s.exam_id "
            "WHERE a.status='in_progress' AND a.deadline + e.grace_seconds < ?", (clock.now(),))]
    for aid in ids:
        with db.tx() as conn:
            maybe_finalize(conn, _attempt(conn, aid))
    return len(ids)


def force_submit(conn, actor, aid):
    a = _attempt(conn, aid)
    if a["status"] != "in_progress":
        raise ValidationError("Attempt is not in progress")
    finalize(conn, a, "terminated")
    audit.log(conn, actor, "force_submit", "attempt", aid)
    return {"status": "terminated"}


# ---------------------------------------------------------------- scoring

def score_breakdown(conn, a):
    """Score an attempt. Returns totals plus per-question and per-subject detail."""
    paper = json.loads(a["paper"])
    neg_on = conn.execute("SELECT e.negative_marking FROM schedules s JOIN exams e ON e.id=s.exam_id WHERE s.id=?",
                          (a["schedule_id"],)).fetchone()[0]
    qs = {r["id"]: dict(r) for r in conn.execute(
        f"SELECT * FROM questions WHERE id IN ({','.join('?' * len(paper))})", [p["q"] for p in paper])}
    ans = {r["question_id"]: json.loads(r["selected"]) for r in conn.execute(
        "SELECT question_id, selected FROM answers WHERE attempt_id=?", (a["id"],))}
    total = max_score = 0.0
    correct = wrong = unanswered = 0
    per_q, subjects = [], {}
    for p in paper:
        q = qs[p["q"]]
        right = json.loads(q["correct"])
        sel = ans.get(q["id"]) or []
        max_score += q["marks"]
        sub = subjects.setdefault(q["subject"], {"subject": q["subject"], "score": 0.0, "max": 0.0,
                                                 "correct": 0, "wrong": 0, "unanswered": 0})
        sub["max"] += q["marks"]
        if not sel:
            outcome, got = "unanswered", 0.0
            unanswered += 1
            sub["unanswered"] += 1
        elif sorted(sel) == sorted(right):
            outcome, got = "correct", q["marks"]
            correct += 1
            sub["correct"] += 1
        else:
            outcome, got = "wrong", -(q["negative"] if neg_on else 0.0)
            wrong += 1
            sub["wrong"] += 1
        total += got
        sub["score"] += got
        per_q.append({"question_id": q["id"], "outcome": outcome, "marks": got, "selected": sel, "correct": right})
    return {"score": round(total, 2), "max_score": round(max_score, 2), "correct": correct, "wrong": wrong,
            "unanswered": unanswered, "questions": per_q,
            "subjects": [dict(s, score=round(s["score"], 2)) for s in subjects.values()]}


def finalize(conn, a, status):
    b = score_breakdown(conn, a)
    conn.execute("UPDATE attempts SET status=?, submitted_at=?, score=?, max_score=?, correct=?, wrong=?, "
                 "unanswered=? WHERE id=? AND status='in_progress'",
                 (status, clock.now(), b["score"], b["max_score"], b["correct"], b["wrong"], b["unanswered"], a["id"]))
    return b
