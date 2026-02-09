CREATE TABLE IF NOT EXISTS vectors_new (
    id TEXT PRIMARY KEY,
    entry_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    chunk_index INTEGER NOT NULL DEFAULT 0,
    dim INTEGER NOT NULL,
    nonce BLOB NOT NULL,
    ciphertext BLOB NOT NULL,
    alg BLOB NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (entry_id) REFERENCES entries(id) ON DELETE CASCADE
);

INSERT INTO vectors_new (id, entry_id, user_id, chunk_index, dim, nonce, ciphertext, alg, created_at)
SELECT id, id, user_id, 0, dim, nonce, ciphertext, alg, created_at
FROM vectors;

DROP TABLE vectors;

ALTER TABLE vectors_new RENAME TO vectors;

CREATE INDEX IF NOT EXISTS idx_vectors_user_id ON vectors(user_id);
CREATE INDEX IF NOT EXISTS idx_vectors_entry_id ON vectors(entry_id);
