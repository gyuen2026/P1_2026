-- ============================================================
-- PRODUCTION RLS — fixes Supabase "Table publicly accessible" alert
-- Run in Supabase SQL Editor (P1_2026 project)
--
-- Your Flutter app talks to Render API only (service_role in .env).
-- Mobile clients never use anon key → enable RLS + no public policies = secure.
-- ============================================================

ALTER TABLE IF EXISTS bus_signal_observations ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS signal_patterns ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crowd_signal_reports ENABLE ROW LEVEL SECURITY;

-- Drop permissive dev policies if they exist
DROP POLICY IF EXISTS "allow_insert_observations" ON bus_signal_observations;
DROP POLICY IF EXISTS "allow_insert_patterns" ON signal_patterns;
DROP POLICY IF EXISTS "allow_insert_crowd" ON crowd_signal_reports;
DROP POLICY IF EXISTS "public_read_observations" ON bus_signal_observations;
DROP POLICY IF EXISTS "public_read_patterns" ON signal_patterns;
DROP POLICY IF EXISTS "public_read_crowd" ON crowd_signal_reports;

-- No policies for anon/authenticated → direct API access blocked.
-- service_role key (Render SUPABASE_KEY) bypasses RLS for collector + API.

-- Optional: read-only for authenticated users later (not needed for MVP)
-- CREATE POLICY "auth_read_crowd" ON crowd_signal_reports
--   FOR SELECT TO authenticated USING (true);

-- Verify
SELECT tablename, rowsecurity
FROM pg_tables
WHERE schemaname = 'public'
  AND tablename IN (
    'bus_signal_observations',
    'signal_patterns',
    'crowd_signal_reports'
  );
-- rowsecurity should be TRUE for all three
