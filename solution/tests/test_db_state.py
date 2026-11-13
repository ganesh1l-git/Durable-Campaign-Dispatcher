"""Unit tests for the SQLite state machine and business rules R2, R3, R4, R5, R7, R8, R9."""
import os
import tempfile
from datetime import datetime, timedelta

import pytest

from solution.clock import IST
from solution.db import Database
from solution.models import LeadState


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = Database(path)
    yield db
    if os.path.exists(path):
        os.remove(path)


def test_tenant_concurrency_limit_shared(temp_db: Database):
    # Tenant 'acme' has max_concurrent_calls = 2, shared across 2 campaigns c1 and c2
    with temp_db.connection(immediate=True) as conn:
        conn.execute("INSERT INTO tenants VALUES ('acme', 2)")
        conn.execute("INSERT INTO campaigns VALUES ('c1', 'acme', '10:00', '19:00', 'mon,tue,wed,thu,fri,sat,sun', 3, 'f1.csv')")
        conn.execute("INSERT INTO campaigns VALUES ('c2', 'acme', '10:00', '19:00', 'mon,tue,wed,thu,fri,sat,sun', 3, 'f2.csv')")

        conn.execute("INSERT INTO leads VALUES ('c1', 'l1', '+911111111111', 'fresh', 0, '2026-11-12T10:00:00+05:30', null, null, null, null, null, null, 1)")
        conn.execute("INSERT INTO leads VALUES ('c1', 'l2', '+912222222222', 'fresh', 0, '2026-11-12T10:00:00+05:30', null, null, null, null, null, null, 2)")
        conn.execute("INSERT INTO leads VALUES ('c2', 'l3', '+913333333333', 'fresh', 0, '2026-11-12T10:00:00+05:30', null, null, null, null, null, null, 3)")

    sim_now = datetime(2026, 11, 12, 12, 0, 0, tzinfo=IST)
    camps = temp_db.get_campaigns()

    # Reserve 1st slot
    res1 = temp_db.find_and_reserve_next_lead("acme", sim_now, camps)
    assert res1 is not None

    # Reserve 2nd slot
    res2 = temp_db.find_and_reserve_next_lead("acme", sim_now, camps)
    assert res2 is not None

    # 3rd slot must be denied because max_concurrent_calls = 2
    res3 = temp_db.find_and_reserve_next_lead("acme", sim_now, camps)
    assert res3 is None


def test_one_call_per_phone_cross_campaign(temp_db: Database):
    # Same phone '+919999999999' exists in c1 and c2
    with temp_db.connection(immediate=True) as conn:
        conn.execute("INSERT INTO tenants VALUES ('t1', 5)")
        conn.execute("INSERT INTO campaigns VALUES ('c1', 't1', '10:00', '19:00', 'mon,tue,wed,thu,fri,sat,sun', 3, 'f1.csv')")
        conn.execute("INSERT INTO campaigns VALUES ('c2', 't1', '10:00', '19:00', 'mon,tue,wed,thu,fri,sat,sun', 3, 'f2.csv')")

        conn.execute("INSERT INTO leads VALUES ('c1', 'l1', '+919999999999', 'fresh', 0, '2026-11-12T10:00:00+05:30', null, null, null, null, null, null, 1)")
        conn.execute("INSERT INTO leads VALUES ('c2', 'l2', '+919999999999', 'fresh', 0, '2026-11-12T10:00:00+05:30', null, null, null, null, null, null, 2)")

    sim_now = datetime(2026, 11, 12, 12, 0, 0, tzinfo=IST)
    camps = temp_db.get_campaigns()

    # Reserve 1st call for this phone
    res1 = temp_db.find_and_reserve_next_lead("t1", sim_now, camps)
    assert res1 is not None
    assert res1[0]["phone"] == "+919999999999"

    # 2nd call for the same phone must be denied while the 1st is in-flight/active!
    res2 = temp_db.find_and_reserve_next_lead("t1", sim_now, camps)
    assert res2 is None


