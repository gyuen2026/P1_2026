-- ============================================================
-- delay_sec 분포 확인 (수정 후 재수집 필요 — 기존 row는 대부분 0)
-- ============================================================

-- 1) 요약
SELECT
  COUNT(*) AS total_rows,
  COUNT(*) FILTER (WHERE delay_sec > 0) AS any_delay,
  COUNT(*) FILTER (WHERE delay_sec >= 10) AS delay_10s_plus,
  COUNT(*) FILTER (WHERE delay_sec >= 20) AS delay_20s_plus,
  COUNT(*) FILTER (WHERE delay_sec = 0) AS zero_delay,
  ROUND(AVG(delay_sec) FILTER (WHERE delay_sec > 0), 1) AS avg_when_delayed,
  ROUND(100.0 * COUNT(*) FILTER (WHERE delay_sec > 0) / NULLIF(COUNT(*), 0), 1) AS pct_delayed
FROM bus_signal_observations;


-- 2) 최근 수집분만 (재수집 후 observed_at 기준 필터 조정)
SELECT
  COUNT(*) AS recent_rows,
  COUNT(*) FILTER (WHERE delay_sec > 0) AS recent_delayed
FROM bus_signal_observations
WHERE observed_at > NOW() - INTERVAL '2 hours';


-- 3) 지연 TOP 10
SELECT stop_name, delay_sec, estimated_wait_sec, sample_count, observed_at
FROM bus_signal_observations
WHERE delay_sec >= 10
ORDER BY delay_sec DESC
LIMIT 10;
