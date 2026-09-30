"""Runtime settings, read from environment variables (12-factor style)."""
import os
import secrets
from pathlib import Path


def _flag(name, default=False):
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


class Settings:
    def __init__(self):
        self.secret = os.environ.get("PWEXAM_SECRET", "")
        self.otp_candidates = _flag("PWEXAM_CANDIDATE_OTP", False)
        self.otp_staff = _flag("PWEXAM_STAFF_OTP", False)
        self.trust_proxy = _flag("PWEXAM_TRUST_PROXY", False)
        self.base_url = os.environ.get("PWEXAM_BASE_URL", "").rstrip("/")
        self.smtp_host = os.environ.get("PWEXAM_SMTP_HOST", "")
        self.smtp_port = int(os.environ.get("PWEXAM_SMTP_PORT", "587"))
        self.smtp_user = os.environ.get("PWEXAM_SMTP_USER", "")
        self.smtp_password = os.environ.get("PWEXAM_SMTP_PASSWORD", "")
        self.smtp_from = os.environ.get("PWEXAM_SMTP_FROM", "no-reply@localhost")
        self.smtp_tls = _flag("PWEXAM_SMTP_TLS", True)
        # SMS gateway: POST JSON {"to": ..., "message": ...} to this URL (most Indian gateways
        # can be fronted this way); optional bearer token.
        self.sms_webhook = os.environ.get("PWEXAM_SMS_WEBHOOK", "")
        self.sms_token = os.environ.get("PWEXAM_SMS_TOKEN", "")
        self.staff_token_hours = int(os.environ.get("PWEXAM_STAFF_TOKEN_HOURS", "10"))
        self.candidate_token_hours = int(os.environ.get("PWEXAM_CANDIDATE_TOKEN_HOURS", "6"))
        self.max_failed_logins = 5
        self.lockout_seconds = 15 * 60

    def load_secret(self, data_dir):
        """Use PWEXAM_SECRET, else a random secret persisted next to the database."""
        if self.secret:
            return
        path = Path(data_dir) / "secret.key"
        if path.exists():
            self.secret = path.read_text().strip()
        else:
            self.secret = secrets.token_urlsafe(48)
            path.write_text(self.secret)
            try:
                path.chmod(0o600)
            except OSError:
                pass


settings = Settings()
