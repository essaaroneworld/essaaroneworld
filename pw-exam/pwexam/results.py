"""Results: ranking, percentile, publishing, analytics, live monitor, exports and report cards."""
import csv
import io
import json
import statistics

from . import PRODUCT, VENDOR, audit, clock, notify
from .attempts import _attempt, score_breakdown
from .errors import Forbidden, NotFound, ValidationError
from .exams import get_schedule
from .pdf import PDF

FINISHED = ("submitted", "auto_submitted", "terminated")


def _finished_attempts(conn, sid):
    return [dict(r) for r in conn.execute(
        "SELECT a.*, u.login_id, u.name, u.email, u.phone FROM attempts a JOIN users u ON u.id=a.user_id "
        f"WHERE a.schedule_id=? AND a.status IN ({','.join('?' * len(FINISHED))})", (sid, *FINISHED))]


def rank_attempts(attempts):
    """Rank by score (desc), then time taken (asc). Equal score and time share a rank.

    Percentile (NTA style) = 100 × candidates scoring ≤ you / total candidates.
    """
    n = len(attempts)
    ordered = sorted(attempts, key=lambda a: (-(a["score"] or 0), (a["submitted_at"] or 0) - a["started_at"]))
    scores = [a["score"] or 0 for a in attempts]
    prev_key, prev_rank = None, 0
    for i, a in enumerate(ordered, 1):
        key = (a["score"] or 0, (a["submitted_at"] or 0) - a["started_at"])
        a["rank"] = prev_rank if key == prev_key else i
        prev_key, prev_rank = key, a["rank"]
        a["percentile"] = round(100 * sum(1 for s in scores if s <= (a["score"] or 0)) / n, 2)
    return ordered


def schedule_results(conn, sid):
    s = get_schedule(conn, sid)
    pass_percent = conn.execute("SELECT pass_percent FROM exams WHERE id=?", (s["exam_id"],)).fetchone()[0]
    rows = []
    for a in rank_attempts(_finished_attempts(conn, sid)):
        pct = round(100 * (a["score"] or 0) / a["max_score"], 2) if a["max_score"] else 0
        rows.append({
            "attempt_id": a["id"], "login_id": a["login_id"], "name": a["name"], "status": a["status"],
            "score": a["score"], "max_score": a["max_score"], "percent": pct, "passed": pct >= pass_percent,
            "correct": a["correct"], "wrong": a["wrong"], "unanswered": a["unanswered"],
            "time_taken": (a["submitted_at"] or 0) - a["started_at"], "violations": a["violations"],
            "rank": a["rank"], "percentile": a["percentile"], "submitted_at": a["submitted_at"],
        })
    absent = [dict(r) for r in conn.execute(
        "SELECT login_id, name FROM users WHERE batch_id=? AND role='candidate' AND active=1 AND id NOT IN "
        "(SELECT user_id FROM attempts WHERE schedule_id=?) ORDER BY login_id", (s["batch_id"], sid))]
    return {"schedule": s, "pass_percent": pass_percent, "rows": rows, "absent": absent}


def results_csv(conn, sid):
    r = schedule_results(conn, sid)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Rank", "Login ID", "Name", "Score", "Max", "Percent", "Result", "Percentile", "Correct", "Wrong",
                "Unanswered", "Time (min)", "Violations", "Status"])
    for x in r["rows"]:
        w.writerow([x["rank"], x["login_id"], x["name"], x["score"], x["max_score"], x["percent"],
                    "PASS" if x["passed"] else "FAIL", x["percentile"], x["correct"], x["wrong"], x["unanswered"],
                    round(x["time_taken"] / 60, 1), x["violations"], x["status"]])
    for x in r["absent"]:
        w.writerow(["", x["login_id"], x["name"], "", "", "", "ABSENT", "", "", "", "", "", "", ""])
    return "﻿" + buf.getvalue()


def publish(conn, actor, sid, force=False):
    s = get_schedule(conn, sid)
    now = clock.now()
    if s["in_progress"] and not force:
        raise ValidationError(f"{s['in_progress']} candidate(s) are still writing this exam")
    if now < s["ends_at"] and not force:
        raise ValidationError("The exam window is still open. Publish after it closes (or force publish).")
    conn.execute("UPDATE schedules SET results_published=1, published_at=? WHERE id=?", (now, sid))
    res = schedule_results(conn, sid)
    sent = 0
    for r in res["rows"]:
        u = dict(conn.execute("SELECT * FROM users WHERE login_id=?", (r["login_id"],)).fetchone())
        body = (f"Dear {u['name']}, result of '{s['exam_title']}': {_n(r['score'])}/{_n(r['max_score'])} "
                f"({_n(r['percent'])}%), rank {r['rank']} of {len(res['rows'])}, "
                f"{'PASS' if r['passed'] else 'FAIL'}. Download your report card from the exam portal.")
        sent += len(notify.queue_user(conn, u, "Exam result published", body))
    audit.log(conn, actor, "publish", "schedule", sid, {"candidates": len(res["rows"]), "messages": sent})
    return {"published": True, "candidates": len(res["rows"]), "messages": sent}


