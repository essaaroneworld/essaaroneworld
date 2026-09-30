"""Authentication, role-based access control, staff, batches and candidates."""
import csv
import io
import secrets

from . import audit, clock, notify
from .config import settings
from .errors import AuthError, Conflict, Forbidden, NotFound, ValidationError
from .security import (check_password_strength, generate_otp, generate_password, hash_otp, hash_password,
                       jwt_decode, jwt_encode, verify_password)

STAFF_ROLES = ("admin", "examiner", "proctor")

# Permission names used by the API. Admin implicitly has all of them.
PERMISSIONS = {
    "examiner": {"questions", "exams", "schedules.view", "results.view", "dashboard"},
    "proctor": {"monitor", "schedules.view", "results.view", "dashboard"},
    "candidate": {"take_exam"},
}

OTP_TTL = 300


def can(user, perm):
    if user["role"] == "admin":
        return perm != "take_exam"
    return perm in PERMISSIONS.get(user["role"], set())


def require(user, perm):
    if not user:
        raise AuthError("Login required")
    if not can(user, perm):
        raise Forbidden("You do not have permission for this action")


def public_user(r):
    d = dict(r)
    for k in ("password_hash", "failed_logins", "locked_until", "token_version"):
        d.pop(k, None)
    d["active"] = bool(d["active"])
    d["must_change_password"] = bool(d.get("must_change_password"))
    return d


def _clean(v):
    if v is None:
        return None
    v = str(v).strip()
    return v or None


def _user(conn, user_id):
    r = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not r:
        raise NotFound("User not found")
    return dict(r)


# ---------------------------------------------------------------- sessions

def issue_token(user):
    hours = settings.candidate_token_hours if user["role"] == "candidate" else settings.staff_token_hours
    now = clock.now()
    payload = {"sub": user["id"], "role": user["role"], "name": user["name"], "login_id": user["login_id"],
               "tv": user["token_version"], "iat": now, "exp": now + hours * 3600}
    return {"token": jwt_encode(payload, settings.secret), "expires_at": payload["exp"],
            "user": public_user(user)}


def authenticate(conn, token):
    payload = jwt_decode(token, settings.secret)
    r = conn.execute("SELECT * FROM users WHERE id=?", (payload.get("sub"),)).fetchone()
    if not r or not r["active"] or r["token_version"] != payload.get("tv"):
        raise AuthError("Session is no longer valid, please log in again")
    return dict(r)


def login(conn, login_id, password, ip=None):
    login_id = (login_id or "").strip()
    if not login_id or not password:
        raise ValidationError("Enter your login ID and password")
    r = conn.execute("SELECT * FROM users WHERE login_id=? COLLATE NOCASE", (login_id,)).fetchone()
    now = clock.now()
    if not r:
        verify_password(password, hash_password("timing-equaliser"))  # avoid user enumeration by timing
        raise AuthError("Invalid login ID or password")
    user = dict(r)
    if not user["active"]:
        raise AuthError("This account is disabled. Contact the exam administrator.")
    if user["locked_until"] > now:
        mins = (user["locked_until"] - now + 59) // 60
        raise AuthError(f"Too many failed attempts. Try again in {mins} minute(s).")
    if not verify_password(password, user["password_hash"]):
        fails = user["failed_logins"] + 1
        locked = now + settings.lockout_seconds if fails >= settings.max_failed_logins else 0
        conn.execute("UPDATE users SET failed_logins=?, locked_until=? WHERE id=?",
                     (0 if locked else fails, locked, user["id"]))
        audit.log(conn, user, "login_failed", "user", user["id"], {"locked": bool(locked)}, ip)
        # Returned (not raised) so the failure counter is committed; the API turns it into a 401.
        return {"_auth_error": "Invalid login ID or password"}
    conn.execute("UPDATE users SET failed_logins=0, locked_until=0 WHERE id=?", (user["id"],))
    needs_otp = settings.otp_candidates if user["role"] == "candidate" else settings.otp_staff
    if needs_otp and (user["email"] or user["phone"]):
        code = generate_otp()
        token = secrets.token_urlsafe(24)
        conn.execute("INSERT INTO otps(user_id, token, code_hash, expires_at) VALUES (?,?,?,?)",
                     (user["id"], token, hash_otp(code, settings.secret), now + OTP_TTL))
        notify.queue_user(conn, user, "Login OTP",
                          f"Your PW Batch exam login OTP is {code}. Valid for 5 minutes. Do not share it.",
                          sensitive=True)
        audit.log(conn, user, "otp_sent", "user", user["id"], None, ip)
        where = " and ".join(x for x in (_mask(user["email"]), _mask(user["phone"])) if x)
        return {"otp_required": True, "otp_token": token, "sent_to": where}
    audit.log(conn, user, "login", "user", user["id"], None, ip)
    return issue_token(user)


