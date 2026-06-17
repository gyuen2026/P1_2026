-- ============================================================
-- bus_signal_observations 검증 SQL
-- 목적: "중복"이 같은 정류장·다른 시각(정상)인지 vs 진짜 중복(문제)인지 구분
-- ============================================================


-- ── 1) 요약 ─────────────────────────────────────────────────
SELECT
  COUNT(*) AS total_rows,
  COUNT(DISTINCT stop_id) AS unique_stops,
  COUNT(*) - COUNT(DISTINCT stop_id) AS extra_rows_vs_unique_stops,
  MIN(observed_at) AS first_observed,
  MAX(observed_at) AS last_observed,
  ROUND(
    EXTRACT(EPOCH FROM (MAX(observed_at) - MIN(observed_at))) / 60.0,
    1
  ) AS span_minutes
FROM bus_signal_observations;


-- ── 2) 정상 vs 문제 중복 분류 ───────────────────────────────
-- 정상: 같은 stop_id + 다른 observed_at (시계열)
-- 문제: 같은 stop_id + 같은 observed_at (초 단위까지 동일)
WITH dup_exact AS (
  SELECT stop_id, observed_at, COUNT(*) AS cnt
  FROM bus_signal_observations
  GROUP BY stop_id, observed_at
  HAVING COUNT(*) > 1
),
dup_same_minute AS (
  SELECT
    stop_id,
    date_trunc('minute', observed_at) AS obs_minute,
    COUNT(*) AS cnt
  FROM bus_signal_observations
  GROUP BY stop_id, date_trunc('minute', observed_at)
  HAVING COUNT(*) > 1
),
stop_visits AS (
  SELECT
    stop_id,
    COUNT(*) AS visit_count,
    COUNT(DISTINCT observed_at) AS distinct_timestamps,
    COUNT(DISTINCT date_trunc('minute', observed_at)) AS distinct_minutes,
    MIN(observed_at) AS first_at,
    MAX(observed_at) AS last_at
  FROM bus_signal_observations
  GROUP BY stop_id
)
SELECT
  (SELECT COUNT(*) FROM dup_exact) AS bad_rows_exact_same_timestamp,
  (SELECT COALESCE(SUM(cnt - 1), 0) FROM dup_exact) AS bad_extra_rows_exact,
  (SELECT COUNT(*) FROM dup_same_minute) AS same_stop_same_minute_groups,
  (SELECT COALESCE(SUM(cnt - 1), 0) FROM dup_same_minute) AS extra_rows_within_same_minute,
  (SELECT COUNT(*) FROM stop_visits WHERE visit_count > 1) AS stops_seen_more_than_once,
  (SELECT COUNT(*) FROM stop_visits
   WHERE visit_count > 1 AND distinct_timestamps = visit_count) AS stops_all_different_times_OK,
  (SELECT COUNT(*) FROM stop_visits
   WHERE visit_count > distinct_timestamps) AS stops_with_true_duplicate_PROBLEM;


-- ── 3) 해석 가이드 (결과 읽는 법) ─────────────────────────────
-- bad_rows_exact_same_timestamp = 0  → 완벽 (같은 초 중복 없음)
-- stops_all_different_times_OK ≈ stops_seen_more_than_once → 전부 시계열 중복 (정상)
-- stops_with_true_duplicate_PROBLEM > 0 → 같은 시각 중복 insert (조사 필요)


-- ── 4) 정류장별 방문 횟수 + 시간 간격 (정상 시계열 확인) ─────
SELECT
  stop_id,
  MAX(stop_name) AS stop_name,
  COUNT(*) AS visits,
  COUNT(DISTINCT observed_at) AS distinct_times,
  MIN(observed_at) AS first_seen,
  MAX(observed_at) AS last_seen,
  ROUND(
    EXTRACT(EPOCH FROM (MAX(observed_at) - MIN(observed_at))) / 60.0,
    1
  ) AS span_minutes,
  CASE
    WHEN COUNT(*) = COUNT(DISTINCT observed_at) THEN 'OK — all different times'
    ELSE 'CHECK — has same-timestamp dup'
  END AS status
