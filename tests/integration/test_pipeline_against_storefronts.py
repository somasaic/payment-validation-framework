"""
Pipeline Integration Tests
==========================
Real scraper + scorer against the mock storefronts, compared to ground truth.

TC_301  Every merchant: accepted methods → AUTO_ACCEPT, all others → AUTO_REJECT
TC_302  Slow checkout widget (4s): wallets/BNPL still detected
        (regression — a fixed 2s post-navigation sleep scanned the checkout
        before the widget rendered, so checkout-only methods were missed)
TC_303  Scored results accepted by the validation-runs API with zero mismatches
"""

import pytest

from mock_merchant import catalog
from mock_merchant.server import MockMerchantServer
from scrapers.payment_scraper import PaymentMethodScraper
from utils.csv_reader import load_detection_rules
from validators.confidence_scorer import ConfidenceScorer

RULES = load_detection_rules()


@pytest.fixture(scope="module")
def scan(browser):
    def _scan(base_url: str, merchant_id: str) -> dict:
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        try:
            merchant = {**catalog.get_merchant(merchant_id), "base_url": f"{base_url}/store/{merchant_id}"}
            evidence = PaymentMethodScraper(context.new_page(), RULES).scan_merchant(merchant)
            return ConfidenceScorer(RULES).score_merchant(evidence)
        finally:
            context.close()
    return _scan


def _status_by_expectation(scored: dict, accepted: list[str]) -> tuple[set, set]:
    missed = {m for m in accepted if scored[m]["status"] != "AUTO_ACCEPT"}
    false_positives = {m for m, r in scored.items() if m not in accepted and r["status"] != "AUTO_REJECT"}
    return missed, false_positives


@pytest.mark.parametrize("merchant_id", sorted(catalog.merchants()))
def test_detection_matches_ground_truth(scan, base_url, ground_truth, merchant_id):
    """TC_301"""
    scored = scan(base_url, merchant_id)
    missed, false_positives = _status_by_expectation(scored, ground_truth[merchant_id])
    assert not missed, f"accepted but not detected: {missed}"
    assert not false_positives, f"detected but not accepted: {false_positives}"


def test_slow_checkout_widget_still_detected(scan, ground_truth):
    """TC_302"""
    with MockMerchantServer(payment_latency_ms=4000) as slow:
        scored = scan(slow.url, "M001")

    checkout_only = {"paypal", "apple_pay", "google_pay", "shop_pay"}
    assert {m for m, r in scored.items() if r["detected"]} >= checkout_only
    missed, false_positives = _status_by_expectation(scored, ground_truth["M001"])
    assert not missed and not false_positives


@pytest.mark.parametrize("merchant_id", ["M002", "M008"])
def test_results_published_without_mismatches(scan, base_url, api, api_key, merchant_id):
    """TC_303"""
    scored = scan(base_url, merchant_id)
    payload = {
        "merchant_id": merchant_id,
        "results": [
            {"payment_method": m, "detected": r["detected"], "confidence_score": r["score"],
             "status": r["status"], "signals": r["signals"]}
            for m, r in scored.items()
        ],
    }
    resp = api.post("/api/v1/validation-runs", data=payload, headers={"X-API-Key": api_key})
    assert resp.status == 201, resp.text()
    assert resp.json()["mismatches"] == []
    assert resp.json()["summary"]["total"] == len(RULES["payment_methods"])
