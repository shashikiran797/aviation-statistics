"""Polite HTTP: one session, retries, min delay, on-disk cache for HTML pages."""
import hashlib
import time
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from . import config

_last = 0.0


def session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = config.USER_AGENT
    retry = Retry(total=4, backoff_factor=1.0, status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=("GET", "HEAD"))
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s


def _throttle():
    global _last
    wait = config.MIN_DELAY_S - (time.monotonic() - _last)
    if wait > 0:
        time.sleep(wait)
    _last = time.monotonic()


def norm_url(url: str) -> str:
    """Spaces and non-ascii in portal paths -> percent-encoded, keeping existing escapes."""
    return quote(url, safe="/:%?&=+#~@!$'()*,;")


def get_html(sess: requests.Session, url: str, refresh: bool = False) -> str | None:
    """Fetch a portal HTML page, cached on disk. Returns None on 404."""
    url = norm_url(url)
    config.HTML_CACHE.mkdir(parents=True, exist_ok=True)
    f = config.HTML_CACHE / (hashlib.sha1(url.encode()).hexdigest() + ".html")
    if f.exists() and not refresh and time.time() - f.stat().st_mtime < config.HTML_MAX_AGE_S:
        return f.read_text(encoding="utf-8", errors="replace")
    _throttle()
    r = sess.get(url, timeout=60)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    f.write_bytes(r.content)
    return r.content.decode(r.encoding or "utf-8", errors="replace")
