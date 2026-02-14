CREATE TABLE IF NOT EXISTS lockbox (
    namespace TEXT NOT NULL,
    key TEXT NOT NULL,
    value BLOB NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (namespace, key)
) WITHOUT ROWID;
