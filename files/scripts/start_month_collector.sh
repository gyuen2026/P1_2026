#!/usr/bin/env bash
# Start 30-day 24/7 local collector (Mac — prevents sleep)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

mkdir -p logs

if [[ -f logs/collector.pid ]] && kill -0 "$(cat logs/collector.pid)" 2>/dev/null; then
  echo "⚠️  Collector already running (PID $(cat logs/collector.pid))"
  echo "   Stop: kill \$(cat logs/collector.pid)"
  exit 1
fi

DAYS="${1:-30}"
echo "Starting ${DAYS}-day collector (interval 90 min)..."

caffeinate -dims nohup python3 scripts/collect_daemon.py --days "$DAYS" --interval 90 \
  >> logs/collector.out 2>&1 &
echo $! > logs/collector.pid

echo "✅ Started PID $(cat logs/collector.pid)"
echo "   Log: tail -f logs/collector.log"
echo "   Stop: kill \$(cat logs/collector.pid)"
