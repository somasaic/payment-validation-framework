"""
Master Dataset Validation — Integration Tests
=============================================
TC_310  Every master-dataset record → expected status, reason and Yes/No values
        (store locations via locator, site-level merchants, every reason code)
TC_311  Location scan is scoped: site-wide footer logos don't leak into a store
TC_312  CLI run is resumable: a second run completes only the pending rows
TC_313  Stale site profile (selectors no longer match) → LOCATOR_PAGE_CHANGED, not retried
TC_314  Store locator moved (HTTP 404) → LOCATOR_PAGE_CHANGED
TC_315  503 maintenance page → SITE_UNREACHABLE, not mistaken for bot protection
TC_316  robots.txt disallows the store locator → DISALLOWED_BY_ROBOTS, page never requested
TC_317  robots.txt disallows one store page → DISALLOWED_BY_ROBOTS before it is opened
TC_318  Site scan never requests a page robots.txt disallows (M001 checkout)
TC_319  Live allowlist: unlisted domains are NOT_ALLOWLISTED; CLI with an empty allowlist requests nothing
"""

import csv
import dataclasses
import subprocess
import sys
from pathlib import Path

import pytest

from mock_merchant import catalog, chain
from mock_merchant.server import MockMerchantServer, rewrite_url
from pages.site_profile import profile_for_url
from scrapers import record_validator
from scrapers.record_validator import RecordValidator
from utils.csv_reader import load_detection_rules
from utils.master_dataset import load_master
from utils.site_policy import SitePolicy

ROOT = Path(__file__).resolve().parents[2]
RULES = load_detection_rules()
MASTER, _, METHODS = load_master(str(ROOT / "data" / "master_dataset.csv"), set(RULES["payment_methods"]))
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


def _read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


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

    subprocess.run(cmd + ["--limit", "1", "--output", str(out)], check=True, capture_output=True, cwd=ROOT)
    first = _read_csv(out)
    assert [r["record_id"] for r in first] == ["R0011"]

    subprocess.run(cmd + ["--resume", str(out)], check=True, capture_output=True, cwd=ROOT)
    final = _read_csv(out)
    assert [r["record_id"] for r in final] == ["R0011", "R0012"]
    assert final[0]["validated_at"] == first[0]["validated_at"]
    assert final[1]["visa_accepted"] == "Yes" and final[1]["amex_accepted"] == "No"


FAST_RULES = {**RULES, "page_readiness": {**RULES["page_readiness"], "settle_timeout_ms": 2000,
                                          "payment_context_timeout_ms": 500}}


def _validate_with(browser, base_url, record_id, rules=RULES, route=None, policy=None, requests=None):
    record = RECORDS[record_id]
    context = browser.new_context()
    try:
        page = context.new_page()
        if route:
            page.route(*route)
        if requests is not None:
            page.on("request", lambda r: requests.append(r.url))
        return RecordValidator(page, rules, policy).validate(record, rewrite_url(record["website_url"], base_url))
    finally:
        context.close()


@pytest.mark.parametrize("field, stale_value", [
    ("search_input",   "role=searchbox[name=\"Search restaurants\"]"),
    ("result_address", "[data-testid='store-address-line']"),
    ("store_content",  "[data-testid='restaurant-detail']"),
])
def test_stale_site_profile(browser, base_url, monkeypatch, field, stale_value):
    """TC_313"""
    stale = dataclasses.replace(profile_for_url(RECORDS["R0001"]["website_url"]), **{field: stale_value})
    monkeypatch.setattr(record_validator, "profile_for_url", lambda url: stale)

    outcome = _validate_with(browser, base_url, "R0001", FAST_RULES)

    assert (outcome.status, outcome.reason) == ("NOT_VALIDATED", "LOCATOR_PAGE_CHANGED")
    assert "needs updating" in outcome.reason_detail
    assert not outcome.retryable


