"""Core campaign dispatcher orchestrating call dispatch, concurrency, and recovery."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from .clock import Clock
from .db import Database
from .models import SystemConfig
from .telephony import CallDispatchResult, TelephonyClient
from .window import next_window_open

logger = logging.getLogger(__name__)


class Dispatcher:
    """Orchestrates outbound call dispatching, concurrency control, and failure recovery."""

    def __init__(
        self,
        db: Database,
        telephony: TelephonyClient,
        clock: Clock,
        callback_url: str,
        output_csv_path: str = "solution/final_state.csv",
        tick_interval_real_sec: float = 0.05,
        reconcile_interval_real_sec: float = 1.0,
    ):
        self.db = db
        self.telephony = telephony
        self.clock = clock
        self.callback_url = callback_url
        self.output_csv_path = output_csv_path
        self.tick_interval_real_sec = tick_interval_real_sec
        self.reconcile_interval_real_sec = reconcile_interval_real_sec
        self._running = False
        self._dispatch_tasks: set[asyncio.Task] = set()

    async def startup_recovery(self) -> None:
        """
        Recover state after a crash or restart:
        1. Query provider for any calls stuck in 'dispatching' state.
        2. Query provider for any calls in 'active' state to see if they ended while stopped.
        """
        sim_now = self.clock.now()
        logger.info("Starting crash recovery audit at sim_time=%s", sim_now.isoformat())

        # 1. Resolve dispatching calls
        dispatching_calls = self.db.get_dispatching_calls()
        for call in dispatching_calls:
            req_id = call["request_id"]
            logger.info("Auditing in-flight dispatching call: %s", req_id)
            prov_call = await self.telephony.get_call_by_request_id(req_id)
            if prov_call is None:
                # Was never accepted by provider
                logger.info("Call %s was not placed, reverting lead", req_id)
                self.db.record_call_not_placed(req_id, "crash_recovery_not_placed")
            else:
                call_sid = prov_call.get("call_sid")
                prov_state = prov_call.get("state")
                logger.info("Call %s was accepted (sid=%s, state=%s)", req_id, call_sid, prov_state)
                self.db.record_call_accepted(req_id, call_sid, sim_now)
                if prov_state == "ended" and "result" in prov_call:
                    self.db.process_call_ended(prov_call["result"], sim_now)

        # 2. Reconcile active calls
        active_calls = self.db.get_stale_active_calls(max_age_sim_seconds=0.0, sim_now=sim_now)
        for call in active_calls:
            call_sid = call.get("call_sid")
            if not call_sid:
                continue
            prov_call = await self.telephony.get_call_by_sid(call_sid)
            if prov_call and prov_call.get("state") == "ended" and "result" in prov_call:
                logger.info("Call %s already ended during downtime", call_sid)
                self.db.process_call_ended(prov_call["result"], sim_now)

        logger.info("Crash recovery audit complete.")

    async def reconcile_lost_webhooks(self) -> None:
        """Periodic check to recover calls whose webhooks were dropped by the carrier."""
        sim_now = self.clock.now()
        # Any call active in sim time for >= 15 sim seconds is checked
        stale_calls = self.db.get_stale_active_calls(max_age_sim_seconds=15.0, sim_now=sim_now)
        for call in stale_calls:
            call_sid = call.get("call_sid")
            if not call_sid:
                continue
            prov_call = await self.telephony.get_call_by_sid(call_sid)
            if prov_call and prov_call.get("state") == "ended" and "result" in prov_call:
                logger.info("Recovered lost webhook for call %s", call_sid)
                self.db.process_call_ended(prov_call["result"], sim_now)

    async def _dispatch_single_call(self, lead_data: Dict[str, Any], request_id: str) -> None:
        """Place call through telephony provider and update database with outcome."""
        cid = lead_data["campaign_id"]
        lid = lead_data["lead_id"]
        phone = lead_data["phone"]

        try:
            result = await self.telephony.place_call(
                campaign_id=cid,
                lead_id=lid,
                phone=phone,
                request_id=request_id,
                callback_url=self.callback_url,
            )

            sim_now = self.clock.now()
            if result.status == "accepted":
                self.db.record_call_accepted(request_id, result.call_sid, sim_now)
                # If provider returned ended call in result data immediately:
                if result.data and result.data.get("state") == "ended" and "result" in result.data:
                    self.db.process_call_ended(result.data["result"], sim_now)
            elif result.status == "not_placed":
                self.db.record_call_not_placed(request_id, result.error_detail or "not_placed")
            else:
                # Ambiguous: leave as dispatching to be resolved by reconciliation
                logger.warning("Call %s status ambiguous, left for reconciliation", request_id)

        except Exception as exc:
            logger.error("Unhandled exception dispatching call %s: %s", request_id, exc)
            self.db.record_call_not_placed(request_id, f"exception: {exc}")

    async def run(self) -> None:
        """Main execution loop until all campaigns are fully completed."""
        self._running = True
        logger.info("Dispatcher engine starting...")
        await self.startup_recovery()

        tenants = self.db.get_tenants()
        campaigns_map = self.db.get_campaigns()
        last_reconcile = asyncio.get_event_loop().time()
        last_progress_log = asyncio.get_event_loop().time()

        while self._running:
            sim_now = self.clock.now()

            # 1. Periodic lost webhook reconciliation
            now_real = asyncio.get_event_loop().time()
            if now_real - last_reconcile >= self.reconcile_interval_real_sec:
                await self.reconcile_lost_webhooks()
                last_reconcile = now_real

            # Periodic progress log every 5 real seconds
            if now_real - last_progress_log >= 5.0:
                summary = self.db.get_status_summary()
                completed = summary["total_leads"] - sum(
                    summary["leads_by_state"].get(s, 0)
                    for s in ("fresh", "pending_dial", "active", "retry_scheduled", "callback_scheduled")
                )
                logger.info(
                    "Progress: %d/%d leads finalized | %d active calls | Sim Time: %s",
                    completed,
                    summary["total_leads"],
                    summary["active_calls"],
                    sim_now.strftime("%a %d %b %H:%M IST"),
                )
                last_progress_log = now_real

            # 2. Check completion
            if self.db.is_all_completed():
                logger.info("All leads have reached final states! Exporting final_state.csv...")
                self.db.export_final_state_csv(self.output_csv_path)
                logger.info("Final state saved to %s", self.output_csv_path)
                break

            # 3. Clean finished background dispatch tasks
            self._dispatch_tasks = {t for t in self._dispatch_tasks if not t.done()}

            # 4. Dispatch eligible leads per tenant
            dispatched_any = False
            for tenant in tenants:
                tid = tenant["tenant_id"]
                max_conc = tenant["max_concurrent_calls"]

                while True:
                    lead_reservation = self.db.find_and_reserve_next_lead(
                        tenant_id=tid,
                        sim_now=sim_now,
                        campaigns_map=campaigns_map,
                    )
                    if not lead_reservation:
                        break

                    lead_data, request_id = lead_reservation
                    dispatched_any = True
                    task = asyncio.create_task(self._dispatch_single_call(lead_data, request_id))
                    self._dispatch_tasks.add(task)

            # 5. Sleep appropriate interval
            if dispatched_any:
                await asyncio.sleep(0.01)
            else:
                await asyncio.sleep(self.tick_interval_real_sec)

        # Wait for any lingering dispatch tasks to settle
        if self._dispatch_tasks:
            await asyncio.gather(*self._dispatch_tasks, return_exceptions=True)

        logger.info("Dispatcher engine finished.")

    def stop(self) -> None:
        self._running = False

