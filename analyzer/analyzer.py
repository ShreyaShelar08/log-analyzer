"""
analyzer.py — Derives on-call insights from parsed log entries.

Produces:
  - Status code distribution
  - Top slow endpoints (p95 response time)
  - Error rate over time (per minute buckets)
  - Top IPs by request count
  - Top endpoints by request count
  - 4xx / 5xx breakdown
  - Parse health summary (skipped lines, anomalies)
"""

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional
import statistics


@dataclass
class AnalysisReport:
    # parse health
    total_lines:     int = 0
    parsed_ok:       int = 0
    skipped:         int = 0
    anomaly_count:   int = 0
    skipped_sample:  list = field(default_factory=list)   # up to 10 examples
    anomaly_sample:  list = field(default_factory=list)

    # traffic overview
    total_requests:  int = 0
    status_dist:     dict = field(default_factory=dict)   # {code: count}
    method_dist:     dict = field(default_factory=dict)
    error_rate_pct:  float = 0.0

    # endpoint stats
    slowest_endpoints: list = field(default_factory=list)  # [{path, p95_ms, count, avg_ms}]
    top_endpoints:     list = field(default_factory=list)  # [{path, count, error_count}]

    # IP stats
    top_ips:         list = field(default_factory=list)    # [{ip, count, error_count}]

    # time series (errors per minute)
    errors_over_time: list = field(default_factory=list)   # [{minute, errors, total}]

    # 4xx / 5xx breakdown
    client_errors:   dict = field(default_factory=dict)    # {code: {path: count}}
    server_errors:   dict = field(default_factory=dict)


def analyse(parse_result, top_n: int = 10) -> AnalysisReport:
    entries       = parse_result.entries
    skipped_lines = parse_result.skipped_lines
    anomalies     = parse_result.format_anomalies

    report = AnalysisReport(
        total_lines   = len(entries) + parse_result.skipped,
        parsed_ok     = len(entries),
        skipped       = parse_result.skipped,
        anomaly_count = len(anomalies),
        skipped_sample  = skipped_lines[:10],
        anomaly_sample  = anomalies[:10],
        total_requests  = len(entries),
    )

    if not entries:
        return report

    # ── status distribution ────────────────────────────────────────────────────
    status_counts: dict[Optional[int], int] = defaultdict(int)
    for e in entries:
        status_counts[e.status] += 1
    report.status_dist = {
        (str(k) if k is not None else "unknown"): v
        for k, v in sorted(status_counts.items(), key=lambda x: -(x[1]))
    }

    # ── method distribution ────────────────────────────────────────────────────
    method_counts: dict[str, int] = defaultdict(int)
    for e in entries:
        method_counts[e.method] += 1
    report.method_dist = dict(sorted(method_counts.items(), key=lambda x: -x[1]))

    # ── error rate ─────────────────────────────────────────────────────────────
    errors = sum(1 for e in entries if e.status and e.status >= 400)
    report.error_rate_pct = round(errors / len(entries) * 100, 2) if entries else 0.0

    # ── endpoint analysis ─────────────────────────────────────────────────────
    path_times:  dict[str, list] = defaultdict(list)
    path_counts: dict[str, int]  = defaultdict(int)
    path_errors: dict[str, int]  = defaultdict(int)

    for e in entries:
        path_counts[e.path] += 1
        if e.status and e.status >= 400:
            path_errors[e.path] += 1
        if e.response_ms is not None:
            path_times[e.path].append(e.response_ms)

    # top endpoints by volume
    report.top_endpoints = [
        {
            "path":        path,
            "count":       path_counts[path],
            "error_count": path_errors.get(path, 0),
            "error_pct":   round(path_errors.get(path, 0) / path_counts[path] * 100, 1),
        }
        for path in sorted(path_counts, key=lambda p: -path_counts[p])[:top_n]
    ]

    # slowest endpoints by p95 response time
    def p95(times):
        if not times:
            return 0.0
        s = sorted(times)
        idx = max(0, int(len(s) * 0.95) - 1)
        return round(s[idx], 1)

    endpoints_with_times = [
        (path, times) for path, times in path_times.items() if len(times) >= 3
    ]
    endpoints_with_times.sort(key=lambda x: -p95(x[1]))
    report.slowest_endpoints = [
        {
            "path":   path,
            "p95_ms": p95(times),
            "avg_ms": round(statistics.mean(times), 1),
            "max_ms": round(max(times), 1),
            "count":  len(times),
        }
        for path, times in endpoints_with_times[:top_n]
    ]

    # ── IP analysis ────────────────────────────────────────────────────────────
    ip_counts: dict[str, int] = defaultdict(int)
    ip_errors: dict[str, int] = defaultdict(int)

    for e in entries:
        ip_counts[e.ip] += 1
        if e.status and e.status >= 400:
            ip_errors[e.ip] += 1

    report.top_ips = [
        {
            "ip":          ip,
            "count":       ip_counts[ip],
            "error_count": ip_errors.get(ip, 0),
            "error_pct":   round(ip_errors.get(ip, 0) / ip_counts[ip] * 100, 1),
        }
        for ip in sorted(ip_counts, key=lambda ip: -ip_counts[ip])[:top_n]
    ]

    # ── errors over time ───────────────────────────────────────────────────────
    # Bucket by minute; ignore entries with no timestamp (shouldn't happen)
    minute_total:  dict[str, int] = defaultdict(int)
    minute_errors: dict[str, int] = defaultdict(int)

    for e in entries:
        if e.timestamp:
            bucket = e.timestamp.strftime("%Y-%m-%dT%H:%M")
            minute_total[bucket]  += 1
            if e.status and e.status >= 400:
                minute_errors[bucket] += 1

    report.errors_over_time = [
        {
            "minute": minute,
            "total":  minute_total[minute],
            "errors": minute_errors.get(minute, 0),
        }
        for minute in sorted(minute_total)
    ]

    # ── 4xx / 5xx breakdown ────────────────────────────────────────────────────
    client_err: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    server_err: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for e in entries:
        if e.status:
            if 400 <= e.status < 500:
                client_err[e.status][e.path] += 1
            elif e.status >= 500:
                server_err[e.status][e.path] += 1

    report.client_errors = {
        code: dict(sorted(paths.items(), key=lambda x: -x[1])[:5])
        for code, paths in sorted(client_err.items())
    }
    report.server_errors = {
        code: dict(sorted(paths.items(), key=lambda x: -x[1])[:5])
        for code, paths in sorted(server_err.items())
    }

    return report