def _mask(v):
    if not v:
        return None
    if "@" in v:
        name, dom = v.split("@", 1)
        return f"{name[:2]}***@{dom}"
    return f"******{v[-4:]}"


def verify_otp(conn, token, code, ip=None):
    r = conn.execute("SELECT * FROM otps WHERE token=?", (token or "",)).fetchone()
    if not r or r["used"] or r["expires_at"] < clock.now():
        raise AuthError("OTP expired. Please log in again.")
    if r["attempts"] >= 5:
        raise AuthError("Too many wrong OTP attempts. Please log in again.")
    if hash_otp(str(code or "").strip(), settings.secret) != r["code_hash"]:
        conn.execute("UPDATE otps SET attempts=attempts+1 WHERE id=?", (r["id"],))
        return {"_auth_error": "Incorrect OTP"}
    conn.execute("UPDATE otps SET used=1 WHERE id=?", (r["id"],))
    user = _user(conn, r["user_id"])
    audit.log(conn, user, "login", "user", user["id"], {"otp": True}, ip)
    return issue_token(user)


def logout_everywhere(conn, user):
    conn.execute("UPDATE users SET token_version=token_version+1 WHERE id=?", (user["id"],))
    audit.log(conn, user, "logout", "user", user["id"])


def change_password(conn, user, old, new):
    if not verify_password(old or "", user["password_hash"]):
        raise ValidationError("Current password is incorrect")
    check_password_strength(new)
    conn.execute("UPDATE users SET password_hash=?, must_change_password=0, token_version=token_version+1 "
                 "WHERE id=?", (hash_password(new), user["id"]))
    audit.log(conn, user, "password_changed", "user", user["id"])
    return issue_token(_user(conn, user["id"]))


# ---------------------------------------------------------------- staff

def list_staff(conn):
    return [public_user(r) for r in conn.execute(
        "SELECT * FROM users WHERE role != 'candidate' ORDER BY role, name")]


def create_staff(conn, actor, data):
    role = data.get("role")
    if role not in STAFF_ROLES:
        raise ValidationError("Role must be admin, examiner or proctor")
    return _create_user(conn, actor, data, role, None)


def _create_user(conn, actor, data, role, batch_id, notify_credentials=False):
    login_id = _clean(data.get("login_id"))
    name = _clean(data.get("name"))
    if not login_id or not name:
        raise ValidationError("Login ID and name are required")
    if len(login_id) > 64 or any(c.isspace() for c in login_id):
        raise ValidationError("Login ID must be a single word (max 64 characters)")
    if conn.execute("SELECT 1 FROM users WHERE login_id=? COLLATE NOCASE", (login_id,)).fetchone():
        raise Conflict(f"Login ID '{login_id}' already exists")
    password = data.get("password") or generate_password()
    check_password_strength(password)
    email, phone = _clean(data.get("email")), _clean(data.get("phone"))
    if email and "@" not in email:
        raise ValidationError(f"Invalid email for {login_id}")
    uid = conn.execute(
        "INSERT INTO users(role, login_id, name, email, phone, password_hash, batch_id, must_change_password, "
        "created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (role, login_id, name, email, phone, hash_password(password), batch_id,
         0 if data.get("password") else 1, clock.now())).lastrowid
    audit.log(conn, actor, "create", "user", uid, {"role": role, "login_id": login_id})
    user = _user(conn, uid)
    if notify_credentials:
        _send_credentials(conn, user, password)
    out = public_user(user)
    out["initial_password"] = password  # shown once to the admin
    return out


