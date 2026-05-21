"""
log_parser.py — Robust parser for mixed-format web server logs.

Handles:
  - ISO 8601 timestamps  (2024-03-15T14:23:01Z)
  - Slash format         (2024/03/15 14:23:01)
  - Human format         (15-Mar-2024 14:23:01)
  - Unix epoch           (1710512581)
  - Response times in ms, s, or bare integer
  - Status codes missing or replaced with "-"
  - Extra fields (user agent, referrer)
  - JSON-formatted lines
  - Indented / leading-whitespace lines
  - Blank lines, stack traces, binary garbage — all skipped gracefully
"""

import re
import json
import logging
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ── Data model ─────────────────────────────────────────────────────────────────

@dataclass
class LogEntry:
    timestamp:    datetime
    ip:           str
    method:       str
    path:         str
    status:       Optional[int]   # None if missing/unparseable
    response_ms:  Optional[float] # normalised to milliseconds
    raw:          str             = field(repr=False)
    extra_fields: list            = field(default_factory=list, repr=False)


@dataclass
class ParseResult:
    entries:        list          # list[LogEntry]
    skipped:        int   = 0
    skipped_lines:  list  = field(default_factory=list)   # (line_no, reason, raw)
    format_anomalies: list = field(default_factory=list)  # (line_no, note, raw)


# ── Timestamp patterns (order matters — most specific first) ───────────────────

_TS_PATTERNS = [
    # ISO 8601: 2024-03-15T14:23:01Z  or  2024-03-15T14:23:01+00:00
    (re.compile(r'^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:Z|[+-]\d{2}:\d{2})?'),
     lambda m: datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)),

    # Slash: 2024/03/15 14:23:01
    (re.compile(r'^(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2})'),
     lambda m: datetime.strptime(m.group(1), "%Y/%m/%d %H:%M:%S").replace(tzinfo=timezone.utc)),

    # Human: 15-Mar-2024 14:23:01
    (re.compile(r'^(\d{2}-[A-Za-z]{3}-\d{4} \d{2}:\d{2}:\d{2})'),
     lambda m: datetime.strptime(m.group(1), "%d-%b-%Y %H:%M:%S").replace(tzinfo=timezone.utc)),

    # Unix epoch: 10-digit integer (standalone token, no trailing space needed)
    (re.compile(r'^(\d{10})$'),
     lambda m: datetime.fromtimestamp(int(m.group(1)), tz=timezone.utc)),
]

def _parse_timestamp(token: str):
    """Return (datetime, matched_format_name) or (None, None)."""
    for pattern, converter in _TS_PATTERNS:
        m = pattern.match(token)
        if m:
            try:
                return converter(m), pattern.pattern[:20]
            except (ValueError, OSError):
                continue
    return None, None


# ── Response-time normaliser ───────────────────────────────────────────────────

_RT_MS  = re.compile(r'^(\d+(?:\.\d+)?)ms$', re.IGNORECASE)
_RT_S   = re.compile(r'^(\d+(?:\.\d+)?)s$',  re.IGNORECASE)
_RT_INT = re.compile(r'^(\d+)$')

def _parse_response_time(token: str) -> Optional[float]:
    """Normalise response time to milliseconds. Returns None if unparseable."""
    # --- Edge case 1: "142ms" -----------------------------------------------
    # Without this branch we'd fall to the bare-int check and misread "142ms"
    # as 142 ms (accidentally correct) but "0.142s" would be missed entirely.
    m = _RT_MS.match(token)
    if m:
        return float(m.group(1))

    # --- Edge case 2: "0.142s" → convert to ms ------------------------------
    m = _RT_S.match(token)
    if m:
        return float(m.group(1)) * 1000.0

    # --- Edge case 3: bare integer (no unit) ---------------------------------
    m = _RT_INT.match(token)
    if m:
        return float(m.group(1))

    return None


# ── IP validator ───────────────────────────────────────────────────────────────

_IP_RE = re.compile(
    r'^(\d{1,3}\.){3}\d{1,3}$'
)

def _is_ip(token: str) -> bool:
    return bool(_IP_RE.match(token))


# ── HTTP method set ────────────────────────────────────────────────────────────

_HTTP_METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS", "CONNECT", "TRACE"}


# ── JSON line parser ───────────────────────────────────────────────────────────

def _try_parse_json(raw: str) -> Optional[LogEntry]:
    """
    Attempt to parse a JSON-formatted log line.
    Supports keys: time/timestamp, ip/remote_addr, method, path/url,
                   status/status_code, duration_ms/response_time/latency_ms
    """
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None

    if not isinstance(obj, dict):
        return None

    # timestamp
    ts_raw = obj.get("time") or obj.get("timestamp")
    if ts_raw is None:
        return None
    try:
        ts = datetime.fromtimestamp(int(ts_raw), tz=timezone.utc)
    except (ValueError, TypeError, OSError):
        return None

    ip     = obj.get("ip") or obj.get("remote_addr", "")
    method = (obj.get("method") or "").upper()
    path   = obj.get("path") or obj.get("url") or ""

    status_raw = obj.get("status") or obj.get("status_code")
    try:
        status = int(status_raw) if status_raw not in (None, "-", "") else None
    except (ValueError, TypeError):
        status = None

    rt_raw = (obj.get("duration_ms") or obj.get("response_time")
              or obj.get("latency_ms"))
    try:
        response_ms = float(rt_raw) if rt_raw is not None else None
    except (ValueError, TypeError):
        response_ms = None

    return LogEntry(
        timestamp=ts, ip=ip, method=method, path=path,
        status=status, response_ms=response_ms, raw=raw
    )


