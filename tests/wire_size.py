"""How big a page is on the wire, and the budget that keeps it to one round trip (docs/performance-testing.md,
"Page size and the first round trip"). Used by `tests/perf` (every page over the seeded world) and the e2e page weight
budgets (the anonymized real log)."""

import gzip

KB = 1024
WIRE_MAX_KB = 13
"""A new connection's first burst is 10 TCP packets (RFC 6928), about 14 KB: a page that fits arrives one round trip
after the request, a bigger one waits one more round trip for each doubling of that burst. 13 KB leaves room for the
response headers and the TLS records (maintainer decision, 2026-11-06)."""


def wire_bytes(body: bytes) -> int:
    """Size after gzip at level 5, the bundled Caddy's default (`encode zstd gzip`); its zstd is a little smaller."""
    return len(gzip.compress(body, compresslevel=5))
