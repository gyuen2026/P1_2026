-- Run in Supabase SQL Editor AFTER backfilling NULL rows.
-- Step 1: fix existing NULLs (optional cleanup)
-- UPDATE bus_signal_observations SET stop_name = 'Unknown Stop' WHERE stop_name IS NULL;
-- DELETE FROM bus_signal_observations WHERE lat IS NULL OR lon IS NULL;

-- Step 2: add NOT NULL constraints (zero-NULL policy)
ALTER TABLE bus_signal_observations
    ALTER COLUMN stop_name SET NOT NULL,
    ALTER COLUMN lat SET NOT NULL,
    ALTER COLUMN lon SET NOT NULL,
    ALTER COLUMN hour_of_day SET NOT NULL,
    ALTER COLUMN day_of_week SET NOT NULL;

-- Step 3: sensible defaults for numeric fields
ALTER TABLE bus_signal_observations
    ALTER COLUMN delay_sec SET DEFAULT 0,
    ALTER COLUMN estimated_cycle_sec SET DEFAULT 90,
    ALTER COLUMN estimated_wait_sec SET DEFAULT 30,
    ALTER COLUMN sample_count SET DEFAULT 1;

-- Step 4: validate day_of_week range (1=Mon .. 7=Sun, matches Python isoweekday)
ALTER TABLE bus_signal_observations
    ADD CONSTRAINT chk_hour_of_day CHECK (hour_of_day BETWEEN 0 AND 23),
    ADD CONSTRAINT chk_day_of_week CHECK (day_of_week BETWEEN 1 AND 7);
