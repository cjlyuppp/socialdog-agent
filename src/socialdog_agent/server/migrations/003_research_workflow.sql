ALTER TABLE users ADD COLUMN evaluation_reserve INTEGER NOT NULL DEFAULT 0 CHECK(evaluation_reserve>=0);
ALTER TABLE users ADD COLUMN notice_version INTEGER NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN notice_accepted_at TEXT;
CREATE TABLE question_bank_version (
 id TEXT PRIMARY KEY, cutoff_T TEXT, threshold_N INTEGER, dataset_version TEXT,
 questions TEXT NOT NULL, legacy INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
ALTER TABLE attempts ADD COLUMN bank_version_id TEXT REFERENCES question_bank_version(id);
CREATE TABLE assignment (
 id TEXT PRIMARY KEY, question_id TEXT NOT NULL, bank_version_id TEXT NOT NULL REFERENCES question_bank_version(id),
 user_id TEXT NOT NULL REFERENCES users(id), assigned_by TEXT NOT NULL REFERENCES users(id),
 due_at TEXT, created_at TEXT NOT NULL, attempt_id TEXT UNIQUE REFERENCES attempts(id),
 UNIQUE(question_id, bank_version_id, user_id)
);
CREATE TABLE feedback_issue (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), source TEXT NOT NULL CHECK(source IN ('evaluation','chat')),
 turn_id TEXT NOT NULL REFERENCES turns(id), attempt_id TEXT REFERENCES attempts(id),
 question TEXT NOT NULL, model_answer TEXT NOT NULL, evidence_snapshot TEXT NOT NULL,
 stage TEXT NOT NULL CHECK(stage IN ('retrieval','definition','calculation','reasoning','conclusion','other')),
 description TEXT NOT NULL, correct_approach TEXT NOT NULL, evidence_refs TEXT NOT NULL,
 review_status TEXT NOT NULL DEFAULT 'pending' CHECK(review_status IN ('pending','confirmed','rejected','discuss')),
 review_note TEXT NOT NULL DEFAULT '', reviewed_by TEXT REFERENCES users(id), reviewed_at TEXT, created_at TEXT NOT NULL
);
CREATE INDEX feedback_queue ON feedback_issue(review_status,source,stage,user_id);
CREATE TABLE research_idea (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), question TEXT NOT NULL,
 data_used TEXT NOT NULL, findings TEXT NOT NULL, next_steps TEXT NOT NULL, linked_conversations TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','feedback_given','invited','not_suitable')),
 reviewer_feedback TEXT NOT NULL DEFAULT '', reviewed_by TEXT REFERENCES users(id),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
UPDATE settings SET value=json_set(value, '$.anonymous_ab_evaluation', json('false'));
