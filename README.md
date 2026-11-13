# Durable Campaign Dispatcher (Agentic Universe Take-Home: Assignment B)

[![Tests](https://img.shields.io/badge/pytest-18%20passed-brightgreen)](#how-to-run)
[![Violations](https://img.shields.io/badge/violations-0%20(100%25%20compliance)-brightgreen)](#results--verification)
[![Crash%20Recovery](https://img.shields.io/badge/kill%20--9%20recovery-verified-blue)](#results--verification)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)

An autonomous, crash-resilient outbound campaign dispatcher orchestrating voice calls across India/APAC under strict regulatory windows (09:00–21:00 IST), multi-tenant concurrency limits, retry backoffs, scheduled callbacks, and global DND constraints.

Full engineering design, state diagrams, and mathematical proofs are detailed in **[`SUBMISSION.md`](SUBMISSION.md)** and **[`AI_USAGE.md`](AI_USAGE.md)**.

---

## Quick Start (Run from Project Root)

### 1. Setup Environment
```bash
python -m venv .venv
# On Windows:
.venv\Scripts\activate
# On Linux/macOS:
source .venv/bin/activate

pip install -r requirements.txt
```

### 2. Run Fast Unit Tests (4.3s)
Validates calling window maths, regulatory hours, priority queue logic (Callbacks > Retries > Fresh), state machine transitions, DND propagation, and HMAC signature verification:
```bash
python -m pytest solution/tests
```

### 3. Run Automated End-to-End Validation Test
Automatically boots the mock carrier API on port `8099`, launches the dispatcher on port `8200`, runs through all 200 leads under **chaos failure scale 1.0** (503s, 504 timeouts, lost & duplicate webhooks), compares with `/admin/leads`, and verifies 0 violations:
```bash
python solution/validate_run.py 1200 1.0 7
```

### 4. Run the Hard Crash (`kill -9`) Recovery Test
Simulates an abrupt `kill -9` while active calls are in flight, restarts from SQLite, audits in-flight requests against the carrier API, and verifies 100% recovery with 0 violations and 0 lost leads:
```bash
python solution/test_crash_kill.py
```

### 5. Run as Live Production Services (Two Terminals)
```bash
# Terminal 1: Run carrier mock API
cd assignment-b-workflow
python -m mock_telephony --speed 1200 --seed 7 --failure-scale 1.0

# Terminal 2: Run durable dispatcher
python -m solution
```
- **Live Carrier Admin Report:** [http://127.0.0.1:8099/admin/report](http://127.0.0.1:8099/admin/report)
- **Live Carrier Expected Leads:** [http://127.0.0.1:8099/admin/leads](http://127.0.0.1:8099/admin/leads)
- **Live Dispatcher Progress:** [http://127.0.0.1:8200/status](http://127.0.0.1:8200/status)

---

## Results & Verification

| Metric | Target | Achieved Result |
|---|---|---|
| **Compliance Violations** | `0` | **`0`** |
| **Final State Agreement** | 100% match with `/admin/leads` | **200 / 200 leads (0 mismatches)** |
| **Max Concurrency Seen** | `acme: 4, zen: 3` | **`acme: 4, zen: 3` (Strictly respected)** |
| **Duplicate Webhooks Handled** | Harmless | **39 duplicate deliveries safely ignored** |
| **Lost Webhooks Recovered** | No stalled leads | **15 lost carrier webhooks recovered via active audit** |
| **Ambiguous 504 Timeouts** | At-most-once dialling | **100% resolved via `GET /v1/calls?request_id=...`** |
| **Median Callback Lateness (`p50`)** | Minimised | **4.2 simulated minutes** |

Final results file generated at **[`solution/final_state.csv`](solution/final_state.csv)**.

---

## Directory Structure

```
.
├── solution/                       # Complete, self-contained solution codebase
│   ├── clock.py                    # SimClock & FakeClock time scaling engines
│   ├── window.py                   # Regulatory (09:00-21:00) & campaign window validation
│   ├── models.py                   # Data models and lifecycle state definitions
│   ├── db.py                       # SQLite WAL-mode transactional state machine
│   ├── telephony.py                # Carrier HTTP client (handles 503s & resolves 504s)
│   ├── webhook_server.py           # FastAPI webhook receiver (HMAC & idempotency)
│   ├── dispatcher.py               # Priority scheduler & active lost-webhook reconciler
│   ├── runner.py                   # Single-process orchestrator
│   ├── validate_run.py             # End-to-end chaos test runner
│   ├── test_crash_kill.py          # Automated kill -9 crash recovery test
│   ├── final_state.csv             # Final output matching /admin/leads
│   └── tests/                      # 18 unit tests
├── assignment-b-workflow/          # Assignment fixtures & mock telephony provider
│   ├── fixtures/                   # campaigns.json & leads CSV files
│   └── mock_telephony/             # Carrier API server
├── SUBMISSION.md                   # Detailed design walkthrough and failure analysis
├── AI_USAGE.md                     # AI usage disclosure
└── requirements.txt                # Project dependencies
```

---

## Original Assignment Context

Agentic Universe builds AI voice agents for insurers, lenders, and consumer brands across India and APAC. Our platform has three layers:
- **Data:** leads, call events, dispositions, and the interaction ledger that every client report is built on.
- **Workflow:** campaigns, scheduling, dialling, retries, and callbacks, all orchestrated durably.
- **Application:** dashboards, consoles, and APIs.

This repository implements **Assignment B: Durable Campaign Dispatcher**.
