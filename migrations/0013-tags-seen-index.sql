-- Materialize tag-index reads from tags.seen and add an index that matches
-- the get_tags_index() access pattern (user_id + seen > 0).

UPDATE tags
SET seen = COALESCE(
        (
            SELECT COUNT(*)
            FROM tag_entry_xref x
            WHERE x.user_id = tags.user_id
              AND x.tag_hash = tags.tag_hash
        ),
        0
    ),
    last_seen = (
        SELECT MAX(e.created_at)
        FROM tag_entry_xref x
        JOIN entries e
          ON e.user_id = x.user_id AND e.id = x.entry_id
        WHERE x.user_id = tags.user_id
          AND x.tag_hash = tags.tag_hash
    );

CREATE INDEX IF NOT EXISTS idx_tags_user_seen_positive
    ON tags(user_id, seen DESC, tag_hash)
    WHERE seen > 0;
