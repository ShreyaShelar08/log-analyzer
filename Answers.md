## 1. How to run
**Requirements:** Python 3.9 or newer, pip.	

```bash
git clone https://github.com/YOUR_USERNAME/log-analyzer.git
cd log-analyzer
pip install -r requirements.txt
python app.py
```

Open **http://localhost:5000**,in your browser and upload a log file.

If you don't have a log file, generate one first:
```bash
python scripts/generate_logs.py
```
this creates logs/sample.log with ~1000 lines including all the messy formats

No database, no environment variables, no Docker needed — Flask is the only dependency.

---
## 2. Stack choice
**What I picked:** Python + Flask + vanilla HTML/CSS/JS (Chart.js via CDN).
Honestly my first instinct was to use React + FastAPI because that's what I've been building with lately.

**Why Python:**
Python was the obvious choice for the parsing side.
- Python's standard library handles every text and regex need without extra packages; the only pip dependency is Flask itself.
- Flask starts in one command on any machine, with no build step, no Node, no compiled assets.
- The entire frontend is a single `dashboard.html` served by Flask — zero bundler configuration, no React build pipeline to break on a fresh machine.
- Chart.js loads from a CDN, so reviewers get interactive charts without installing anything.

**What would have been worse:**<br>
*React + Vite + FastAPI:* This is my usual stack for bigger projects, but it requires `npm install`, a build step, two servers running simultaneously, and several more environment variables. For a tool whose main job is parsing a file and rendering charts, that overhead is unjustified. A reviewer on a fresh machine would hit `npm` errors before even seeing the app.

*Go or Rust:* would be faster, but then I'd need to ship a compiled binary per OS. Not great for a "run this on a fresh machine" requirement.

---

## 3. One real edge case
**Edge case: Response time in seconds instead of milliseconds**

**File:** `parser/log_parser.py`, function `_parse_response_time`, lines ~86–90.

Some log lines record response time as `0.142s` (seconds) rather than `142ms` (milliseconds). Without explicit handling, `0.142s` would either be silently dropped (returning `None`) or — if mistakenly matched as a bare integer — parsed as `0`, making every such endpoint appear to respond in 0 ms and skewing all p95 calculations toward zero.

The function checks for the `s` suffix explicitly and multiplies by 1000:

```python
m = _RT_S.match(token)
if m:
    return float(m.group(1)) * 1000.0
```

Without this branch, a line like:
```
2024-03-15T14:00:03Z 192.168.1.42 GET /api/users 200 0.142s
```
would have `response_ms = None`, causing that request to be excluded from p95 calculations entirely. On a log file where a format change mid-deployment switched units from `ms` to `s`, roughly half the response time data for every endpoint would disappear silently — the slowest-endpoints table would show artificially low numbers and miss real performance regressions.

---

## 4. AI usage

I used **Claude (claude.ai)** throughout this project. 
| # | What I asked | What it gave me |
|---|---|---|
| 1 |Should I build CLI or web dashboard, which stack | Suggested Flask + vanilla JS, "on-call overview" angle |
| 2 | Generate the log generator script (`generate_logs.py`) | A complete script with all deviation types |
| 3 | Generate the parser (`log_parser.py`) | Full parser with timestamp patterns, response-time normaliser, JSON handler, tokeniser |
| 4 | Generate the analysis engine (`analyzer.py`) | Full analyser with p95 calculation, time-series bucketing, error breakdown |
| 5 | Generate (`Readme.md`) draft |

**One thing I changed and why:**
In the original generator script Claude produced, the malformed-line probability was 15% and blank lines were treated identically to garbage — both incremented `skipped` without distinction. I changed two things:
-	First, dropped the malformed rate to ~7% because the spec says "5-10% deviate" and I wanted my generator to actually match what's described.
-	Second — separated blank lines from meaningful skips in the parser. Blank lines still count toward the skipped total, but they don't show up in the "skipped lines sample" panel on the dashboard.

The change is at `parser/log_parser.py`, inside the `except ValueError` block:
```python
if reason != "blank":
    result.skipped_lines.append((line_no, reason, raw_line.rstrip()))
result.skipped += 1
```

---

## 5. Honest gap

**What isn't good enough:** 
The time-series chart down-samples naively — it picks every Nth data point when there are more than 120 minutes of data. This means a 24-hour log with a 2-minute error spike at hour 18 could be skipped entirely if that minute lands between sample points. An engineer looking for the cause of a 2am incident might never see the spike.

**What I would do with another day:** 
Replace the naive down-sampling with a proper aggregation step in `analyzer.py` — bucket by 5-minute or 15-minute windows rather than by raw minute, and keep the bucket with the highest error count within each window (a "max-preserving" downsample). This guarantees that any error spike, no matter how brief, appears in the chart. I would also add a zoom interaction so engineers can click a spike and see the raw log lines from that minute.










