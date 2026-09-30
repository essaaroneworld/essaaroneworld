"""Question bank: MCQs (single or multiple correct), difficulty, subject/topic, CSV import."""
import csv
import io
import json
import re

from . import audit, clock
from .errors import NotFound, ValidationError

LETTERS = "ABCDEFGH"
DIFFICULTIES = ("easy", "medium", "hard")


def question_out(r, with_answer=True):
    d = dict(r)
    d["options"] = json.loads(d["options"])
    d["correct"] = json.loads(d["correct"])
    d["active"] = bool(d["active"])
    if not with_answer:
        d.pop("correct", None)
        d.pop("explanation", None)
    return d


def get_question(conn, qid):
    r = conn.execute("SELECT * FROM questions WHERE id=?", (qid,)).fetchone()
    if not r:
        raise NotFound("Question not found")
    return question_out(r)


def list_questions(conn, subject=None, difficulty=None, search=None, active=None, limit=500, offset=0):
    sql, args = "SELECT * FROM questions WHERE 1=1", []
    if subject:
        sql += " AND subject=? COLLATE NOCASE"
        args.append(subject)
    if difficulty:
        sql += " AND difficulty=?"
        args.append(difficulty)
    if search:
        sql += " AND (text LIKE ? OR topic LIKE ?)"
        args += [f"%{search}%"] * 2
    if active is not None:
        sql += " AND active=?"
        args.append(1 if active else 0)
    total = conn.execute(sql.replace("SELECT *", "SELECT COUNT(*)"), args).fetchone()[0]
    rows = conn.execute(sql + " ORDER BY id DESC LIMIT ? OFFSET ?", args + [min(int(limit), 2000), int(offset)])
    return {"total": total, "rows": [question_out(r) for r in rows]}


def subjects(conn):
    return [dict(r) for r in conn.execute(
        "SELECT subject, difficulty, COUNT(*) AS count FROM questions WHERE active=1 "
        "GROUP BY subject, difficulty ORDER BY subject, difficulty")]


def _validate(data):
    text = (data.get("text") or "").strip()
    subject = (data.get("subject") or "").strip()
    if not text:
        raise ValidationError("Question text is required")
    if not subject:
        raise ValidationError("Subject is required")
    qtype = data.get("qtype") or "single"
    if qtype not in ("single", "multi"):
        raise ValidationError("Type must be 'single' or 'multi'")
    options = [str(o).strip() for o in (data.get("options") or [])]
    while options and not options[-1]:
        options.pop()
    if len(options) < 2 or len(options) > len(LETTERS):
        raise ValidationError(f"Provide between 2 and {len(LETTERS)} options")
    if any(not o for o in options):
        raise ValidationError("Options cannot be blank")
    if len(set(o.lower() for o in options)) != len(options):
        raise ValidationError("Options must be different from each other")
    try:
        correct = sorted({int(c) for c in (data.get("correct") or [])})
    except (TypeError, ValueError):
        raise ValidationError("Correct answer must be option numbers")
    if not correct or any(c < 0 or c >= len(options) for c in correct):
        raise ValidationError("Mark the correct option(s)")
    if qtype == "single" and len(correct) != 1:
        raise ValidationError("A single-answer question needs exactly one correct option")
    try:
        marks = float(data.get("marks", 1) or 1)
        negative = float(data.get("negative", 0) or 0)
    except (TypeError, ValueError):
        raise ValidationError("Marks must be numbers")
    if marks <= 0 or negative < 0 or negative > marks * 4:
        raise ValidationError("Marks must be positive and negative marks non-negative")
    difficulty = (data.get("difficulty") or "medium").lower()
    if difficulty not in DIFFICULTIES:
        raise ValidationError("Difficulty must be easy, medium or hard")
    return {"subject": subject, "topic": (data.get("topic") or "").strip() or None, "qtype": qtype, "text": text,
            "options": json.dumps(options, ensure_ascii=False), "correct": json.dumps(correct), "marks": marks,
            "negative": negative, "difficulty": difficulty,
            "explanation": (data.get("explanation") or "").strip() or None,
            "active": 0 if data.get("active") is False else 1}


