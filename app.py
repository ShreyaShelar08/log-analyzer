"""
app.py — Flask web application for the Log Analyzer dashboard.
"""

import os
import json
import tempfile
from flask import Flask, request, jsonify, render_template, send_from_directory

from parser.log_parser import parse_log_file
from analyzer.analyzer import analyse

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024  # 100 MB upload limit


# ── helpers ────────────────────────────────────────────────────────────────────

def _report_to_dict(report):
    """Convert AnalysisReport to a JSON-serialisable dict."""
    # derive top status/method deterministically on server-side so clients
    # don't rely on object key ordering in JS environments
    top_status = None
    if getattr(report, 'status_dist', None):
        try:
            top_status = list(report.status_dist.keys())[0]
        except Exception:
            top_status = None

    top_method = None
    if getattr(report, 'method_dist', None):
        try:
            top_method = list(report.method_dist.keys())[0]
        except Exception:
            top_method = None

    return {
        "parse_health": {
            "total_lines":    report.total_lines,
            "parsed_ok":      report.parsed_ok,
            "skipped":        report.skipped,
            "anomaly_count":  report.anomaly_count,
            "skipped_sample": [
                {"line_no": ln, "reason": r, "raw": raw}
                for ln, r, raw in report.skipped_sample
            ],
            "anomaly_sample": [
                {"line_no": ln, "note": note, "raw": raw}
                for ln, note, raw in report.anomaly_sample
            ],
        },
        "overview": {
            "total_requests":  report.total_requests,
            "error_rate_pct":  report.error_rate_pct,
            "status_dist":     report.status_dist,
            "method_dist":     report.method_dist,
            "top_status":      top_status,
            "top_method":      top_method,
        },
        "slowest_endpoints":  report.slowest_endpoints,
        "top_endpoints":      report.top_endpoints,
        "top_ips":            report.top_ips,
        "errors_over_time":   report.errors_over_time,
        "client_errors":      {str(k): v for k, v in report.client_errors.items()},
        "server_errors":      {str(k): v for k, v in report.server_errors.items()},
    }


# ── routes ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("dashboard.html")


@app.route("/api/analyse", methods=["POST"])
def analyse_upload():
    """Accept a log file upload, parse + analyse it, return JSON report."""
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    uploaded = request.files["file"]
    if uploaded.filename == "":
        return jsonify({"error": "Empty filename"}), 400

    # Write to a temp file so our parser can use a file path
    suffix = os.path.splitext(uploaded.filename)[1] or ".log"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        uploaded.save(tmp.name)
        tmp_path = tmp.name

    try:
        parse_result = parse_log_file(tmp_path)
        report       = analyse(parse_result)
        return jsonify(_report_to_dict(report))
    except Exception as exc:
        app.logger.exception("Analysis failed")
        return jsonify({"error": str(exc)}), 500
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


@app.route("/api/analyse-path", methods=["POST"])
def analyse_path():
    """Accept a server-side file path (useful for CLI testing)."""
    data = request.get_json(silent=True) or {}
    filepath = data.get("path", "").strip()

    if not filepath:
        return jsonify({"error": "No path provided"}), 400
    if not os.path.isfile(filepath):
        return jsonify({"error": f"File not found: {filepath}"}), 404

    try:
        parse_result = parse_log_file(filepath)
        report       = analyse(parse_result)
        return jsonify(_report_to_dict(report))
    except Exception as exc:
        app.logger.exception("Analysis failed")
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, host="0.0.0.0", port=port)
