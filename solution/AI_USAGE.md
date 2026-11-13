# AI Usage Disclosure

## Tools used
- Antigravity AI (Gemini 3.8 Flash)

## What I used them for
- Scaffolding the repository structure, FastAPI webhook receiver, and data models.
- Drafting initial unit tests for calling window maths and time conversions.
- Writing boilerplate SQLite table definitions and indexes.

## What I wrote or decided myself
- The core state machine lifecycle and atomic transaction design (`BEGIN IMMEDIATE` in SQLite with WAL mode).
- At-most-once dialling mathematical proof and ambiguous HTTP 504 query resolution (`GET /v1/calls?request_id=...`).
- Priority queue ordering algorithm enforcing Rule R6: Due Callbacks > Due Retries > Fresh Leads, with callbacks ordered by `callback_at ASC`.
- Active polling reconciliation logic to recover from lost carrier webhooks.
- Multi-tenancy concurrency allocation and cross-campaign phone locking.
- Global DND propagation preserving the strict state precedence (`completed` > `exhausted` > `dnd_blocked`).

## Where the AI was wrong or unhelpful
- In Python 3.12+, SQLite autocommit behavior and `executescript` automatically execute an implicit `COMMIT` before executing DDL. The initial context manager called `conn.execute("COMMIT;")` unconditionally, triggering `sqlite3.OperationalError: cannot commit - no transaction is active`. This was caught during unit test execution and resolved by checking `if conn.in_transaction:` before committing.
- Initial window checking considered using full 120s clock skew grace from the mock provider; I overrode this to use strict 0s grace internally to guarantee 100% boundary safety and eliminate any possibility of `outside_calling_window` violations.

## How I verified AI-generated code
- Automated test suite running 18 unit tests across `clock`, `window`, `priority`, `db_state`, `idempotency`, and `recovery`.
- Automated full end-to-end integration tests (`validate_run.py`) against the mock telephony carrier API under `--failure-scale 1.0` and `--speed 1200`, asserting 0 violations and 0 diffs against `/admin/leads`.
- Dedicated abrupt crash recovery test (`test_crash_kill.py`) that terminates the dispatcher process with `kill -9` while calls are active and verifies seamless resumption from SQLite.

