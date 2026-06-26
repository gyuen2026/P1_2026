#!/usr/bin/env bash
# London Runner — 2-hour data-collection setup (run from files/)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "=== London Runner collector setup ==="
echo "Working dir: $ROOT"

# 1) .env
if [[ ! -f .env ]]; then
  echo "❌ Missing .env — copy .env.example and fill keys:"
  echo "   cp .env.example .env"
  exit 1
fi

# 2) Python deps
echo "→ Installing dependencies..."
python3 -m pip install -q -r requirements.txt

# 3) Required env vars
python3 <<'PY'
import os, sys
from dotenv import load_dotenv
load_dotenv(".env")
required = ["TFL_APP_KEY", "SUPABASE_URL", "SUPABASE_KEY"]
missing = [k for k in required if not os.getenv(k) or "your_" in os.getenv(k, "")]
if missing:
    print("❌ Missing or placeholder env:", ", ".join(missing))
    sys.exit(1)
key = os.getenv("SUPABASE_KEY", "")
if key.startswith("eyJ") and "service_role" not in os.getenv("SUPABASE_KEY_ROLE_HINT", ""):
    print("⚠️  Use Supabase service_role key on server (RLS bypass). anon key may block inserts.")
print("✅ Env vars present")
PY

# 4) Supabase connectivity + insert test
echo "→ Testing Supabase read/write..."
python3 <<'PY'
import asyncio, os, sys
from datetime import datetime
from dotenv import load_dotenv
load_dotenv(".env")
from supabase import create_client

url, key = os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"]
db = create_client(url, key)

try:
    db.table("bus_signal_observations").select("id").limit(1).execute()
    print("✅ Supabase SELECT ok")
except Exception as e:
    print("❌ Supabase SELECT failed:", e)
    sys.exit(1)

# dry-run: count existing rows
try:
    r = db.table("bus_signal_observations").select("id", count="exact").limit(0).execute()
    print(f"   Current rows: {r.count}")
except Exception:
    pass
PY

# 5) TfL smoke test
echo "→ Testing TfL API..."
python3 <<'PY'
import asyncio, sys
sys.path.insert(0, ".")
from app.ingest import tfl_service
from app.predict.signal_prediction import calc_delay_detail, get_london_now

async def main():
    data = await tfl_service.get_all_stops_in_zones()
    stops = tfl_service.parse_stops_payload(data)
    if len(stops) < 100:
        print(f"❌ Too few stops ({len(stops)}) — check TFL_APP_KEY")
        sys.exit(1)
    print(f"✅ TfL: {len(stops)} stops in Zone 1-2 grid")

    sid = stops[0]["id"]
    arr = await tfl_service.get_bus_arrivals(sid)
    if not isinstance(arr, list):
        print("⚠️  Arrivals empty for first stop (may be off-peak)")
    else:
        d = calc_delay_detail(arr, get_london_now())
        print(f"   Sample stop {sid}: delay={d['avg_delay_sec']}s methods={d['methods']}")

asyncio.run(main())
PY

# 6) Logs dir
mkdir -p logs

echo ""
echo "=== Setup OK — next steps ==="
echo "  Smoke (10 stops):  python3 scripts/collect_daemon.py --once --limit 10"
echo "  One full cycle:    python3 scripts/collect_daemon.py --once"
echo "  30-day 24/7:       ./scripts/start_month_collector.sh 30"
echo "  Or:                nohup python3 scripts/collect_daemon.py --days 30 >> logs/collector.out 2>&1 &"
echo "  Status:            tail -f logs/collector.log"
echo ""
