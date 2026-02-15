-- Replace the single-column reply_to index with a composite index that
-- matches how the column is actually queried (always with user_id).
DROP INDEX IF EXISTS idx_entries_reply_to;
CREATE INDEX IF NOT EXISTS idx_entries_user_reply_to
    ON entries(user_id, reply_to);
