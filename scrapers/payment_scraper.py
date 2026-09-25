"""
PaymentMethodScraper
====================
Core engine. For each merchant URL:
  1. Load landing page → scan for payment icons/text/network signals
  2. Discover public info pages from the site's own links
     (store locator, payment/help, FAQ, privacy, terms, contact) and scan each
  3. Find and scan the checkout page
  4. Capture screenshots as evidence
  5. Return raw evidence dict for scoring (+ which pages each method was seen on)

For store-location validation the caller navigates to the store page itself
(see scrapers/location_validator.py) and calls scan_current_page() with a
scope selector, so site-wide footer logos are not mistaken for per-store
acceptance.

Evidence sources (layered approach):
  - DOM text     : keyword match in visible text near payment context
  - IMG src/alt  : payment icon filenames / alt text
  - CSS classes  : payment-related class names on elements
  - Network      : intercepted resource URLs containing payment method names
                   (scoped scans: only icons inside the scope that actually loaded)

This is READ-ONLY. No form fill. No card submission. No PII.
"""

import logging
import os
from urllib.parse import urljoin, urlparse

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

from utils.site_policy import SitePolicy

logger = logging.getLogger(__name__)

ARTIFACTS_DIR = "reports/screenshots"

DEFAULT_READINESS = {
    "busy_selectors":             ["[aria-busy='true']"],
    "payment_selectors":          ["[data-payment-method]", "[class*='payment-icon']"],
    "load_timeout_ms":            15000,
    "settle_timeout_ms":          10000,
    "payment_context_timeout_ms": 5000,
}

DEFAULT_DISCOVERY = {"max_pages": 6, "link_text": {}}

BOT_WALL_MARKERS = ["verify you are human", "captcha", "access denied", "unusual traffic"]

# Page types expected to carry payment UI — worth waiting for payment context
PAYMENT_PAGE_LABELS = {"landing", "checkout", "payment"}


