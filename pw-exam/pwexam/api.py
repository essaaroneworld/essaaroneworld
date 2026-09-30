"""JSON API routes with authentication and role-based permissions."""
import re
import threading
import time
from collections import defaultdict, deque

from . import attempts, audit, exams, notify, questions, results, users
from .errors import AppError, AuthError

ROUTES = []


class Ctx:
    def __init__(self, conn, user, body, query, params, ip):
        self.conn, self.user, self.body, self.query, self.params, self.ip = conn, user, body, query, params, ip


class Raw:
    """Non-JSON response (CSV / PDF download)."""

    def __init__(self, data, content_type, filename=None):
        self.data, self.content_type, self.filename = data, content_type, filename


def route(method, pattern, perm=None, write=False, public=False):
    rx = re.compile("^/api" + re.sub(r"{(\w+)}", r"(?P<\1>\\d+)", pattern) + "$")

    def deco(fn):
        ROUTES.append((method, rx, fn, perm, write, public))
        return fn
    return deco


# ---------------------------------------------------------------- login rate limiting (per IP)

_login_hits = defaultdict(deque)
_login_lock = threading.Lock()
LOGIN_LIMIT, LOGIN_WINDOW = 30, 300


def _rate_limit(ip):
    now = time.monotonic()
    with _login_lock:
        q = _login_hits[ip]
        while q and q[0] < now - LOGIN_WINDOW:
            q.popleft()
        if len(q) >= LOGIN_LIMIT:
            raise AppError("Too many login attempts from this device. Wait a few minutes.", 429)
        q.append(now)


def dispatch(db, method, path, query, body, token, ip):
    matched_path = False
    for m, rx, fn, perm, write, public in ROUTES:
        match = rx.match(path)
        if not match:
            continue
        matched_path = True
        if m != method:
            continue
        params = {k: int(v) for k, v in match.groupdict().items()}
        ctx_mgr = db.tx() if write else db.read()
        deferred = None
        with ctx_mgr as conn:
            user = None
            if not public:
                if not token:
                    raise AuthError("Login required")
                user = users.authenticate(conn, token)
                if perm:
                    users.require(user, perm)
            result = fn(Ctx(conn, user, body or {}, query, params, ip))
            if isinstance(result, dict) and "_auth_error" in result:
                deferred = result["_auth_error"]  # commit side effects (lockout counters) first
        if deferred:
            raise AuthError(deferred)
        return result
    if matched_path:
        raise AppError(f"Method {method} not allowed", 405)
    raise AppError(f"No API route for {method} {path}", 404)


# ---------------------------------------------------------------- auth

@route("POST", "/auth/login", write=True, public=True)
def _login(c):
    _rate_limit(c.ip)
    return users.login(c.conn, c.body.get("login_id"), c.body.get("password"), c.ip)


@route("POST", "/auth/verify-otp", write=True, public=True)
def _verify_otp(c):
    _rate_limit(c.ip)
    return users.verify_otp(c.conn, c.body.get("otp_token"), c.body.get("code"), c.ip)


@route("GET", "/auth/me")
def _me(c):
    return users.public_user(c.user) | {"permissions": sorted(
        p for p in ("questions", "exams", "schedules", "schedules.view", "results.view", "results", "monitor",
                    "users", "dashboard", "audit", "take_exam") if users.can(c.user, p))}


@route("POST", "/auth/logout", write=True)
def _logout(c):
    users.logout_everywhere(c.conn, c.user)
    return {"ok": True}


@route("POST", "/auth/change-password", write=True)
def _change_password(c):
    return users.change_password(c.conn, c.user, c.body.get("old_password"), c.body.get("new_password"))


# ---------------------------------------------------------------- staff & batches & candidates (admin)

@route("GET", "/users", perm="users")
def _staff(c):
    return users.list_staff(c.conn)


@route("POST", "/users", perm="users", write=True)
def _staff_create(c):
    return users.create_staff(c.conn, c.user, c.body)


@route("PUT", "/users/{id}", perm="users", write=True)
def _user_update(c):
    return users.update_user(c.conn, c.user, c.params["id"], c.body)


