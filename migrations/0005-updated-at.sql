ALTER TABLE entries ADD COLUMN updated_at TIMESTAMP;
ALTER TABLE tags ADD COLUMN updated_at TIMESTAMP;

UPDATE entries
SET updated_at = created_at
WHERE updated_at IS NULL;

UPDATE tags
SET updated_at = COALESCE(last_seen, CURRENT_TIMESTAMP)
WHERE updated_at IS NULL;

CREATE TRIGGER IF NOT EXISTS trg_entries_updated_at_insert
AFTER INSERT ON entries
FOR EACH ROW
WHEN NEW.updated_at IS NULL
BEGIN
    UPDATE entries
    SET updated_at = CURRENT_TIMESTAMP
    WHERE id = NEW.id AND user_id = NEW.user_id;
END;

CREATE TRIGGER IF NOT EXISTS trg_entries_updated_at
AFTER UPDATE ON entries
FOR EACH ROW
BEGIN
    UPDATE entries
    SET updated_at = CURRENT_TIMESTAMP
    WHERE id = NEW.id AND user_id = NEW.user_id;
END;

CREATE TRIGGER IF NOT EXISTS trg_tags_updated_at_insert
AFTER INSERT ON tags
FOR EACH ROW
WHEN NEW.updated_at IS NULL
BEGIN
    UPDATE tags
    SET updated_at = CURRENT_TIMESTAMP
    WHERE user_id = NEW.user_id AND tag_hash = NEW.tag_hash;
END;

CREATE TRIGGER IF NOT EXISTS trg_tags_updated_at
AFTER UPDATE ON tags
FOR EACH ROW
BEGIN
    UPDATE tags
    SET updated_at = CURRENT_TIMESTAMP
    WHERE user_id = NEW.user_id AND tag_hash = NEW.tag_hash;
END;
