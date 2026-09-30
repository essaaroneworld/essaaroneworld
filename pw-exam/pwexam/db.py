"""SQLite storage. One file holds everything; WAL mode allows concurrent readers.

All timestamps are integer Unix epoch seconds (UTC); the browser converts to
local time for display.
"""
import sqlite3
import threading
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS batches (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    description TEXT,
    created_at INTEGER NOT NULL
);

-- Staff (admin / examiner / proctor) and candidates share one table.
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    role TEXT NOT NULL CHECK (role IN ('admin','examiner','proctor','candidate')),
    login_id TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name TEXT NOT NULL,
    email TEXT,
    phone TEXT,
    password_hash TEXT NOT NULL,
    batch_id INTEGER REFERENCES batches(id),
    active INTEGER NOT NULL DEFAULT 1,
    failed_logins INTEGER NOT NULL DEFAULT 0,
    locked_until INTEGER NOT NULL DEFAULT 0,
    token_version INTEGER NOT NULL DEFAULT 0,
    must_change_password INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_users_batch ON users(batch_id);

CREATE TABLE IF NOT EXISTS otps (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token TEXT NOT NULL UNIQUE,
    code_hash TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    used INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY,
    subject TEXT NOT NULL,
    topic TEXT,
    qtype TEXT NOT NULL CHECK (qtype IN ('single','multi')),
    text TEXT NOT NULL,
    options TEXT NOT NULL,            -- JSON array of strings
    correct TEXT NOT NULL,            -- JSON array of option indexes
    marks REAL NOT NULL DEFAULT 1,
    negative REAL NOT NULL DEFAULT 0,
    difficulty TEXT NOT NULL DEFAULT 'medium' CHECK (difficulty IN ('easy','medium','hard')),
    explanation TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    created_by INTEGER REFERENCES users(id),
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_questions_subject ON questions(subject, difficulty);

CREATE TABLE IF NOT EXISTS exams (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT,
    instructions TEXT,
    duration_min INTEGER NOT NULL,
    pass_percent REAL NOT NULL DEFAULT 40,
    shuffle_questions INTEGER NOT NULL DEFAULT 1,
    shuffle_options INTEGER NOT NULL DEFAULT 1,
    negative_marking INTEGER NOT NULL DEFAULT 1,
    max_violations INTEGER NOT NULL DEFAULT 3,       -- 0 = unlimited (only logged)
    require_fullscreen INTEGER NOT NULL DEFAULT 1,
    show_result TEXT NOT NULL DEFAULT 'on_publish' CHECK (show_result IN ('immediate','on_publish')),
    selection_mode TEXT NOT NULL DEFAULT 'fixed' CHECK (selection_mode IN ('fixed','random')),
    random_rules TEXT,                                -- JSON [{subject, difficulty, count}]
    grace_seconds INTEGER NOT NULL DEFAULT 120,       -- accept offline-queued answers this long after time-up
    created_by INTEGER REFERENCES users(id),
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS exam_questions (
    exam_id INTEGER NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
    question_id INTEGER NOT NULL REFERENCES questions(id),
    position INTEGER NOT NULL,
    PRIMARY KEY (exam_id, question_id)
);

CREATE TABLE IF NOT EXISTS schedules (
    id INTEGER PRIMARY KEY,
    exam_id INTEGER NOT NULL REFERENCES exams(id),
    batch_id INTEGER NOT NULL REFERENCES batches(id),
    starts_at INTEGER NOT NULL,
    ends_at INTEGER NOT NULL,
    results_published INTEGER NOT NULL DEFAULT 0,
    published_at INTEGER,
    notified_at INTEGER,
    created_by INTEGER REFERENCES users(id),
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS attempts (
    id INTEGER PRIMARY KEY,
    schedule_id INTEGER NOT NULL REFERENCES schedules(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id),
    status TEXT NOT NULL CHECK (status IN ('in_progress','submitted','auto_submitted','terminated')),
    started_at INTEGER NOT NULL,
    deadline INTEGER NOT NULL,
    submitted_at INTEGER,
    paper TEXT NOT NULL,               -- JSON [{q: id, o: [option order]}]
    score REAL, max_score REAL,
    correct INTEGER, wrong INTEGER, unanswered INTEGER,
    violations INTEGER NOT NULL DEFAULT 0,
    last_seen INTEGER,
    client_info TEXT,
    ip TEXT,
    UNIQUE (schedule_id, user_id)
);

CREATE TABLE IF NOT EXISTS answers (
    attempt_id INTEGER NOT NULL REFERENCES attempts(id) ON DELETE CASCADE,
    question_id INTEGER NOT NULL,
    selected TEXT NOT NULL,            -- JSON array of ORIGINAL option indexes
    flagged INTEGER NOT NULL DEFAULT 0,
    seq INTEGER NOT NULL DEFAULT 0,    -- client sequence; highest wins (offline sync)
    saved_at INTEGER NOT NULL,
    PRIMARY KEY (attempt_id, question_id)
);

CREATE TABLE IF NOT EXISTS violations (
    id INTEGER PRIMARY KEY,
    attempt_id INTEGER NOT NULL REFERENCES attempts(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    detail TEXT,
    counted INTEGER NOT NULL DEFAULT 1,
    at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY,
    channel TEXT NOT NULL CHECK (channel IN ('email','sms')),
    recipient TEXT NOT NULL,
    subject TEXT,
    body TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','sent','failed','logged')),
    tries INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    sensitive INTEGER NOT NULL DEFAULT 0,  -- OTPs: body hidden from the admin outbox
    created_at INTEGER NOT NULL,
    sent_at INTEGER
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY,
    ts INTEGER NOT NULL,
    actor_id INTEGER,
    actor TEXT,
    action TEXT NOT NULL,
    entity TEXT,
    entity_id INTEGER,
    detail TEXT,
    ip TEXT
);
CREATE INDEX IF NOT EXISTS ix_audit_ts ON audit_log(id DESC);
"""


class Database:
    def __init__(self, path=":memory:"):
        self.path = path
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        if path != ":memory:":
            self.conn.execute("PRAGMA journal_mode = WAL")
            self.conn.execute("PRAGMA synchronous = NORMAL")
        self.conn.executescript(SCHEMA)

    @contextmanager
    def tx(self):
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            else:
                self.conn.execute("COMMIT")

    @contextmanager
    def read(self):
        with self.lock:
            yield self.conn

    def get_meta(self, key):
        with self.lock:
            r = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return r[0] if r else None

    def set_meta(self, key, value):
        with self.lock:
            self.conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?,?)", (key, value))

    def backup_to(self, dest_path):
        with self.lock:
            dest = sqlite3.connect(dest_path)
            try:
                self.conn.backup(dest)
            finally:
                dest.close()

    def close(self):
        self.conn.close()
