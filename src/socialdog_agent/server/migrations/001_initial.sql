CREATE TABLE IF NOT EXISTS users (
 id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, display_name TEXT NOT NULL,
 role TEXT NOT NULL CHECK(role IN ('admin','researcher')), daily_limit INTEGER NOT NULL,
 disabled INTEGER NOT NULL DEFAULT 0, password_hash TEXT, invite_hash TEXT UNIQUE,
 invite_expires_at TEXT, last_active_at TEXT
);
CREATE TABLE IF NOT EXISTS auth_sessions (
 token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
 csrf TEXT NOT NULL, expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS login_attempts (key TEXT PRIMARY KEY, count INTEGER NOT NULL, expires_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), title TEXT NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS turns (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), session_id TEXT REFERENCES sessions(id),
 question TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('running','ok','error')),
 created_at TEXT NOT NULL, job_id TEXT NOT NULL UNIQUE, result TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS jobs (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), session_id TEXT REFERENCES sessions(id),
 turn_id TEXT NOT NULL UNIQUE REFERENCES turns(id), status TEXT NOT NULL CHECK(status IN ('queued','running','ok','error')),
 created_at TEXT NOT NULL, quota_day TEXT NOT NULL, config TEXT NOT NULL, events TEXT NOT NULL, error TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_job_per_user ON jobs(user_id) WHERE status IN ('queued','running');
CREATE INDEX IF NOT EXISTS jobs_quota ON jobs(quota_day,user_id);
CREATE TABLE IF NOT EXISTS model_calls (
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), started_at TEXT NOT NULL,
 finished_at TEXT, model TEXT, response_id TEXT, status TEXT NOT NULL,
 input_tokens INTEGER, output_tokens INTEGER, raw_usage TEXT
);
CREATE TABLE IF NOT EXISTS attempts (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), question_id TEXT NOT NULL,
 question_title TEXT NOT NULL, own_answer TEXT NOT NULL, turn_id TEXT NOT NULL UNIQUE REFERENCES turns(id), job_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ratings (
 id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL UNIQUE REFERENCES attempts(id),
 user_id TEXT NOT NULL REFERENCES users(id), value TEXT NOT NULL, created_at TEXT NOT NULL
);
