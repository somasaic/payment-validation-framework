"""
PaymentMethodScraper
====================
Core engine. For each merchant URL:
  1. Load landing page → scan for payment icons/text/network signals
  2. Find and navigate to checkout page
  3. Scan checkout page for payment method evidence
  4. Capture screenshots as evidence
  5. Return raw evidence dict for scoring

Evidence sources (layered approach):
  - DOM text     : keyword match in visible text near payment context
  - IMG src/alt  : payment icon filenames / alt text
  - CSS classes  : payment-related class names on elements
  - Network      : intercepted resource URLs containing payment method names

This is READ-ONLY. No form fill. No card submission. No PII.
"""

import re
import json
import logging
import os
from datetime import datetime
from playwright.sync_api import Page, Route

logger = logging.getLogger(__name__)

ARTIFACTS_DIR = "reports/screenshots"


class PaymentMethodScraper:

    def __init__(self, page: Page, detection_rules: dict):
        self.page = page
        self.rules = detection_rules["payment_methods"]
        self.checkout_paths = detection_rules["checkout_path_patterns"]
        self._network_urls: list[str] = []

    # ──────────────────────────────────────────────────────────────────────
    #  Public entrypoint
    # ──────────────────────────────────────────────────────────────────────

    def scan_merchant(self, merchant: dict) -> dict:
        """
        Full scan of one merchant.
        Returns evidence dict: {
            method_name: {
                "text": bool, "img": bool, "alt": bool,
                "css_class": bool, "network": bool,
                "screenshot": str | None
            }
        }
        """
        merchant_id  = merchant["merchant_id"]
        base_url     = merchant["base_url"]
        region       = merchant["region"]
        logger.info(f"[{merchant_id}] Scanning {merchant['merchant_name']} [{region}]")

        self._network_urls = []
        self._attach_network_listener()

        evidence: dict[str, dict] = {m: self._empty_evidence() for m in self.rules}

        # ── Step 1: Landing page ─────────────────────────────────────────
        try:
            self.page.goto(base_url, wait_until="domcontentloaded", timeout=30000)
            self.page.wait_for_timeout(2000)   # let lazy-load settle
            self._scan_current_page(evidence, label="landing")
            self._screenshot(merchant_id, "landing")
        except Exception as e:
            logger.warning(f"[{merchant_id}] Landing page error: {e}")

        # ── Step 2: Find checkout ────────────────────────────────────────
        checkout_url = self._find_checkout_url(base_url)
        if checkout_url:
            try:
                self.page.goto(checkout_url, wait_until="domcontentloaded", timeout=30000)
                self.page.wait_for_timeout(2000)
                self._scan_current_page(evidence, label="checkout")
                self._screenshot(merchant_id, "checkout")
            except Exception as e:
                logger.warning(f"[{merchant_id}] Checkout page error: {e}")

        # ── Step 3: Apply network evidence ───────────────────────────────
        self._apply_network_evidence(evidence)

        logger.info(f"[{merchant_id}] Scan complete. Methods with any signal: "
                    f"{[m for m, e in evidence.items() if any(e.values()) if m != 'screenshot']}")
        return evidence

    # ──────────────────────────────────────────────────────────────────────
    #  Network interception
    # ──────────────────────────────────────────────────────────────────────

    def _attach_network_listener(self):
        """Passively collect all resource URLs — never blocks requests."""
        def handle_request(request):
            self._network_urls.append(request.url.lower())

        self.page.on("request", handle_request)

    def _apply_network_evidence(self, evidence: dict):
        network_text = " ".join(self._network_urls)
        for method, rule in self.rules.items():
            for pattern in rule.get("network_patterns", []):
                if pattern.lower() in network_text:
                    evidence[method]["network"] = True
                    logger.debug(f"Network hit: {method} → pattern '{pattern}'")
                    break

    # ──────────────────────────────────────────────────────────────────────
    #  DOM scanning
    # ──────────────────────────────────────────────────────────────────────

    def _scan_current_page(self, evidence: dict, label: str):
        """Extract all evidence signals from current page DOM."""
        page_text   = self._get_page_text()
        img_srcs    = self._get_img_srcs()
        img_alts    = self._get_img_alts()
        css_classes = self._get_css_classes()

        for method, rule in self.rules.items():
            # Text match
            for kw in rule.get("keywords", []):
                if kw.lower() in page_text:
                    evidence[method]["text"] = True
                    break

            # IMG src match
            for pattern in rule.get("img_patterns", []):
                if any(pattern.lower() in src for src in img_srcs):
                    evidence[method]["img"] = True
                    break

            # IMG alt match
            for pattern in rule.get("alt_patterns", []):
                if any(pattern.lower() in alt for alt in img_alts):
                    evidence[method]["alt"] = True
                    break

            # CSS class match
            for pattern in rule.get("class_patterns", []):
                if any(pattern.lower() in cls for cls in css_classes):
                    evidence[method]["css_class"] = True
                    break

    def _get_page_text(self) -> str:
        try:
            return self.page.evaluate("() => document.body.innerText").lower()
        except Exception:
            return ""

    def _get_img_srcs(self) -> list[str]:
        try:
            return self.page.evaluate(
                "() => Array.from(document.querySelectorAll('img')).map(i => i.src.toLowerCase())"
            )
        except Exception:
            return []

    def _get_img_alts(self) -> list[str]:
        try:
            return self.page.evaluate(
                "() => Array.from(document.querySelectorAll('img')).map(i => (i.alt || '').toLowerCase())"
            )
        except Exception:
            return []

    def _get_css_classes(self) -> list[str]:
        try:
            all_classes = self.page.evaluate(
                "() => Array.from(document.querySelectorAll('*')).flatMap(el => Array.from(el.classList))"
            )
            return [c.lower() for c in all_classes]
        except Exception:
            return []

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
            try:
                resp = self.page.request.get(candidate, timeout=8000)
                if resp.status < 400:
                    logger.info(f"Checkout found: {candidate}")
                    return candidate
            except Exception:
                continue
        logger.info("No checkout URL found — landing scan only")
        return None

    # ──────────────────────────────────────────────────────────────────────
    #  Screenshots
    # ──────────────────────────────────────────────────────────────────────

    def _screenshot(self, merchant_id: str, label: str) -> str:
        os.makedirs(ARTIFACTS_DIR, exist_ok=True)
        path = os.path.join(ARTIFACTS_DIR, f"{merchant_id}_{label}.png")
        try:
            self.page.screenshot(path=path, full_page=True)
            logger.info(f"Screenshot saved: {path}")
        except Exception as e:
            logger.warning(f"Screenshot failed [{merchant_id}/{label}]: {e}")
        return path

    # ──────────────────────────────────────────────────────────────────────
    #  Helpers
    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    def _empty_evidence() -> dict:
        return {"text": False, "img": False, "alt": False, "css_class": False, "network": False}
