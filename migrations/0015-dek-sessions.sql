BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS dek_sessions (
    sid        TEXT    PRIMARY KEY,
    ciphertext BLOB   NOT NULL,
    expires_at INTEGER NOT NULL
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_dek_sessions_expires
    ON dek_sessions(expires_at);

COMMIT;
