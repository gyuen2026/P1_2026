-- London Runner — free-tier setup (run once in Supabase SQL Editor)

-- 1) Core tables (if not exists)
CREATE TABLE IF NOT EXISTS bus_signal_observations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    stop_id         TEXT NOT NULL,
    stop_name       TEXT,
    lat             FLOAT,
    lon             FLOAT,
    observed_at     TIMESTAMPTZ NOT NULL,
    hour_of_day     INT,
    day_of_week     INT,
    delay_sec       FLOAT,
    estimated_cycle_sec FLOAT,
    estimated_wait_sec  FLOAT,
    sample_count    INT DEFAULT 1,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS signal_patterns (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    stop_id         TEXT NOT NULL,
    hour_of_day     INT NOT NULL,
    day_of_week     INT NOT NULL,
    avg_delay_sec   FLOAT,
    avg_cycle_sec   FLOAT,
    avg_wait_sec    FLOAT,
    green_probability FLOAT,
    observation_count INT DEFAULT 0,
    last_updated    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(stop_id, hour_of_day, day_of_week)
);

-- 2) Crowd reports (free accuracy boost)
CREATE TABLE IF NOT EXISTS crowd_signal_reports (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lat             FLOAT NOT NULL,
    lon             FLOAT NOT NULL,
    stop_id         TEXT,
    reported_color  TEXT NOT NULL,
    waited_sec      FLOAT DEFAULT 0,
    reported_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- 3) Indexes
CREATE INDEX IF NOT EXISTS idx_obs_stop_id ON bus_signal_observations(stop_id);
CREATE INDEX IF NOT EXISTS idx_obs_time ON bus_signal_observations(observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_obs_hour ON bus_signal_observations(hour_of_day, day_of_week);
CREATE INDEX IF NOT EXISTS idx_pattern_stop ON signal_patterns(stop_id, hour_of_day, day_of_week);
CREATE INDEX IF NOT EXISTS idx_crowd_reported_at ON crowd_signal_reports(reported_at DESC);
CREATE INDEX IF NOT EXISTS idx_crowd_lat_lon ON crowd_signal_reports(lat, lon);

-- 4) Allow service_role inserts (disable RLS)
ALTER TABLE bus_signal_observations DISABLE ROW LEVEL SECURITY;
ALTER TABLE signal_patterns DISABLE ROW LEVEL SECURITY;
ALTER TABLE crowd_signal_reports DISABLE ROW LEVEL SECURITY;
