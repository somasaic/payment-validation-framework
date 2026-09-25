"""
run_master_validation.py
========================
Validates a master dataset (thousands of merchants / store locations) and
writes it back with every `<method>_accepted` column filled (Yes/No/Review),
a validation_status, a reason code for anything that could not be validated,
and evidence (matched address, pages, screenshot).

Built for large batches:
  - N worker threads, each with its own browser; fresh context per record
  - per-domain throttle so one brand's site is never hammered
  - checkpoint file appended as rows finish; --resume skips completed rows
  - retry of transient failures (unreachable / unexpected errors)

Safeguards: robots.txt is honoured for every request, and live runs only
touch domains listed in data/live_allowlist.txt (empty by default).

Usage:
  python run_master_validation.py                                   # data/master_dataset.csv
  python run_master_validation.py --workers 6 --region US
  python run_master_validation.py --resume reports/master_validated_20260925_101500.csv
      (skips finished records; SITE_UNREACHABLE / UNEXPECTED_ERROR ones are tried again)
  python run_master_validation.py --mock                            # local mock sites (CI)
  python run_master_validation.py --allowlist my_allowlist.txt      # live, approved domains only
"""

import argparse
import contextlib
import json
import logging
import queue
import sys
import threading
import time
from datetime import datetime
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

from scrapers.record_validator import RecordValidator, ValidationOutcome
from utils.csv_reader import load_detection_rules
from utils.site_policy import DEFAULT_ALLOWLIST, SitePolicy, load_allowlist
from utils.master_dataset import (
    RETRY_ON_RESUME, CheckpointWriter, Reason, Status, fill_row, load_master, output_fieldnames, summarize,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(threadName)s %(name)s - %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


class DomainThrottle:
    """Minimum interval between record starts on the same domain."""

    def __init__(self, min_interval_s: float):
        self.min_interval_s = min_interval_s
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, url: str):
        domain = urlparse(url).netloc
        while True:
            with self._lock:
                now = time.monotonic()
                ready_at = self._last.get(domain, 0) + self.min_interval_s
                if now >= ready_at:
                    self._last[domain] = now
                    return
            time.sleep(ready_at - now)


def worker(jobs: queue.Queue, rules: dict, methods: list[str], writer: CheckpointWriter,
           throttle: DomainThrottle, retries: int, url_for, progress: dict, policy: SitePolicy):
    with sync_playwright() as p:
        launch = lambda: p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])  # noqa: E731
        browser = launch()
        try:
            while True:
                try:
                    record = jobs.get_nowait()
                except queue.Empty:
                    return
                target = url_for(record["website_url"])
                outcome = None
                for attempt in range(retries + 1):
                    if not browser.is_connected():  # a crashed browser must not fail every later record
                        logger.warning("Browser disconnected — relaunching")
                        browser = launch()
                    throttle.wait(target)
                    context = None
                    try:
                        context = browser.new_context(viewport={"width": 1280, "height": 800}, locale="en-US")
                        outcome = RecordValidator(context.new_page(), rules, policy).validate(record, target)
                    except Exception as e:  # never lose a row to one bad site
                        logger.exception(f"[{record['record_id']}] validation crashed")
                        outcome = ValidationOutcome(Status.NOT_VALIDATED, Reason.UNEXPECTED_ERROR, str(e)[:200])
                    finally:
                        if context:
                            with contextlib.suppress(Exception):
                                context.close()
                    if not outcome.retryable or attempt == retries:
                        break
                    logger.info(f"[{record['record_id']}] {outcome.reason} — retry {attempt + 1}/{retries}")

                writer.append(fill_row(record, methods, outcome))
                with progress["lock"]:
                    progress["done"] += 1
                    logger.info(f"[{record['record_id']}] {outcome.status} {outcome.reason} "
                                f"({progress['done']}/{progress['total']})")
        finally:
            with contextlib.suppress(Exception):
                browser.close()


