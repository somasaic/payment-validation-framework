"""
Master Dataset Validation — Integration Tests
=============================================
TC_310  Every master-dataset record → expected status, reason and Yes/No values
        (store locations via locator, site-level merchants, every reason code)
TC_311  Location scan is scoped: site-wide footer logos don't leak into a store
TC_312  CLI run is resumable: a second run completes only the pending rows
"""

import csv
import subprocess
import sys

import pytest

from mock_merchant import catalog, chain
from mock_merchant.server import rewrite_url
from scrapers.record_validator import RecordValidator
from utils.csv_reader import load_detection_rules
from utils.master_dataset import load_master

RULES = load_detection_rules()
MASTER, _, METHODS = load_master("data/master_dataset.csv", set(RULES["payment_methods"]))
RECORDS = {r["record_id"]: r for r in MASTER}

STORE_FOR_RECORD = {
    "R0001": "US-1001", "R0002": "US-1002", "R0003": "US-1003", "R0007": "UK-2001",
    "R0008": "UK-2002", "R0009": "IN-3001", "R0010": "IN-3002", "R0011": "AU-4001", "R0012": "AU-4002",
}
SITE_FOR_RECORD = {"R0015": "M001", "R0016": "M002", "R0017": "M004", "R0018": "M010"}

EXPECTED = {
    **{rid: ("VALIDATED", "") for rid in list(STORE_FOR_RECORD) + ["R0015", "R0016", "R0017"]},
    "R0004": ("VALIDATED",     "NO_PAYMENT_INFO_DISPLAYED"),
    "R0005": ("NOT_VALIDATED", "STORE_TEMPORARILY_CLOSED"),
    "R0006": ("NOT_VALIDATED", "STORE_NOT_FOUND"),
    "R0013": ("NOT_VALIDATED", "BLOCKED_BY_BOT_PROTECTION"),
    "R0014": ("NOT_VALIDATED", "SITE_UNREACHABLE"),
    "R0018": ("NEEDS_REVIEW",  "LOCATOR_NOT_SUPPORTED"),
}


def _expected_yes(record_id: str) -> set[str] | None:
    if record_id in STORE_FOR_RECORD:
        return set(chain.get_store(STORE_FOR_RECORD[record_id])["accepted_methods"]) & set(METHODS)
    if record_id in SITE_FOR_RECORD:
        return set(catalog.get_merchant(SITE_FOR_RECORD[record_id])["accepted_methods"]) & set(METHODS)
    if record_id == "R0004":
        return set()
    return None


@pytest.fixture(scope="module")
def validate(browser, base_url):
    def _validate(record_id: str):
        record = RECORDS[record_id]
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        try:
            return RecordValidator(context.new_page(), RULES).validate(
                record, rewrite_url(record["website_url"], base_url))
        finally:
            context.close()
    return _validate


@pytest.mark.parametrize("record_id", sorted(RECORDS))
def test_record_outcome(validate, record_id):
    """TC_310"""
    outcome = validate(record_id)
    assert (outcome.status, outcome.reason) == EXPECTED[record_id], outcome.reason_detail

    expected_yes = _expected_yes(record_id)
    if expected_yes is not None:
        yes = {m for m in METHODS if outcome.scored.get(m, {}).get("status") == "AUTO_ACCEPT"}
        assert yes == expected_yes
        assert not any(outcome.scored[m]["status"] == "REVIEW" for m in METHODS)


def test_location_scan_scoped_to_store(validate):
    """TC_311 — US footer shows Amex; Embarcadero (R0002) doesn't take it."""
    assert "amex" in chain.data()["regions"]["us"]["footer_methods"]
    outcome = validate("R0002")
    assert outcome.scored["amex"]["signals"] == []
    assert outcome.evidence_pages.get("amex") == []


def test_cli_resume(tmp_path):
    """TC_312"""
    out = tmp_path / "au.csv"
    cmd = [sys.executable, "run_master_validation.py", "--mock", "--region", "AU",
           "--workers", "1", "--domain-interval", "0"]

    subprocess.run(cmd + ["--limit", "1", "--output", str(out)], check=True, capture_output=True)
    first = list(csv.DictReader(out.open(encoding="utf-8")))
    assert [r["record_id"] for r in first] == ["R0011"]

    subprocess.run(cmd + ["--resume", str(out)], check=True, capture_output=True)
    final = list(csv.DictReader(out.open(encoding="utf-8")))
    assert [r["record_id"] for r in final] == ["R0011", "R0012"]
    assert final[0]["validated_at"] == first[0]["validated_at"]
    assert final[1]["visa_accepted"] == "Yes" and final[1]["amex_accepted"] == "No"
