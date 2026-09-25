"""
Data Loading & Report Writer Unit Tests
=======================================
(Confidence scorer tests: test_confidence_scorer.py)
TC_005  CSV loader — merchants.csv loads correct count
TC_006  Detection rules — all required methods present
TC_007  Report writer — full CSV written with correct columns
TC_008  Report writer — review queue contains only REVIEW rows
"""

import os
import csv

import pytest

from utils.csv_reader import load_merchants, load_detection_rules
from utils.report_writer import write_full_report, write_review_queue


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def detection_rules():
    return load_detection_rules()


# ── TC_005 ────────────────────────────────────────────────────────────────────

def test_merchants_csv_loads(detection_rules):
    """TC_005 — merchants.csv loads and has expected columns."""
    merchants = load_merchants()
    assert len(merchants) >= 10, "Expected at least 10 merchants"
    required_cols = {"merchant_id", "merchant_name", "region", "base_url", "platform"}
    for col in required_cols:
        assert col in merchants[0], f"Missing column: {col}"


# ── TC_006 ────────────────────────────────────────────────────────────────────

def test_detection_rules_methods_present(detection_rules):
    """TC_006 — All major payment methods defined in detection_rules.json."""
    required = {"amex", "visa", "mastercard", "paypal", "apple_pay", "google_pay"}
    defined  = set(detection_rules["payment_methods"].keys())
    missing  = required - defined
    assert not missing, f"Missing methods in rules: {missing}"


# ── TC_007 ────────────────────────────────────────────────────────────────────

def test_full_report_csv_written(tmp_path, detection_rules):
    """TC_007 — write_full_report outputs CSV with correct columns."""
    os.makedirs("reports", exist_ok=True)

    fake_results = [{
        "merchant_id": "M001",
        "merchant_name": "Test Merchant",
        "region": "US",
        "country_code": "US",
        "base_url": "https://test.com",
        "platform": "shopify",
        "segment": "ecommerce",
        "scored_methods": {
            "amex": {"score": 0.9, "status": "AUTO_ACCEPT", "detected": True, "signals": ["network", "img"]},
            "visa": {"score": 0.3, "status": "AUTO_REJECT", "detected": False, "signals": []},
        },
        "run_timestamp": "2026-06-08T10:00:00",
    }]

    filepath = write_full_report(fake_results, filename="test_report.csv")
    assert os.path.exists(filepath)

    with open(filepath, newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    assert len(rows) == 2   # 1 merchant × 2 methods
    assert rows[0]["payment_method"] in ("amex", "visa")
    assert "confidence_score" in rows[0]
    assert "status" in rows[0]
    os.remove(filepath)


# ── TC_008 ────────────────────────────────────────────────────────────────────

def test_review_queue_contains_only_review_rows(detection_rules):
    """TC_008 — Review queue CSV has only REVIEW-status rows."""
    os.makedirs("reports", exist_ok=True)

    fake_results = [{
        "merchant_id": "M002",
        "merchant_name": "Review Test",
        "region": "GB",
        "base_url": "https://test.co.uk",
        "scored_methods": {
            "amex":   {"score": 0.9,  "status": "AUTO_ACCEPT", "detected": True,  "signals": ["network"]},
            "visa":   {"score": 0.6,  "status": "REVIEW",      "detected": False, "signals": ["text"]},
            "paypal": {"score": 0.2,  "status": "AUTO_REJECT",  "detected": False, "signals": []},
        },
        "run_timestamp": "2026-06-08T10:00:00",
    }]

    filepath = write_review_queue(fake_results, filename="test_review.csv")
    assert os.path.exists(filepath)

    with open(filepath, newline="") as f:
        rows = list(csv.DictReader(f))

    assert len(rows) == 1             # Only visa is REVIEW
    assert rows[0]["payment_method"] == "visa"
    os.remove(filepath)
