"""Unit tests for lead selection priority ordering (Rule R6)."""
import os
import tempfile
from datetime import datetime, timedelta

import pytest

from solution.clock import IST, FakeClock
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


def test_priority_ordering(temp_db: Database):
    # Setup 1 tenant, 1 campaign
    with temp_db.connection(immediate=True) as conn:
        conn.execute("INSERT INTO tenants VALUES ('t1', 5)")
        conn.execute("INSERT INTO campaigns VALUES ('c1', 't1', '10:00', '19:00', 'mon,tue,wed,thu,fri,sat,sun', 3, 'f.csv')")

        # Insert 3 leads:
        # Lead 1: Fresh
        conn.execute(
            "INSERT INTO leads (campaign_id, lead_id, phone, state, attempts, next_eligible_at, created_order) "
            "VALUES ('c1', 'fresh_lead', '+910000000001', 'fresh', 0, '2026-11-12T10:00:00+05:30', 1)"
        )

        # Lead 2: Retry scheduled for 11:30
        conn.execute(
            "INSERT INTO leads (campaign_id, lead_id, phone, state, attempts, next_eligible_at, created_order) "
            "VALUES ('c1', 'retry_lead', '+910000000002', 'retry_scheduled', 1, '2026-11-12T11:30:00+05:30', 2)"
        )

        # Lead 3: Callback scheduled for 11:15
        conn.execute(
            "INSERT INTO leads (campaign_id, lead_id, phone, state, attempts, callback_at, next_eligible_at, created_order) "
            "VALUES ('c1', 'callback_lead', '+910000000003', 'callback_scheduled', 1, '2026-11-12T11:15:00+05:30', '2026-11-12T11:15:00+05:30', 3)"
        )

    campaigns_map = temp_db.get_campaigns()

    # At 11:45 IST, both the callback and retry are due.
    # Expected selection order:
    # 1. callback_lead (Rule R6: Due callbacks go before retries)
    # 2. retry_lead (Rule R6: retries before fresh)
    # 3. fresh_lead
    sim_now = datetime(2026, 11, 12, 11, 45, 0, tzinfo=IST)

    res1 = temp_db.find_and_reserve_next_lead("t1", sim_now, campaigns_map)
    assert res1 is not None
    assert res1[0]["lead_id"] == "callback_lead"

    res2 = temp_db.find_and_reserve_next_lead("t1", sim_now, campaigns_map)
    assert res2 is not None
    assert res2[0]["lead_id"] == "retry_lead"

    res3 = temp_db.find_and_reserve_next_lead("t1", sim_now, campaigns_map)
    assert res3 is not None
    assert res3[0]["lead_id"] == "fresh_lead"


def test_callback_ordering_by_earliest_due(temp_db: Database):
    # Callbacks with earlier callback_at should be called first
    with temp_db.connection(immediate=True) as conn:
        conn.execute("INSERT INTO tenants VALUES ('t1', 5)")
        conn.execute("INSERT INTO campaigns VALUES ('c1', 't1', '10:00', '19:00', 'mon,tue,wed,thu,fri,sat,sun', 3, 'f.csv')")

        conn.execute(
            "INSERT INTO leads (campaign_id, lead_id, phone, state, attempts, callback_at, created_order) "
            "VALUES ('c1', 'cb_late', '+910000000001', 'callback_scheduled', 1, '2026-11-12T11:30:00+05:30', 1)"
        )
        conn.execute(
            "INSERT INTO leads (campaign_id, lead_id, phone, state, attempts, callback_at, created_order) "
            "VALUES ('c1', 'cb_early', '+910000000002', 'callback_scheduled', 1, '2026-11-12T10:45:00+05:30', 2)"
        )

    campaigns_map = temp_db.get_campaigns()
    sim_now = datetime(2026, 11, 12, 12, 0, 0, tzinfo=IST)

    res1 = temp_db.find_and_reserve_next_lead("t1", sim_now, campaigns_map)
    assert res1[0]["lead_id"] == "cb_early"

    res2 = temp_db.find_and_reserve_next_lead("t1", sim_now, campaigns_map)
    assert res2[0]["lead_id"] == "cb_late"

