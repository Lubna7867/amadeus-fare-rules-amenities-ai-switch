-- Flight amenities: per-item cache (one row per amenity text x language x mode)
-- and fare-family name labels. New languages need no schema change.
CREATE TABLE IF NOT EXISTS amenity_cache (
    cache_key        TEXT NOT NULL,
    lang             TEXT NOT NULL,
    mode             TEXT NOT NULL,              -- summary | translate
    source_text      TEXT NOT NULL,
    item             JSONB NOT NULL,
    provider         TEXT,
    model            TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_accessed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    hit_count        BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (cache_key, lang, mode)
);
CREATE INDEX IF NOT EXISTS idx_amenity_cache_last_accessed ON amenity_cache (last_accessed_at);

CREATE TABLE IF NOT EXISTS fare_name_cache (
    code             TEXT NOT NULL,
    lang             TEXT NOT NULL,
    label            TEXT NOT NULL,
    provider         TEXT,
    model            TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_accessed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    hit_count        BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (code, lang)
);
