"""Unit tests for idempotency, HMAC verification, and 504 ambiguity resolution."""
import hashlib
import hmac
import json
import os
import tempfile
from datetime import datetime
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from solution.clock import IST, FakeClock
from solution.db import Database
from solution.telephony import TelephonyClient
from solution.webhook_server import create_webhook_app


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = Database(path)
    yield db
    if os.path.exists(path):
        os.remove(path)


def test_hmac_signature_verification(temp_db: Database):
    secret = "test_webhook_secret_123"
    clock = FakeClock(start_time=datetime(2026, 11, 12, 12, 0, 0, tzinfo=IST))
    app = create_webhook_app(temp_db, clock, webhook_secret=secret)
    client = TestClient(app)

    body = {
        "event": "call.completed",
        "call_sid": "CA123456",
        "request_id": "REQ123",
        "campaign_id": "c1",
        "lead_id": "l1",
        "phone": "+919876543210",
        "status": "completed",
        "disposition": "interested",
    }
    body_bytes = json.dumps(body).encode("utf-8")
    valid_sig = hmac.new(secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()

    # 1. Missing signature -> 401
    resp_no_sig = client.post("/webhooks/telephony", content=body_bytes)
    assert resp_no_sig.status_code == 401

    # 2. Invalid signature -> 401
    resp_bad_sig = client.post(
        "/webhooks/telephony",
        content=body_bytes,
        headers={"X-AU-Signature": "invalid_hex_string"},
    )
    assert resp_bad_sig.status_code == 401

    # 3. Valid signature -> 200
    resp_valid = client.post(
        "/webhooks/telephony",
        content=body_bytes,
        headers={"X-AU-Signature": valid_sig, "X-AU-Delivery-Id": "deliv-1"},
    )
    assert resp_valid.status_code == 200

    # 4. Duplicate delivery with same X-AU-Delivery-Id -> 200 (duplicate_ignored)
    resp_dup = client.post(
        "/webhooks/telephony",
        content=body_bytes,
        headers={"X-AU-Signature": valid_sig, "X-AU-Delivery-Id": "deliv-1"},
    )
    assert resp_dup.status_code == 200
    assert resp_dup.json().get("status") == "duplicate_ignored"


def test_webhook_arrives_before_accepted_commit(temp_db: Database):
    # Setup lead and dispatching call
    with temp_db.connection(immediate=True) as conn:
        conn.execute("INSERT INTO tenants VALUES ('t1', 5)")
        conn.execute("INSERT INTO campaigns VALUES ('c1', 't1', '10:00', '19:00', 'mon,tue,wed,thu,fri,sat,sun', 3, 'f1.csv')")
        conn.execute("INSERT INTO leads VALUES ('c1', 'l1', '+919999999999', 'pending_dial', 0, null, null, null, null, null, 'REQ_EARLY', null, 1)")
        conn.execute("INSERT INTO calls VALUES ('REQ_EARLY', null, 'c1', 'l1', '+919999999999', 't1', 1, 'dispatching', null, null, null, null, null, null, 0, null)")

    sim_now = datetime(2026, 11, 12, 12, 0, 0, tzinfo=IST)

    # 1. Webhook arrives first
    webhook_payload = {
        "event": "call.completed",
        "call_sid": "CA_EARLY_01",
        "request_id": "REQ_EARLY",
        "campaign_id": "c1",
        "lead_id": "l1",
        "phone": "+919999999999",
        "status": "completed",
        "disposition": "interested",
        "ended_at": "2026-11-12T12:00:15+05:30",
        "duration_sec": 15,
    }
    temp_db.process_call_ended(webhook_payload, sim_now)

    with temp_db.connection() as conn:
        call_row = conn.execute("SELECT state, status, disposition FROM calls WHERE request_id='REQ_EARLY'").fetchone()
        assert call_row["state"] == "ended"
        assert call_row["disposition"] == "interested"

        lead_row = conn.execute("SELECT state, attempts FROM leads WHERE lead_id='l1'").fetchone()
        assert lead_row["state"] == "completed"
        assert lead_row["attempts"] == 1

    # 2. Later, the dispatcher's HTTP 201 response returns and calls record_call_accepted
    temp_db.record_call_accepted("REQ_EARLY", "CA_EARLY_01", sim_now)

    # Must NOT revert call or lead back to 'active'
    with temp_db.connection() as conn:
        call_row = conn.execute("SELECT state FROM calls WHERE request_id='REQ_EARLY'").fetchone()
        assert call_row["state"] == "ended"

        lead_row = conn.execute("SELECT state FROM leads WHERE lead_id='l1'").fetchone()
        assert lead_row["state"] == "completed"


@pytest.mark.asyncio
async def test_telephony_504_ambiguity_resolution():
    client = TelephonyClient(base_url="http://mock.test")

    # Mock place_call encountering 504
    # Case A: GET /v1/calls?request_id=... returns 200 (call was placed!)
    with patch.object(client, "get_client") as mock_get_client, \
         patch.object(client, "get_call_by_request_id", new_callable=AsyncMock) as mock_get_req:

        mock_http = AsyncMock()
        mock_http.post.return_value = httpx.Response(504, text="upstream_timeout")
        mock_get_client.return_value = mock_http

        mock_get_req.return_value = {
            "call_sid": "CA_RECOVERED_504",
            "request_id": "REQ_504",
            "state": "active",
        }

        res = await client.place_call("c1", "l1", "+919999999999", "REQ_504", "http://callback")
        assert res.status == "accepted"
        assert res.call_sid == "CA_RECOVERED_504"

    # Case B: GET /v1/calls?request_id=... returns None (404, call was not placed!)
    with patch.object(client, "get_client") as mock_get_client, \
         patch.object(client, "get_call_by_request_id", new_callable=AsyncMock) as mock_get_req:

        mock_http = AsyncMock()
        mock_http.post.return_value = httpx.Response(504, text="upstream_timeout")
        mock_get_client.return_value = mock_http

        mock_get_req.return_value = None

        res = await client.place_call("c1", "l1", "+919999999999", "REQ_504", "http://callback")
        assert res.status == "not_placed"
        assert res.retryable is True

