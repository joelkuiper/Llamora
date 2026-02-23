-- Llamora schema

-- Users -------------------------------------------------------------------

CREATE TABLE users (
    id             TEXT    PRIMARY KEY,
    username       TEXT    UNIQUE NOT NULL CHECK(length(username) <= 30),
    password_hash  TEXT    NOT NULL,
    dek_pw_salt    BLOB   NOT NULL,
    dek_pw_nonce   BLOB   NOT NULL,
    dek_pw_cipher  BLOB   NOT NULL,
    dek_rc_salt    BLOB   NOT NULL,
    dek_rc_nonce   BLOB   NOT NULL,
    dek_rc_cipher  BLOB   NOT NULL,
    current_epoch  INTEGER NOT NULL DEFAULT 1,
    state          TEXT,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE key_epochs (
    user_id         TEXT    NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    epoch           INTEGER NOT NULL,
    suite           TEXT    NOT NULL,
    pw_salt         BLOB   NOT NULL,
    pw_nonce        BLOB   NOT NULL,
    pw_cipher       BLOB   NOT NULL,
    rc_salt         BLOB,
    rc_nonce        BLOB,
    rc_cipher       BLOB,
    prev_dek_nonce  BLOB,
    prev_dek_cipher BLOB,
    retired_at      TIMESTAMP,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, epoch)
);

-- Entries -----------------------------------------------------------------

CREATE TABLE entries (
    id              TEXT      PRIMARY KEY,
    user_id         TEXT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role            TEXT      NOT NULL,
    reply_to        TEXT,
    nonce           BLOB      NOT NULL,
    ciphertext      BLOB      NOT NULL,
    alg             BLOB      NOT NULL,
    prompt_tokens   INTEGER   DEFAULT 0,
    digest          TEXT      NOT NULL,
    digest_version  INTEGER   NOT NULL DEFAULT 2,
    flags           TEXT      NOT NULL DEFAULT '',
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_date    TEXT      DEFAULT (date('now')),
    updated_at      TIMESTAMP
);

CREATE INDEX idx_entries_user_date       ON entries(user_id, created_date);
CREATE INDEX idx_entries_user_created_at ON entries(user_id, created_at DESC, id DESC);
CREATE INDEX idx_entries_user_reply_to   ON entries(user_id, reply_to);
CREATE INDEX idx_entries_user_date_flags ON entries(user_id, created_date, flags);

CREATE TRIGGER trg_entries_updated_at_insert
AFTER INSERT ON entries FOR EACH ROW WHEN NEW.updated_at IS NULL
BEGIN
    UPDATE entries SET updated_at = CURRENT_TIMESTAMP
    WHERE id = NEW.id;
END;

CREATE TRIGGER trg_entries_updated_at
AFTER UPDATE ON entries FOR EACH ROW WHEN OLD.updated_at IS NEW.updated_at
BEGIN
    UPDATE entries SET updated_at = CURRENT_TIMESTAMP
    WHERE id = NEW.id;
END;

CREATE TRIGGER trg_entries_digest_not_null_insert
BEFORE INSERT ON entries FOR EACH ROW
WHEN NEW.digest IS NULL OR TRIM(NEW.digest) = ''
BEGIN SELECT RAISE(ABORT, 'entries.digest must not be null'); END;

CREATE TRIGGER trg_entries_digest_not_null_update
BEFORE UPDATE OF digest ON entries FOR EACH ROW
WHEN NEW.digest IS NULL OR TRIM(NEW.digest) = ''
BEGIN SELECT RAISE(ABORT, 'entries.digest must not be null'); END;

CREATE TRIGGER trg_entries_digest_version_not_null_insert
BEFORE INSERT ON entries FOR EACH ROW WHEN NEW.digest_version IS NULL
BEGIN SELECT RAISE(ABORT, 'entries.digest_version must not be null'); END;

CREATE TRIGGER trg_entries_digest_version_not_null_update
BEFORE UPDATE OF digest_version ON entries FOR EACH ROW
WHEN NEW.digest_version IS NULL
BEGIN SELECT RAISE(ABORT, 'entries.digest_version must not be null'); END;

-- Vectors (embedding chunks) ----------------------------------------------

