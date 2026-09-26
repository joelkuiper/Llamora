-- Image attachments -------------------------------------------------------
--
-- Files live on disk (IMAGES.path), encrypted with a per-image key that is
-- stored here wrapped by the user's DEK. A row with entry_id IS NULL is
-- either pending (uploaded, not yet sent: attached_at IS NULL) or orphaned
-- (its entry was deleted or it was removed from it: attached_at IS NOT NULL);
-- both are swept together with their files.

CREATE TABLE images (
    id           TEXT      PRIMARY KEY,
    user_id      TEXT      NOT NULL REFERENCES users(id)   ON DELETE CASCADE,
    entry_id     TEXT               REFERENCES entries(id) ON DELETE SET NULL,
    position     INTEGER   NOT NULL DEFAULT 0,
    key_nonce    BLOB      NOT NULL,
    key_cipher   BLOB      NOT NULL,
    meta_nonce   BLOB      NOT NULL,
    meta_cipher  BLOB      NOT NULL,
    alg          TEXT      NOT NULL,
    attached_at  TIMESTAMP,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Images of entries (day views): WHERE user_id = ? AND entry_id IN (...)
CREATE INDEX idx_images_entry    ON images(user_id, entry_id, position);
-- Pending and orphaned images, for the sweeper
CREATE INDEX idx_images_detached ON images(created_at) WHERE entry_id IS NULL;