def test_dnd_global_propagation_and_precedence(temp_db: Database):
    # Phone '+918888888888' is present in 3 leads:
    # 1. c1/lead_trigger: will receive DND
    # 2. c2/lead_fresh: fresh -> should become dnd_blocked
    # 3. c3/lead_completed: already completed with 'interested' -> should STAY completed!
    # 4. c4/lead_exhausted: already exhausted -> should STAY exhausted!
    with temp_db.connection(immediate=True) as conn:
        conn.execute("INSERT INTO tenants VALUES ('t1', 10)")
        conn.execute("INSERT INTO campaigns VALUES ('c1', 't1', '10:00', '19:00', 'mon,tue,wed,thu,fri,sat,sun', 3, 'f1.csv')")
        conn.execute("INSERT INTO campaigns VALUES ('c2', 't1', '10:00', '19:00', 'mon,tue,wed,thu,fri,sat,sun', 3, 'f2.csv')")
        conn.execute("INSERT INTO campaigns VALUES ('c3', 't1', '10:00', '19:00', 'mon,tue,wed,thu,fri,sat,sun', 3, 'f3.csv')")
        conn.execute("INSERT INTO campaigns VALUES ('c4', 't1', '10:00', '19:00', 'mon,tue,wed,thu,fri,sat,sun', 3, 'f4.csv')")

        conn.execute("INSERT INTO leads VALUES ('c1', 'l_trig', '+918888888888', 'active', 1, null, null, null, null, 'CA001', 'REQ001', null, 1)")
        conn.execute("INSERT INTO calls VALUES ('REQ001', 'CA001', 'c1', 'l_trig', '+918888888888', 't1', 1, 'active', null, null, null, null, null, null, 0, null)")

        conn.execute("INSERT INTO leads VALUES ('c2', 'l_fresh', '+918888888888', 'fresh', 0, '2026-11-12T10:00:00+05:30', null, null, null, null, null, null, 2)")
        conn.execute("INSERT INTO leads VALUES ('c3', 'l_comp', '+918888888888', 'completed', 1, null, null, 'completed', 'interested', 'CA003', null, null, 3)")
        conn.execute("INSERT INTO leads VALUES ('c4', 'l_exh', '+918888888888', 'exhausted', 3, null, null, 'no_answer', null, 'CA004', null, null, 4)")

    sim_now = datetime(2026, 11, 12, 12, 30, 0, tzinfo=IST)
    webhook_payload = {
        "event": "call.completed",
        "call_sid": "CA001",
        "request_id": "REQ001",
        "tenant_id": "t1",
        "campaign_id": "c1",
        "lead_id": "l_trig",
        "phone": "+918888888888",
        "status": "completed",
        "disposition": "dnd",
        "started_at": "2026-11-12T12:28:00+05:30",
        "ended_at": "2026-11-12T12:30:00+05:30",
        "duration_sec": 120,
    }

    temp_db.process_call_ended(webhook_payload, sim_now)

    with temp_db.connection() as conn:
        # l_trig reached terminal disposition dnd -> completed
        r1 = conn.execute("SELECT state, last_disposition FROM leads WHERE campaign_id='c1'").fetchone()
        assert r1["state"] == "completed"
        assert r1["last_disposition"] == "dnd"

        # l_fresh was fresh -> now dnd_blocked
        r2 = conn.execute("SELECT state FROM leads WHERE campaign_id='c2'").fetchone()
        assert r2["state"] == "dnd_blocked"

        # l_comp was completed -> preserves completed! (Rule R9: completed > exhausted > dnd_blocked)
        r3 = conn.execute("SELECT state FROM leads WHERE campaign_id='c3'").fetchone()
        assert r3["state"] == "completed"

        # l_exh was exhausted -> preserves exhausted!
        r4 = conn.execute("SELECT state FROM leads WHERE campaign_id='c4'").fetchone()
        assert r4["state"] == "exhausted"


def test_retry_intervals_and_exhaustion(temp_db: Database):
    with temp_db.connection(immediate=True) as conn:
        conn.execute("INSERT INTO tenants VALUES ('t1', 5)")
        conn.execute("INSERT INTO campaigns VALUES ('c1', 't1', '10:00', '19:00', 'mon,tue,wed,thu,fri,sat,sun', 2, 'f1.csv')")
        conn.execute("INSERT INTO leads VALUES ('c1', 'l1', '+917777777777', 'active', 1, null, null, null, null, 'CA001', 'REQ001', null, 1)")
        conn.execute("INSERT INTO calls VALUES ('REQ001', 'CA001', 'c1', 'l1', '+917777777777', 't1', 1, 'active', null, null, null, null, null, null, 0, null)")

    sim_now = datetime(2026, 11, 12, 12, 0, 0, tzinfo=IST)

    # Call 1 ends with no_answer -> retry scheduled after 60 minutes
    temp_db.process_call_ended(
        {
            "call_sid": "CA001",
            "request_id": "REQ001",
            "campaign_id": "c1",
            "lead_id": "l1",
            "phone": "+917777777777",
            "status": "no_answer",
            "disposition": None,
            "ended_at": "2026-11-12T12:00:00+05:30",
        },
        sim_now,
    )

    with temp_db.connection() as conn:
        row = conn.execute("SELECT state, attempts, next_eligible_at FROM leads WHERE lead_id='l1'").fetchone()
        assert row["state"] == "retry_scheduled"
        assert row["attempts"] == 1
        assert row["next_eligible_at"] == "2026-11-12T13:00:00+05:30"

    # Attempt 2 is dispatched
    sim_now2 = datetime(2026, 11, 12, 13, 5, 0, tzinfo=IST)
    camps = temp_db.get_campaigns()
    res = temp_db.find_and_reserve_next_lead("t1", sim_now2, camps)
    assert res is not None
    req2 = res[1]
    temp_db.record_call_accepted(req2, "CA002", sim_now2)

    # Call 2 ends with busy -> since max_attempts = 2, lead must become 'exhausted'!
    temp_db.process_call_ended(
        {
            "call_sid": "CA002",
            "request_id": req2,
            "campaign_id": "c1",
            "lead_id": "l1",
            "phone": "+917777777777",
            "status": "busy",
            "disposition": None,
            "ended_at": "2026-11-12T13:06:00+05:30",
        },
        sim_now2,
    )

    with temp_db.connection() as conn:
        row = conn.execute("SELECT state, attempts FROM leads WHERE lead_id='l1'").fetchone()
        assert row["state"] == "exhausted"
        assert row["attempts"] == 2

