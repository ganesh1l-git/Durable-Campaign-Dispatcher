# Assignment B: Durable Campaign Dispatcher — Solution

This folder contains the complete, self-contained implementation of the Durable Campaign Dispatcher.

## Directory Structure

```
solution/
├── __init__.py
├── __main__.py             # CLI entry point: python -m solution
├── models.py               # Data models and lifecycle state definitions
├── clock.py                # SimClock and FakeClock simulated time engines
├── window.py               # Regulatory (09:00-21:00) & Campaign window validation
├── db.py                   # SQLite WAL-mode transactional state machine
├── telephony.py            # HTTP carrier client (handles 503s & resolves 504s)
├── webhook_server.py       # FastAPI webhook receiver (HMAC verification & idempotency)
├── dispatcher.py           # Core dispatch loop, priority queue & lost webhook recovery
├── runner.py               # Single-process runner orchestrating webhook server & dispatcher
├── validate_run.py         # End-to-end runner comparing against /admin/leads
├── test_crash_kill.py      # Automated kill -9 crash recovery test
├── SUBMISSION.md           # Full engineering documentation and design rationale
├── AI_USAGE.md             # AI usage disclosure
├── final_state.csv         # Generated output matching /admin/leads
└── tests/
    ├── __init__.py
    ├── test_clock.py       # Unit tests for clock formula and conversions
    ├── test_window.py      # Unit tests for calling window and next_open maths
    ├── test_priority.py    # Unit tests for Rule R6 priority ordering
    ├── test_db_state.py    # Unit tests for lifecycle, concurrency, DND & attempts
    ├── test_idempotency.py # Unit tests for HMAC, duplicate webhooks, 504 resolution
    └── test_recovery.py    # Unit tests for startup crash recovery
```

## How to Run

### Run Unit Tests
```bash
python -m pytest solution/tests
```

### Run Service
```bash
# Terminal 1: Run mock carrier
cd assignment-b-workflow
python -m mock_telephony --speed 1200 --seed 7 --failure-scale 1.0

# Terminal 2: Run durable dispatcher
python -m solution
```

### Run End-to-End Validation
```bash
python solution/validate_run.py 1200 1.0 7
```

### Run Crash Recovery Test (kill -9 simulation)
```bash
python solution/test_crash_kill.py
```
