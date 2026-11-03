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


def response_shape(body: str | bytes) -> dict:
    """Describe a response without retaining even a prefix, key, URL or value."""
    result = {"length": len(body), "length_unit": "bytes" if isinstance(body, bytes) else "characters"}
    if isinstance(body, bytes):
        if body.startswith(b"\x1f\x8b"):
            return {**result, "kind": "gzip_bytes"}
        try:
            body = body.decode("utf-8")
        except UnicodeDecodeError:
            return {**result, "kind": "invalid_utf8"}
    text = body.lstrip()
    kind = ("empty" if not text else "utf8_bom" if text.startswith("\ufeff") else
            "html_like" if re.match(r"(?is)<!doctype\s+html\b|<html\b", text) else
            "markup_like" if text.startswith("<") else
            "json_like_invalid" if text.startswith(("[", "{")) else "other_non_json")
    try:
        value = json.loads(body)
    except json.JSONDecodeError as exc:
        # Never retain exc.doc, exc.msg, offending characters or surrounding text.
        return {**result, "kind": kind, "error_position": exc.pos,
                "error_line": exc.lineno, "error_column": exc.colno}
    json_type = ("array" if isinstance(value, list) else "object" if isinstance(value, dict) else "scalar")
    return {**result, "kind": "valid_json", "json_type": json_type,
            **({"count": len(value)} if isinstance(value, (list, dict)) else {})}


def provider_failure(exc, provider, operation):
    """Only categorized adapter data and recognized vocabulary leave this boundary."""
    record = getattr(exc, "diagnostic", None) or {}
    category = record.get("category", "invalid_schema")
    allowed = {"authorization", "transient_http", "permanent_http", "timeout", "connection_reset",
               "cli_error", "executable_error", "malformed_response", "invalid_schema", "configuration"}
    if category not in allowed:
        category = "cli_error"
    detail = stderr_summary(record.get("stderr", ""), "")
    status = re.search(r"\b[1-5]\d\d\b", detail)
    reached = "HTTP response received; origin may be ABS or upstream" if status else (
        "CLI stdout received; ABS HTTP status/content type unknown" if category == "malformed_response" else
        "Response received" if category == "invalid_schema" else "Not established")
    summary = {"authorization": "authorization error; check ABS and provider credentials/configuration",
               "transient_http": "rate limited or upstream unavailable",
               "permanent_http": "HTTP request failed",
               "timeout": "timeout; ABS/upstream completion unknown",
               "connection_reset": "connection failed", "cli_error": "CLI command failed",
               "executable_error": "CLI could not start",
               "malformed_response": "CLI stdout is not valid JSON; exit 0 does not establish a JSON API response",
               "invalid_schema": "provider returned an unsupported response",
               "configuration": "requires authentication/configuration; independent Google Books API key missing"}[category]
    diagnostic = {"provider": provider, "operation": operation, "category": category,
            "summary": summary, "detail": detail, "http_status": status.group(0) if status else None,
            "exit_code": record.get("exit_code"), "reached_abs": reached}
    if record.get("transport") == "direct_get":
        diagnostic["transport"] = "direct_get"
        if category == "malformed_response":
            diagnostic["summary"] = "metadata search returned a non-JSON HTTP body"
    shape = record.get("response_shape", {})
    safe_shape = {k: v for k, v in shape.items() if (
        k in {"length", "error_position", "error_line", "error_column", "count"}
        and type(v) is int and v >= 0) or (
        k == "kind" and v in {"gzip_bytes", "invalid_utf8", "empty", "utf8_bom", "html_like",
                              "markup_like", "json_like_invalid", "other_non_json", "valid_json"}) or (
        k == "json_type" and v in {"array", "object", "scalar"}) or (
        k == "length_unit" and v in {"bytes", "characters"})}
    if safe_shape:
        diagnostic["response_shape"] = safe_shape
    return diagnostic
