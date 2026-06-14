-- Supabase: allow backend inserts (fixes 0 rows when RLS blocks writes)
-- Run in SQL Editor if Render logs show "Insert failed" / permission errors

-- Option A (recommended for backend-only tables): disable RLS
ALTER TABLE bus_signal_observations DISABLE ROW LEVEL SECURITY;
ALTER TABLE signal_patterns DISABLE ROW LEVEL SECURITY;

-- Option B: keep RLS but allow all inserts/selects (less strict than disable)
-- ALTER TABLE bus_signal_observations ENABLE ROW LEVEL SECURITY;
-- DROP POLICY IF EXISTS "backend_insert" ON bus_signal_observations;
-- CREATE POLICY "backend_insert" ON bus_signal_observations FOR INSERT WITH CHECK (true);
-- CREATE POLICY "backend_select" ON bus_signal_observations FOR SELECT USING (true);

-- Verify row count after next collection cycle
-- SELECT COUNT(*) FROM bus_signal_observations;
