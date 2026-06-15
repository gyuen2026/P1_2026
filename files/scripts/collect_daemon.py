#!/usr/bin/env python3
"""
4-day 24/7 collection daemon.

Waits for each full cycle to finish before starting the next (avoids overlap).
Default interval: 90 min between cycle starts (one Zone 1-2 pass ≈ 60–90 min).

Usage:
  python3 scripts/collect_daemon.py --once              # single cycle
  python3 scripts/collect_daemon.py --once --limit 50   # smoke test
  python3 scripts/collect_daemon.py                       # run until --days elapsed
  python3 scripts/collect_daemon.py --days 4 --interval 90
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
    from app.services.osm_crossings import ensure_crossings_loaded
    from app.services.vehicle_signal_service import load_bus_positions_for_cycle
    from app.services.signal_collector import run_global_collection

    log.info("Preloading free-tier sources (OSM + VehiclePositions)...")
    await ensure_crossings_loaded()
    vehicles = await load_bus_positions_for_cycle()
    log.info("Buses tracked: %s", len(vehicles))

    result = await run_global_collection(stop_limit=limit)
    return result or {"saved": 0, "skipped": 0}


async def daemon_loop(days: float, interval_min: int) -> None:
    deadline = datetime.now(LONDON) + timedelta(days=days)
    cycle_num = 0
    log.info("Daemon started — run until %s (interval=%s min)", deadline.isoformat(), interval_min)

    while datetime.now(LONDON) < deadline:
        cycle_num += 1
        t0 = time.monotonic()
        try:
            summary = await run_one_cycle()
            log.info("Cycle #%s summary: %s", cycle_num, summary)
        except Exception:
            log.exception("Cycle #%s failed — retry after interval", cycle_num)

        elapsed_min = (time.monotonic() - t0) / 60
        sleep_min = max(0, interval_min - elapsed_min)
        if datetime.now(LONDON) + timedelta(minutes=sleep_min) >= deadline:
            log.info("Deadline reached after cycle #%s", cycle_num)
            break
        log.info("Sleeping %.1f min until next cycle", sleep_min)
        await asyncio.sleep(sleep_min * 60)

    log.info("Daemon finished (%s cycles)", cycle_num)


def main() -> None:
    p = argparse.ArgumentParser(description="London Runner 24/7 collector")
    p.add_argument("--once", action="store_true", help="Run one cycle and exit")
    p.add_argument("--limit", type=int, default=None, help="Max stops (smoke test)")
    p.add_argument("--days", type=float, default=4.0, help="Run duration in days (default 4)")
    p.add_argument(
        "--interval",
        type=int,
        default=90,
        help="Minutes between cycle starts (default 90; full pass ~60-90 min)",
    )
    args = p.parse_args()

    if args.once:
        asyncio.run(run_one_cycle(limit=args.limit))
    else:
        asyncio.run(daemon_loop(days=args.days, interval_min=args.interval))


if __name__ == "__main__":
    main()