def unpublish(conn, actor, sid):
    get_schedule(conn, sid)
    conn.execute("UPDATE schedules SET results_published=0 WHERE id=?", (sid,))
    audit.log(conn, actor, "unpublish", "schedule", sid)
    return {"published": False}


def analytics(conn, sid):
    res = schedule_results(conn, sid)
    rows = res["rows"]
    pcts = [r["percent"] for r in rows]
    buckets = [0] * 10
    for p in pcts:
        buckets[min(int(p // 10), 9) if p >= 0 else 0] += 1
    qstats = {}
    subj = {}
    for a in _finished_attempts(conn, sid):
        b = score_breakdown(conn, a)
        for q in b["questions"]:
            st = qstats.setdefault(q["question_id"], {"question_id": q["question_id"], "attempts": 0,
                                                      "correct": 0, "wrong": 0, "unanswered": 0})
            st["attempts"] += 1
            st[q["outcome"]] += 1
        for s in b["subjects"]:
            t = subj.setdefault(s["subject"], {"subject": s["subject"], "score": 0.0, "max": 0.0})
            t["score"] += s["score"]
            t["max"] += s["max"]
    texts = {}
    if qstats:
        texts = {r["id"]: dict(r) for r in conn.execute(
            f"SELECT id, subject, text, difficulty FROM questions WHERE id IN ({','.join('?' * len(qstats))})",
            list(qstats))}
    questions = []
    for qid, st in qstats.items():
        t = texts.get(qid, {})
        pc = round(100 * st["correct"] / st["attempts"], 1) if st["attempts"] else 0
        questions.append(dict(st, subject=t.get("subject"), text=(t.get("text") or "")[:140],
                              difficulty=t.get("difficulty"), percent_correct=pc,
                              flag="too hard / check key" if pc < 20 and st["attempts"] >= 5 else
                              ("too easy" if pc > 95 and st["attempts"] >= 5 else "")))
    questions.sort(key=lambda q: q["percent_correct"])
    viol = [dict(r) for r in conn.execute(
        "SELECT v.kind, COUNT(*) AS count FROM violations v JOIN attempts a ON a.id=v.attempt_id "
        "WHERE a.schedule_id=? GROUP BY v.kind ORDER BY count DESC", (sid,))]
    return {
        "schedule": res["schedule"], "appeared": len(rows), "absent": len(res["absent"]),
        "average": round(statistics.mean(pcts), 2) if pcts else None,
        "median": round(statistics.median(pcts), 2) if pcts else None,
        "highest": max(pcts) if pcts else None, "lowest": min(pcts) if pcts else None,
        "pass_rate": round(100 * sum(r["passed"] for r in rows) / len(rows), 1) if rows else None,
        "distribution": [{"range": f"{i * 10}-{i * 10 + 10}%", "count": c} for i, c in enumerate(buckets)],
        "subjects": [dict(s, percent=round(100 * s["score"] / s["max"], 1) if s["max"] else 0) for s in subj.values()],
        "questions": questions, "violations": viol,
    }


def monitor(conn, sid):
    s = get_schedule(conn, sid)
    now = clock.now()
    rows = []
    for a in conn.execute(
            "SELECT a.*, u.login_id, u.name, (SELECT COUNT(*) FROM answers x WHERE x.attempt_id=a.id AND "
            "x.selected != '[]') AS answered FROM attempts a JOIN users u ON u.id=a.user_id WHERE a.schedule_id=? "
            "ORDER BY a.status, u.login_id", (sid,)):
        a = dict(a)
        total = len(json.loads(a["paper"]))
        rows.append({"attempt_id": a["id"], "login_id": a["login_id"], "name": a["name"], "status": a["status"],
                     "started_at": a["started_at"], "remaining": max(0, a["deadline"] - now),
                     "answered": a["answered"], "total": total, "violations": a["violations"],
                     "online": a["status"] == "in_progress" and (a["last_seen"] or 0) >= now - 90,
                     "last_seen": a["last_seen"], "ip": a["ip"]})
    started = {r["login_id"] for r in rows}
    waiting = [dict(r) for r in conn.execute(
        "SELECT login_id, name FROM users WHERE batch_id=? AND role='candidate' AND active=1 ORDER BY login_id",
        (s["batch_id"],)) if r["login_id"] not in started]
    return {"schedule": s, "server_now": now, "attempts": rows, "not_started": waiting}


def attempt_detail(conn, aid):
    a = _attempt(conn, aid)
    u = dict(conn.execute("SELECT login_id, name FROM users WHERE id=?", (a["user_id"],)).fetchone())
    viol = [dict(r) for r in conn.execute("SELECT * FROM violations WHERE attempt_id=? ORDER BY at", (aid,))]
    b = score_breakdown(conn, a)
    return {"attempt_id": aid, "candidate": u, "status": a["status"], "started_at": a["started_at"],
            "deadline": a["deadline"], "submitted_at": a["submitted_at"], "client_info": a["client_info"],
            "ip": a["ip"], "violations": viol, "score": b["score"], "max_score": b["max_score"],
            "subjects": b["subjects"]}


# ---------------------------------------------------------------- candidate view

def candidate_result(conn, user, aid):
    a = _attempt(conn, aid)
    if a["user_id"] != user["id"]:
        raise Forbidden("This is not your exam")
    if a["status"] == "in_progress":
        raise ValidationError("The exam is still in progress")
    s = get_schedule(conn, a["schedule_id"])
    exam = dict(conn.execute("SELECT * FROM exams WHERE id=?", (s["exam_id"],)).fetchone())
    published = s["results_published"]
    if not (published or exam["show_result"] == "immediate"):
        return {"available": False, "status": a["status"], "exam_title": s["exam_title"],
                "message": "Your answers have been submitted. Results will be published by the examiner."}
    b = score_breakdown(conn, a)
    pct = round(100 * b["score"] / b["max_score"], 2) if b["max_score"] else 0
    out = {"available": True, "published": published, "status": a["status"], "exam_title": s["exam_title"],
           "batch": s["batch_name"], "candidate": {"login_id": user["login_id"], "name": user["name"]},
           "score": b["score"], "max_score": b["max_score"], "percent": pct,
           "passed": pct >= exam["pass_percent"], "pass_percent": exam["pass_percent"],
           "correct": b["correct"], "wrong": b["wrong"], "unanswered": b["unanswered"],
           "subjects": b["subjects"], "started_at": a["started_at"], "submitted_at": a["submitted_at"],
           "time_taken": (a["submitted_at"] or 0) - a["started_at"], "violations": a["violations"]}
    if published:
        ranked = {x["id"]: x for x in rank_attempts(_finished_attempts(conn, a["schedule_id"]))}
        me = ranked.get(aid)
        out.update(rank=me["rank"], percentile=me["percentile"], appeared=len(ranked))
        # Answer key review after publication.
        qs = {r["id"]: dict(r) for r in conn.execute(
            f"SELECT id, text, options, explanation FROM questions WHERE id IN "
            f"({','.join('?' * len(b['questions']))})", [q["question_id"] for q in b["questions"]])}
        out["review"] = [{"number": i + 1, "text": qs[q["question_id"]]["text"],
                          "options": json.loads(qs[q["question_id"]]["options"]), "selected": q["selected"],
                          "correct": q["correct"], "outcome": q["outcome"], "marks": q["marks"],
                          "explanation": qs[q["question_id"]]["explanation"]}
                         for i, q in enumerate(b["questions"])]
    return out


def report_card_pdf(conn, user_or_none, aid):
    """PDF report card. Candidates get their own; staff (user_or_none=None) may fetch any."""
    a = _attempt(conn, aid)
    owner = dict(conn.execute("SELECT * FROM users WHERE id=?", (a["user_id"],)).fetchone())
    if user_or_none is not None:
        r = candidate_result(conn, user_or_none, aid)
        if not r.get("available"):
            raise ValidationError("Your result is not available yet")
    else:
        r = candidate_result(conn, owner, aid)
        if not r.get("available"):
            # staff can always see the score; build it without rank
            b = score_breakdown(conn, a)
            pct = round(100 * b["score"] / b["max_score"], 2) if b["max_score"] else 0
            s = get_schedule(conn, a["schedule_id"])
            r = {"exam_title": s["exam_title"], "batch": s["batch_name"], "score": b["score"],
                 "max_score": b["max_score"], "percent": pct, "passed": None, "correct": b["correct"],
                 "wrong": b["wrong"], "unanswered": b["unanswered"], "subjects": b["subjects"],
                 "started_at": a["started_at"], "submitted_at": a["submitted_at"],
                 "time_taken": (a["submitted_at"] or 0) - a["started_at"], "violations": a["violations"],
                 "status": a["status"]}
    return build_report_pdf(owner, r)


def _n(v):
    """12.0 -> '12', 12.5 -> '12.5' for display."""
    return f"{v:g}" if isinstance(v, (int, float)) else str(v)


def build_report_pdf(owner, r):
    navy, gold, grey = (0.06, 0.16, 0.29), (0.75, 0.55, 0.1), (0.4, 0.4, 0.45)
    pdf = PDF(f"Report card - {owner['login_id']}")
    pdf.rect(0, 0, pdf.W, 92, fill=navy)
    pdf.text(40, 42, PRODUCT, 18, True, (1, 1, 1))
    pdf.text(40, 64, "Candidate Report Card", 11, False, (0.85, 0.9, 1))
    pdf.text_right(555, 64, f"A product of {VENDOR}", 9, False, (0.95, 0.8, 0.45))
    y = 130
    rows = [("Candidate", owner["name"]), ("Login ID", owner["login_id"]), ("Batch", r.get("batch") or ""),
            ("Examination", r["exam_title"]), ("Submitted", clock.fmt(r["submitted_at"]) if r.get("submitted_at") else "-"),
            ("Time taken", f"{round((r.get('time_taken') or 0) / 60, 1)} minutes")]
    for label, value in rows:
        pdf.text(40, y, label, 10, False, grey)
        pdf.text(150, y, value, 11, True)
        y += 20
    y += 10
    pdf.rect(40, y, 515, 78)
    boxes = [("SCORE", f"{_n(r['score'])} / {_n(r['max_score'])}"), ("PERCENT", f"{_n(r['percent'])}%"),
             ("RANK", f"{r['rank']} of {r['appeared']}" if r.get("rank") else "-"),
             ("PERCENTILE", _n(r["percentile"]) if r.get("percentile") is not None else "-")]
    for i, (label, value) in enumerate(boxes):
        x = 55 + i * 128
        pdf.text(x, y + 28, label, 9, True, grey)
        pdf.text(x, y + 56, value, 16, True, navy)
    y += 110
    if r.get("passed") is not None:
        verdict = "PASS" if r["passed"] else "FAIL"
        pdf.text(40, y, "Result:", 12, True)
        pdf.text(100, y, verdict, 14, True, (0.07, 0.45, 0.29) if r["passed"] else (0.7, 0.14, 0.1))
        pdf.text(170, y, f"(pass mark {_n(r.get('pass_percent', ''))}%)", 10, False, grey)
        y += 30
    pdf.text(40, y, f"Correct: {r['correct']}    Wrong: {r['wrong']}    Not answered: {r['unanswered']}    "
                    f"Proctoring flags: {r.get('violations', 0)}", 10)
    y += 34
    pdf.text(40, y, "Subject-wise performance", 12, True, navy)
    y += 10
    pdf.line(40, y, 555, y, 1, gold)
    y += 18
    for h, x in (("Subject", 40), ("Correct", 250), ("Wrong", 320), ("Skipped", 385), ("Score", 470)):
        pdf.text(x, y, h, 9, True, grey)
    y += 8
    pdf.line(40, y, 555, y)
    for s in r.get("subjects") or []:
        y += 18
        if y > 780:
            pdf.new_page()
            y = 60
        pdf.text(40, y, s["subject"], 10)
        pdf.text(250, y, s["correct"], 10)
        pdf.text(320, y, s["wrong"], 10)
        pdf.text(385, y, s["unanswered"], 10)
        pdf.text(470, y, f"{_n(s['score'])} / {_n(s['max'])}", 10, True)
    y += 12
    pdf.line(40, y, 555, y)
    pdf.text(40, 800, f"Generated {clock.fmt(clock.now())}. This is a computer-generated document.", 8, False, grey)
    pdf.text_right(555, 800, VENDOR, 8, True, grey)
    return pdf.output()


# ---------------------------------------------------------------- dashboard

def dashboard(conn):
    now = clock.now()
    one = lambda sql, *a: conn.execute(sql, a).fetchone()[0]  # noqa: E731
    return {
        "server_now": now,
        "questions": one("SELECT COUNT(*) FROM questions WHERE active=1"),
        "exams": one("SELECT COUNT(*) FROM exams"),
        "batches": one("SELECT COUNT(*) FROM batches"),
        "candidates": one("SELECT COUNT(*) FROM users WHERE role='candidate' AND active=1"),
        "live_schedules": one("SELECT COUNT(*) FROM schedules WHERE starts_at<=? AND ends_at>=?", now, now),
        "upcoming_schedules": one("SELECT COUNT(*) FROM schedules WHERE starts_at>?", now),
        "in_progress": one("SELECT COUNT(*) FROM attempts WHERE status='in_progress'"),
        "submitted_today": one("SELECT COUNT(*) FROM attempts WHERE status!='in_progress' AND submitted_at>=?",
                               now - 86400),
        "pending_publish": one("SELECT COUNT(*) FROM schedules WHERE ends_at<? AND results_published=0", now),
        "queued_notifications": one("SELECT COUNT(*) FROM notifications WHERE status='queued'"),
    }


