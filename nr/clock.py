"""True UTC time. This PC's clock drifts (Windows Time service not running; it
was measured 20s fast), while market data is stamped in true time. Every
experiment timestamp goes through here: the offset is measured from HTTPS
Date headers of several servers and refreshed hourly."""
import email.utils
import statistics
import threading
import time
from datetime import datetime, timezone

import requests

_REFS = ("https://api.dexscreener.com", "https://api.geckoterminal.com",
         "https://www.google.com")
_lock = threading.Lock()
_offset = 0.0
_checked = None


def offset() -> float:
    """Seconds to ADD to the local clock to get true time."""
    global _offset, _checked
    with _lock:
        if _checked is not None and time.monotonic() - _checked < 3600:
            return _offset
        samples = []
        for url in _REFS:
            try:
                t0 = time.time()
                r = requests.head(url, timeout=8)
                t1 = time.time()
                srv = email.utils.parsedate_to_datetime(r.headers["Date"]).timestamp()
                # Date has 1s resolution; +0.5 centres it, midpoint removes latency.
                samples.append(srv + 0.5 - (t0 + t1) / 2)
            except Exception:
                continue
        if samples:
            _offset = statistics.median(samples)
        _checked = time.monotonic()
        return _offset


def now() -> float:
    return time.time() + offset()


def now_utc() -> datetime:
    return datetime.fromtimestamp(now(), timezone.utc)
