-- ============================================================
-- London Runner — 전체 데이터 초기화 (테이블 구조 유지)
-- 30일 수집 시작 전 Supabase SQL Editor에서 실행
-- ============================================================

-- 1) 관측·학습·크라우드 데이터 삭제
TRUNCATE TABLE bus_signal_observations;
TRUNCATE TABLE signal_patterns;
TRUNCATE TABLE crowd_signal_reports;

-- TRUNCATE 권한 오류 시 아래 DELETE 사용:
-- DELETE FROM bus_signal_observations;
-- DELETE FROM signal_patterns;
-- DELETE FROM crowd_signal_reports;


-- 2) 삭제 확인 (모두 0이어야 함)
SELECT 'bus_signal_observations' AS table_name, COUNT(*) AS rows
FROM bus_signal_observations
UNION ALL
SELECT 'signal_patterns', COUNT(*) FROM signal_patterns
UNION ALL
SELECT 'crowd_signal_reports', COUNT(*) FROM crowd_signal_reports;

-- 3) 테이블 구조는 그대로인지 확인
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN (
    'bus_signal_observations',
    'signal_patterns',
    'crowd_signal_reports'
  )
ORDER BY table_name;

-- rows = 0, tables = 3 → 초기화 완료, 수집 시작 OK
