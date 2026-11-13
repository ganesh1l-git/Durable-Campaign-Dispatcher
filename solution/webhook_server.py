"""Webhook receiver FastAPI server for mock telephony provider callbacks."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from .clock import Clock
from .db import Database

logger = logging.getLogger(__name__)


def create_webhook_app(db: Database, clock: Clock, webhook_secret: str) -> FastAPI:
    """Create FastAPI application for handling telephony webhooks and status."""
    app = FastAPI(title="AU Dispatcher Webhook Receiver", version="1.0")

    @app.post("/webhooks/telephony")
    async def receive_telephony_webhook(
        request: Request,
        x_au_signature: Optional[str] = Header(None, alias="X-AU-Signature"),
        x_au_delivery_id: Optional[str] = Header(None, alias="X-AU-Delivery-Id"),
    ):
        body_bytes = await request.body()

        # 1. Signature verification
        if not x_au_signature:
            logger.warning("Webhook rejected: missing X-AU-Signature")
            raise HTTPException(status_code=401, detail="missing_signature")

        expected_sig = hmac.new(
            webhook_secret.encode("utf-8"), body_bytes, hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(x_au_signature, expected_sig):
            logger.warning("Webhook rejected: signature mismatch")
            raise HTTPException(status_code=401, detail="invalid_signature")

        # 2. Parse payload
        try:
            payload = json.loads(body_bytes.decode("utf-8"))
        except Exception:
            raise HTTPException(status_code=400, detail="invalid_json")

        call_sid = payload.get("call_sid")

        # 3. Idempotency check via delivery_id
        if x_au_delivery_id:
            with db.connection(immediate=True) as conn:
                already_seen = conn.execute(
                    "SELECT 1 FROM processed_webhooks WHERE delivery_id = ?",
                    (x_au_delivery_id,),
                ).fetchone()
                if already_seen:
                    # Harmless duplicate delivery
                    return JSONResponse({"status": "duplicate_ignored"}, status_code=200)

                conn.execute(
                    "INSERT INTO processed_webhooks (delivery_id, call_sid, processed_at) VALUES (?, ?, ?)",
                    (x_au_delivery_id, call_sid or "", clock.now().isoformat()),
                )

        # 4. Process call completion durably in state machine
        sim_now = clock.now()
        db.process_call_ended(payload, sim_now)

        return JSONResponse({"status": "accepted", "call_sid": call_sid}, status_code=200)

    @app.get("/health")
    async def health():
        return {"status": "ok", "sim_now": clock.now().isoformat()}

    @app.get("/status")
    async def status():
        summary = db.get_status_summary()
        summary["sim_now"] = clock.now().isoformat()
        return summary

    return app

