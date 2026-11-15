"""Live-change feed support: the table whitelist and a per-client rate limit.

GET /api/db/live/changes returns {"changed": [{"table_name", "change_seq"}]}
for every whitelisted table - no record data, no ids. The browser compares
each table's change_seq to the last one it saw and reloads only the tabs that
show a table whose number moved. db/05_live_change.sql installs the triggers
that bump the markers; its watched list must equal LIVE_FEED_TABLES.

Never add credential/session/secret tables here.
"""
import collections
import threading
import time

LIVE_FEED_TABLES = ("road", "activity", "weekly_entry", "data_issue", "edit_log", "manual_issue", "road_issue")

RATE_LIMIT = 60          # requests ...
RATE_WINDOW = 60.0       # ... per this many seconds, per client


class RateLimiter:
    """Sliding-window limiter. There is no login in this app, so the key is
    the client address (X-Forwarded-For first hop behind Render/Vercel)."""

    def __init__(self, limit=RATE_LIMIT, window=RATE_WINDOW, clock=time.monotonic):
        self.limit, self.window, self.clock = limit, window, clock
        self._hits = collections.defaultdict(collections.deque)
        self._lock = threading.Lock()

    def check(self, key):
        """Returns 0 if allowed, else seconds to wait (Retry-After)."""
        now = self.clock()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] >= self.window:
                q.popleft()
            if len(q) >= self.limit:
                return max(1, int(self.window - (now - q[0])) + 1)
            q.append(now)
            if len(self._hits) > 1000:  # drop idle clients
                for k in [k for k, v in self._hits.items() if not v or now - v[-1] >= self.window]:
                    self._hits.pop(k, None)
            return 0


limiter = RateLimiter()


def client_key(request):
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
