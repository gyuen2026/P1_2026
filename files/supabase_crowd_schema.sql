-- Crowd signal reports (free accuracy boost — no paid API)
CREATE TABLE IF NOT EXISTS crowd_signal_reports (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lat             FLOAT NOT NULL,
    lon             FLOAT NOT NULL,
    stop_id         TEXT,
    reported_color  TEXT NOT NULL,   -- GREEN, RED, AMBER, WAITING
    waited_sec      FLOAT DEFAULT 0,
    reported_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_crowd_reported_at ON crowd_signal_reports(reported_at DESC);
CREATE INDEX IF NOT EXISTS idx_crowd_lat_lon ON crowd_signal_reports(lat, lon);

ALTER TABLE crowd_signal_reports DISABLE ROW LEVEL SECURITY;
