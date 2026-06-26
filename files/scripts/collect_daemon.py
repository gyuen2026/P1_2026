#!/usr/bin/env python3
"""
30-day (default) 24/7 collection daemon.

Runs alongside the FastAPI app — user API requests are not blocked
(collector uses a separate asyncio lock; only one cycle at a time).

Usage:
  python3 scripts/collect_daemon.py --once              # single cycle
  python3 scripts/collect_daemon.py --once --limit 10   # smoke test
  python3 scripts/collect_daemon.py                     # 30 days (default)
  python3 scripts/collect_daemon.py --days 30 --interval 90
  python3 scripts/collect_daemon.py --forever           # no end date
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

LONDON = ZoneInfo("Europe/London")
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
DEFAULT_DAYS = 30
DEFAULT_INTERVAL_MIN = 90
FAIL_RETRY_MIN = 10

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "collector.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("collect_daemon")


async def run_one_cycle(limit: int | None = None) -> dict:
    from app.ingest.osm_crossings import ensure_crossings_loaded
    from app.ingest.vehicle_signal_service import load_bus_positions_for_cycle
    from app.ingest.signal_collector import run_global_collection

    log.info("Preloading free-tier sources (OSM + VehiclePositions)...")
    await ensure_crossings_loaded()
    vehicles = await load_bus_positions_for_cycle()
    log.info("Buses tracked: %s", len(vehicles))

    result = await run_global_collection(stop_limit=limit)
    return result or {"saved": 0, "skipped": 0}


async def daemon_loop(days: float | None, interval_min: int) -> None:
    started = datetime.now(LONDON)
    deadline = started + timedelta(days=days) if days else None
    cycle_num = 0

    if deadline:
        log.info(
            "Daemon started — %s-day run until %s (interval=%s min)",
            days,
            deadline.isoformat(),
            interval_min,
        )
    else:
        log.info("Daemon started — running forever (interval=%s min)", interval_min)

    while deadline is None or datetime.now(LONDON) < deadline:
        cycle_num += 1
        t0 = time.monotonic()
        failed = False
        try:
            summary = await run_one_cycle()
            log.info("Cycle #%s summary: %s", cycle_num, summary)
        except Exception:
            log.exception("Cycle #%s failed — retry in %s min", cycle_num, FAIL_RETRY_MIN)
            failed = True

        if deadline and datetime.now(LONDON) >= deadline:
            break

        if failed:
            sleep_min = FAIL_RETRY_MIN
        else:
            elapsed_min = (time.monotonic() - t0) / 60
            sleep_min = max(5, interval_min - elapsed_min)

        if deadline and datetime.now(LONDON) + timedelta(minutes=sleep_min) >= deadline:
            log.info("Deadline reached after cycle #%s", cycle_num)
            break

        log.info("Sleeping %.1f min until next cycle", sleep_min)
        await asyncio.sleep(sleep_min * 60)

    log.info("Daemon finished (%s cycles, started %s)", cycle_num, started.isoformat())


def main() -> None:
    p = argparse.ArgumentParser(description="London Runner 24/7 collector")
    p.add_argument("--once", action="store_true", help="Run one cycle and exit")
    p.add_argument("--limit", type=int, default=None, help="Max stops (smoke test)")
    p.add_argument(
        "--days",
        type=float,
        default=DEFAULT_DAYS,
        help=f"Run duration in days (default {DEFAULT_DAYS})",
    )
    p.add_argument(
        "--forever",
        action="store_true",
        help="Run with no end date (overrides --days)",
    )
    p.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_INTERVAL_MIN,
        help=f"Minutes between cycle starts (default {DEFAULT_INTERVAL_MIN})",
    )
    args = p.parse_args()

    if args.once:
        asyncio.run(run_one_cycle(limit=args.limit))
    else:
        days = None if args.forever else args.days
        asyncio.run(daemon_loop(days=days, interval_min=args.interval))


if __name__ == "__main__":
    main()
