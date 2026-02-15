ALTER TABLE users ADD COLUMN current_epoch INTEGER NOT NULL DEFAULT 1;

ALTER TABLE lockbox ADD COLUMN alg TEXT NOT NULL DEFAULT 'xchacha20poly1305_ietf/argon2id_moderate/hmac_sha256_v2';

CREATE TABLE IF NOT EXISTS key_epochs (
    user_id         TEXT    NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    epoch           INTEGER NOT NULL,
    suite           TEXT    NOT NULL,
    -- Password-wrapped DEK for this epoch
    pw_salt         BLOB    NOT NULL,
    pw_nonce        BLOB    NOT NULL,
    pw_cipher       BLOB    NOT NULL,
    -- Recovery-wrapped DEK for this epoch
    rc_salt         BLOB,
    rc_nonce        BLOB,
    rc_cipher       BLOB,
    -- Previous epoch's DEK encrypted under THIS epoch's DEK (NULL for epoch 1)
    prev_dek_nonce  BLOB,
    prev_dek_cipher BLOB,
    retired_at      TIMESTAMP,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, epoch)
);

-- Seed epoch 1 for all existing users from their current wrapping columns
INSERT INTO key_epochs (user_id, epoch, suite, pw_salt, pw_nonce, pw_cipher, rc_salt, rc_nonce, rc_cipher)
SELECT id, 1, 'xchacha20poly1305_ietf/argon2id_moderate/hmac_sha256_v2',
       dek_pw_salt, dek_pw_nonce, dek_pw_cipher,
       dek_rc_salt, dek_rc_nonce, dek_rc_cipher
FROM users;