def _drain(jobs: queue.Queue) -> int:
    """Remove pending records so workers stop after their current one."""
    dropped = 0
    while True:
        try:
            jobs.get_nowait()
            dropped += 1
        except queue.Empty:
            return dropped


def main():
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Validate payment acceptance for a master dataset")
    parser.add_argument("--input", default="data/master_dataset.csv")
    parser.add_argument("--output", help="Output CSV (default: reports/master_validated_<ts>.csv)")
    parser.add_argument("--resume", help="Existing output CSV to continue; completed rows are skipped")
    parser.add_argument("--region", help="Only records for this region (e.g. US, GB, IN)")
    parser.add_argument("--limit", type=int, help="Only the first N pending records")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--domain-interval", type=float, default=1.0,
                        help="Min seconds between records on the same domain")
    parser.add_argument("--mock", action="store_true", help="Serve known websites from the local mock service")
    parser.add_argument("--allowlist", default=str(DEFAULT_ALLOWLIST),
                        help="Live runs: file of domains approved for scanning (ignored with --mock)")
    args = parser.parse_args()

    rules = load_detection_rules()
    rows, input_fields, methods = load_master(args.input, set(rules["payment_methods"]))
    if args.region:
        rows = [r for r in rows if r["region"].upper() == args.region.upper()]

    output = args.resume or args.output or \
        f"reports/master_validated_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    writer = CheckpointWriter(output, output_fieldnames(input_fields))
    done = writer.done_ids(retry_reasons=RETRY_ON_RESUME) if args.resume else set()
    pending = [r for r in rows if r["record_id"] not in done][: args.limit]
    logger.info(f"{len(rows)} records, {len(done)} already done, {len(pending)} to validate → {output}")

    with contextlib.ExitStack() as stack:
        url_for = lambda url: url  # noqa: E731
        if args.mock:
            from mock_merchant.server import MockMerchantServer, rewrite_url
            server = stack.enter_context(MockMerchantServer())
            url_for = lambda url: rewrite_url(url, server.url)  # noqa: E731
            policy = SitePolicy(allowlist=None)            # local mock: no allowlist; robots.txt still honoured
            logger.info(f"Mock mode: websites served from {server.url}")
        else:
            allowlist = load_allowlist(args.allowlist)
            policy = SitePolicy(allowlist=allowlist)
            logger.info(f"Live mode: {len(allowlist)} allowlisted domain pattern(s) from {args.allowlist}")
            if not allowlist:
                logger.warning("Allowlist is empty — every record will be NOT_ALLOWLISTED (nothing is requested)")

        jobs: queue.Queue = queue.Queue()
        for r in pending:
            jobs.put(r)
        progress = {"done": 0, "total": len(pending), "lock": threading.Lock()}
        throttle = DomainThrottle(args.domain_interval)
        threads = [
            threading.Thread(target=worker, name=f"worker-{i + 1}",
                             args=(jobs, rules, methods, writer, throttle, args.retries, url_for, progress, policy))
            for i in range(max(1, min(args.workers, len(pending))))
        ]
        for t in threads:
            t.start()
        try:
            # join with a timeout so Ctrl+C is delivered on every platform
            while any(t.is_alive() for t in threads):
                for t in threads:
                    t.join(timeout=0.5)
        except KeyboardInterrupt:
            dropped = _drain(jobs)
            logger.warning(f"Interrupted: finishing records in progress, {dropped} not started. "
                           f"Continue later with --resume {output}")
            for t in threads:
                t.join()

    final_rows = writer.finalize([r["record_id"] for r in rows])
    summary = summarize(final_rows, methods)
    print("\n" + "=" * 65)
    print("  MASTER DATASET VALIDATION — RUN SUMMARY")
    print("=" * 65)
    print(json.dumps(summary, indent=2))
    print(f"\n  Output: {output}\n")


if __name__ == "__main__":
    main()
