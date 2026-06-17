-- ============================================================
-- Step 1: DIAGNOSE — run this first, read the counts
-- ============================================================
SELECT
  COUNT(*) AS total_rows,
  COUNT(*) FILTER (WHERE stop_name IS NULL) AS null_stop_name,
  COUNT(*) FILTER (WHERE lat IS NULL) AS null_lat,
  COUNT(*) FILTER (WHERE lon IS NULL) AS null_lon,
  COUNT(*) FILTER (WHERE hour_of_day IS NULL) AS null_hour,
  COUNT(*) FILTER (WHERE day_of_week IS NULL) AS null_dow
FROM bus_signal_observations;

-- Preview bad rows (optional)
-- SELECT id, stop_id, stop_name, lat, lon, hour_of_day, day_of_week, observed_at
-- FROM bus_signal_observations
-- WHERE stop_name IS NULL OR lat IS NULL OR lon IS NULL
--    OR hour_of_day IS NULL OR day_of_week IS NULL
-- LIMIT 20;


-- ============================================================
-- Step 2: CLEAN — delete rows that cannot satisfy NOT NULL
-- (Safest option; old collector inserted incomplete rows)
-- ============================================================
DELETE FROM bus_signal_observations
WHERE stop_name IS NULL
   OR lat IS NULL
   OR lon IS NULL
   OR hour_of_day IS NULL
   OR day_of_week IS NULL;

-- Also remove invalid 0,0 coordinates if any exist
DELETE FROM bus_signal_observations
WHERE lat = 0 AND lon = 0;


-- ============================================================
-- Step 3: VERIFY — all counts must be 0 before Step 4
-- ============================================================
SELECT
  COUNT(*) FILTER (WHERE lat IS NULL OR lon IS NULL OR stop_name IS NULL
                   OR hour_of_day IS NULL OR day_of_week IS NULL) AS remaining_bad_rows
FROM bus_signal_observations;
-- ↑ Must return 0


-- ============================================================
-- Step 4: ADD CONSTRAINTS — only run when remaining_bad_rows = 0
-- ============================================================
ALTER TABLE bus_signal_observations
    ALTER COLUMN stop_name SET NOT NULL,
    ALTER COLUMN lat SET NOT NULL,
    ALTER COLUMN lon SET NOT NULL,
    ALTER COLUMN hour_of_day SET NOT NULL,
    ALTER COLUMN day_of_week SET NOT NULL;

ALTER TABLE bus_signal_observations
    ALTER COLUMN delay_sec SET DEFAULT 0,
    ALTER COLUMN estimated_cycle_sec SET DEFAULT 90,
    ALTER COLUMN estimated_wait_sec SET DEFAULT 30,
    ALTER COLUMN sample_count SET DEFAULT 1;

ALTER TABLE bus_signal_observations
    ADD CONSTRAINT chk_hour_of_day CHECK (hour_of_day BETWEEN 0 AND 23),
    ADD CONSTRAINT chk_day_of_week CHECK (day_of_week BETWEEN 1 AND 7);