@route("DELETE", "/users/{id}", perm="users", write=True)
def _user_delete(c):
    return users.delete_user(c.conn, c.user, c.params["id"])


@route("POST", "/users/{id}/reset-password", perm="users", write=True)
def _user_reset(c):
    return users.reset_password(c.conn, c.user, c.params["id"], bool(c.body.get("send")))


@route("GET", "/batches", perm="schedules.view")
def _batches(c):
    return users.list_batches(c.conn)


@route("POST", "/batches", perm="users", write=True)
def _batch_create(c):
    return users.save_batch(c.conn, c.user, c.body)


@route("PUT", "/batches/{id}", perm="users", write=True)
def _batch_update(c):
    return users.save_batch(c.conn, c.user, c.body, c.params["id"])


@route("DELETE", "/batches/{id}", perm="users", write=True)
def _batch_delete(c):
    return users.delete_batch(c.conn, c.user, c.params["id"])


@route("GET", "/candidates", perm="users")
def _candidates(c):
    return users.list_candidates(c.conn, c.query.get("batch_id"))


@route("POST", "/candidates", perm="users", write=True)
def _candidate_create(c):
    return users.create_candidate(c.conn, c.user, c.body)


@route("POST", "/candidates/import", perm="users", write=True)
def _candidate_import(c):
    return users.import_candidates(c.conn, c.user, c.body.get("batch_id"), c.body.get("csv"),
                                   bool(c.body.get("send_credentials")))


# ---------------------------------------------------------------- question bank

@route("GET", "/questions", perm="questions")
def _questions(c):
    q = c.query
    active = {"1": True, "0": False}.get(q.get("active"))
    return questions.list_questions(c.conn, q.get("subject"), q.get("difficulty"), q.get("search"), active,
                                    q.get("limit", 500), q.get("offset", 0))


@route("GET", "/questions/subjects", perm="questions")
def _subjects(c):
    return questions.subjects(c.conn)


@route("GET", "/questions/{id}", perm="questions")
def _question(c):
    return questions.get_question(c.conn, c.params["id"])


@route("POST", "/questions", perm="questions", write=True)
def _question_create(c):
    return questions.save_question(c.conn, c.user, c.body)


@route("PUT", "/questions/{id}", perm="questions", write=True)
def _question_update(c):
    return questions.save_question(c.conn, c.user, c.body, c.params["id"])


@route("DELETE", "/questions/{id}", perm="questions", write=True)
def _question_delete(c):
    return questions.delete_question(c.conn, c.user, c.params["id"])


@route("POST", "/questions/import", perm="questions", write=True)
def _question_import(c):
    return questions.import_csv(c.conn, c.user, c.body.get("csv"))


# ---------------------------------------------------------------- exams & schedules

@route("GET", "/exams", perm="schedules.view")
def _exams(c):
    return exams.list_exams(c.conn)


@route("GET", "/exams/{id}", perm="schedules.view")
def _exam(c):
    return exams.get_exam(c.conn, c.params["id"])


@route("POST", "/exams", perm="exams", write=True)
def _exam_create(c):
    return exams.save_exam(c.conn, c.user, c.body)


@route("PUT", "/exams/{id}", perm="exams", write=True)
def _exam_update(c):
    return exams.save_exam(c.conn, c.user, c.body, c.params["id"])


@route("POST", "/exams/{id}/duplicate", perm="exams", write=True)
def _exam_dup(c):
    return exams.duplicate_exam(c.conn, c.user, c.params["id"])


@route("DELETE", "/exams/{id}", perm="exams", write=True)
def _exam_delete(c):
    return exams.delete_exam(c.conn, c.user, c.params["id"])


@route("GET", "/schedules", perm="schedules.view")
def _schedules(c):
    return exams.list_schedules(c.conn)


@route("POST", "/schedules", perm="schedules", write=True)
def _schedule_create(c):
    return exams.save_schedule(c.conn, c.user, c.body)


@route("PUT", "/schedules/{id}", perm="schedules", write=True)
def _schedule_update(c):
    return exams.save_schedule(c.conn, c.user, c.body, c.params["id"])


@route("DELETE", "/schedules/{id}", perm="schedules", write=True)
def _schedule_delete(c):
    return exams.delete_schedule(c.conn, c.user, c.params["id"])


