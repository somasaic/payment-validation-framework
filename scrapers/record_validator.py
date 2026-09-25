"""
RecordValidator
===============
Validates one master-dataset record and explains the outcome.

Store-location record (has store_address and the brand has a site profile):
  1. Open the brand's store locator         → unreachable / bot wall?
  2. Search the record's address + postcode
  3. Pick the matching candidate            → STORE_NOT_FOUND if none
  4. Open the store page, re-check address, closed banner
  5. Scan ONLY the store's own content (site-wide footer logos are brand
     claims, not proof a given location accepts a card)
  6. Score → Yes / No / Review per method, screenshot as evidence

Site-level record (no address, or no locator profile for the brand):
  multi-page scan via PaymentMethodScraper.scan_merchant().
"""

import logging
from dataclasses import dataclass, field

from playwright.sync_api import Error as PlaywrightError, Page, TimeoutError as PlaywrightTimeoutError

from pages.site_profile import SiteProfile, profile_for_url
from pages.store_locator_page import StoreLocatorPage
from scrapers.payment_scraper import PaymentMethodScraper
from utils.address_matcher import best_match, score as address_score
from utils.master_dataset import Reason, Status
from validators.confidence_scorer import ConfidenceScorer

logger = logging.getLogger(__name__)

NETWORK_ERRORS = ("ERR_NAME_NOT_RESOLVED", "ERR_CONNECTION", "ERR_ADDRESS_UNREACHABLE",
                  "ERR_TIMED_OUT", "ERR_INTERNET_DISCONNECTED", "ERR_SSL", "ERR_CERT")


@dataclass
class ValidationOutcome:
    status:          str
    reason:          str = ""
    reason_detail:   str = ""
    scored:          dict = field(default_factory=dict)
    evidence_pages:  dict = field(default_factory=dict)
    matched_address: str = ""
    match_score:     float | None = None
    screenshot:      str | None = None

    @property
    def retryable(self) -> bool:
        return self.reason in (Reason.SITE_UNREACHABLE, Reason.UNEXPECTED_ERROR)


