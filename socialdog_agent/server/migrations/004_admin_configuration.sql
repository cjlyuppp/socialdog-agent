ALTER TABLE users ADD COLUMN permissions TEXT NOT NULL DEFAULT '[]';
ALTER TABLE question_bank_version ADD COLUMN name TEXT NOT NULL DEFAULT '';
CREATE TABLE question_bank_draft (
    id INTEGER PRIMARY KEY CHECK(id=1),
    revision INTEGER NOT NULL,
    value TEXT NOT NULL
);
CREATE TABLE question_bank_active (
    id INTEGER PRIMARY KEY CHECK(id=1),
    version_id TEXT NOT NULL REFERENCES question_bank_version(id)
);
ALTER TABLE research_idea ADD COLUMN form_snapshot TEXT;
ALTER TABLE research_idea ADD COLUMN criteria_snapshot TEXT NOT NULL DEFAULT '';
ALTER TABLE attempts ADD COLUMN anonymous_ab INTEGER NOT NULL DEFAULT 0;
ALTER TABLE attempts ADD COLUMN answer_order TEXT;
