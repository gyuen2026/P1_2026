# 30-Day 24/7 Collection Plan

## Query 10 verdict ✅

`OK — same stop at DIFFERENT observed_at (time-series)` → 데이터 정상.  
같은 정류장이 여러 row인 것은 **시간대별 시계열**입니다.

---

## Architecture (app + collector together)

```
┌─────────────────────────────────────────────────┐
│  FastAPI (Render or local uvicorn)              │
│  ├─ /routes/recommend   ← 사용자 앱 (실시간)     │
│  ├─ /routes/check-status                        │
│  └─ background: run_scheduler() every 90 min      │  ← asyncio, API와 병렬
└─────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────┐
│  Local: collect_daemon.py (recommended 30-day)    │
│  └─ same Supabase, 90 min cycles, Mac caffeinate  │
└─────────────────────────────────────────────────┘
```

- 수집기는 **asyncio 백그라운드** → 앱 API와 **동시 실행** (lock으로 사이클 1개만)
- 사용자가 `/routes/recommend` 호출해도 수집 **중단되지 않음**

---

## Start 30-day collection

### Local (recommended for 30 days — Render Free sleeps)

```bash
cd files
chmod +x scripts/start_month_collector.sh
./scripts/start_month_collector.sh 30
```

Or manually:

```bash
caffeinate -dims nohup python3 scripts/collect_daemon.py --days 30 --interval 90 >> logs/collector.out 2>&1 &
echo $! > logs/collector.pid
tail -f logs/collector.log
```

Forever (no end date):

```bash
python3 scripts/collect_daemon.py --forever --interval 90
```

### Render (API + backup collector)

Already runs `run_scheduler(90)` on startup.  
**Free tier sleeps after ~15 min idle** — add external cron:

1. [cron-job.org](https://cron-job.org) (free)
2. Every **10 minutes**: `GET https://london-runner-api.onrender.com/`
3. Every **90 minutes**: `POST https://london-runner-api.onrender.com/collector/run`

> **Tip:** 로컬 데몬 + Render 동시 실행 시 row 2배 빠름. **하나만** 쓰는 것을 권장.

---

## 30-day volume estimate

| Parameter | Value |
|-----------|-------|
| Signal-near stops / cycle | ~250–350 |
| Cycle interval | 90 min |
| Cycles in 30 days | ~480 |
| **Estimated rows** | **~120,000–170,000** |
| DB size | ~30–60 MB (Supabase Free OK) |

---

## Monitor

```bash
tail -f logs/collector.log
```

Supabase:

```sql
SELECT COUNT(*) AS total,
       COUNT(DISTINCT stop_id) AS unique_stops,
       MAX(observed_at) AS last_seen
FROM bus_signal_observations;
```

Validation: `supabase_verify_duplicates.sql` (query 10)

---

## Stop

```bash
kill $(cat logs/collector.pid)
```

---

## Cost

**£0/month** — TfL + OSM + Supabase Free + Fusion
