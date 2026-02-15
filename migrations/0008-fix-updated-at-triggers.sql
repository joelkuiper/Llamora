-- Fix: The UPDATE triggers fire unconditionally, causing a redundant
-- extra UPDATE when the INSERT trigger sets updated_at.  Replace them
-- with versions that only fire when the application hasn't already set
-- updated_at in the same statement (OLD.updated_at = NEW.updated_at).

DROP TRIGGER IF EXISTS trg_entries_updated_at;
CREATE TRIGGER trg_entries_updated_at
AFTER UPDATE ON entries
FOR EACH ROW
WHEN OLD.updated_at IS NEW.updated_at
BEGIN
    UPDATE entries
    SET updated_at = CURRENT_TIMESTAMP
    WHERE id = NEW.id AND user_id = NEW.user_id;
END;

DROP TRIGGER IF EXISTS trg_tags_updated_at;
CREATE TRIGGER trg_tags_updated_at
AFTER UPDATE ON tags
FOR EACH ROW
WHEN OLD.updated_at IS NEW.updated_at
BEGIN
    UPDATE tags
    SET updated_at = CURRENT_TIMESTAMP
    WHERE user_id = NEW.user_id AND tag_hash = NEW.tag_hash;
END;
