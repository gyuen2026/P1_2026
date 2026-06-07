-- Supabase SQL Editor에 붙여넣고 실행하세요

-- 러닝 세션 기록 테이블
CREATE TABLE running_sessions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     TEXT NOT NULL,
    route_id    TEXT,
    distance_km FLOAT,
    duration_sec INT,
    avg_pace_min_per_km FLOAT,
    avg_heart_rate INT,
    calories_burned FLOAT,
    efficiency_score FLOAT,   -- 신호 없이 달린 비율 (0~100)
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- 인덱스 (사용자별 기록 조회 최적화)
CREATE INDEX idx_sessions_user_id ON running_sessions(user_id);
CREATE INDEX idx_sessions_created_at ON running_sessions(created_at DESC);
