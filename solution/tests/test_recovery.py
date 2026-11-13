"""Unit tests for crash recovery and lost webhook recovery."""
import os
import tempfile
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from solution.clock import IST, FakeClock
from solution.db import Database
from solution.dispatcher import Dispatcher
from solution.telephony import TelephonyClient


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = Database(path)
    yield db
    if os.path.exists(path):
        os.remove(path)


@pytest.mark.asyncio
async def test_startup_crash_recovery(temp_db: Database):
    # Setup:
    # Call 1: stuck in 'dispatching', provider returns 404 (never accepted)
    # Call 2: stuck in 'dispatching', provider returns 200 (was accepted and ended!)
    # Call 3: stuck in 'active', provider returns 200 ended (lost webhook during downtime)
    with temp_db.connection(immediate=True) as conn:
        conn.execute("INSERT INTO tenants VALUES ('t1', 5)")
        conn.execute("INSERT INTO campaigns VALUES ('c1', 't1', '10:00', '19:00', 'mon,tue,wed,thu,fri,sat,sun', 3, 'f1.csv')")

        conn.execute("INSERT INTO leads VALUES ('c1', 'l1', '+911111111111', 'pending_dial', 0, null, null, null, null, null, 'REQ_CRASH_1', null, 1)")
        conn.execute("INSERT INTO calls VALUES ('REQ_CRASH_1', null, 'c1', 'l1', '+911111111111', 't1', 1, 'dispatching', null, null, null, null, null, null, 0, null)")

        conn.execute("INSERT INTO leads VALUES ('c1', 'l2', '+912222222222', 'pending_dial', 0, null, null, null, null, null, 'REQ_CRASH_2', null, 2)")
        conn.execute("INSERT INTO calls VALUES ('REQ_CRASH_2', null, 'c1', 'l2', '+912222222222', 't1', 1, 'dispatching', null, null, null, null, null, null, 0, null)")

        conn.execute("INSERT INTO leads VALUES ('c1', 'l3', '+913333333333', 'active', 1, null, null, null, null, 'CA_CRASH_3', 'REQ_CRASH_3', null, 3)")
        conn.execute("INSERT INTO calls VALUES ('REQ_CRASH_3', 'CA_CRASH_3', 'c1', 'l3', '+913333333333', 't1', 1, 'active', null, null, null, null, null, null, 0, null)")

    clock = FakeClock(start_time=datetime(2026, 11, 12, 12, 0, 0, tzinfo=IST))
    telephony = TelephonyClient()

    # Mock provider calls
    async def mock_get_by_req(request_id):
        if request_id == "REQ_CRASH_1":
            return None  # 404
        if request_id == "REQ_CRASH_2":
            return {
                "call_sid": "CA_RECOVERED_2",
                "request_id": "REQ_CRASH_2",
                "state": "ended",
                "result": {
                    "call_sid": "CA_RECOVERED_2",
                    "request_id": "REQ_CRASH_2",
                    "campaign_id": "c1",
                    "lead_id": "l2",
                    "phone": "+912222222222",
                    "status": "completed",
                    "disposition": "interested",
                    "ended_at": "2026-11-12T12:00:10+05:30",
                },
            }
        return None

    async def mock_get_by_sid(call_sid):
        if call_sid == "CA_CRASH_3":
            return {
                "call_sid": "CA_CRASH_3",
                "state": "ended",
                "result": {
                    "call_sid": "CA_CRASH_3",
                    "request_id": "REQ_CRASH_3",
                    "campaign_id": "c1",
                    "lead_id": "l3",
                    "phone": "+913333333333",
                    "status": "completed",
                    "disposition": "not_interested",
                    "ended_at": "2026-11-12T12:00:20+05:30",
                },
            }
        return None

    telephony.get_call_by_request_id = AsyncMock(side_effect=mock_get_by_req)
    telephony.get_call_by_sid = AsyncMock(side_effect=mock_get_by_sid)

    dispatcher = Dispatcher(
        db=temp_db,
        telephony=telephony,
        clock=clock,
        callback_url="http://127.0.0.1:8200/webhooks/telephony",
    )

    await dispatcher.startup_recovery()

    with temp_db.connection() as conn:
        # l1: call not placed, lead reverted to fresh, attempts=0
        r1 = conn.execute("SELECT state, attempts FROM leads WHERE lead_id='l1'").fetchone()
        assert r1["state"] == "fresh"
        assert r1["attempts"] == 0

        # l2: call placed and ended with interested -> completed
        r2 = conn.execute("SELECT state, attempts, last_disposition FROM leads WHERE lead_id='l2'").fetchone()
        assert r2["state"] == "completed"
        assert r2["attempts"] == 1
        assert r2["last_disposition"] == "interested"

        # l3: call active ended with not_interested -> completed
        r3 = conn.execute("SELECT state, attempts, last_disposition FROM leads WHERE lead_id='l3'").fetchone()
        assert r3["state"] == "completed"
        assert r3["attempts"] == 1
        assert r3["last_disposition"] == "not_interested"

