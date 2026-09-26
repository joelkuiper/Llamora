"""Start the Llamora server, optionally with its clock moved to a fixed instant.

Used by the e2e harness instead of ``python -m llamora`` so tests can put the
*server* at a chosen time (e.g. just past UTC midnight) independently of the
browser's clock. ``LLAMORA_TEST_NOW`` is an ISO-8601 instant with an offset;
the clock keeps ticking from there. Monotonic time is left alone, so asyncio
and timeouts behave normally.
"""

from __future__ import annotations

import os

now = os.environ.get("LLAMORA_TEST_NOW")
if now:
    import time_machine

    time_machine.travel(now, tick=True).start()

# Test servers derive keys with libsodium's *minimum* Argon2id cost. At the
# real (MODERATE) cost every registration takes ~5 s and every login ~3 s of
# CPU and 256 MiB, which dominated the suite; the key hierarchy, wrapping and
# encryption are exercised exactly the same, only cheaper. Never done outside
# this test-only entry point (set LLAMORA_TEST_REAL_KDF=1 to keep real costs).
if os.environ.get("LLAMORA_TEST_REAL_KDF") != "1":
    from nacl import pwhash

    from llamora.app.services import crypto

    _MIN = {
        "opslimit": pwhash.argon2id.OPSLIMIT_MIN,
        "memlimit": pwhash.argon2id.MEMLIMIT_MIN,
    }
    crypto.OPSLIMIT = _MIN["opslimit"]
    crypto.MEMLIMIT = _MIN["memlimit"]
    _hash_password = pwhash.argon2id.str
    pwhash.argon2id.str = lambda password, **_: _hash_password(password, **_MIN)

from llamora.__main__ import main  # noqa: E402  (import after the clock moves)

if __name__ == "__main__":
    main()
