PRAGMA foreign_keys=off;
BEGIN TRANSACTION;

ALTER TABLE messages RENAME TO entries;
ALTER TABLE tag_message_xref RENAME TO tag_entry_xref;
ALTER TABLE tag_entry_xref RENAME COLUMN message_id TO entry_id;

DROP INDEX IF EXISTS idx_messages_user_date;
DROP INDEX IF EXISTS idx_messages_reply_to;
DROP INDEX IF EXISTS idx_tag_message_hash;
DROP INDEX IF EXISTS idx_tag_message_message;

CREATE INDEX IF NOT EXISTS idx_entries_user_date ON entries(user_id, created_date);
CREATE INDEX IF NOT EXISTS idx_entries_reply_to ON entries(reply_to);
CREATE INDEX IF NOT EXISTS idx_tag_entry_hash ON tag_entry_xref(user_id, tag_hash);
CREATE INDEX IF NOT EXISTS idx_tag_entry_entry ON tag_entry_xref(user_id, entry_id);

COMMIT;
PRAGMA foreign_keys=on;
