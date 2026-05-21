# LogScope — Server Log Analyzer

A web dashboard for on-call engineers that ingests mixed-format server log files and surfaces actionable insights: error rates, slowest endpoints, top IPs, status code distributions, and errors over time.

---

## Quick Start (fresh machine)

```bash
# 1. Clone
git clone https://github.com/YOUR_USERNAME/log-analyzer.git
cd log-analyzer

# 2. Install dependencies (Python 3.9+ required)
pip install -r requirements.txt

# 3. Run
python app.py
```

Open **http://localhost:5000** in your browser, then drag-and-drop any log file onto the upload zone.

---

## Generate a test log file

```bash
python scripts/generate_logs.py                   # → logs/sample.log (1 000 lines)
python scripts/generate_logs.py -n 50000 -o logs/big.log
```

The generator produces a representative file that includes all supported deviations:
- ISO 8601, slash, human-readable, and Unix epoch timestamps
- Response times in `ms`, `s`, and bare integers
- Missing / dash status codes
- JSON-formatted lines mixed in
- Lines with extra fields (user agent, referrer)
- Indented lines (leading whitespace)
- Blank lines, stack trace fragments, and binary garbage

---

## Project structure

```
log-analyzer/
├── app.py                  # Flask application & API routes
├── parser/
│   └── log_parser.py       # Robust multi-format log parser
├── analyzer/
│   └── analyzer.py         # Analysis engine (stats, p95, time series)
├── scripts/
│   └── generate_logs.py    # Test data generator
├── templates/
│   └── dashboard.html      # Single-page dashboard UI
├── requirements.txt
├── README.md
└── ANSWERS.md
```

---

## What the dashboard shows

| Section | Detail |
|---|---|
| **Parse Health** | Total lines, parsed OK, skipped count, format anomalies — with a sample of skipped lines and their reasons |
| **Traffic Overview** | Total requests, error rate %, top status code, top HTTP method |
| **Status Code Distribution** | Doughnut chart grouped by 2xx / 3xx / 4xx / 5xx |
| **HTTP Methods** | Bar chart of GET / POST / PUT etc. |
| **Errors Over Time** | Line chart of total vs error requests per minute |
| **Slowest Endpoints** | p95 / avg / max response time per path |
| **Top IPs** | Request volume and error % per source IP |
| **Top Endpoints** | Hit count and error % per path |
| **Error Breakdown** | Per-status-code table of which paths triggered 4xx / 5xx |

---

## API endpoints

| Method | Path | Body |
|---|---|---|
| POST | `/api/analyse` | multipart form with `file` field |
| POST | `/api/analyse-path` | JSON `{"path": "/absolute/path/to/file.log"}` |
