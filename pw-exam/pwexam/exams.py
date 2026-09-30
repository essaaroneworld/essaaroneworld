"""Exams (rules + question selection) and schedules (exam × batch × time window)."""
import json

from . import audit, clock, notify
from .errors import NotFound, ValidationError

BOOL_FIELDS = ("shuffle_questions", "shuffle_options", "negative_marking", "require_fullscreen")


def _exam_row(conn, exam_id):
    r = conn.execute("SELECT * FROM exams WHERE id=?", (exam_id,)).fetchone()
    if not r:
        raise NotFound("Exam not found")
    return dict(r)


def exam_out(conn, r):
    d = dict(r)
    for k in BOOL_FIELDS:
        d[k] = bool(d[k])
    d["random_rules"] = json.loads(d["random_rules"]) if d["random_rules"] else []
    d["question_ids"] = [x["question_id"] for x in conn.execute(
        "SELECT question_id FROM exam_questions WHERE exam_id=? ORDER BY position", (d["id"],))]
    if d["selection_mode"] == "fixed":
        row = conn.execute("SELECT COUNT(*), COALESCE(SUM(q.marks),0) FROM exam_questions eq JOIN questions q "
                           "ON q.id=eq.question_id WHERE eq.exam_id=?", (d["id"],)).fetchone()
        d["question_count"], d["max_marks"] = row[0], row[1]
    else:
        d["question_count"] = sum(int(r["count"]) for r in d["random_rules"])
        d["max_marks"] = None  # depends on the questions drawn for each candidate
    d["has_attempts"] = bool(conn.execute(
        "SELECT 1 FROM attempts a JOIN schedules s ON s.id=a.schedule_id WHERE s.exam_id=? LIMIT 1",
        (d["id"],)).fetchone())
    return d


def get_exam(conn, exam_id):
    return exam_out(conn, _exam_row(conn, exam_id))


def list_exams(conn):
    return [exam_out(conn, r) for r in conn.execute("SELECT * FROM exams ORDER BY id DESC")]


def _int(data, key, default, lo, hi, label):
    try:
        v = int(data.get(key, default) if data.get(key, default) not in ("", None) else default)
    except (TypeError, ValueError):
        raise ValidationError(f"{label} must be a whole number")
    if not lo <= v <= hi:
        raise ValidationError(f"{label} must be between {lo} and {hi}")
    return v


def available_count(conn, subject=None, difficulty=None):
    sql, args = "SELECT COUNT(*) FROM questions WHERE active=1", []
    if subject:
        sql += " AND subject=? COLLATE NOCASE"
        args.append(subject)
    if difficulty:
        sql += " AND difficulty=?"
        args.append(difficulty)
    return conn.execute(sql, args).fetchone()[0]


