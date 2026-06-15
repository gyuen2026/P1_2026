# 4-Day 24/7 Collection Plan

## Timeline

| Phase | Duration | What |
|-------|----------|------|
| **Setup** | **2 hours** | Env, Supabase, smoke test, start daemon |
| **Collection** | **4 days** | Daemon runs continuously until deadline |
| **Verify** | 30 min | Run SQL checks on Supabase |

---

## 2-Hour Setup Checklist

### 0:00 – 0:20 · Supabase + `.env`

1. Supabase → Settings → API → copy **Project URL** + **service_role** key (not anon).
2. `cp .env.example .env` and fill:
   - `TFL_APP_KEY`
   - `SUPABASE_URL`
   - `SUPABASE_KEY` = **service_role**
   - `OPENWEATHER_API_KEY` (optional for collection)
3. Run once in SQL editor if not done:
   - `supabase_enable_inserts.sql` (disable RLS)
   - `supabase_signal_schema.sql` (tables)

### 0:20 – 0:35 · Install + verify

```bash
cd files
chmod +x scripts/setup_collector.sh
./scripts/setup_collector.sh
```

### 0:35 – 0:50 · Smoke test (10 stops)

```bash
python3 scripts/collect_daemon.py --once --limit 10
```

Supabase → Table Editor → `bus_signal_observations` → newest rows should have non-null `stop_name`, `lat`, `lon`, `delay_sec`.

### 0:50 – 1:30 · One full cycle (optional but recommended)

```bash
python3 scripts/collect_daemon.py --once
```

Expect ~4,000+ rows, 60–90 minutes. You can start the 4-day daemon in parallel after smoke test passes.

### 1:30 – 2:00 · Start 4-day daemon

**Mac (keep machine awake):**

```bash
cd files
caffeinate -dims nohup python3 scripts/collect_daemon.py --days 4 --interval 90 >> logs/collector.out 2>&1 &
echo $! > logs/collector.pid
```

**Monitor:**

```bash
tail -f logs/collector.log
```

**Stop early:**

```bash
kill $(cat logs/collector.pid)
```

---

## 4-Day Collection Math

| Parameter | Value |
|-----------|-------|
| Stops per cycle | ~4,000–4,500 |
| Cycle duration | ~60–90 min |
| Interval between starts | 90 min (default) |
| Cycles in 96 h | ~64 |
| **Total new rows (approx)** | **~256,000** |

Rows are **appended** each cycle (time-series). `signal_patterns` is upserted hourly slot aggregates.

---

## After 4 Days — Verify

Run `supabase_check_delays.sql`:

```sql
SELECT COUNT(*) FILTER (WHERE delay_sec >= 10) FROM bus_signal_observations;
```

Expect **25–40%** of rows with `delay_sec >= 10` (time-of-day dependent).

---

## Cost (this plan only)

| Item | Cost |
|------|------|
| TfL API | **£0** |
| OSM Overpass | **£0** |
| Supabase Free | **£0** (~256k rows ≈ 50–80 MB) |
| **Free fusion tier (G+V+P+N+O+H+C)** | **£0** — matches paid traffic API accuracy |
| HERE/TomTom | **Not required** |

**Total: £0/month**

Run crowd schema once in Supabase SQL editor: `supabase_crowd_schema.sql`

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| All `delay_sec = 0` | Deploy latest code (`calc_delay_detail` fix) |
| Zero inserts | Use **service_role** key; run `supabase_enable_inserts.sql` |
| Mac sleeps | Use `caffeinate -dims` or System Settings → prevent sleep |
| Cycle overlap | Daemon already waits; do not run two instances |
| TfL rate limit | Increase `BATCH_DELAY_SEC` in collector to 1.0 |
