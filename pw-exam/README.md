# PW Batch Online Examination System

**A product of ESS AAR SOFTEK PLC.** This is a secure, proctored MCQ
examination platform. One codebase runs on **localhost**, in an **offline LAN
exam lab**, and on an **online VPS** with HTTPS. Candidates can take exams on
desktop or mobile, and the app can be installed on a phone as a PWA.

It needs only Python 3.9 or newer and has no other dependencies: the server
uses the standard library, the data lives in SQLite, and the web client is
plain JavaScript.

## How the flowchart maps to the system

| Flowchart step | What it does |
|---|---|
| **1. Admin login** (JWT / RBAC) | Signed JWT sessions (HS256). There are four roles: **admin** (everything), **examiner** (question bank and exams), **proctor** (live monitoring) and **candidate**. Passwords are hashed with PBKDF2 (200k rounds). Five wrong passwords lock the account for 15 minutes, and logins are rate-limited per device. Staff and candidates can be required to enter an OTP. First-time passwords must be changed at first login. |
| **2. Build question bank** | Add or edit MCQs with one or several correct answers and 2–8 options. Each question has a subject, topic, **difficulty (easy/medium/hard)**, positive and negative marks, and an explanation. Questions can be bulk-imported from **CSV** (a template is included). Questions already used in an exam are deactivated rather than deleted. |
| **3. Create exam** | Set the duration, pass mark, **question and option shuffling per candidate**, negative marking, the **full-screen requirement**, the **number of warnings before auto-submit**, the offline grace period, and whether results are shown immediately or after publishing. Questions are either a **fixed set** or **random rules** (e.g. 10 Physics hard + 5 any easy), checked against what the bank has. |
| **4. Schedule exam** | Assign an exam to a **batch** with a time window. Candidates are added one by one or by CSV import, and their credentials are shown once. **Email and SMS notifications** go through an outbox. |
| **5. Platform delivery** | **Online (web)**: a VPS behind Caddy or nginx with HTTPS. **Offline lab (LAN)**: `start-lan` on a local server with no internet needed. **Mobile**: a responsive PWA ("Add to Home Screen") that caches the app shell for offline use. |
| **6. Candidate login** | Login ID + password, with an optional **OTP** by email or SMS. Failed attempts go back to the retry loop with lockout. |
| **7. Exam starts** | Each candidate gets a paper generated and shuffled for them. The **timer runs on the server**: the deadline is fixed when the exam starts and capped at the window end, and it keeps running if the browser closes. |
| **8. Candidate answers** | Candidates navigate with a question palette and can **flag for review**, clear a response, and use keyboard shortcuts. Answers are **saved automatically every 30 s** and 2 s after each change. **Anti-cheat**: tab switches, leaving the window, and exiting full screen count as warnings and trigger an auto-submit past the limit. Copy, paste, right-click and print are blocked and logged. |
| **Network online?** | **Yes** → the answer is saved to the server (step 9). **No** → it is **saved locally in a queue**: answers are stored on the device, survive a reload, and the page can even be **reopened offline** from the device cache. |
| **Time up?** | **No** → back to answering. **Yes** → auto-submit. If the device is offline at time-up, submission retries until it reconnects. The server auto-submits abandoned attempts after the grace period. |
| **10. Submit exam** | The candidate confirms (with answered, unanswered and flagged counts), then the session is locked and the answers uploaded. |
| **11. Auto scoring** | Scores apply negative marking, and multi-answer questions only score on an exact match. Candidates get a **rank** (ties broken by time taken) and a **percentile**, plus a subject-wise breakdown. |
| **12. Publish results** | A **PDF report card**, a candidate result dashboard with the **answer key and explanations**, a merit list, and an **SMS/Email** result notice. |
| **13. Offline sync** | Queued answers upload on reconnect. Uploads are idempotent and the **highest sequence number wins**, so late or out-of-order uploads never overwrite newer answers. Answers saved before time-up are still accepted within the exam's grace period. |
| **14. Admin review** | **Analytics** (score distribution, subject averages, per-question correct %, and flags for "too hard / check the answer key"), the **audit log** of every action, the proctoring event log per candidate, **CSV export**, and database backup. |
| **15. Maintain & iterate** | Docker image, systemd unit, nginx and Caddy configs, nightly backup script, health endpoint, and test suite. |

## Quick start (localhost)

```bash
cd pw-exam
python3 -m pwexam --demo
```

Open <http://127.0.0.1:8080>. `--demo` loads a sample batch, 16 questions and a
live exam:

| Role | Login | Password |
|---|---|---|
| Admin | `admin` | `Admin@2026` |
| Examiner | `examiner` | `Setter@2026` |
| Proctor | `proctor` | `Proctor@2026` |
| Candidates | `PW001` … `PW008` | `Exam@2026` |

> The demo passwords are public. Never use `--demo` on a real server.

Without `--demo`, the first start creates the `admin` account. You are asked
for its password, or one is generated and printed once.

