"""Passwords, JWT (HS256), OTP codes — standard library only."""
import base64
import hashlib
import hmac
import json
import os
import secrets
import string

from . import clock
from .errors import AuthError, ValidationError

PBKDF2_ITERATIONS = 200_000
PASSWORD_ALPHABET = "".join(c for c in string.ascii_letters + string.digits if c not in "0O1lI")


def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password):
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${_b64(salt)}${_b64(dk)}"


def verify_password(password, stored):
    try:
        algo, iterations, salt, digest = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), _unb64(salt), int(iterations))
        return hmac.compare_digest(_b64(dk), digest)
    except (ValueError, TypeError):
        return False


def check_password_strength(password):
    if not password or len(password) < 8:
        raise ValidationError("Password must be at least 8 characters")
    if password.isdigit() or password.isalpha():
        raise ValidationError("Password must contain both letters and digits")


def generate_password(length=10):
    while True:
        pw = "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(length))
        if any(c.isdigit() for c in pw) and any(c.isalpha() for c in pw):
            return pw


def generate_otp():
    return f"{secrets.randbelow(10 ** 6):06d}"


def hash_otp(code, secret):
    return hmac.new(secret.encode(), code.encode(), hashlib.sha256).hexdigest()


def jwt_encode(payload, secret):
    header = {"alg": "HS256", "typ": "JWT"}
    signing_input = f"{_b64(json.dumps(header, separators=(',', ':')).encode())}." \
                    f"{_b64(json.dumps(payload, separators=(',', ':')).encode())}"
    sig = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_b64(sig)}"


def jwt_decode(token, secret):
    try:
        head, body, sig = token.split(".")
        expected = hmac.new(secret.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(_b64(expected), sig):
            raise AuthError("Invalid token")
        if json.loads(_unb64(head)).get("alg") != "HS256":
            raise AuthError("Invalid token")
        payload = json.loads(_unb64(body))
    except (ValueError, json.JSONDecodeError):
        raise AuthError("Invalid token")
    if payload.get("exp", 0) < clock.now():
        raise AuthError("Session expired, please log in again")
    return payload
