"""
Report Writer
=============
Generates two outputs:

1. validation_results_<timestamp>.csv
   — Full structured dataset (one row per merchant per method)
   — This is the deliverable that would go to the client (Amex)

2. review_queue_<timestamp>.csv
   — Only rows with status=REVIEW (manual QA needed)

3. Console summary with pass rate, review count, auto-reject count
"""

import csv
import os
import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)
REPORTS_DIR = "reports"


def write_full_report(merchant_results: list[dict], filename: str = None) -> str:
    """
    merchant_results: list of {
        merchant_id, merchant_name, region, country_code,
        base_url, platform, segment,
        scored_methods: { method: { score, status, detected, signals } },
        run_timestamp
    }
    """
    os.makedirs(REPORTS_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = filename or f"validation_results_{ts}.csv"
    filepath = os.path.join(REPORTS_DIR, filename)

    fieldnames = [
        "merchant_id", "merchant_name", "region", "country_code",
        "base_url", "platform", "segment",
        "payment_method", "detected", "confidence_score", "status",
        "signals_fired", "screenshot_landing", "screenshot_checkout",
        "run_timestamp"
    ]

    rows = []
    for mr in merchant_results:
        for method, result in mr["scored_methods"].items():
            rows.append({
                "merchant_id":          mr["merchant_id"],
                "merchant_name":        mr["merchant_name"],
                "region":               mr["region"],
                "country_code":         mr["country_code"],
                "base_url":             mr["base_url"],
                "platform":             mr["platform"],
                "segment":              mr["segment"],
                "payment_method":       method,
                "detected":             result["detected"],
                "confidence_score":     result["score"],
                "status":               result["status"],
                "signals_fired":        "|".join(result["signals"]),
                "screenshot_landing":   f"reports/screenshots/{mr['merchant_id']}_landing.png",
                "screenshot_checkout":  f"reports/screenshots/{mr['merchant_id']}_checkout.png",
                "run_timestamp":        mr["run_timestamp"],
            })

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    logger.info(f"Full report saved: {filepath} ({len(rows)} rows)")
    return filepath


def write_review_queue(merchant_results: list[dict], filename: str = None) -> str:
    """Write only REVIEW-status rows for manual QA triage."""
    os.makedirs(REPORTS_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = filename or f"review_queue_{ts}.csv"
    filepath = os.path.join(REPORTS_DIR, filename)

    fieldnames = [
        "merchant_id", "merchant_name", "region", "base_url",
        "payment_method", "confidence_score", "signals_fired",
        "screenshot_landing", "screenshot_checkout", "reviewer_decision", "notes"
    ]

    review_rows = []
    for mr in merchant_results:
        for method, result in mr["scored_methods"].items():
            if result["status"] == "REVIEW":
                review_rows.append({
                    "merchant_id":         mr["merchant_id"],
                    "merchant_name":       mr["merchant_name"],
                    "region":              mr["region"],
                    "base_url":            mr["base_url"],
                    "payment_method":      method,
                    "confidence_score":    result["score"],
                    "signals_fired":       "|".join(result["signals"]),
                    "screenshot_landing":  f"reports/screenshots/{mr['merchant_id']}_landing.png",
                    "screenshot_checkout": f"reports/screenshots/{mr['merchant_id']}_checkout.png",
                    "reviewer_decision":   "",   # QA fills this
                    "notes":               "",
                })

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(review_rows)

    logger.info(f"Review queue saved: {filepath} ({len(review_rows)} items need manual check)")
    return filepath


def print_console_summary(merchant_results: list[dict]):
    total_checks = sum(len(mr["scored_methods"]) for mr in merchant_results)
    auto_accept  = sum(
        1 for mr in merchant_results
        for r in mr["scored_methods"].values() if r["status"] == "AUTO_ACCEPT"
    )
    review       = sum(
        1 for mr in merchant_results
        for r in mr["scored_methods"].values() if r["status"] == "REVIEW"
    )
    auto_reject  = total_checks - auto_accept - review
    auto_rate    = (auto_accept / total_checks * 100) if total_checks else 0

    print("\n" + "=" * 65)
    print("  GLOBAL PAYMENT METHOD VALIDATION — RUN SUMMARY")
    print("=" * 65)
    print(f"  Merchants scanned  : {len(merchant_results)}")
    print(f"  Total checks       : {total_checks}")
    print(f"  ✅ AUTO ACCEPT     : {auto_accept}  ({auto_rate:.1f}%)")
    print(f"  🔍 NEEDS REVIEW    : {review}")
    print(f"  ❌ AUTO REJECT     : {auto_reject}")
    print("=" * 65)
    print("\n  AUTO-ACCEPTED (High confidence detected):")
    for mr in merchant_results:
        accepted = [m for m, r in mr["scored_methods"].items() if r["detected"]]
        if accepted:
            print(f"  [{mr['region']}] {mr['merchant_name']}: {', '.join(accepted)}")
    print()
