-- Clear stale v1 digests so they are recomputed with the v2 HMAC-based
-- key derivation on next access.
UPDATE entries SET digest = NULL WHERE digest IS NOT NULL;
