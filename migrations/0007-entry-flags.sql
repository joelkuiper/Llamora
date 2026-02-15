ALTER TABLE entries ADD COLUMN flags TEXT NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_entries_user_date_flags
    ON entries(user_id, created_date, flags);
