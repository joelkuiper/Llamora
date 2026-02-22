-- Add ON DELETE CASCADE FK from search_history.user_id -> users.id.
-- SQLite requires table rebuild to add a foreign key constraint.

BEGIN IMMEDIATE;

DROP INDEX IF EXISTS idx_search_history_user_last_used;

ALTER TABLE search_history RENAME TO search_history__old;

CREATE TABLE search_history (
    user_id TEXT NOT NULL,
    query_hash BLOB(32) NOT NULL,
    query_nonce BLOB(24) NOT NULL,
    query_ct BLOB NOT NULL,
    alg TEXT NOT NULL,
    usage_count INTEGER DEFAULT 1,
    last_used TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(user_id, query_hash),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- Discard orphan rows created before account-delete cleanup existed.
INSERT INTO search_history (
    user_id, query_hash, query_nonce, query_ct, alg, usage_count, last_used
)
SELECT
    sh.user_id,
    sh.query_hash,
    sh.query_nonce,
    sh.query_ct,
    sh.alg,
    sh.usage_count,
    sh.last_used
FROM search_history__old AS sh
JOIN users AS u ON u.id = sh.user_id;

DROP TABLE search_history__old;

CREATE INDEX IF NOT EXISTS idx_search_history_user_last_used
    ON search_history(user_id, last_used DESC);

COMMIT;
