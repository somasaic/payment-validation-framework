"""
Site access policy
==================
Safeguards applied before the framework requests any page:

1. Allowlist (live runs) — only domains listed in data/live_allowlist.txt are
   scanned: the client's contracted merchant list, or sites whose owners agreed.
   The file ships empty, so a live run scans nothing until someone adds domains.
   Mock runs target the local mock service and pass allowlist=None.

2. robots.txt (always) — every URL is checked against the site's robots.txt
   for our user agent, following RFC 9309:
     200            → obey the rules
     401 / 403      → disallow everything
     other 4xx      → allow everything (no robots.txt)
     5xx            → treat as disallow (server can't tell us what's allowed)
     network error  → allow here; the navigation itself then fails and the
                      record is reported as SITE_UNREACHABLE
   There is deliberately no switch to ignore robots.txt.
"""

import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

USER_AGENT = "PaymentValidationBot"
DEFAULT_ALLOWLIST = Path(__file__).resolve().parents[1] / "data" / "live_allowlist.txt"

Fetcher = Callable[[str], tuple[int | None, str]]


def fetch_robots(url: str, timeout: float = 10) -> tuple[int | None, str]:
    """(HTTP status, body) for a robots.txt URL; (None, "") on network errors."""
    request = urllib.request.Request(url, headers={"User-Agent": f"{USER_AGENT}/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except (urllib.error.URLError, TimeoutError, OSError):
        return None, ""


def load_allowlist(path: str | Path = DEFAULT_ALLOWLIST) -> list[str]:
    """Domains from an allowlist file: one per line, '#' comments, '*.example.com' wildcards."""
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [ln.split("#", 1)[0].strip().lower() for ln in lines if ln.split("#", 1)[0].strip()]


class SitePolicy:

    def __init__(self, allowlist: list[str] | None = None, fetch: Fetcher = fetch_robots,
                 user_agent: str = USER_AGENT):
        self.allowlist = None if allowlist is None else [d.lower() for d in allowlist]
        self.user_agent = user_agent
        self._fetch = fetch
        self._robots: dict[str, tuple[RobotFileParser | None, str | None]] = {}
        self._lock = threading.Lock()

    # ── Allowlist ────────────────────────────────────────────────────────

    def is_allowlisted(self, url: str) -> bool:
        if self.allowlist is None:
            return True
        host = urlparse(url).hostname or ""
        for entry in self.allowlist:
            if entry.startswith("*."):
                if host.endswith(entry[1:]):          # "*.brand.example" → any subdomain
                    return True
            elif host == entry:
                return True
        return False

    # ── robots.txt ───────────────────────────────────────────────────────

    def robots_block_reason(self, url: str) -> str | None:
        """None if the URL may be fetched, else a human-readable reason."""
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        with self._lock:
            if origin not in self._robots:
                self._robots[origin] = self._load(origin)
            parser, blanket_reason = self._robots[origin]
        if blanket_reason:
            return blanket_reason
        if parser is None or parser.can_fetch(self.user_agent, url):
            return None
        return f"robots.txt disallows {parsed.path or '/'} for {self.user_agent}"

    def _load(self, origin: str) -> tuple[RobotFileParser | None, str | None]:
        status, body = self._fetch(f"{origin}/robots.txt")
        if status is None:
            return None, None                                    # unreachable → navigation reports it
        if status in (401, 403):
            return None, f"robots.txt returned HTTP {status} (treated as disallow all)"
        if 400 <= status < 500:
            return None, None                                    # no robots.txt → allowed
        if status >= 500:
            return None, f"robots.txt returned HTTP {status} (treated as disallow per RFC 9309)"
        parser = RobotFileParser()
        parser.parse(body.splitlines())
        return parser, None
