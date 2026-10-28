"""Local diagnostics deliberately exclude arbitrary CLI output and exception bodies."""
from __future__ import annotations

import json
import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path


def local_log(path: Path) -> logging.Logger:
    path = path.resolve()
    logger = logging.getLogger(f"abs.audit.{path}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        logger.addHandler(handler)
    return logger


def identifier(value: str, secret: str = "") -> str:
    # IDs are useful; URLs, headers, arbitrary command arguments and bodies are not.
    if (secret and secret in value) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        return "[redacted]"
    return value


def stderr_summary(stderr: str | bytes | None, secret: str) -> str:
    """Extract only known diagnostic vocabulary, never free-form text or raw secrets."""
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", errors="replace")
    text = (stderr or "").replace(secret, "[redacted]") if secret else (stderr or "")
    terms = (r"HTTP(?:/\d(?:\.\d)?)?\s+(?:status(?:\s+code)?[:=]?\s*)?[1-5]\d\d\b",
             r"\bAPI error\s*\([1-5]\d\d\)",
             r"\b(?:429 Too Many Requests|502 Bad Gateway|503 Service Unavailable|504 Gateway Timeout)\b",
             r"\b(?:status(?:\s+code)?|statusCode)[\s\"':=]+[1-5]\d\d\b",
             r"\b(?:ECONNRESET|ETIMEDOUT|ECONNREFUSED|EAI_AGAIN|ENOTFOUND)\b",
             (r"\b(?:connection reset|timed out|timeout|bad gateway|service unavailable|"
              r"gateway timeout|too many requests|unauthorized|forbidden)\b"))
    found = [m.group(0) for pattern in terms for m in re.finditer(pattern, text, re.IGNORECASE)]
    return "; ".join(dict.fromkeys(found))[:400] or "No recognized stderr diagnostic (details withheld)."


def event(logger: logging.Logger, **values):
    logger.info(json.dumps(values, ensure_ascii=True, sort_keys=True))