def _send_credentials(conn, user, password):
    url = f" at {settings.base_url}" if settings.base_url else ""
    notify.queue_user(conn, user, "Exam portal login",
                      f"Dear {user['name']}, your PW Batch exam login{url}: ID {user['login_id']}, "
                      f"password {password}. Please change it after first login.", sensitive=True)


def update_user(conn, actor, user_id, data):
    user = _user(conn, user_id)
    fields = {}
    if "name" in data:
        fields["name"] = _clean(data["name"]) or user["name"]
    for k in ("email", "phone"):
        if k in data:
            fields[k] = _clean(data[k])
    if "active" in data:
        if user_id == actor["id"] and not data["active"]:
            raise ValidationError("You cannot disable your own account")
        fields["active"] = 1 if data["active"] else 0
        if not data["active"]:
            fields["token_version"] = user["token_version"] + 1
    if "role" in data and user["role"] != "candidate":
        if data["role"] not in STAFF_ROLES:
            raise ValidationError("Invalid role")
        if user_id == actor["id"] and data["role"] != "admin":
            raise ValidationError("You cannot remove your own admin role")
        fields["role"] = data["role"]
    if "batch_id" in data and user["role"] == "candidate":
        fields["batch_id"] = _batch(conn, data["batch_id"])["id"]
    if fields:
        conn.execute(f"UPDATE users SET {', '.join(k + '=?' for k in fields)} WHERE id=?",
                     list(fields.values()) + [user_id])
    audit.log(conn, actor, "update", "user", user_id, {k: v for k, v in fields.items() if k != "token_version"})
    return public_user(_user(conn, user_id))


def reset_password(conn, actor, user_id, send=False):
    user = _user(conn, user_id)
    password = generate_password()
    conn.execute("UPDATE users SET password_hash=?, must_change_password=1, failed_logins=0, locked_until=0, "
                 "token_version=token_version+1 WHERE id=?", (hash_password(password), user_id))
    audit.log(conn, actor, "reset_password", "user", user_id)
    if send:
        _send_credentials(conn, user, password)
    return {"login_id": user["login_id"], "password": password}


def delete_user(conn, actor, user_id):
    user = _user(conn, user_id)
    if user_id == actor["id"]:
        raise ValidationError("You cannot delete your own account")
    if conn.execute("SELECT 1 FROM attempts WHERE user_id=? LIMIT 1", (user_id,)).fetchone():
        conn.execute("UPDATE users SET active=0, token_version=token_version+1 WHERE id=?", (user_id,))
        audit.log(conn, actor, "deactivate", "user", user_id, "has exam attempts; deactivated instead")
        return {"deactivated": user["login_id"]}
    conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    audit.log(conn, actor, "delete", "user", user_id, {"login_id": user["login_id"]})
    return {"deleted": user["login_id"]}


# ---------------------------------------------------------------- batches & candidates

def _batch(conn, batch_id):
    r = conn.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
    if not r:
        raise NotFound("Batch not found")
    return dict(r)


def list_batches(conn):
    return [dict(r) for r in conn.execute(
        "SELECT b.*, (SELECT COUNT(*) FROM users u WHERE u.batch_id=b.id AND u.role='candidate' AND u.active=1) "
        "AS candidates FROM batches b ORDER BY b.name")]