def test_locator_moved(browser, base_url, monkeypatch):
    """TC_314"""
    moved = dataclasses.replace(profile_for_url(RECORDS["R0001"]["website_url"]), locator_path="/restaurant-finder")
    monkeypatch.setattr(record_validator, "profile_for_url", lambda url: moved)

    outcome = _validate_with(browser, base_url, "R0001")

    assert (outcome.status, outcome.reason) == ("NOT_VALIDATED", "LOCATOR_PAGE_CHANGED")
    assert "HTTP 404" in outcome.reason_detail


def test_maintenance_page_is_not_bot_wall(browser, base_url):
    """TC_315"""
    maintenance = ("**/*", lambda route: route.fulfill(
        status=503, content_type="text/html",
        body="<h1>Down for maintenance</h1><p>We'll be back shortly.</p>"))

    outcome = _validate_with(browser, base_url, "R0015", route=maintenance)

    assert (outcome.status, outcome.reason) == ("NOT_VALIDATED", "SITE_UNREACHABLE")
    assert "503" in outcome.reason_detail


@pytest.fixture(scope="module")
def robots_server():
    """Mock service whose robots.txt forbids the AU locator, one US store page and M001's checkout."""
    robots = ("User-agent: *\n"
              "Disallow: /chain/au/stores\n"
              "Disallow: /chain/us/stores/US-1002\n"
              "Disallow: /store/M001/checkout\n")
    with MockMerchantServer(robots_txt=robots) as server:
        yield server.url


def test_robots_disallows_locator(browser, robots_server):
    """TC_316"""
    requests = []
    outcome = _validate_with(browser, robots_server, "R0011", policy=SitePolicy(), requests=requests)
    assert (outcome.status, outcome.reason) == ("NOT_VALIDATED", "DISALLOWED_BY_ROBOTS")
    assert not any("/chain/au/" in url for url in requests), "disallowed page was requested"


def test_robots_disallows_store_page(browser, robots_server):
    """TC_317"""
    requests = []
    outcome = _validate_with(browser, robots_server, "R0002", policy=SitePolicy(), requests=requests)
    assert (outcome.status, outcome.reason) == ("NOT_VALIDATED", "DISALLOWED_BY_ROBOTS")
    assert not any(url.endswith("/stores/US-1002") for url in requests)
    assert _validate_with(browser, robots_server, "R0001", policy=SitePolicy()).status == "VALIDATED"


def test_site_scan_skips_disallowed_pages(browser, robots_server):
    """TC_318 — M001 shows wallets only at checkout, which robots.txt forbids."""
    requests = []
    outcome = _validate_with(browser, robots_server, "R0015", policy=SitePolicy(), requests=requests)
    assert not any(url.split("?")[0].endswith("/store/M001/checkout") for url in requests)
    assert outcome.status == "VALIDATED"
    assert outcome.scored["visa"]["status"] == "AUTO_ACCEPT"        # footer cards still seen
    assert outcome.scored["paypal"]["status"] == "AUTO_REJECT"      # checkout-only, never visited


def test_live_allowlist(browser, base_url, tmp_path):
    """TC_319"""
    policy = SitePolicy(allowlist=["us.crustandco.example"])
    requests = []
    blocked = _validate_with(browser, base_url, "R0007", policy=policy, requests=requests)
    assert (blocked.status, blocked.reason) == ("NOT_VALIDATED", "NOT_ALLOWLISTED")
    assert requests == []
    assert _validate_with(browser, base_url, "R0001", policy=policy).status == "VALIDATED"

    empty, out = tmp_path / "allowlist.txt", tmp_path / "live.csv"
    empty.write_text("# nothing approved\n", encoding="utf-8")
    subprocess.run([sys.executable, "run_master_validation.py", "--region", "AU", "--workers", "1",
                    "--allowlist", str(empty), "--output", str(out)],
                   check=True, capture_output=True, cwd=ROOT)
    assert {r["reason"] for r in _read_csv(out)} == {"NOT_ALLOWLISTED"}