FROM bus_signal_observations
GROUP BY stop_id
HAVING COUNT(*) > 1
ORDER BY visits DESC, span_minutes DESC
LIMIT 30;


-- ── 5) 진짜 중복만 (같은 stop_id + 같은 observed_at) ─────────
SELECT
  stop_id,
  MAX(stop_name) AS stop_name,
  observed_at,
  COUNT(*) AS duplicate_count,
  ARRAY_AGG(id ORDER BY created_at) AS row_ids
FROM bus_signal_observations
GROUP BY stop_id, observed_at
HAVING COUNT(*) > 1
ORDER BY duplicate_count DESC, observed_at DESC;


-- ── 6) 같은 정류장·같은 분(minute) — Render+로컬 동시 수집 의심 ─
SELECT
  stop_id,
  MAX(stop_name) AS stop_name,
  date_trunc('minute', observed_at) AS obs_minute,
  COUNT(*) AS rows_in_minute,
  MIN(observed_at) AS first_in_minute,
  MAX(observed_at) AS last_in_minute,
  CASE
    WHEN COUNT(DISTINCT observed_at) = COUNT(*) THEN 'OK — same minute, different seconds'
    ELSE 'WARN — identical timestamp in same minute'
  END AS status
FROM bus_signal_observations
GROUP BY stop_id, date_trunc('minute', observed_at)
HAVING COUNT(*) > 1
ORDER BY rows_in_minute DESC, obs_minute DESC
LIMIT 30;


-- ── 7) 시간대별 수집 분포 (언제 몰려 쌓였는지) ───────────────
SELECT
  date_trunc('minute', observed_at) AS collection_minute,
  COUNT(*) AS rows_inserted,
  COUNT(DISTINCT stop_id) AS unique_stops_that_minute
FROM bus_signal_observations
GROUP BY 1
ORDER BY 1 DESC
LIMIT 30;


-- ── 8) hour_of_day / day_of_week 패턴 (시계열 학습용 정상 여부) ─
SELECT
  stop_id,
  MAX(stop_name) AS stop_name,
  hour_of_day,
  day_of_week,
  COUNT(*) AS rows,
  COUNT(DISTINCT observed_at) AS distinct_observed_at,
  MIN(observed_at) AS first_at,
  MAX(observed_at) AS last_at
FROM bus_signal_observations
GROUP BY stop_id, hour_of_day, day_of_week
HAVING COUNT(*) > 1
ORDER BY rows DESC
LIMIT 20;


-- ── 9) 데이터 품질 ───────────────────────────────────────────
SELECT
  COUNT(*) AS total,
  COUNT(*) FILTER (WHERE delay_sec > 0) AS has_delay,
  ROUND(100.0 * COUNT(*) FILTER (WHERE delay_sec > 0) / NULLIF(COUNT(*), 0), 1) AS pct_delayed,
  COUNT(*) FILTER (WHERE lat IS NOT NULL AND lat != 0) AS has_coords,
  COUNT(*) FILTER (WHERE stop_name IS NOT NULL AND stop_name != '') AS has_name
FROM bus_signal_observations;


-- ── 10) 한 줄 판정 ───────────────────────────────────────────
SELECT
  CASE
    WHEN EXISTS (
      SELECT 1 FROM bus_signal_observations
      GROUP BY stop_id, observed_at HAVING COUNT(*) > 1
    ) THEN 'PROBLEM — exact duplicate rows exist (same stop + same observed_at)'
    WHEN (
      SELECT COUNT(*) FROM (
        SELECT stop_id FROM bus_signal_observations
        GROUP BY stop_id HAVING COUNT(*) > 1
      ) t
    ) > 0 THEN 'OK — same stop appears multiple times at DIFFERENT observed_at (time-series)'
    ELSE 'OK — each stop appears once'
  END AS verdict;