def save_batch(conn, actor, data, batch_id=None):
    name = _clean(data.get("name"))
    if not name:
        raise ValidationError("Batch name is required")
    clash = conn.execute("SELECT id FROM batches WHERE name=? COLLATE NOCASE AND id != ?",
                         (name, batch_id or 0)).fetchone()
    if clash:
        raise Conflict(f"Batch '{name}' already exists")
    if batch_id:
        _batch(conn, batch_id)
        conn.execute("UPDATE batches SET name=?, description=? WHERE id=?",
                     (name, _clean(data.get("description")), batch_id))
    else:
        batch_id = conn.execute("INSERT INTO batches(name, description, created_at) VALUES (?,?,?)",
                                (name, _clean(data.get("description")), clock.now())).lastrowid
    audit.log(conn, actor, "save", "batch", batch_id, {"name": name})
    return _batch(conn, batch_id)


def delete_batch(conn, actor, batch_id):
    b = _batch(conn, batch_id)
    if conn.execute("SELECT 1 FROM users WHERE batch_id=? LIMIT 1", (batch_id,)).fetchone() or \
            conn.execute("SELECT 1 FROM schedules WHERE batch_id=? LIMIT 1", (batch_id,)).fetchone():
        raise ValidationError("Batch has candidates or scheduled exams")
    conn.execute("DELETE FROM batches WHERE id=?", (batch_id,))
    audit.log(conn, actor, "delete", "batch", batch_id, {"name": b["name"]})
    return {"deleted": b["name"]}


def list_candidates(conn, batch_id=None):
    sql = ("SELECT u.*, b.name AS batch_name FROM users u LEFT JOIN batches b ON b.id=u.batch_id "
           "WHERE u.role='candidate'")
    args = []
    if batch_id:
        sql += " AND u.batch_id=?"
        args.append(int(batch_id))
    return [public_user(r) for r in conn.execute(sql + " ORDER BY u.login_id", args)]


def create_candidate(conn, actor, data):
    batch = _batch(conn, data.get("batch_id"))
    return _create_user(conn, actor, data, "candidate", batch["id"], bool(data.get("send_credentials")))


def import_candidates(conn, actor, batch_id, csv_text, send_credentials=False):
    """CSV columns: login_id,name,email,phone[,password]. Header row required."""
    batch = _batch(conn, batch_id)
    reader = csv.DictReader(io.StringIO((csv_text or "").strip()))
    if not reader.fieldnames or not {"login_id", "name"} <= {f.strip().lower() for f in reader.fieldnames}:
        raise ValidationError("CSV must have a header with at least: login_id,name (optional email,phone,password)")
    created, errors = [], []
    for line, row in enumerate(reader, start=2):
        row = {(k or "").strip().lower(): v for k, v in row.items()}
        conn.execute("SAVEPOINT cand")
        try:
            c = _create_user(conn, actor, row, "candidate", batch["id"], send_credentials)
            conn.execute("RELEASE cand")
            created.append({"login_id": c["login_id"], "name": c["name"], "password": c["initial_password"]})
        except (ValidationError, Conflict) as e:
            conn.execute("ROLLBACK TO cand")
            conn.execute("RELEASE cand")
            errors.append({"line": line, "error": e.message})
    audit.log(conn, actor, "import", "candidates", batch["id"], {"created": len(created), "errors": len(errors)})
    return {"created": created, "errors": errors}


def ensure_admin(conn, login_id, password, name="Administrator"):
    if conn.execute("SELECT 1 FROM users WHERE role='admin' LIMIT 1").fetchone():
        return None
    check_password_strength(password)
    uid = conn.execute("INSERT INTO users(role, login_id, name, password_hash, created_at) VALUES "
                       "('admin',?,?,?,?)", (login_id, name, hash_password(password), clock.now())).lastrowid
    audit.log(conn, None, "create", "user", uid, {"role": "admin", "bootstrap": True})
    return uid