def _validate(conn, data):
    title = (data.get("title") or "").strip()
    if not title:
        raise ValidationError("Exam title is required")
    f = {
        "title": title, "description": (data.get("description") or "").strip() or None,
        "instructions": (data.get("instructions") or "").strip() or None,
        "duration_min": _int(data, "duration_min", 60, 1, 720, "Duration (minutes)"),
        "max_violations": _int(data, "max_violations", 3, 0, 100, "Maximum violations"),
        "grace_seconds": _int(data, "grace_seconds", 120, 0, 3600, "Offline grace period (seconds)"),
    }
    try:
        f["pass_percent"] = float(data.get("pass_percent", 40))
    except (TypeError, ValueError):
        raise ValidationError("Pass percentage must be a number")
    if not 0 <= f["pass_percent"] <= 100:
        raise ValidationError("Pass percentage must be between 0 and 100")
    for k in BOOL_FIELDS:
        f[k] = 1 if data.get(k, True) else 0
    f["show_result"] = data.get("show_result") or "on_publish"
    if f["show_result"] not in ("immediate", "on_publish"):
        raise ValidationError("Show result must be 'immediate' or 'on_publish'")
    mode = data.get("selection_mode") or "fixed"
    if mode not in ("fixed", "random"):
        raise ValidationError("Question selection must be 'fixed' or 'random'")
    f["selection_mode"] = mode
    qids = []
    if mode == "fixed":
        seen = set()
        for q in data.get("question_ids") or []:
            q = int(q)
            if q not in seen:
                seen.add(q)
                qids.append(q)
        if not qids:
            raise ValidationError("Select at least one question")
        found = {r[0] for r in conn.execute(
            f"SELECT id FROM questions WHERE active=1 AND id IN ({','.join('?' * len(qids))})", qids)}
        missing = [q for q in qids if q not in found]
        if missing:
            raise ValidationError(f"Questions not found or inactive: {missing[:10]}")
        f["random_rules"] = None
    else:
        rules = []
        for rule in data.get("random_rules") or []:
            count = int(rule.get("count") or 0)
            if count <= 0:
                continue
            subject = (rule.get("subject") or "").strip() or None
            difficulty = (rule.get("difficulty") or "").strip().lower() or None
            have = available_count(conn, subject, difficulty)
            if have < count:
                raise ValidationError(f"Only {have} active question(s) for "
                                      f"{subject or 'any subject'} / {difficulty or 'any difficulty'}, "
                                      f"but {count} requested")
            rules.append({"subject": subject, "difficulty": difficulty, "count": count})
        if not rules:
            raise ValidationError("Add at least one random selection rule")
        f["random_rules"] = json.dumps(rules)
    return f, qids


