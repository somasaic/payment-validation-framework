"""
run_validation.py
=================
Main pipeline runner. Orchestrates:
  1. Load merchants from CSV
  2. For each merchant: scrape → score → collect
  3. Write full report CSV + review queue CSV
  4. Print console summary

Usage:
  python run_validation.py                    # All merchants
  python run_validation.py --region US        # Filter by region
  python run_validation.py --merchant M001    # Single merchant

OUTPUT:
  reports/validation_results_<ts>.csv   ← full dataset (client deliverable)
  reports/review_queue_<ts>.csv          ← manual QA queue
  reports/screenshots/<id>_landing.png   ← evidence screenshots
  reports/screenshots/<id>_checkout.png
"""

import argparse
import logging
import os
from datetime import datetime
from playwright.sync_api import sync_playwright

from utils.csv_reader import load_merchants, load_detection_rules
from scrapers.payment_scraper import PaymentMethodScraper
from validators.confidence_scorer import ConfidenceScorer
from utils.report_writer import write_full_report, write_review_queue, print_console_summary

# ── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

os.makedirs("reports/screenshots", exist_ok=True)


# ── Pipeline ─────────────────────────────────────────────────────────────────

def run_pipeline(merchants: list[dict], detection_rules: dict) -> list[dict]:
    scorer = ConfidenceScorer(detection_rules)
    all_results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )

        for merchant in merchants:
            context = browser.new_context(
                viewport={"width": 1280, "height": 800},
                locale="en-US",
                extra_http_headers={
                    "Accept-Language": "en-US,en;q=0.9",
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                }
            )
            page = context.new_page()

            try:
                scraper  = PaymentMethodScraper(page, detection_rules)
                evidence = scraper.scan_merchant(merchant)
                scored   = scorer.score_merchant(evidence)

                all_results.append({
                    **merchant,
                    "scored_methods": scored,
                    "run_timestamp":  datetime.now().isoformat(),
                })

                # Per-merchant summary
                detected = [m for m, r in scored.items() if r["detected"]]
                review   = [m for m, r in scored.items() if r["status"] == "REVIEW"]
                logger.info(
                    f"[{merchant['merchant_id']}] ✅ Detected: {detected} | "
                    f"🔍 Review: {review}"
                )

            except Exception as e:
                logger.error(f"[{merchant['merchant_id']}] Pipeline error: {e}")
                all_results.append({
                    **merchant,
                    "scored_methods": {},
                    "run_timestamp":  datetime.now().isoformat(),
                    "error": str(e),
                })
            finally:
                context.close()

        browser.close()

    return all_results


# ── Entrypoint ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Global Payment Method Validation Pipeline")
    parser.add_argument("--region",   help="Filter merchants by region code (e.g. US, GB, SG)")
    parser.add_argument("--merchant", help="Run single merchant by ID (e.g. M001)")
    args = parser.parse_args()

    all_merchants    = load_merchants()
    detection_rules  = load_detection_rules()

    # Apply filters
    merchants = all_merchants
    if args.region:
        merchants = [m for m in merchants if m["region"].upper() == args.region.upper()]
        logger.info(f"Region filter '{args.region}': {len(merchants)} merchants")
    if args.merchant:
        merchants = [m for m in merchants if m["merchant_id"].upper() == args.merchant.upper()]
        logger.info(f"Single merchant filter '{args.merchant}'")

    if not merchants:
        logger.error("No merchants matched filter. Check --region or --merchant value.")
        return

    logger.info(f"Starting pipeline: {len(merchants)} merchant(s) to scan")
    results = run_pipeline(merchants, detection_rules)

    # Write outputs
    full_path   = write_full_report(results)
    review_path = write_review_queue(results)
    print_console_summary(results)

    logger.info(f"Full report  : {full_path}")
    logger.info(f"Review queue : {review_path}")


if __name__ == "__main__":
    main()
