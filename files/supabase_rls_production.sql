/* PRODUCTION RLS — P1_2026
   Fixes "Table publicly accessible" alert.
   Render uses service_role (bypasses RLS). Flutter never hits Supabase directly. */

ALTER TABLE IF EXISTS bus_signal_observations ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS signal_patterns ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crowd_signal_reports ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "allow_insert_observations" ON bus_signal_observations;
DROP POLICY IF EXISTS "allow_insert_patterns" ON signal_patterns;
DROP POLICY IF EXISTS "allow_insert_crowd" ON crowd_signal_reports;
DROP POLICY IF EXISTS "public_read_observations" ON bus_signal_observations;
DROP POLICY IF EXISTS "public_read_patterns" ON signal_patterns;
DROP POLICY IF EXISTS "public_read_crowd" ON crowd_signal_reports;

SELECT tablename, rowsecurity
FROM pg_tables
WHERE schemaname = 'public'
  AND tablename IN (
    'bus_signal_observations',
    'signal_patterns',
    'crowd_signal_reports'
  );
