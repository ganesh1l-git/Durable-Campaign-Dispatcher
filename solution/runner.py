"""Runner that orchestrates both the webhook server and dispatcher in a single process."""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
from datetime import datetime

import httpx
import uvicorn

from .clock import SimClock
from .db import Database
from .dispatcher import Dispatcher
from .telephony import TelephonyClient
from .webhook_server import create_webhook_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger("solution")


async def run_system(
    fixtures_path: str,
    mock_url: str,
    webhook_host: str,
    webhook_port: int,
    webhook_path: str,
    db_path: str,
    output_path: str,
) -> None:
    """Initialize database, sync clock with provider, start webhook receiver and dispatcher."""
    logger.info("Initializing Durable Campaign Dispatcher...")
    logger.info("Using fixtures: %s", fixtures_path)
    logger.info("Mock provider URL: %s", mock_url)
    logger.info("Database path: %s", db_path)

    # 1. Initialize database & fixtures
    db = Database(db_path)
    db.load_fixtures_if_empty(fixtures_path)

    # 2. Sync with mock telephony clock
    callback_url = f"http://{webhook_host}:{webhook_port}{webhook_path}"
    logger.info("Connecting to mock provider clock at %s/v1/clock...", mock_url)

    clock = None
    for attempt in range(15):
        try:
            clock = await SimClock.create_from_provider(mock_url)
            break
        except Exception as exc:
            logger.warning("Waiting for mock provider (%s/15): %s", attempt + 1, exc)
            await asyncio.sleep(1.0)

    if clock is None:
        logger.error("Could not connect to mock telephony provider at %s. Is it running?", mock_url)
        sys.exit(1)

    logger.info(
        "Clock synchronized! sim_now=%s, speed=%sx",
        clock.now().isoformat(),
        clock.speed,
    )

    # 3. Read webhook secret from fixtures
    import json

    with open(fixtures_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    webhook_secret = cfg.get("webhook_secret", "whsec_au_assignment_local")

    # 4. Create Webhook App & Uvicorn Server
    app = create_webhook_app(db, clock, webhook_secret)
    uvicorn_config = uvicorn.Config(
        app,
        host=webhook_host,
        port=webhook_port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(uvicorn_config)

    # 5. Create Telephony Client & Dispatcher
    telephony = TelephonyClient(base_url=mock_url)
    dispatcher = Dispatcher(
        db=db,
        telephony=telephony,
        clock=clock,
        callback_url=callback_url,
        output_csv_path=output_path,
    )

    async def run_dispatcher_wrapper():
        # Small wait for uvicorn to bind
        while not server.started:
            await asyncio.sleep(0.05)
        try:
            await dispatcher.run()
        finally:
            server.should_exit = True

    try:
        await asyncio.gather(
            server.serve(),
            run_dispatcher_wrapper(),
        )
    finally:
        await telephony.close()
        logger.info("Shutdown complete.")


def parse_args():
    parser = argparse.ArgumentParser(description="Durable Campaign Dispatcher (Assignment B)")
    default_fixtures = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "assignment-b-workflow", "fixtures", "campaigns.json")
    )
    parser.add_argument(
        "--fixtures",
        default=default_fixtures,
        help="Path to campaigns.json (default: assignment-b-workflow/fixtures/campaigns.json)",
    )
    parser.add_argument(
        "--mock-url",
        default="http://127.0.0.1:8099",
        help="Base URL of mock telephony provider (default: http://127.0.0.1:8099)",
    )
    parser.add_argument(
        "--webhook-host",
        default="127.0.0.1",
        help="Host for webhook server (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--webhook-port",
        type=int,
        default=8200,
        help="Port for webhook server (default: 8200)",
    )
    parser.add_argument(
        "--webhook-path",
        default="/webhooks/telephony",
        help="Path for telephony callbacks (default: /webhooks/telephony)",
    )
    parser.add_argument(
        "--db-path",
        default="solution/dispatcher.db",
        help="SQLite database path (default: solution/dispatcher.db)",
    )
    parser.add_argument(
        "--output",
        default="solution/final_state.csv",
        help="Output CSV file path (default: solution/final_state.csv)",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    try:
        asyncio.run(
            run_system(
                fixtures_path=args.fixtures,
                mock_url=args.mock_url,
                webhook_host=args.webhook_host,
                webhook_port=args.webhook_port,
                webhook_path=args.webhook_path,
                db_path=args.db_path,
                output_path=args.output,
            )
        )
    except KeyboardInterrupt:
        logger.info("Received interrupt signal, shutting down.")


if __name__ == "__main__":
    main()