class RecordValidator:

    def __init__(self, page: Page, detection_rules: dict):
        self.page    = page
        self.rules   = detection_rules
        self.scorer  = ConfidenceScorer(detection_rules)
        self.scraper = PaymentMethodScraper(page, detection_rules)

    def validate(self, record: dict, site_url: str | None = None) -> ValidationOutcome:
        """site_url overrides record['website_url'] as the navigation target
        (mock mode); the profile is always resolved from the real website_url."""
        site_url = site_url or record["website_url"]
        profile  = profile_for_url(record["website_url"])
        try:
            if record.get("store_address") and profile:
                return self._validate_location(record, site_url, profile)
            outcome = self._validate_site(record, site_url)
            if record.get("store_address") and outcome.reason == "":
                outcome.status = Status.NEEDS_REVIEW
                outcome.reason = Reason.LOCATOR_NOT_SUPPORTED
                outcome.reason_detail = "No store-locator profile for this site; site-level result only"
            return outcome
        except PlaywrightError as e:
            return self._error_outcome(e)

    # ── Location flow ────────────────────────────────────────────────────

    def _validate_location(self, record: dict, site_url: str, profile: SiteProfile) -> ValidationOutcome:
        rid = record["record_id"]
        self.scraper.reset()
        locator = StoreLocatorPage(self.page, profile, site_url)

        timeout = self.scraper.readiness["settle_timeout_ms"]
        response = self.page.goto(site_url.rstrip("/") + profile.locator_path, wait_until="domcontentloaded")
        status = response.status if response else None
        if self.scraper.is_bot_wall(status):
            return ValidationOutcome(
                Status.NOT_VALIDATED, Reason.BLOCKED_BY_BOT_PROTECTION,
                f"HTTP {status or '?'} on store locator",
                screenshot=self.scraper.screenshot(f"{rid}_blocked"),
            )
        if status == 404:
            return ValidationOutcome(Status.NOT_VALIDATED, Reason.LOCATOR_PAGE_CHANGED,
                                     f"Store locator not found at {profile.locator_path} (HTTP 404)")
        if status and status >= 400:
            return ValidationOutcome(Status.NOT_VALIDATED, Reason.SITE_UNREACHABLE,
                                     f"HTTP {status} on store locator")
        try:
            locator.search_input.wait_for(timeout=timeout)
        except PlaywrightTimeoutError:
            return ValidationOutcome(
                Status.NOT_VALIDATED, Reason.LOCATOR_PAGE_CHANGED,
                f"Search box not found ({profile.search_input}); site profile '{profile.name}' needs updating",
                screenshot=self.scraper.screenshot(f"{rid}_locator"),
            )

        query = " ".join(p for p in (record["store_address"], record.get("postal_code", "")) if p)
        try:
            locator.search(query, timeout=timeout)
        except PlaywrightTimeoutError:
            return ValidationOutcome(Status.NOT_VALIDATED, Reason.SITE_UNREACHABLE,
                                     "Store search did not return results in time")
        try:
            candidates = locator.result_list(timeout=timeout)
        except PlaywrightTimeoutError:
            return ValidationOutcome(
                Status.NOT_VALIDATED, Reason.LOCATOR_PAGE_CHANGED,
                f"Search results not recognised ({profile.result_item}); site profile '{profile.name}' needs updating",
                screenshot=self.scraper.screenshot(f"{rid}_locator", profile.results_container),
            )
        match = best_match(record["store_address"], [c.address for c in candidates], record.get("postal_code", ""))
        if match is None:
            return ValidationOutcome(
                Status.NOT_VALIDATED, Reason.STORE_NOT_FOUND,
                f"{len(candidates)} locator result(s), none matched the address",
                screenshot=self.scraper.screenshot(f"{rid}_locator", profile.results_container),
            )

        try:
            detail = locator.open_result(match.index, timeout=timeout)
        except PlaywrightTimeoutError:
            return ValidationOutcome(
                Status.NOT_VALIDATED, Reason.LOCATOR_PAGE_CHANGED,
                f"Store page layout not recognised ({profile.store_content}); "
                f"site profile '{profile.name}' needs updating",
                screenshot=self.scraper.screenshot(f"{rid}_store"),
            )
        self.scraper.wait_for_page_ready(rid, "store_page", expect_payment_context=False)

        detail_address = detail.address_text()
        if address_score(record["store_address"], detail_address, record.get("postal_code", "")) == 0:
            return ValidationOutcome(
                Status.NOT_VALIDATED, Reason.STORE_NOT_FOUND,
                f"Store page address '{detail_address}' does not match",
                matched_address=detail_address,
            )

        screenshot = self.scraper.screenshot(f"{rid}_store", profile.store_content)
        common = dict(matched_address=detail_address, match_score=match.score, screenshot=screenshot)

        if detail.is_temporarily_closed():
            return ValidationOutcome(Status.NOT_VALIDATED, Reason.STORE_TEMPORARILY_CLOSED,
                                     "Unable to verify: store is temporarily closed", **common)

        evidence = self.scraper.empty_evidence_set()
        self.scraper.scan_current_page(evidence, "store_page", scope=profile.store_content)
        return self._scored_outcome(evidence, **common)

    # ── Site-level flow ──────────────────────────────────────────────────

    def _validate_site(self, record: dict, site_url: str) -> ValidationOutcome:
        evidence = self.scraper.scan_merchant({
            "merchant_id":   record["record_id"],
            "merchant_name": record.get("merchant_name", ""),
            "region":        record.get("region", ""),
            "base_url":      site_url,
        })
        landing = self.scraper.visits[0] if self.scraper.visits else {}
        if landing.get("bot_wall"):
            return ValidationOutcome(Status.NOT_VALIDATED, Reason.BLOCKED_BY_BOT_PROTECTION,
                                     f"HTTP {landing.get('status')} on landing page")
        if landing.get("error") or (landing.get("status") or 0) >= 400:
            return ValidationOutcome(Status.NOT_VALIDATED, Reason.SITE_UNREACHABLE,
                                     landing.get("error") or f"HTTP {landing.get('status')}")
        pages = ", ".join(v["label"] for v in self.scraper.visits if not v["error"])
        outcome = self._scored_outcome(evidence, screenshot=f"reports/screenshots/{record['record_id']}_landing.png")
        outcome.reason_detail = outcome.reason_detail or f"Pages scanned: {pages}"
        return outcome

    # ── Helpers ──────────────────────────────────────────────────────────

    def _scored_outcome(self, evidence: dict, **kwargs) -> ValidationOutcome:
        scored = self.scorer.score_merchant(evidence)
        pages  = {m: list(p) for m, p in self.scraper.evidence_pages.items()}
        if not any(r["signals"] for r in scored.values()):
            return ValidationOutcome(Status.VALIDATED, Reason.NO_PAYMENT_INFO_DISPLAYED,
                                     "No payment acceptance shown on the scanned page(s)",
                                     scored=scored, evidence_pages=pages, **kwargs)
        review = [m for m, r in scored.items() if r["status"] == "REVIEW"]
        if review:
            return ValidationOutcome(Status.NEEDS_REVIEW, Reason.LOW_CONFIDENCE,
                                     f"Manual review: {', '.join(review)}",
                                     scored=scored, evidence_pages=pages, **kwargs)
        return ValidationOutcome(Status.VALIDATED, scored=scored, evidence_pages=pages, **kwargs)

    @staticmethod
    def _error_outcome(error: Exception) -> ValidationOutcome:
        message = str(error).splitlines()[0]
        if any(code in message for code in NETWORK_ERRORS) or isinstance(error, PlaywrightTimeoutError):
            return ValidationOutcome(Status.NOT_VALIDATED, Reason.SITE_UNREACHABLE, message)
        logger.exception("Unexpected validation error")
        return ValidationOutcome(Status.NOT_VALIDATED, Reason.UNEXPECTED_ERROR, message)