## Offline LAN exam lab

Run this on one PC or server in the lab. The candidates' machines need no
internet, only the local network.

```bash
./deploy/start-lan.sh          # Linux / macOS
deploy\start-lan.bat           # Windows (allow port 8080 in Windows Firewall)
```

The server prints its LAN address (e.g. `http://192.168.1.20:8080/`), which
candidates open in their browser. Email and SMS messages, including OTPs, are
printed in the server window when no gateway is configured.

> Browsers only allow the "reopen the page while offline" cache on HTTPS or
> localhost. On a plain-HTTP LAN, answers are still queued on the device and
> synced automatically. For the full offline cache on a LAN, put Caddy in
> front with `tls internal`.

## Online VPS (HTTPS)

**Option A: Docker and Caddy, with automatic Let's Encrypt certificates**

```bash
cd pw-exam
cp deploy/env.example .env       # set PWEXAM_DOMAIN, PWEXAM_ADMIN_PASSWORD, PWEXAM_SECRET (+ SMTP/SMS)
docker compose up -d --build
```

**Option B: bare Ubuntu/Debian with systemd, nginx and certbot**

```bash
cd pw-exam
sudo ./deploy/install-ubuntu.sh exam.example.com you@example.com
```

This installs the app to `/opt/pwexam` with its data in `/var/lib/pwexam`,
secrets in `/etc/pwexam.env` (mode 600), a hardened systemd service, an nginx
reverse proxy with HTTPS, and a nightly backup to `/var/backups/pwexam`
(kept for 14 days).

**Moving to another server:** copy the database (use the admin *Account →
Download backup* option or `deploy/backup.sh`), plus `/etc/pwexam.env`, or
`secret.key` next to the database. Everything else is in the code.

## Configuration (environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `PWEXAM_HOST` / `PWEXAM_PORT` / `PWEXAM_DATA` | `127.0.0.1` / `8080` / `data/pwexam.db` | Bind address, port and database file |
| `PWEXAM_SECRET` | random, saved as `secret.key` | Signs login tokens. Keep it stable across restarts. |
| `PWEXAM_ADMIN_PASSWORD` | – | First admin's password (non-interactive installs). Must be changed at first login. |
| `PWEXAM_CANDIDATE_OTP` / `PWEXAM_STAFF_OTP` | `0` | Require an email/SMS OTP at login |
| `PWEXAM_SMTP_HOST`, `_PORT`, `_USER`, `_PASSWORD`, `_FROM`, `_TLS` | – | Email delivery |
| `PWEXAM_SMS_WEBHOOK`, `PWEXAM_SMS_TOKEN` | – | SMS gateway: `POST {"to","message"}` JSON |
| `PWEXAM_TRUST_PROXY` | `0` | Trust `X-Forwarded-For` (set to 1 behind nginx or Caddy) |
| `PWEXAM_BASE_URL` | – | Public URL included in notification messages |
| `PWEXAM_TIMEZONE` | `Asia/Kolkata` | Time zone used in messages and PDFs |

## Security notes

- Tokens are sent as bearer headers, not cookies, so there is no CSRF
  surface. A strict **Content-Security-Policy** is set (no inline scripts, no
  third-party origins), along with `X-Frame-Options: DENY`, `nosniff` and
  `no-referrer`.
- The client never uses `innerHTML`, so question text is always rendered as
  text.
- Correct answers and explanations are never sent to candidates before
  results are published.
- The server enforces every rule: the timer, batch and window checks, the
  answer format, single-choice limits and violation limits.
- Anti-cheat in a browser is a deterrent and an evidence trail, not a
  guarantee. For high-stakes exams, combine it with invigilation or a locked
  kiosk browser (e.g. Safe Exam Browser) in the LAN lab.

## Testing

```bash
cd pw-exam
python3 -m unittest discover -s tests -v
```

There are 27 tests. They cover login, lockout, OTP, token revocation, role
permissions, question validation and CSV import, random paper generation,
offline sequence and grace rules, violation termination, scoring with
negative and multi-answer marking, ranking and percentile, publishing, PDF,
CSV, analytics, and the HTTP layer (security headers, role enforcement,
backup restricted to admins).

## Known limitations and roadmap

- **Biometric login** (shown in the flowchart) is not implemented yet. It
  would use WebAuthn passkeys (fingerprint or face on the device).
  ID + password + OTP is available now.
- **Native Android/iOS apps** are not built. The mobile experience is a PWA
  that installs to the home screen. It could be wrapped as a store app with
  Capacitor/TWA if needed.
- **Question types:** MCQ only (single or multiple answer). Numeric-answer
  questions, images and LaTeX in questions, and section-wise timers are
  planned.
- **Scale:** SQLite in WAL mode with a threaded server comfortably handles
  hundreds of simultaneous candidates on a single VPS. For thousands, move to
  PostgreSQL behind a WSGI/ASGI server; the service layer is already separate
  from the HTTP layer.
- **Webcam proctoring** is not included.