def save_exam(conn, actor, data, exam_id=None):
    if exam_id:
        existing = get_exam(conn, exam_id)
        if existing["has_attempts"]:
            raise ValidationError("Candidates have already attempted this exam. Duplicate it to make changes.")
    f, qids = _validate(conn, data)
    if exam_id:
        conn.execute(f"UPDATE exams SET {', '.join(k + '=?' for k in f)} WHERE id=?", list(f.values()) + [exam_id])
        conn.execute("DELETE FROM exam_questions WHERE exam_id=?", (exam_id,))
        audit.log(conn, actor, "update", "exam", exam_id, {"title": f["title"]})
    else:
        cols = list(f) + ["created_by", "created_at"]
        exam_id = conn.execute(f"INSERT INTO exams({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                               list(f.values()) + [actor["id"] if actor else None, clock.now()]).lastrowid
        audit.log(conn, actor, "create", "exam", exam_id, {"title": f["title"]})
    for pos, q in enumerate(qids):
        conn.execute("INSERT INTO exam_questions(exam_id, question_id, position) VALUES (?,?,?)", (exam_id, q, pos))
    return get_exam(conn, exam_id)


def duplicate_exam(conn, actor, exam_id):
    e = get_exam(conn, exam_id)
    e["title"] = f"{e['title']} (copy)"
    return save_exam(conn, actor, e)


def delete_exam(conn, actor, exam_id):
    e = _exam_row(conn, exam_id)
    if conn.execute("SELECT 1 FROM schedules WHERE exam_id=? LIMIT 1", (exam_id,)).fetchone():
        raise ValidationError("Exam is scheduled; delete its schedules first")
    conn.execute("DELETE FROM exams WHERE id=?", (exam_id,))
    audit.log(conn, actor, "delete", "exam", exam_id, {"title": e["title"]})
    return {"deleted": e["title"]}


# ---------------------------------------------------------------- schedules

def schedule_status(s, now=None):
    now = now or clock.now()
    if now < s["starts_at"]:
        return "upcoming"
    if now <= s["ends_at"]:
        return "live"
    return "closed"


def _schedule_row(conn, sid):
    r = conn.execute(
        "SELECT s.*, e.title AS exam_title, e.duration_min, e.show_result, b.name AS batch_name FROM schedules s "
        "JOIN exams e ON e.id=s.exam_id JOIN batches b ON b.id=s.batch_id WHERE s.id=?", (sid,)).fetchone()
    if not r:
        raise NotFound("Schedule not found")
    return dict(r)


def schedule_out(conn, r):
    d = dict(r)
    d["status"] = schedule_status(d)
    d["results_published"] = bool(d["results_published"])
    c = conn.execute(
        "SELECT (SELECT COUNT(*) FROM users WHERE batch_id=? AND role='candidate' AND active=1), "
        "SUM(status='in_progress'), SUM(status!='in_progress') FROM attempts WHERE schedule_id=?",
        (d["batch_id"], d["id"])).fetchone()
    d["candidates"], d["in_progress"], d["submitted"] = c[0], c[1] or 0, c[2] or 0
    return d


def get_schedule(conn, sid):
    return schedule_out(conn, _schedule_row(conn, sid))


def list_schedules(conn):
    rows = conn.execute(
        "SELECT s.*, e.title AS exam_title, e.duration_min, e.show_result, b.name AS batch_name FROM schedules s "
        "JOIN exams e ON e.id=s.exam_id JOIN batches b ON b.id=s.batch_id ORDER BY s.starts_at DESC")
    return [schedule_out(conn, r) for r in rows]


def save_schedule(conn, actor, data, sid=None):
    exam = _exam_row(conn, int(data.get("exam_id") or 0))
    b = conn.execute("SELECT * FROM batches WHERE id=?", (int(data.get("batch_id") or 0),)).fetchone()
    if not b:
        raise NotFound("Batch not found")
    try:
        starts, ends = int(data.get("starts_at")), int(data.get("ends_at"))
    except (TypeError, ValueError):
        raise ValidationError("Start and end time are required")
    if ends - starts < 300:
        raise ValidationError("The exam window must be at least 5 minutes long")
    # A window shorter than the duration is allowed: the deadline is capped at the window end.
    if sid:
        old = _schedule_row(conn, sid)
        if conn.execute("SELECT 1 FROM attempts WHERE schedule_id=? LIMIT 1", (sid,)).fetchone():
            if old["exam_id"] != exam["id"] or old["batch_id"] != b["id"] or old["starts_at"] != starts:
                raise ValidationError("Exam has started; only the end time can be changed")
        conn.execute("UPDATE schedules SET exam_id=?, batch_id=?, starts_at=?, ends_at=? WHERE id=?",
                     (exam["id"], b["id"], starts, ends, sid))
        audit.log(conn, actor, "update", "schedule", sid, {"starts_at": starts, "ends_at": ends})
    else:
        sid = conn.execute("INSERT INTO schedules(exam_id, batch_id, starts_at, ends_at, created_by, created_at) "
                           "VALUES (?,?,?,?,?,?)",
                           (exam["id"], b["id"], starts, ends, actor["id"] if actor else None, clock.now())).lastrowid
        audit.log(conn, actor, "create", "schedule", sid, {"exam": exam["title"], "batch": b["name"]})
    if data.get("notify"):
        notify_schedule(conn, actor, sid)
    return get_schedule(conn, sid)


def delete_schedule(conn, actor, sid):
    s = _schedule_row(conn, sid)
    if conn.execute("SELECT 1 FROM attempts WHERE schedule_id=? LIMIT 1", (sid,)).fetchone():
        raise ValidationError("Candidates have attempted this exam; it cannot be deleted")
    conn.execute("DELETE FROM schedules WHERE id=?", (sid,))
    audit.log(conn, actor, "delete", "schedule", sid, {"exam": s["exam_title"], "batch": s["batch_name"]})
    return {"deleted": sid}


def notify_schedule(conn, actor, sid):
    s = _schedule_row(conn, sid)
    count = 0
    for u in conn.execute("SELECT * FROM users WHERE batch_id=? AND role='candidate' AND active=1", (s["batch_id"],)):
        u = dict(u)
        body = (f"Dear {u['name']}, your exam '{s['exam_title']}' ({s['duration_min']} min) is scheduled "
                f"from {clock.fmt(s['starts_at'])} to {clock.fmt(s['ends_at'])}. Login ID: {u['login_id']}.")
        count += len(notify.queue_user(conn, u, "Exam scheduled", body))
    conn.execute("UPDATE schedules SET notified_at=? WHERE id=?", (clock.now(), sid))
    audit.log(conn, actor, "notify", "schedule", sid, {"messages": count})
    return {"queued": count}
