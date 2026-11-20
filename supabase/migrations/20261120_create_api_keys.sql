-- Enable pgcrypto for gen_random_uuid
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- API Keys table
CREATE TABLE api_keys (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         TEXT NOT NULL,              -- Roblox user ID (from OAuth session)
    key_hash        TEXT NOT NULL,              -- bcrypt hash of the full key
    key_prefix      TEXT NOT NULL,              -- First 8 chars for identification (e.g. "3drbx_abc1")
    name            TEXT NOT NULL,              -- User-chosen label ("My bot", "CI pipeline")
    scopes          TEXT[] NOT NULL DEFAULT '{}', -- e.g. '{catalog,avatar}'
    expires_at      TIMESTAMPTZ NOT NULL,       -- Required, no NULL
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at      TIMESTAMPTZ,                -- NULL = active
    last_used_at    TIMESTAMPTZ,                -- For analytics / stale key detection
    rate_limit_rpm  INT NOT NULL DEFAULT 60,    -- Requests per minute per key
    rate_limit_rph  INT NOT NULL DEFAULT 1000,  -- Requests per hour per key
    CONSTRAINT valid_scopes CHECK (
        scopes <@ ARRAY['catalog','avatar','admin']::TEXT[]
    )
);

-- Indexes
CREATE INDEX idx_api_keys_user_id ON api_keys(user_id);
CREATE INDEX idx_api_keys_prefix ON api_keys(key_prefix);
CREATE INDEX idx_api_keys_expires_at ON api_keys(expires_at) WHERE revoked_at IS NULL;

-- Row Level Security (RLS)
ALTER TABLE api_keys ENABLE ROW LEVEL SECURITY;

-- Users can only see/manage their own keys
CREATE POLICY "Users manage own keys" ON api_keys
    FOR ALL USING (user_id = current_setting('app.current_user_id', true))
    WITH CHECK (user_id = current_setting('app.current_user_id', true));

-- Service role (backend) bypasses RLS for validation
-- (handled by using service role key in backend, not anon key)