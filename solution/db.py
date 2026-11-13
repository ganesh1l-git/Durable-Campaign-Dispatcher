"""Database operations and durable state machine using SQLite in WAL mode."""
from __future__ import annotations

import csv
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Generator, List, Optional, Tuple

from .models import (
    TERMINAL_DISPOSITIONS,
    CampaignConfig,
    LeadRecord,
    LeadState,
    TenantConfig,
    WindowConfig,
)
from .window import IST, is_call_permitted_now

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


class Database:
    """Thread-safe and process-safe SQLite database manager with WAL mode."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self._init_tables()

    @contextmanager
    def connection(self, immediate: bool = False) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self.db_path, timeout=30.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        try:
            if immediate:
                conn.execute("BEGIN IMMEDIATE;")
            else:
                conn.execute("BEGIN;")
            yield conn
            if conn.in_transaction:
                conn.execute("COMMIT;")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK;")
            raise
        finally:
            conn.close()

    def _init_tables(self) -> None:
        conn = sqlite3.connect(self.db_path, timeout=30.0, isolation_level=None)
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS tenants (
                    tenant_id TEXT PRIMARY KEY,
                    max_concurrent_calls INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS campaigns (
                    campaign_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    window_start TEXT NOT NULL,
                    window_end TEXT NOT NULL,
                    window_days TEXT NOT NULL,
                    max_attempts INTEGER NOT NULL,
                    leads_file TEXT NOT NULL,
                    FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id)
                );

                CREATE TABLE IF NOT EXISTS leads (
                    campaign_id TEXT NOT NULL,
                    lead_id TEXT NOT NULL,
                    phone TEXT NOT NULL,
                    state TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_eligible_at TEXT,
                    callback_at TEXT,
                    last_status TEXT,
                    last_disposition TEXT,
                    last_call_sid TEXT,
                    last_request_id TEXT,
                    last_ended_at TEXT,
                    created_order INTEGER,
                    PRIMARY KEY (campaign_id, lead_id)
                );

                CREATE INDEX IF NOT EXISTS idx_leads_state ON leads(state);
                CREATE INDEX IF NOT EXISTS idx_leads_phone ON leads(phone);
                CREATE INDEX IF NOT EXISTS idx_leads_priority ON leads(state, next_eligible_at, callback_at);

                CREATE TABLE IF NOT EXISTS calls (
                    request_id TEXT PRIMARY KEY,
                    call_sid TEXT UNIQUE,
                    campaign_id TEXT NOT NULL,
                    lead_id TEXT NOT NULL,
                    phone TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    attempt_no INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    created_at_sim TEXT,
                    started_at_sim TEXT,
                    ended_at_sim TEXT,
                    status TEXT,
                    disposition TEXT,
                    callback_at TEXT,
                    duration_sec INTEGER DEFAULT 0,
                    raw_result TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_calls_active_tenant ON calls(tenant_id, state);
                CREATE INDEX IF NOT EXISTS idx_calls_active_phone ON calls(phone, state);
                CREATE INDEX IF NOT EXISTS idx_calls_lead ON calls(campaign_id, lead_id);
                CREATE INDEX IF NOT EXISTS idx_calls_sid ON calls(call_sid);

                CREATE TABLE IF NOT EXISTS dnd_registry (
                    phone TEXT PRIMARY KEY,
                    source_campaign_id TEXT,
                    source_lead_id TEXT,
                    opted_out_at_sim TEXT
                );

                CREATE TABLE IF NOT EXISTS processed_webhooks (
                    delivery_id TEXT PRIMARY KEY,
                    call_sid TEXT,
                    processed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );
                """
            )
        finally:
            conn.close()

    def load_fixtures_if_empty(self, fixtures_path: str) -> None:
        """Load campaigns.json and lead CSVs if the leads table is empty."""
        with self.connection(immediate=True) as conn:
            count = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
            if count > 0:
                # Already populated
                return

            with open(fixtures_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)

            base_dir = os.path.dirname(os.path.abspath(fixtures_path))
            sim_start_str = cfg.get("sim_start", "2026-11-12T17:30:00+05:30")

            # Insert tenants
            for t in cfg["tenants"]:
                conn.execute(
                    "INSERT OR REPLACE INTO tenants (tenant_id, max_concurrent_calls) VALUES (?, ?)",
                    (t["tenant_id"], t["max_concurrent_calls"]),
                )

            # Insert campaigns and leads
            order = 0
            for c in cfg["campaigns"]:
                days_str = ",".join(c["window"]["days"])
                conn.execute(
                    """
                    INSERT OR REPLACE INTO campaigns
                    (campaign_id, tenant_id, window_start, window_end, window_days, max_attempts, leads_file)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        c["campaign_id"],
                        c["tenant_id"],
                        c["window"]["start"],
                        c["window"]["end"],
                        days_str,
                        c["max_attempts"],
                        c["leads_file"],
                    ),
                )

                csv_file = os.path.join(base_dir, c["leads_file"])
                if os.path.exists(csv_file):
                    with open(csv_file, "r", encoding="utf-8") as lf:
                        reader = csv.DictReader(lf)
                        for row in reader:
                            order += 1
                            conn.execute(
                                """
                                INSERT INTO leads (
                                    campaign_id, lead_id, phone, state, attempts,
                                    next_eligible_at, created_order
                                ) VALUES (?, ?, ?, ?, 0, ?, ?)
                                """,
                                (
                                    c["campaign_id"],
                                    row["lead_id"],
                                    row["phone"],
                                    LeadState.FRESH.value,
                                    sim_start_str,
                                    order,
                                ),
                            )

    def get_tenants(self) -> List[Dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute("SELECT tenant_id, max_concurrent_calls FROM tenants").fetchall()
            return [dict(r) for r in rows]

    def get_campaigns(self) -> Dict[str, Dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT campaign_id, tenant_id, window_start, window_end, window_days, max_attempts, leads_file
                FROM campaigns
                """
            ).fetchall()
            result = {}
            for r in rows:
                d = dict(r)
                d["window_days"] = d["window_days"].split(",")
                result[d["campaign_id"]] = d
            return result

    def get_active_tenant_counts(self) -> Dict[str, int]:
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT tenant_id, COUNT(*) as cnt
                FROM calls
                WHERE state IN ('dispatching', 'active')
                GROUP BY tenant_id
                """
            ).fetchall()
            return {r["tenant_id"]: r["cnt"] for r in rows}

    def get_active_phone_numbers(self) -> set[str]:
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT phone
                FROM calls
                WHERE state IN ('dispatching', 'active')
                """
            ).fetchall()
            return {r["phone"] for r in rows}

    def get_dnd_phone_numbers(self) -> set[str]:
        with self.connection() as conn:
            rows = conn.execute("SELECT phone FROM dnd_registry").fetchall()
            return {r["phone"] for r in rows}

    def find_and_reserve_next_lead(
        self,
        tenant_id: str,
        sim_now: datetime,
        campaigns_map: Dict[str, Dict[str, Any]],
        reg_start: str = "09:00",
        reg_end: str = "21:00",
    ) -> Optional[Tuple[Dict[str, Any], str]]:
        """
        Atomically inspect and reserve the highest priority eligible lead for tenant_id.
        Priority:
          1. Due callbacks (callback_at <= sim_now), sorted by callback_at ASC
          2. Due retries (next_eligible_at <= sim_now), sorted by next_eligible_at ASC
          3. Fresh leads, sorted by created_order ASC
        Checks:
          - Tenant concurrency limit
          - Phone is not currently active across ANY campaign
          - Phone is not in DND registry
          - Lead attempts < campaign max_attempts
          - Window check (campaign window and regulatory window)
        Returns (lead_dict, request_id) or None if no lead can be dialed right now.
        """
        now_iso = sim_now.isoformat()
        with self.connection(immediate=True) as conn:
            # 1. Check tenant active concurrency
            tenant_row = conn.execute(
                "SELECT max_concurrent_calls FROM tenants WHERE tenant_id = ?", (tenant_id,)
            ).fetchone()
            if not tenant_row:
                return None
            max_conc = tenant_row["max_concurrent_calls"]

            active_tenant_count = conn.execute(
                "SELECT COUNT(*) FROM calls WHERE tenant_id = ? AND state IN ('dispatching', 'active')",
                (tenant_id,),
            ).fetchone()[0]

            if active_tenant_count >= max_conc:
                return None

            # 2. Get active phones and DND phones
            active_phones = {
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT phone FROM calls WHERE state IN ('dispatching', 'active')"
                ).fetchall()
            }
            dnd_phones = {r[0] for r in conn.execute("SELECT phone FROM dnd_registry").fetchall()}

            # 3. Query candidate leads for this tenant
            # Priority logic:
            # Priority 1: callback_scheduled with callback_at <= now
            # Priority 2: retry_scheduled with next_eligible_at <= now
            # Priority 3: fresh
            candidate_rows = conn.execute(
                """
                SELECT l.campaign_id, l.lead_id, l.phone, l.state, l.attempts,
                       l.next_eligible_at, l.callback_at, l.created_order,
                       c.max_attempts, c.window_start, c.window_end, c.window_days
                FROM leads l
                JOIN campaigns c ON l.campaign_id = c.campaign_id
                WHERE c.tenant_id = ?
                  AND l.state IN ('fresh', 'retry_scheduled', 'callback_scheduled')
                  AND l.attempts < c.max_attempts
                ORDER BY
                    CASE
                        WHEN l.state = 'callback_scheduled' AND l.callback_at <= ? THEN 1
                        WHEN l.state = 'retry_scheduled' AND l.next_eligible_at <= ? THEN 2
                        WHEN l.state = 'fresh' THEN 3
                        ELSE 4
                    END ASC,
                    CASE WHEN l.state = 'callback_scheduled' THEN l.callback_at END ASC,
                    CASE WHEN l.state = 'retry_scheduled' THEN l.next_eligible_at END ASC,
                    l.created_order ASC
                """,
                (tenant_id, now_iso, now_iso),
            ).fetchall()

            for row in candidate_rows:
                state = row["state"]
                phone = row["phone"]
                cid = row["campaign_id"]
                lid = row["lead_id"]
                next_elig_str = row["next_eligible_at"]
                callback_at_str = row["callback_at"]

                # If phone is in DND, mark lead as dnd_blocked right away
                if phone in dnd_phones:
                    conn.execute(
                        "UPDATE leads SET state = ? WHERE campaign_id = ? AND lead_id = ?",
                        (LeadState.DND_BLOCKED.value, cid, lid),
                    )
                    continue

                # Skip if phone is currently active in ANY campaign
                if phone in active_phones:
                    continue

                # Check eligibility time
                if state == LeadState.CALLBACK_SCHEDULED.value:
                    if not callback_at_str:
                        continue
                    cb_time = datetime.fromisoformat(callback_at_str)
                    if sim_now < cb_time:
                        continue
                elif state == LeadState.RETRY_SCHEDULED.value:
                    if not next_elig_str:
                        continue
                    elig_time = datetime.fromisoformat(next_elig_str)
                    if sim_now < elig_time:
                        continue
                elif state == LeadState.FRESH.value:
                    if next_elig_str:
                        elig_time = datetime.fromisoformat(next_elig_str)
                        if sim_now < elig_time:
                            continue

                # Check calling window and regulatory window
                camp_days = row["window_days"].split(",")
                if not is_call_permitted_now(
                    now=sim_now,
                    campaign_start=row["window_start"],
                    campaign_end=row["window_end"],
                    campaign_days=camp_days,
                    regulatory_start=reg_start,
                    regulatory_end=reg_end,
                ):
                    continue

                # Found our lead! Reserve it atomically.
                attempt_no = row["attempts"] + 1
                request_id = f"{cid}_{lid}_att{attempt_no}_{uuid.uuid4().hex[:8]}"

                # Insert into calls table in 'dispatching' state
                conn.execute(
                    """
                    INSERT INTO calls (
                        request_id, campaign_id, lead_id, phone, tenant_id,
                        attempt_no, state, created_at_sim
                    ) VALUES (?, ?, ?, ?, ?, ?, 'dispatching', ?)
                    """,
                    (request_id, cid, lid, phone, tenant_id, attempt_no, now_iso),
                )

                # Update lead state to 'pending_dial'
                conn.execute(
                    """
                    UPDATE leads
                    SET state = ?, last_request_id = ?
                    WHERE campaign_id = ? AND lead_id = ?
                    """,
                    (LeadState.PENDING_DIAL.value, request_id, cid, lid),
                )

                lead_data = {
                    "campaign_id": cid,
                    "lead_id": lid,
                    "phone": phone,
                    "tenant_id": tenant_id,
                    "attempt_no": attempt_no,
                    "state": state,
                }
                return lead_data, request_id

        return None

    def record_call_accepted(self, request_id: str, call_sid: str, sim_now: datetime) -> None:
        """Update call to 'active' state and record call_sid and attempt on lead."""
        with self.connection(immediate=True) as conn:
            call_row = conn.execute("SELECT * FROM calls WHERE request_id = ?", (request_id,)).fetchone()
            if not call_row:
                return

            # If call was already ended by early webhook, do not revert to active
            if call_row["state"] == "ended":
                if not call_row["call_sid"]:
                    conn.execute("UPDATE calls SET call_sid = ? WHERE request_id = ?", (call_sid, request_id))
                return

            cid = call_row["campaign_id"]
            lid = call_row["lead_id"]
            attempt_no = call_row["attempt_no"]

            conn.execute(
                """
                UPDATE calls
                SET state = 'active', call_sid = ?, started_at_sim = COALESCE(started_at_sim, ?)
                WHERE request_id = ?
                """,
                (call_sid, sim_now.isoformat(), request_id),
            )

            conn.execute(
                """
                UPDATE leads
                SET state = 'active', attempts = ?, last_call_sid = ?
                WHERE campaign_id = ? AND lead_id = ?
                """,
                (attempt_no, call_sid, cid, lid),
            )

    def record_call_not_placed(self, request_id: str, error_detail: str = "") -> None:
        """Update call to 'not_placed' and revert lead to eligible state without counting an attempt."""
        with self.connection(immediate=True) as conn:
            call_row = conn.execute("SELECT * FROM calls WHERE request_id = ?", (request_id,)).fetchone()
            if not call_row or call_row["state"] in ("active", "ended"):
                return

            cid = call_row["campaign_id"]
            lid = call_row["lead_id"]

            conn.execute(
                "UPDATE calls SET state = 'not_placed', raw_result = ? WHERE request_id = ?",
                (error_detail, request_id),
            )

            lead_row = conn.execute(
                "SELECT * FROM leads WHERE campaign_id = ? AND lead_id = ?", (cid, lid)
            ).fetchone()
            if not lead_row:
                return

            # Revert lead state
            if lead_row["callback_at"]:
                new_state = LeadState.CALLBACK_SCHEDULED.value
            elif lead_row["last_status"] in ("no_answer", "busy", "failed"):
                new_state = LeadState.RETRY_SCHEDULED.value
            else:
                new_state = LeadState.FRESH.value

            conn.execute(
                "UPDATE leads SET state = ? WHERE campaign_id = ? AND lead_id = ?",
                (new_state, cid, lid),
            )

    def process_call_ended(self, result: Dict[str, Any], sim_now: datetime) -> bool:
        """
        Idempotently process an ended call outcome (from webhook or polling).
        Returns True if processed or already ended; False if ignored.
        """
        call_sid = result.get("call_sid")
        req_id = result.get("request_id")
        cid = result.get("campaign_id")
        lid = result.get("lead_id")
        phone = result.get("phone")
        status = result.get("status")
        disposition = result.get("disposition")
        callback_at = result.get("callback_at")
        started_at = result.get("started_at")
        ended_at = result.get("ended_at")
        duration_sec = int(result.get("duration_sec", 0))

        with self.connection(immediate=True) as conn:
            # Check if this call is already ended
            call_row = None
            if call_sid:
                call_row = conn.execute("SELECT * FROM calls WHERE call_sid = ?", (call_sid,)).fetchone()
            if not call_row and req_id:
                call_row = conn.execute("SELECT * FROM calls WHERE request_id = ?", (req_id,)).fetchone()

            if call_row and call_row["state"] == "ended":
                # Already processed
                return True

            # If call doesn't exist yet (webhook arrived before dispatcher DB commit), insert it
            if not call_row:
                tenant_id = result.get("tenant_id", "")
                if not tenant_id:
                    camp_row = conn.execute(
                        "SELECT tenant_id FROM campaigns WHERE campaign_id = ?", (cid,)
                    ).fetchone()
                    if camp_row:
                        tenant_id = camp_row["tenant_id"]

                lead_row = conn.execute(
                    "SELECT attempts FROM leads WHERE campaign_id = ? AND lead_id = ?", (cid, lid)
                ).fetchone()
                attempt_no = (lead_row["attempts"] + 1) if lead_row else 1

                conn.execute(
                    """
                    INSERT OR REPLACE INTO calls (
                        request_id, call_sid, campaign_id, lead_id, phone, tenant_id,
                        attempt_no, state, created_at_sim, started_at_sim, ended_at_sim,
                        status, disposition, callback_at, duration_sec, raw_result
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'ended', ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        req_id or (call_sid or ""),
                        call_sid,
                        cid,
                        lid,
                        phone,
                        tenant_id,
                        attempt_no,
                        started_at,
                        started_at,
                        ended_at,
                        status,
                        disposition,
                        callback_at,
                        duration_sec,
                        json.dumps(result),
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE calls
                    SET state = 'ended', call_sid = COALESCE(call_sid, ?),
                        status = ?, disposition = ?, callback_at = ?,
                        started_at_sim = COALESCE(started_at_sim, ?),
                        ended_at_sim = ?, duration_sec = ?, raw_result = ?
                    WHERE request_id = ?
                    """,
                    (
                        call_sid,
                        status,
                        disposition,
                        callback_at,
                        started_at,
                        ended_at,
                        duration_sec,
                        json.dumps(result),
                        call_row["request_id"],
                    ),
                )

            # Now update the lead
            lead_row = conn.execute(
                """
                SELECT l.*, c.max_attempts
                FROM leads l
                JOIN campaigns c ON l.campaign_id = c.campaign_id
                WHERE l.campaign_id = ? AND l.lead_id = ?
                """,
                (cid, lid),
            ).fetchone()

            if not lead_row:
                return True

            attempts = lead_row["attempts"]
            # If the attempt wasn't recorded yet (e.g. webhook before 201), increment attempts
            if call_row and call_row["state"] == "dispatching":
                attempts = call_row["attempt_no"]
            elif not call_row:
                attempts = attempts + 1

            max_attempts = lead_row["max_attempts"]

            # Calculate new lead state based on business rules R5, R6, R7, R8, R9
            next_state = None
            next_eligible_at = None

            if disposition in TERMINAL_DISPOSITIONS:
                # R7: Terminal dispositions end the lead
                next_state = LeadState.COMPLETED.value
                if disposition == "dnd":
                    # R8: DND is global!
                    opt_time = ended_at or sim_now.isoformat()
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO dnd_registry (phone, source_campaign_id, source_lead_id, opted_out_at_sim)
                        VALUES (?, ?, ?, ?)
                        """,
                        (phone, cid, lid, opt_time),
                    )
                    # Mark all other leads with this phone in any campaign that are NOT completed or exhausted
                    conn.execute(
                        """
                        UPDATE leads
                        SET state = ?
                        WHERE phone = ?
                          AND state NOT IN (?, ?)
                          AND NOT (campaign_id = ? AND lead_id = ?)
                        """,
                        (
                            LeadState.DND_BLOCKED.value,
                            phone,
                            LeadState.COMPLETED.value,
                            LeadState.EXHAUSTED.value,
                            cid,
                            lid,
                        ),
                    )

            elif disposition == "callback_requested":
                if attempts >= max_attempts:
                    next_state = LeadState.EXHAUSTED.value
                else:
                    next_state = LeadState.CALLBACK_SCHEDULED.value
                    next_eligible_at = callback_at

            elif status in ("no_answer", "busy", "failed"):
                if attempts >= max_attempts:
                    next_state = LeadState.EXHAUSTED.value
                else:
                    next_state = LeadState.RETRY_SCHEDULED.value
                    mins = 15 if status == "failed" else 60
                    ended_dt = datetime.fromisoformat(ended_at) if ended_at else sim_now
                    next_dt = ended_dt + timedelta(minutes=mins)
                    next_eligible_at = next_dt.isoformat()

            else:
                # Default fallback
                if attempts >= max_attempts:
                    next_state = LeadState.EXHAUSTED.value
                else:
                    next_state = LeadState.EXHAUSTED.value

            conn.execute(
                """
                UPDATE leads
                SET state = ?, attempts = ?, last_status = ?, last_disposition = ?,
                    last_ended_at = ?, last_call_sid = COALESCE(last_call_sid, ?),
                    callback_at = ?, next_eligible_at = COALESCE(?, next_eligible_at)
                WHERE campaign_id = ? AND lead_id = ?
                """,
                (
                    next_state,
                    attempts,
                    status,
                    disposition,
                    ended_at,
                    call_sid,
                    callback_at,
                    next_eligible_at,
                    cid,
                    lid,
                ),
            )

            return True

    def get_stale_active_calls(self, max_age_sim_seconds: float, sim_now: datetime) -> List[Dict[str, Any]]:
        """Find active calls that have been active longer than max_age_sim_seconds."""
        now_iso = sim_now.isoformat()
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT request_id, call_sid, campaign_id, lead_id, phone, attempt_no,
                       state, created_at_sim, started_at_sim
                FROM calls
                WHERE state = 'active' AND call_sid IS NOT NULL
                """
            ).fetchall()

            stale = []
            for r in rows:
                c_time_str = r["started_at_sim"] or r["created_at_sim"]
                if not c_time_str:
                    stale.append(dict(r))
                    continue
                c_time = datetime.fromisoformat(c_time_str)
                age = (sim_now - c_time).total_seconds()
                if age >= max_age_sim_seconds:
                    stale.append(dict(r))
            return stale

    def get_dispatching_calls(self) -> List[Dict[str, Any]]:
        """Find calls stuck in 'dispatching' state (e.g. after a crash)."""
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT request_id, call_sid, campaign_id, lead_id, phone, tenant_id, attempt_no, created_at_sim
                FROM calls
                WHERE state = 'dispatching'
                """
            ).fetchall()
            return [dict(r) for r in rows]

    def is_all_completed(self) -> bool:
        """Check if all leads have reached a final state and there are no active calls."""
        with self.connection() as conn:
            active_calls = conn.execute(
                "SELECT COUNT(*) FROM calls WHERE state IN ('dispatching', 'active')"
            ).fetchone()[0]
            if active_calls > 0:
                return False

            incomplete_leads = conn.execute(
                """
                SELECT COUNT(*) FROM leads
                WHERE state NOT IN ('completed', 'exhausted', 'dnd_blocked')
                """
            ).fetchone()[0]
            return incomplete_leads == 0

    def get_status_summary(self) -> Dict[str, Any]:
        """Summary metrics for monitoring /status endpoint."""
        with self.connection() as conn:
            total_leads = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
            leads_by_state = {
                r["state"]: r["cnt"]
                for r in conn.execute("SELECT state, COUNT(*) as cnt FROM leads GROUP BY state").fetchall()
            }
            active_calls = conn.execute(
                "SELECT COUNT(*) FROM calls WHERE state IN ('dispatching', 'active')"
            ).fetchone()[0]
            calls_by_status = {
                r["status"]: r["cnt"]
                for r in conn.execute(
                    "SELECT status, COUNT(*) as cnt FROM calls WHERE state = 'ended' GROUP BY status"
                ).fetchall()
            }
            dnd_count = conn.execute("SELECT COUNT(*) FROM dnd_registry").fetchone()[0]
            return {
                "total_leads": total_leads,
                "leads_by_state": leads_by_state,
                "active_calls": active_calls,
                "calls_by_status": calls_by_status,
                "dnd_phones_registered": dnd_count,
            }

    def export_final_state_csv(self, output_path: str) -> None:
        """
        Export final_state.csv sorted by campaign_id, lead_id.
        Contract:
        campaign_id,lead_id,final_state,attempts,last_status,last_disposition
        """
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT campaign_id, lead_id, state, attempts,
                       COALESCE(last_status, '') as last_status,
                       COALESCE(last_disposition, '') as last_disposition
                FROM leads
                ORDER BY campaign_id ASC, lead_id ASC
                """
            ).fetchall()

            with open(output_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["campaign_id", "lead_id", "final_state", "attempts", "last_status", "last_disposition"])
                for r in rows:
                    writer.writerow([
                        r["campaign_id"],
                        r["lead_id"],
                        r["state"],
                        r["attempts"],
                        r["last_status"],
                        r["last_disposition"],
                    ])