# ── Main line parser ───────────────────────────────────────────────────────────

def _parse_line(raw_line: str, line_no: int):
    """
    Parse one log line.
    Returns (LogEntry, anomaly_note_or_None) or raises ValueError on skip.
    """
    line = raw_line.strip()   # handles leading/trailing whitespace (indented lines)

    # --- Skip: blank or whitespace-only -------------------------------------
    if not line:
        raise ValueError("blank")

    # --- Skip: binary / non-printable content -------------------------------
    # Edge case 4: lines with binary garbage (\x00 etc.) must not crash us.
    try:
        line.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("binary content")
    if any(ord(c) < 9 for c in line):   # control chars except tab/newline
        raise ValueError("binary content")

    # --- Skip: obvious stack-trace lines ------------------------------------
    if line.startswith("at ") and ".java:" in line:
        raise ValueError("stack trace fragment")

    # --- Try JSON first ------------------------------------------------------
    if line.startswith("{"):
        entry = _try_parse_json(line)
        if entry:
            return entry, "json-format"
        raise ValueError("malformed JSON")

    # --- Split into tokens ---------------------------------------------------
    # We split on whitespace but respect quoted strings for extra fields.
    tokens = _tokenise(line)

    if len(tokens) < 4:
        raise ValueError("too few tokens")

    # --- Token 0: timestamp --------------------------------------------------
    # Epoch timestamps are a single token; other formats may span two tokens
    # (e.g. "2024/03/15 14:23:01") — we try single then joined.
    ts, fmt_name = _parse_timestamp(tokens[0])
    ts_width = 1
    if ts is None and len(tokens) > 1:
        ts, fmt_name = _parse_timestamp(tokens[0] + " " + tokens[1])
        ts_width = 2
    if ts is None:
        raise ValueError(f"unparseable timestamp: {tokens[0]!r}")

    rest = tokens[ts_width:]   # remaining tokens after timestamp

    if len(rest) < 3:
        raise ValueError("too few fields after timestamp")

    # --- Token: IP -----------------------------------------------------------
    ip = rest[0]
    if not _is_ip(ip):
        raise ValueError(f"expected IP, got {ip!r}")

    # --- Token: HTTP method --------------------------------------------------
    method = rest[1].upper()
    if method not in _HTTP_METHODS:
        raise ValueError(f"unknown HTTP method: {method!r}")

    # --- Token: path ---------------------------------------------------------
    path = rest[2]
    if not path.startswith("/"):
        raise ValueError(f"path must start with '/': {path!r}")

    # --- Remaining tokens: status? response_time? extra? --------------------
    # Status and response_time can appear in different orders / be missing.
    status      = None
    response_ms = None
    extra       = []
    anomaly     = None

    if fmt_name and fmt_name not in ("", None):
        if "slash" in fmt_name or "Human" in fmt_name or "epoch" in fmt_name:
            anomaly = f"non-ISO timestamp format ({fmt_name})"

    for tok in rest[3:]:
        # quoted string → extra field
        if tok.startswith('"'):
            extra.append(tok)
            continue

        # --- status code FIRST (must precede bare-int RT check) ---------------
        # Edge case: a token like "200" or "404" would match the bare-integer
        # response-time pattern and be stored as response_ms instead of status.
        # Checking status first (3-digit 100-599 range) prevents this.
        if tok == "-":
            if status is None:
                status  = None
                anomaly = (anomaly or "") + " | missing status (-)"
            continue

        if re.match(r'^\d{3}$', tok) and status is None:
            candidate = int(tok)
            if 100 <= candidate <= 599:
                status = candidate
                continue

        # --- response time (ms, s, or bare integer) ---------------------------
        rt = _parse_response_time(tok)
        if rt is not None and response_ms is None:
            response_ms = rt
            continue

        # anything else is an extra field
        extra.append(tok)

    entry = LogEntry(
        timestamp=ts, ip=ip, method=method, path=path,
        status=status, response_ms=response_ms,
        raw=raw_line, extra_fields=extra
    )
    return entry, anomaly


def _tokenise(line: str) -> list:
    """Split line respecting double-quoted strings."""
    tokens = []
    current = []
    in_quotes = False
    for ch in line:
        if ch == '"':
            in_quotes = not in_quotes
            current.append(ch)
        elif ch == ' ' and not in_quotes:
            if current:
                tokens.append("".join(current))
                current = []
        else:
            current.append(ch)
    if current:
        tokens.append("".join(current))
    return tokens


# ── Public API ─────────────────────────────────────────────────────────────────

def parse_log_file(filepath: str) -> ParseResult:
    """
    Parse a log file and return a ParseResult.
    Never raises — all errors are collected into skipped / format_anomalies.
    """
    result = ParseResult(entries=[])

    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError as exc:
        logger.error("Cannot open %s: %s", filepath, exc)
        return result

    for line_no, raw_line in enumerate(lines, start=1):
        try:
            entry, anomaly = _parse_line(raw_line, line_no)
            result.entries.append(entry)
            if anomaly:
                result.format_anomalies.append((line_no, anomaly, raw_line.rstrip()))
        except ValueError as exc:
            reason = str(exc)
            # Only record non-trivial skips (not blank lines) in the skip list
            if reason != "blank":
                result.skipped_lines.append((line_no, reason, raw_line.rstrip()))
            result.skipped += 1

    return result
