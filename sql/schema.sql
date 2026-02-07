CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT UNIQUE NOT NULL CHECK(length(username) <= {max_username_length}),
    password_hash TEXT NOT NULL,
    dek_pw_salt BLOB NOT NULL,
    dek_pw_nonce BLOB NOT NULL,
    dek_pw_cipher BLOB NOT NULL,
    dek_rc_salt BLOB NOT NULL,
    dek_rc_nonce BLOB NOT NULL,
    dek_rc_cipher BLOB NOT NULL,
    state TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS entries (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL,
    reply_to TEXT,
    nonce BLOB NOT NULL,
    ciphertext BLOB NOT NULL,
    alg BLOB NOT NULL,
    prompt_tokens INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_date TEXT DEFAULT (date('now')),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS vectors (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    dim INTEGER NOT NULL,
    nonce BLOB NOT NULL,
    ciphertext BLOB NOT NULL,
    alg BLOB NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (id) REFERENCES entries(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS tags (
    user_id TEXT NOT NULL,
    tag_hash BLOB(32) NOT NULL,
    name_ct BLOB NOT NULL,
    name_nonce BLOB(24) NOT NULL,
    alg TEXT NOT NULL,
    seen INTEGER DEFAULT 0,
    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(user_id, tag_hash)
);

CREATE TABLE IF NOT EXISTS tag_entry_xref (
    user_id TEXT NOT NULL,
    tag_hash BLOB(32) NOT NULL,
    entry_id TEXT NOT NULL,
    ulid TEXT NOT NULL,
    PRIMARY KEY(user_id, tag_hash, entry_id),
    FOREIGN KEY (user_id, tag_hash)
        REFERENCES tags(user_id, tag_hash) ON DELETE CASCADE,
    FOREIGN KEY (entry_id) REFERENCES entries(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS search_history (
    user_id TEXT NOT NULL,
    query_hash BLOB(32) NOT NULL,
    query_nonce BLOB(24) NOT NULL,
    query_ct BLOB NOT NULL,
    alg TEXT NOT NULL,
    usage_count INTEGER DEFAULT 1,
    last_used TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(user_id, query_hash)
);

CREATE INDEX IF NOT EXISTS idx_entries_user_date ON entries(user_id, created_date);
CREATE INDEX IF NOT EXISTS idx_entries_user_created_at
    ON entries(user_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_entries_reply_to ON entries(reply_to);
CREATE INDEX IF NOT EXISTS idx_vectors_user_id ON vectors(user_id);

CREATE INDEX IF NOT EXISTS idx_tag_entry_hash ON tag_entry_xref(user_id, tag_hash);
CREATE INDEX IF NOT EXISTS idx_tag_entry_entry ON tag_entry_xref(user_id, entry_id);
CREATE INDEX IF NOT EXISTS idx_tag_entry_ulid
    ON tag_entry_xref(user_id, tag_hash, ulid DESC);
CREATE INDEX IF NOT EXISTS idx_search_history_user_last_used
    ON search_history(user_id, last_used DESC);

CREATE TRIGGER IF NOT EXISTS trg_tag_entry_xref_delete
AFTER DELETE ON tag_entry_xref
FOR EACH ROW
BEGIN
    UPDATE tags
    SET seen = (
            SELECT COUNT(*)
            FROM tag_entry_xref x
            WHERE x.user_id = OLD.user_id AND x.tag_hash = OLD.tag_hash
        ),
        last_seen = (
            SELECT MAX(e.created_at)
            FROM tag_entry_xref x
            JOIN entries e
              ON e.user_id = x.user_id AND e.id = x.entry_id
            WHERE x.user_id = OLD.user_id AND x.tag_hash = OLD.tag_hash
        )
    WHERE user_id = OLD.user_id AND tag_hash = OLD.tag_hash;
END;