CREATE TABLE vectors (
    id          TEXT      PRIMARY KEY,
    entry_id    TEXT      NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    user_id     TEXT      NOT NULL REFERENCES users(id)   ON DELETE CASCADE,
    chunk_index INTEGER   NOT NULL DEFAULT 0,
    dim         INTEGER   NOT NULL,
    dtype       TEXT      DEFAULT 'float32',
    nonce       BLOB      NOT NULL,
    ciphertext  BLOB      NOT NULL,
    alg         BLOB      NOT NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_vectors_user_id  ON vectors(user_id);
CREATE INDEX idx_vectors_entry_id ON vectors(entry_id);

-- Tags --------------------------------------------------------------------

CREATE TABLE tags (
    user_id    TEXT     NOT NULL,
    tag_hash   BLOB    NOT NULL,
    name_ct    BLOB    NOT NULL,
    name_nonce BLOB    NOT NULL,
    alg        TEXT    NOT NULL,
    seen       INTEGER DEFAULT 0,
    last_seen  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP,
    PRIMARY KEY (user_id, tag_hash)
);

CREATE INDEX idx_tags_user_seen_positive
    ON tags(user_id, seen DESC, tag_hash) WHERE seen > 0;

CREATE TRIGGER trg_tags_updated_at_insert
AFTER INSERT ON tags FOR EACH ROW WHEN NEW.updated_at IS NULL
BEGIN
    UPDATE tags SET updated_at = CURRENT_TIMESTAMP
    WHERE user_id = NEW.user_id AND tag_hash = NEW.tag_hash;
END;

CREATE TRIGGER trg_tags_updated_at
AFTER UPDATE ON tags FOR EACH ROW WHEN OLD.updated_at IS NEW.updated_at
BEGIN
    UPDATE tags SET updated_at = CURRENT_TIMESTAMP
    WHERE user_id = NEW.user_id AND tag_hash = NEW.tag_hash;
END;

CREATE TABLE tag_entry_xref (
    user_id  TEXT NOT NULL,
    tag_hash BLOB NOT NULL,
    entry_id TEXT NOT NULL,
    ulid     TEXT NOT NULL,
    PRIMARY KEY (user_id, tag_hash, entry_id),
    FOREIGN KEY (user_id, tag_hash) REFERENCES tags(user_id, tag_hash) ON DELETE CASCADE,
    FOREIGN KEY (entry_id)          REFERENCES entries(id)             ON DELETE CASCADE
);

CREATE INDEX idx_tag_xref_hash  ON tag_entry_xref(user_id, tag_hash);
CREATE INDEX idx_tag_xref_entry ON tag_entry_xref(user_id, entry_id);
CREATE INDEX idx_tag_xref_ulid  ON tag_entry_xref(user_id, tag_hash, ulid DESC);

CREATE TRIGGER trg_tag_xref_delete
AFTER DELETE ON tag_entry_xref FOR EACH ROW
BEGIN
    UPDATE tags SET
        seen = (SELECT COUNT(*) FROM tag_entry_xref
                WHERE user_id = OLD.user_id AND tag_hash = OLD.tag_hash),
        last_seen = (SELECT MAX(e.created_at)
                     FROM tag_entry_xref x
                     JOIN entries e ON e.id = x.entry_id
                     WHERE x.user_id = OLD.user_id AND x.tag_hash = OLD.tag_hash)
    WHERE user_id = OLD.user_id AND tag_hash = OLD.tag_hash;
END;

-- Search history ----------------------------------------------------------

CREATE TABLE search_history (
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    query_hash  BLOB NOT NULL,
    query_nonce BLOB NOT NULL,
    query_ct    BLOB NOT NULL,
    alg         TEXT NOT NULL,
    usage_count INTEGER   DEFAULT 1,
    last_used   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, query_hash)
);

CREATE INDEX idx_search_history_last_used
    ON search_history(user_id, last_used DESC);

-- Lockbox (encrypted KV) --------------------------------------------------

CREATE TABLE lockbox (
    namespace  TEXT    NOT NULL,
    key        TEXT    NOT NULL,
    value      BLOB   NOT NULL,
    alg        TEXT   NOT NULL DEFAULT 'xchacha20poly1305_ietf/argon2id_moderate/hmac_sha256_v2',
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (namespace, key)
) WITHOUT ROWID;

-- TTL store (sessions, rate limiting) -------------------------------------

CREATE TABLE ttl_store (
    namespace  TEXT    NOT NULL,
    key        TEXT    NOT NULL,
    value      BLOB   NOT NULL,
    expires_at INTEGER NOT NULL,
    PRIMARY KEY (namespace, key)
) WITHOUT ROWID;

CREATE INDEX idx_ttl_store_expires ON ttl_store(expires_at);