@route("POST", "/schedules/{id}/notify", perm="schedules", write=True)
def _schedule_notify(c):
    return exams.notify_schedule(c.conn, c.user, c.params["id"])


@route("GET", "/schedules/{id}/monitor", perm="monitor")
def _monitor(c):
    return results.monitor(c.conn, c.params["id"])


@route("GET", "/schedules/{id}/results", perm="results.view")
def _results(c):
    return results.schedule_results(c.conn, c.params["id"])


@route("GET", "/schedules/{id}/results.csv", perm="results.view")
def _results_csv(c):
    return Raw(results.results_csv(c.conn, c.params["id"]).encode("utf-8"), "text/csv; charset=utf-8",
               f"results-schedule-{c.params['id']}.csv")


@route("GET", "/schedules/{id}/analytics", perm="results.view")
def _analytics(c):
    return results.analytics(c.conn, c.params["id"])


@route("POST", "/schedules/{id}/publish", perm="results", write=True)
def _publish(c):
    return results.publish(c.conn, c.user, c.params["id"], bool(c.body.get("force")))


@route("POST", "/schedules/{id}/unpublish", perm="results", write=True)
def _unpublish(c):
    return results.unpublish(c.conn, c.user, c.params["id"])


@route("GET", "/attempts/{id}", perm="monitor")
def _attempt_detail(c):
    return results.attempt_detail(c.conn, c.params["id"])


@route("POST", "/attempts/{id}/force-submit", perm="monitor", write=True)
def _force_submit(c):
    return attempts.force_submit(c.conn, c.user, c.params["id"])


@route("GET", "/attempts/{id}/report.pdf", perm="results.view")
def _staff_report(c):
    return Raw(results.report_card_pdf(c.conn, None, c.params["id"]), "application/pdf",
               f"report-card-{c.params['id']}.pdf")


@route("GET", "/dashboard", perm="dashboard")
def _dashboard(c):
    return results.dashboard(c.conn)


@route("GET", "/audit", perm="audit")
def _audit(c):
    return audit.list_log(c.conn, c.query.get("limit", 300), c.query.get("entity"))


@route("GET", "/notifications", perm="audit")
def _notifications(c):
    return notify.list_notifications(c.conn, c.query.get("limit", 300))


# ---------------------------------------------------------------- candidate

@route("GET", "/my/exams", perm="take_exam")
def _my_exams(c):
    return attempts.my_exams(c.conn, c.user)


@route("POST", "/my/schedules/{id}/start", perm="take_exam", write=True)
def _start(c):
    return attempts.start_attempt(c.conn, c.user, c.params["id"], c.ip, c.body.get("client_info"))


@route("GET", "/my/attempts/{id}", perm="take_exam", write=True)
def _my_attempt(c):
    return attempts.get_attempt(c.conn, c.user, c.params["id"])


@route("GET", "/my/attempts/{id}/status", perm="take_exam", write=True)
def _heartbeat(c):
    return attempts.heartbeat(c.conn, c.user, c.params["id"])


@route("POST", "/my/attempts/{id}/answers", perm="take_exam", write=True)
def _answers(c):
    return attempts.save_answers(c.conn, c.user, c.params["id"], c.body.get("answers"), c.ip)


@route("POST", "/my/attempts/{id}/violations", perm="take_exam", write=True)
def _violation(c):
    return attempts.record_violation(c.conn, c.user, c.params["id"], c.body.get("kind"), c.body.get("detail"), c.ip)


@route("POST", "/my/attempts/{id}/submit", perm="take_exam", write=True)
def _submit(c):
    return attempts.submit(c.conn, c.user, c.params["id"], c.body.get("answers"), c.ip)


@route("GET", "/my/attempts/{id}/result", perm="take_exam")
def _my_result(c):
    return results.candidate_result(c.conn, c.user, c.params["id"])


@route("GET", "/my/attempts/{id}/report.pdf", perm="take_exam")
def _my_report(c):
    return Raw(results.report_card_pdf(c.conn, c.user, c.params["id"]), "application/pdf",
               f"report-card-{c.user['login_id']}.pdf")
