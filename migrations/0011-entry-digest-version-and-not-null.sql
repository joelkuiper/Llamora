-- Record entry digest algorithm version and enforce non-null digests.
--
-- Digest backfill follows the same behavior as scripts/backfill_entry_digests.py
-- by deriving HMAC digests from decrypted entry payloads. Existing rows without
-- a digest must be backfilled before this migration is applied.

ALTER TABLE entries ADD COLUMN digest_version INTEGER NOT NULL DEFAULT 2;

UPDATE entries
SET digest_version = 2
WHERE digest IS NOT NULL
  AND TRIM(digest) != '';

CREATE TRIGGER IF NOT EXISTS trg_entries_digest_required_insert
BEFORE INSERT ON entries
FOR EACH ROW
WHEN NEW.digest IS NULL OR TRIM(NEW.digest) = ''
BEGIN
    SELECT RAISE(ABORT, 'entries.digest must not be null');
END;

CREATE TRIGGER IF NOT EXISTS trg_entries_digest_required_update
BEFORE UPDATE OF digest ON entries
FOR EACH ROW
WHEN NEW.digest IS NULL OR TRIM(NEW.digest) = ''
BEGIN
    SELECT RAISE(ABORT, 'entries.digest must not be null');
END;

CREATE TRIGGER IF NOT EXISTS trg_entries_digest_version_required_insert
BEFORE INSERT ON entries
FOR EACH ROW
WHEN NEW.digest_version IS NULL
BEGIN
    SELECT RAISE(ABORT, 'entries.digest_version must not be null');
END;

CREATE TRIGGER IF NOT EXISTS trg_entries_digest_version_required_update
BEFORE UPDATE OF digest_version ON entries
FOR EACH ROW
WHEN NEW.digest_version IS NULL
BEGIN
    SELECT RAISE(ABORT, 'entries.digest_version must not be null');
END;

