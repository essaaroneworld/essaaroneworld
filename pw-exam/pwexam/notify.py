"""Email / SMS outbox.

Messages are queued in the database inside the same transaction as the action
that caused them, then delivered by the background worker. Without SMTP or an
SMS gateway configured, messages are marked 'logged' and printed to the server
console — convenient on localhost and in an offline LAN lab.
"""
import json
import smtplib
import sys
import urllib.request
from email.message import EmailMessage

from . import clock
from .config import settings


def queue(conn, channel, recipient, body, subject=None, sensitive=False):
    if not recipient:
        return None
    return conn.execute(
        "INSERT INTO notifications(channel, recipient, subject, body, sensitive, created_at) VALUES (?,?,?,?,?,?)",
        (channel, recipient, subject, body, 1 if sensitive else 0, clock.now()),
    ).lastrowid


def queue_user(conn, user, subject, body, sensitive=False):
    """Queue to every channel the user has (email and/or phone)."""
    ids = []
    if user.get("email"):
        ids.append(queue(conn, "email", user["email"], body, subject, sensitive))
    if user.get("phone"):
        ids.append(queue(conn, "sms", user["phone"], f"{subject}: {body}" if subject else body, None, sensitive))
    return ids


def _send_email(n):
    msg = EmailMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = n["recipient"]
    msg["Subject"] = n["subject"] or "PW Batch Online Examination"
    msg.set_content(n["body"])
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as s:
        if settings.smtp_tls:
            s.starttls()
        if settings.smtp_user:
            s.login(settings.smtp_user, settings.smtp_password)
        s.send_message(msg)


def _send_sms(n):
    req = urllib.request.Request(
        settings.sms_webhook, data=json.dumps({"to": n["recipient"], "message": n["body"]}).encode(),
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {settings.sms_token}"} if settings.sms_token else {})},
        method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
        if resp.status >= 300:
            raise RuntimeError(f"SMS gateway returned {resp.status}")


def dispatch_pending(db, limit=50):
    with db.read() as conn:
        pending = [dict(r) for r in conn.execute(
            "SELECT * FROM notifications WHERE status='queued' AND tries < 5 ORDER BY id LIMIT ?", (limit,))]
    for n in pending:
        status, error = "sent", None
        try:
            if n["channel"] == "email" and settings.smtp_host:
                _send_email(n)
            elif n["channel"] == "sms" and settings.sms_webhook:
                _send_sms(n)
            else:
                status = "logged"
                sys.stderr.write(f"[notify:{n['channel']}] to {n['recipient']}: "
                                 f"{(n['subject'] + ' — ') if n['subject'] else ''}{n['body']}\n")
        except Exception as e:  # delivery failures are retried by the worker
            status, error = "queued", str(e)[:300]
        with db.tx() as conn:
            conn.execute(
                "UPDATE notifications SET status=?, tries=tries+1, error=?, sent_at=? WHERE id=?",
                ("failed" if status == "queued" and n["tries"] >= 4 else status, error,
                 clock.now() if status in ("sent", "logged") else None, n["id"]))


def list_notifications(conn, limit=300):
    out = []
    for r in conn.execute("SELECT * FROM notifications ORDER BY id DESC LIMIT ?", (min(int(limit), 2000),)):
        d = dict(r)
        if d["sensitive"]:
            d["body"] = "•••••• (one-time password hidden)"
        out.append(d)
    return out
