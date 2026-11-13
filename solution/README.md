# Durable Campaign Dispatcher — Solution (Assignment B)

[![Tests](https://img.shields.io/badge/pytest-18%20passed-brightgreen)](#how-to-run)
[![Violations](https://img.shields.io/badge/violations-0%20(100%25%20compliance)-brightgreen)](#results--verification)
[![Crash%20Recovery](https://img.shields.io/badge/kill%20--9%20recovery-verified-blue)](#results--verification)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)

This folder contains the complete, self-contained implementation of **Assignment B: Durable Campaign Dispatcher** for Agentic Universe.

It is an autonomous, crash-resilient outbound voice campaign dispatcher orchestrating calls across India/APAC under strict regulatory windows (09:00–21:00 IST), multi-tenant concurrency limits, retry backoffs, scheduled callbacks, and global DND constraints.

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
Validates calling window maths, regulatory hours, priority queue logic (Due Callbacks > Due Retries > Fresh Leads), state machine transitions, DND propagation, and HMAC signature verification:
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

Final results file generated at **[`solution/final_state.csv`](final_state.csv)**.

---

## Directory Structure

```
solution/
├── clock.py                    # SimClock & FakeClock time scaling engines
├── window.py                   # Regulatory (09:00-21:00) & campaign window validation
├── models.py                   # Data models and lifecycle state definitions
├── db.py                       # SQLite WAL-mode transactional state machine
├── telephony.py                # Carrier HTTP client (handles 503s & resolves 504s)
├── webhook_server.py           # FastAPI webhook receiver (HMAC & idempotency)
├── dispatcher.py               # Priority scheduler & active lost-webhook reconciler
├── runner.py                   # Single-process orchestrator
├── validate_run.py             # End-to-end chaos test runner
├── test_crash_kill.py          # Automated kill -9 crash recovery test
├── SUBMISSION.md               # Detailed design walkthrough and failure analysis
├── AI_USAGE.md                 # AI usage disclosure
├── final_state.csv             # Final output matching /admin/leads
└── tests/                      # 18 unit tests
    ├── test_clock.py           # Unit tests for clock formula and conversions
    ├── test_window.py          # Unit tests for calling window and next_open maths
    ├── test_priority.py        # Unit tests for Rule R6 priority ordering
    ├── test_db_state.py        # Unit tests for lifecycle, concurrency, DND & attempts
    ├── test_idempotency.py     # Unit tests for HMAC, duplicate webhooks, 504 resolution
    └── test_recovery.py        # Unit tests for startup crash recovery
```

---

## Key Architecture & Design Highlights

1. **Transactional State Machine (`db.py`):**
   - Built on SQLite in Write-Ahead Logging (WAL) mode (`PRAGMA journal_mode = WAL; PRAGMA synchronous = NORMAL;`).
   - Slot reservations and state transitions execute within atomic `BEGIN IMMEDIATE` transactions, serializing concurrent writers and eliminating race conditions.
2. **At-Most-Once Dialling:**
   - A unique, deterministic `request_id` (`{cid}_{lid}_att{n}_{uuid}`) is stored in the database *before* sending the HTTP request. If the process is killed mid-flight, the reboot audit queries the carrier's `by_request` registry.
3. **Ambiguity Resolution (504s):**
   - If an HTTP 504 or network timeout occurs, the client queries `GET /v1/calls?request_id=...` with backoff. If 200, the call is adopted; if 404, it was never placed and the slot is safely freed.
4. **Active Lost Webhook Reconciliation (`dispatcher.py`):**
   - Periodically queries active calls older than 15 sim seconds via `GET /v1/calls/{call_sid}`. If ended, the outcome is ingested immediately, ensuring dropped webhooks never stall campaigns.
5. **Strict Calling Windows (`window.py`):**
   - Validates that calls are placed strictly inside both campaign hours and Indian regulatory hours (09:00–21:00 IST) with 0-second grace tolerance for 100% compliance.
6. **Rule R6 Priority Queue:**
   - Always prioritizes **Due Callbacks > Due Retries > Fresh Leads**, sorting callbacks by earliest `callback_at` to minimize callback lateness.
