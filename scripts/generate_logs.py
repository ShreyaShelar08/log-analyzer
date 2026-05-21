#!/usr/bin/env python3
"""
Log file generator for testing the log analyzer.
Produces a representative log file matching the shape described in the assessment,
including all specified deviations.

Usage:
    python scripts/generate_logs.py                  # generates logs/sample.log (1000 lines)
    python scripts/generate_logs.py -o myfile.log -n 5000
"""

import random
import argparse
import os
from datetime import datetime, timedelta

# ── realistic weighted data ────────────────────────────────────────────────────
ENDPOINTS = [
    "/api/users", "/api/users/12", "/api/users/99", "/api/login", "/api/logout",
    "/api/products", "/api/products/7", "/api/orders", "/api/orders/42",
    "/api/search", "/api/health", "/api/metrics", "/api/admin/config",
    "/static/main.css", "/static/app.js", "/favicon.ico",
]

# Real-world weighting: GET dominates, DELETE is rare
METHODS = (
    ["GET"] * 50 +
    ["POST"] * 25 +
    ["PUT"] * 10 +
    ["PATCH"] * 8 +
    ["DELETE"] * 7
)

# Real-world weighting: 200 is by far the most common
STATUS_CODES = (
    [200] * 60 +   # 60% success
    [201] * 5 +
    [204] * 3 +
    [301] * 2 +
    [302] * 2 +
    [400] * 4 +
    [401] * 5 +
    [403] * 3 +
    [404] * 7 +    # 404 second most common error
    [429] * 2 +
    [500] * 4 +    # 5xx errors are uncommon
    [502] * 1 +
    [503] * 1 +
    [504] * 1
)

IPS = [
    "192.168.1.42", "10.0.0.7", "172.16.0.5", "203.0.113.12",
    "198.51.100.23", "192.168.1.1", "10.0.0.255", "8.8.8.8",
]

USER_AGENTS = [
    '"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"',
    '"curl/7.68.0"',
    '"python-requests/2.28.0"',
    '"Googlebot/2.1 (+http://www.google.com/bot.html)"',
]
REFERRERS = ['"https://example.com/home"', '"https://google.com"', '"-"']

# ── timestamp formatters ───────────────────────────────────────────────────────
def fmt_iso(dt):    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
def fmt_slash(dt):  return dt.strftime("%Y/%m/%d %H:%M:%S")
def fmt_human(dt):  return dt.strftime("%d-%b-%Y %H:%M:%S")
def fmt_epoch(dt):  return str(int(dt.timestamp()))

# ISO weighted heavily — it's the standard format
TIMESTAMP_FMTS = [fmt_iso] * 7 + [fmt_slash, fmt_human, fmt_epoch]

# ── response time formatters ───────────────────────────────────────────────────
def fmt_ms(ms):   return f"{ms}ms"
def fmt_s(ms):    return f"{ms/1000:.3f}s"
def fmt_bare(ms): return str(ms)

RESP_FMTS = [fmt_ms] * 7 + [fmt_s, fmt_bare]  # ms is most common

# ── line builders ──────────────────────────────────────────────────────────────

def normal_line(dt):
    ip     = random.choice(IPS)
    method = random.choice(METHODS)
    path   = random.choice(ENDPOINTS)
    status = random.choice(STATUS_CODES)
    ms     = random.randint(5, 4000)
    ts     = random.choice(TIMESTAMP_FMTS)(dt)
    rt     = random.choice(RESP_FMTS)(ms)
    return f"{ts} {ip} {method} {path} {status} {rt}"

def line_with_extra_fields(dt):
    base = normal_line(dt)
    ua   = random.choice(USER_AGENTS)
    ref  = random.choice(REFERRERS)
    return f"{base} {ua} {ref}"

def line_missing_status(dt):
    ip     = random.choice(IPS)
    method = random.choice(METHODS)
    path   = random.choice(ENDPOINTS)
    ms     = random.randint(5, 4000)
    ts     = random.choice(TIMESTAMP_FMTS)(dt)
    rt     = random.choice(RESP_FMTS)(ms)
    return f"{ts} {ip} {method} {path} - {rt}"

def json_line(dt):
    ip     = random.choice(IPS)
    method = random.choice(METHODS)
    path   = random.choice(ENDPOINTS)
    status = random.choice(STATUS_CODES)
    ms     = random.randint(5, 4000)
    ts     = int(dt.timestamp())
    return (
        f'{{"time":{ts},"ip":"{ip}","method":"{method}",'
        f'"path":"{path}","status":{status},"duration_ms":{ms}}}'
    )

def malformed_line():
    choices = [
        "",
        "    ",
        "ERROR: connection reset by peer",
        "at com.example.Service.handle(Service.java:142)",
        "2024-03-15T14:23:01Z INCOMPLETE",
        "??##CORRUPT##??",
        "\x00\x01\x02",
    ]
    return random.choice(choices)

def indented_line(dt):
    return "  " + normal_line(dt)

# ── main generator ─────────────────────────────────────────────────────────────

def generate(n_lines: int, output_path: str):
    os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".", exist_ok=True)

    start_dt = datetime(2024, 3, 15, 14, 0, 0)
    delta    = timedelta(seconds=1)
    current  = start_dt

    lines = []
    for i in range(n_lines):
        r = random.random()

        if r < 0.60:        # 60% normal
            lines.append(normal_line(current))
        elif r < 0.70:      # 10% extra fields
            lines.append(line_with_extra_fields(current))
        elif r < 0.75:      # 5%  missing status
            lines.append(line_missing_status(current))
        elif r < 0.80:      # 5%  JSON format
            lines.append(json_line(current))
        elif r < 0.83:      # 3%  indented
            lines.append(indented_line(current))
        else:               # 7%  malformed / blank
            lines.append(malformed_line())
            if random.random() < 0.3:
                lines.append("    at com.example.Handler.process(Handler.java:89)")
                lines.append("    at com.example.Server.run(Server.java:201)")

        current += delta + timedelta(milliseconds=random.randint(0, 999))

    with open(output_path, "w", encoding="utf-8", errors="replace") as f:
        f.write("\n".join(lines) + "\n")

    print(f"✅ Generated {len(lines)} lines → {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate test log files")
    parser.add_argument("-o", "--output", default="logs/sample.log", help="Output file path")
    parser.add_argument("-n", "--lines",  type=int, default=1000,    help="Number of lines")
    args = parser.parse_args()
    generate(args.lines, args.output)