def save_question(conn, actor, data, qid=None):
    f = _validate(data)
    if qid:
        get_question(conn, qid)
        conn.execute(f"UPDATE questions SET {', '.join(k + '=?' for k in f)} WHERE id=?", list(f.values()) + [qid])
        audit.log(conn, actor, "update", "question", qid)
    else:
        cols = list(f) + ["created_by", "created_at"]
        qid = conn.execute(f"INSERT INTO questions({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                           list(f.values()) + [actor["id"] if actor else None, clock.now()]).lastrowid
        audit.log(conn, actor, "create", "question", qid)
    return get_question(conn, qid)


def delete_question(conn, actor, qid):
    get_question(conn, qid)
    used = conn.execute("SELECT 1 FROM exam_questions WHERE question_id=? LIMIT 1", (qid,)).fetchone() or \
        conn.execute("SELECT 1 FROM answers WHERE question_id=? LIMIT 1", (qid,)).fetchone()
    if used:
        conn.execute("UPDATE questions SET active=0 WHERE id=?", (qid,))
        audit.log(conn, actor, "deactivate", "question", qid, "in use; deactivated instead of deleted")
        return {"deactivated": qid}
    conn.execute("DELETE FROM questions WHERE id=?", (qid,))
    audit.log(conn, actor, "delete", "question", qid)
    return {"deleted": qid}


def parse_correct(text, n_options):
    """'B' or 'A;C' or 'a, c' or '1,3' -> [0-based indexes]."""
    out = []
    for tok in re.split(r"[;,|\s]+", (text or "").strip()):
        if not tok:
            continue
        if tok.isdigit():
            out.append(int(tok) - 1)
        elif len(tok) == 1 and tok.upper() in LETTERS:
            out.append(LETTERS.index(tok.upper()))
        else:
            raise ValidationError(f"Cannot read correct answer '{tok}'")
    if any(i < 0 or i >= n_options for i in out):
        raise ValidationError("Correct answer refers to a missing option")
    return out


CSV_HEADER = ["subject", "topic", "type", "difficulty", "question", "option_a", "option_b", "option_c",
              "option_d", "option_e", "option_f", "correct", "marks", "negative", "explanation"]


def import_csv(conn, actor, csv_text):
    reader = csv.DictReader(io.StringIO((csv_text or "").strip().lstrip("﻿")))
    fields = {(f or "").strip().lower() for f in (reader.fieldnames or [])}
    if not {"subject", "question", "option_a", "option_b", "correct"} <= fields:
        raise ValidationError("CSV header must include: subject, question, option_a, option_b, correct "
                              f"(full format: {','.join(CSV_HEADER)})")
    created, errors = 0, []
    for line, row in enumerate(reader, start=2):
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items() if k}
        try:
            options = [row.get(f"option_{c}", "") for c in "abcdefgh"]
            while options and not options[-1]:
                options.pop()
            correct = parse_correct(row.get("correct"), len(options))
            qtype = (row.get("type") or ("multi" if len(correct) > 1 else "single")).lower()
            # save_question validates fully before writing, so a bad row writes nothing.
            save_question(conn, actor, {
                "subject": row.get("subject"), "topic": row.get("topic"), "qtype": qtype,
                "difficulty": row.get("difficulty") or "medium", "text": row.get("question"),
                "options": options, "correct": correct, "marks": row.get("marks") or 1,
                "negative": row.get("negative") or 0, "explanation": row.get("explanation")})
            created += 1
        except ValidationError as e:
            errors.append({"line": line, "error": e.message})
    audit.log(conn, actor, "import", "questions", None, {"created": created, "errors": len(errors)})
    return {"created": created, "errors": errors}

