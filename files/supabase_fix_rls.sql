-- ============================================================
-- FIX: 0 rows — RLS blocking inserts (error 42501)
-- Run this entire script in Supabase SQL Editor
-- ============================================================

-- Option A (recommended): disable RLS for collector tables
ALTER TABLE IF EXISTS bus_signal_observations DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS signal_patterns DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crowd_signal_reports DISABLE ROW LEVEL SECURITY;

-- Option B: if you must keep RLS on, allow inserts (anon key)
DROP POLICY IF EXISTS "allow_insert_observations" ON bus_signal_observations;
DROP POLICY IF EXISTS "allow_insert_patterns" ON signal_patterns;
DROP POLICY IF EXISTS "allow_insert_crowd" ON crowd_signal_reports;

CREATE POLICY "allow_insert_observations" ON bus_signal_observations
  FOR INSERT WITH CHECK (true);

CREATE POLICY "allow_insert_patterns" ON signal_patterns
  FOR ALL USING (true) WITH CHECK (true);

CREATE POLICY "allow_insert_crowd" ON crowd_signal_reports
  FOR INSERT WITH CHECK (true);

-- Verify RLS status
SELECT tablename, rowsecurity
FROM pg_tables
WHERE schemaname = 'public'
  AND tablename IN (
    'bus_signal_observations',
    'signal_patterns',
    'crowd_signal_reports'
  );

-- rowsecurity = false → inserts work with any key
-- rowsecurity = true  → need policies OR service_role key in .env
