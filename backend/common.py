import hashlib
import json
from datetime import datetime, timezone, timedelta
from uuid import uuid4

TERMINAL = {"completed", "refused", "failed", "cancelled"}


def uid():
    return str(uuid4())


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def expires(hours):
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat().replace("+00:00", "Z")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else canonical(value).encode()).hexdigest()


class Problem(Exception):
    def __init__(self, status, code, message, retryable=False, details=None):
        self.status, self.code, self.message = status, code, message
        self.retryable, self.details = retryable, details or {}


def warning(code, message, **kwargs):
    return {"code": code, "message": message, "severity": "warning", **kwargs}

