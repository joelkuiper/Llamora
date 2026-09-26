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

from llamora.__main__ import main  # noqa: E402  (import after the clock moves)

if __name__ == "__main__":
    main()
