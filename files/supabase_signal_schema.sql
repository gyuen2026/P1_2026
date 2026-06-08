-- 버스 도착 지연 데이터 (신호 패턴 학습용)
CREATE TABLE bus_signal_observations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    stop_id         TEXT NOT NULL,          -- 버스 정류장 ID
    stop_name       TEXT,
    lat             FLOAT,
    lon             FLOAT,
    observed_at     TIMESTAMPTZ NOT NULL,   -- 관측 시간
    hour_of_day     INT,                    -- 0~23 (시간대 패턴용)
    day_of_week     INT,                    -- 0=월 ~ 6=일
    delay_sec       FLOAT,                  -- 버스 지연 시간(초)
    estimated_cycle_sec FLOAT,             -- 추정 신호 사이클
    estimated_wait_sec  FLOAT,             -- 추정 신호 대기
    sample_count    INT DEFAULT 1,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- 신호 패턴 요약 테이블 (학습 결과)
CREATE TABLE signal_patterns (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    stop_id         TEXT NOT NULL,
    hour_of_day     INT NOT NULL,           -- 시간대
    day_of_week     INT NOT NULL,           -- 요일
    avg_delay_sec   FLOAT,                  -- 평균 지연
    avg_cycle_sec   FLOAT,                  -- 평균 사이클
    avg_wait_sec    FLOAT,                  -- 평균 대기
    green_probability FLOAT,               -- 초록불 확률
    observation_count INT DEFAULT 0,       -- 누적 관측 수
    last_updated    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(stop_id, hour_of_day, day_of_week)
);

-- 인덱스
CREATE INDEX idx_obs_stop_id ON bus_signal_observations(stop_id);
CREATE INDEX idx_obs_time ON bus_signal_observations(observed_at DESC);
CREATE INDEX idx_obs_hour ON bus_signal_observations(hour_of_day, day_of_week);
CREATE INDEX idx_pattern_stop ON signal_patterns(stop_id, hour_of_day, day_of_week);