class PaymentMethodScraper:

    def __init__(self, page: Page, detection_rules: dict, policy: SitePolicy | None = None):
        self.page = page
        self.policy = policy          # robots.txt checks before every request (None = no checks)
        self.rules = detection_rules["payment_methods"]
        self.checkout_paths = detection_rules["checkout_path_patterns"]
        self.readiness = {**DEFAULT_READINESS, **detection_rules.get("page_readiness", {})}
        self.discovery = {**DEFAULT_DISCOVERY, **detection_rules.get("page_discovery", {})}
        self._network_urls: list[str] = []
        self._listening = False
        self.evidence_pages: dict[str, list[str]] = {}
        self.visits: list[dict] = []

    # ──────────────────────────────────────────────────────────────────────
    #  Public entrypoints
    # ──────────────────────────────────────────────────────────────────────

    def scan_merchant(self, merchant: dict) -> dict:
        """
        Full site-level scan of one merchant.
        Returns evidence dict: {
            method_name: {
                "text": bool, "img": bool, "alt": bool,
                "css_class": bool, "network": bool
            }
        }
        self.evidence_pages / self.visits hold per-page detail for reporting.
        """
        merchant_id  = merchant["merchant_id"]
        base_url     = merchant["base_url"]
        logger.info(f"[{merchant_id}] Scanning {merchant['merchant_name']} [{merchant['region']}]")

        self.reset()
        evidence = self.empty_evidence_set()

        # ── Step 1: Landing page ─────────────────────────────────────────
        landing_ok = self._visit_and_scan(base_url, evidence, merchant_id, "landing")

        # ── Step 2: Public info pages linked from the site ───────────────
        pages = self._discover_info_pages(base_url) if landing_ok else []

        # ── Step 3: Checkout ─────────────────────────────────────────────
        checkout_url = self._find_checkout_url(base_url) if landing_ok else None
        if checkout_url:
            pages.append(("checkout", checkout_url))

        for label, url in pages:
            self._visit_and_scan(url, evidence, merchant_id, label)

        # ── Step 4: Apply network evidence ───────────────────────────────
        self._apply_network_evidence(evidence, self._network_urls)

        logger.info(f"[{merchant_id}] Scan complete. Methods with any signal: "
                    f"{[m for m, e in evidence.items() if any(e.values())]}")
        return evidence

    def scan_current_page(self, evidence: dict, label: str, scope: str | None = None) -> bool:
        """
        Scan the page the browser is on right now. With `scope`, only that
        element's subtree counts (text, images, classes, loaded icons).
        Returns False if the scope element is not on the page.
        """
        if scope and self.page.locator(scope).count() == 0:
            return False
        self._scan_dom(evidence, label, scope)
        if scope:
            self._apply_network_evidence(evidence, self._get_loaded_img_srcs(scope), label)
        return True

    def reset(self):
        self._network_urls = []
        self._attach_network_listener()
        self.evidence_pages = {m: [] for m in self.rules}
        self.visits = []

    def empty_evidence_set(self) -> dict[str, dict]:
        return {m: self._empty_evidence() for m in self.rules}

    # ──────────────────────────────────────────────────────────────────────
    #  Navigation
    # ──────────────────────────────────────────────────────────────────────

    def _visit_and_scan(self, url: str, evidence: dict, merchant_id: str, label: str) -> bool:
        visit = {"label": label, "url": url, "status": None, "error": None, "bot_wall": False, "robots": None}
        self.visits.append(visit)
        blocked = self.policy.robots_block_reason(url) if self.policy else None
        if blocked:
            visit["robots"] = blocked
            logger.info(f"[{merchant_id}] {label}: skipped — {blocked}")
            return False
        try:
            response = self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
            visit["status"] = response.status if response else None
            if self.is_bot_wall(visit["status"]):
                visit["bot_wall"] = True
                logger.warning(f"[{merchant_id}] {label}: bot protection page ({visit['status']})")
                self._screenshot(merchant_id, label)
                return False
            self.wait_for_page_ready(merchant_id, label,
                                     expect_payment_context=label in PAYMENT_PAGE_LABELS)
            self.scan_current_page(evidence, label)
            self._screenshot(merchant_id, label)
            return visit["status"] is None or visit["status"] < 400
        except Exception as e:
            visit["error"] = str(e).splitlines()[0]
            logger.warning(f"[{merchant_id}] {label} page error: {visit['error']}")
            return False

    def is_bot_wall(self, status: int | None) -> bool:
        if status in (403, 429):
            return True
        # 503 is ambiguous (maintenance vs JS challenge): decide on page content
        text = self._get_page_text()
        # Interstitials are short; long pages merely mentioning "captcha" are not walls
        return len(text) < 2000 and any(marker in text for marker in BOT_WALL_MARKERS)

    def _discover_info_pages(self, base_url: str) -> list[tuple[str, str]]:
        """Same-origin links whose text matches configured page types."""
        try:
            links = self.page.evaluate(
                "() => Array.from(document.querySelectorAll('a[href]'))"
                "      .map(a => [a.innerText.trim().toLowerCase(), a.href])"
            )
        except Exception:
            return []

        origin = urlparse(base_url).netloc
        found, seen = [], {self.page.url.rstrip("/")}
        for page_type, phrases in self.discovery["link_text"].items():
            for text, href in links:
                href = urljoin(base_url, href).split("#")[0]
                if urlparse(href).netloc != origin or href.rstrip("/") in seen:
                    continue
                if any(p in text for p in phrases):
                    found.append((page_type, href))
                    seen.add(href.rstrip("/"))
                    break
        return found[: self.discovery["max_pages"]]

    # ──────────────────────────────────────────────────────────────────────
    #  Page readiness (conditional waits — no fixed sleeps)
    # ──────────────────────────────────────────────────────────────────────

    def wait_for_page_ready(self, merchant_id: str, label: str, expect_payment_context: bool = True):
        """
        Wait until the page is actually ready to scan instead of sleeping:
          1. `load` event fired (icons/scripts fetched)
          2. no visible busy indicator (aria-busy, spinners, skeletons) —
             async payment widgets render methods only after this clears
          3. at least one payment-context element is visible
             (skipped for pages that are not expected to carry payment UI)
        Each step is soft: a timeout is logged and the scan continues with
        whatever evidence is on the page, so one slow site never blocks a run.
        """
        steps = [
            ("load", lambda: self.page.wait_for_load_state(
                "load", timeout=self.readiness["load_timeout_ms"])),
            ("busy indicators cleared", lambda: self.page.wait_for_function(
                """sel => [...document.querySelectorAll(sel)]
                            .every(el => !el.checkVisibility())""",
                arg=", ".join(self.readiness["busy_selectors"]),
                timeout=self.readiness["settle_timeout_ms"])),
        ]
        if expect_payment_context:
            steps.append(("payment context visible", lambda: self.page.locator(
                ", ".join(self.readiness["payment_selectors"])).first.wait_for(
                state="visible", timeout=self.readiness["payment_context_timeout_ms"])))
        for name, wait in steps:
            try:
                wait()
            except PlaywrightTimeoutError:
                logger.info(f"[{merchant_id}] {label}: timed out waiting for {name}")

    # ──────────────────────────────────────────────────────────────────────
    #  Network interception
    # ──────────────────────────────────────────────────────────────────────

    def _attach_network_listener(self):
        """Passively collect all resource URLs — never blocks requests."""
        if self._listening:
            return

        def handle_request(request):
            self._network_urls.append(request.url.lower())

        self.page.on("request", handle_request)
        self._listening = True

    def _apply_network_evidence(self, evidence: dict, urls: list[str], label: str = "network"):
        network_text = " ".join(urls)
        for method, rule in self.rules.items():
            for pattern in rule.get("network_patterns", []):
                if pattern.lower() in network_text:
                    evidence[method]["network"] = True
                    self._record_page(method, label)
                    logger.debug(f"Network hit: {method} → pattern '{pattern}'")
                    break

    # ──────────────────────────────────────────────────────────────────────
    #  DOM scanning
    # ──────────────────────────────────────────────────────────────────────

    def _scan_dom(self, evidence: dict, label: str, scope: str | None = None):
        """Extract all evidence signals from current page DOM (or a subtree)."""
        page_text   = self._get_page_text(scope)
        img_srcs    = self._get_img_attr("src", scope)
        img_alts    = self._get_img_attr("alt", scope)
        css_classes = self._get_css_classes(scope)

        for method, rule in self.rules.items():
            hits = {
                "text":      any(kw.lower() in page_text for kw in rule.get("keywords", [])),
                "img":       any(p.lower() in s for p in rule.get("img_patterns", []) for s in img_srcs),
                "alt":       any(p.lower() in a for p in rule.get("alt_patterns", []) for a in img_alts),
                "css_class": any(p.lower() in c for p in rule.get("class_patterns", []) for c in css_classes),
            }
            for signal, hit in hits.items():
                if hit:
                    evidence[method][signal] = True
            if any(hits.values()):
                self._record_page(method, label)

    def _record_page(self, method: str, label: str):
        pages = self.evidence_pages.setdefault(method, [])
        if label not in pages:
            pages.append(label)

    def _eval_in_scope(self, body: str, scope: str | None, default):
        """Run `body` (JS using `root`) against the document or a scoped subtree."""
        try:
            return self.page.evaluate(
                f"scope => {{ const root = scope ? document.querySelector(scope) : document.body;"
                f" if (!root) return null; {body} }}",
                scope,
            ) or default
        except Exception:
            return default

    def _get_page_text(self, scope: str | None = None) -> str:
        return self._eval_in_scope("return root.innerText;", scope, "").lower()

    def _get_img_attr(self, attr: str, scope: str | None = None) -> list[str]:
        return self._eval_in_scope(
            f"return Array.from(root.querySelectorAll('img')).map(i => (i.{attr} || '').toLowerCase());",
            scope, [],
        )

    def _get_css_classes(self, scope: str | None = None) -> list[str]:
        classes = self._eval_in_scope(
            "return Array.from(root.querySelectorAll('*')).flatMap(el => Array.from(el.classList));",
            scope, [],
        )
        return [c.lower() for c in classes]

    def _get_loaded_img_srcs(self, scope: str) -> list[str]:
        """Icons inside scope that were actually fetched and decoded."""
        return self._eval_in_scope(
            "return Array.from(root.querySelectorAll('img'))"
            "  .filter(i => i.complete && i.naturalWidth > 0).map(i => i.currentSrc.toLowerCase());",
            scope, [],
        )

    # ──────────────────────────────────────────────────────────────────────
    #  Checkout URL discovery
    # ──────────────────────────────────────────────────────────────────────

    def _find_checkout_url(self, base_url: str) -> str | None:
        """
        Try known checkout path patterns on current domain.
        Returns first URL that responds with 200.
        """
        for path in self.checkout_paths:
            candidate = base_url.rstrip("/") + path
            if self.policy and self.policy.robots_block_reason(candidate):
                continue
            try:
                resp = self.page.request.get(candidate, timeout=8000)
                if resp.status < 400:
                    logger.info(f"Checkout found: {candidate}")
                    return candidate
            except Exception:
                continue
        logger.info("No checkout URL found")
        return None

    # ──────────────────────────────────────────────────────────────────────
    #  Screenshots
    # ──────────────────────────────────────────────────────────────────────

    def _screenshot(self, merchant_id: str, label: str) -> str:
        os.makedirs(ARTIFACTS_DIR, exist_ok=True)
        path = f"{ARTIFACTS_DIR}/{merchant_id}_{label}.png"
        try:
            self.page.screenshot(path=path, full_page=True)
            logger.info(f"Screenshot saved: {path}")
        except Exception as e:
            logger.warning(f"Screenshot failed [{merchant_id}/{label}]: {e}")
        return path

    def screenshot(self, name: str, scope: str | None = None) -> str | None:
        """Evidence capture: element screenshot when scoped, else full page."""
        os.makedirs(ARTIFACTS_DIR, exist_ok=True)
        path = f"{ARTIFACTS_DIR}/{name}.png"
        try:
            if scope and self.page.locator(scope).count():
                self.page.locator(scope).first.screenshot(path=path)
            else:
                self.page.screenshot(path=path, full_page=True)
            return path
        except Exception as e:
            logger.warning(f"Screenshot failed [{name}]: {e}")
            return None

    # ──────────────────────────────────────────────────────────────────────
    #  Helpers
    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    def _empty_evidence() -> dict:
        return {"text": False, "img": False, "alt": False, "css_class": False, "network": False}
