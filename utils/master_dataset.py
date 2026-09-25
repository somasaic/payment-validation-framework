"""
Master dataset I/O
==================
Input: one row per merchant (site-level) or per store location, e.g.

  record_id, merchant_name, brand, region, country_code, website_url,
  store_address, postal_code, amex_accepted, visa_accepted, ..., validation_status, reason

Every `<method>_accepted` column is filled with Yes / No / Review. Rows with a
store_address go through the brand's store locator; rows without one get a
site-level multi-page scan.

Output: the same columns filled in, plus evidence columns. Rows are appended
to the output as they finish (checkpoint), so a 10k-row run can be resumed.
"""

import csv
import os
import threading
from collections import Counter
from datetime import datetime, timezone

METHOD_SUFFIX = "_accepted"

ACCEPTANCE_VALUE = {"AUTO_ACCEPT": "Yes", "REVIEW": "Review", "AUTO_REJECT": "No"}


class Reason:
    SITE_UNREACHABLE          = "SITE_UNREACHABLE"
    BLOCKED_BY_BOT_PROTECTION = "BLOCKED_BY_BOT_PROTECTION"
    LOCATOR_NOT_SUPPORTED     = "LOCATOR_NOT_SUPPORTED"
    LOCATOR_PAGE_CHANGED      = "LOCATOR_PAGE_CHANGED"
    NOT_ALLOWLISTED           = "NOT_ALLOWLISTED"
    DISALLOWED_BY_ROBOTS      = "DISALLOWED_BY_ROBOTS"
    STORE_NOT_FOUND           = "STORE_NOT_FOUND"
    STORE_TEMPORARILY_CLOSED  = "STORE_TEMPORARILY_CLOSED"
    NO_PAYMENT_INFO_DISPLAYED = "NO_PAYMENT_INFO_DISPLAYED"
    LOW_CONFIDENCE            = "LOW_CONFIDENCE"
    UNEXPECTED_ERROR          = "UNEXPECTED_ERROR"


# Transient outcomes: validated again when a run is resumed
RETRY_ON_RESUME = frozenset({Reason.SITE_UNREACHABLE, Reason.UNEXPECTED_ERROR})


class Status:
    VALIDATED     = "VALIDATED"
    NEEDS_REVIEW  = "NEEDS_REVIEW"
    NOT_VALIDATED = "NOT_VALIDATED"


EVIDENCE_COLUMNS = [
    "matched_address", "match_score", "evidence_pages", "evidence_screenshot",
    "reason_detail", "validated_at",
]


def load_master(path: str, known_methods: set[str]) -> tuple[list[dict], list[str], list[str]]:
    """Returns (rows, input fieldnames, method codes tracked by the dataset)."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])

    methods = [c[: -len(METHOD_SUFFIX)] for c in fieldnames if c.endswith(METHOD_SUFFIX)]
    unknown = set(methods) - known_methods
    if unknown:
        raise ValueError(f"Master dataset tracks methods with no detection rules: {sorted(unknown)}")
    for col in ("record_id", "website_url", "validation_status", "reason"):
        if col not in fieldnames:
            raise ValueError(f"Master dataset missing required column: {col}")
    ids = [r["record_id"] for r in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("Master dataset has duplicate record_id values")
    return rows, fieldnames, methods


def output_fieldnames(input_fields: list[str]) -> list[str]:
    return input_fields + [c for c in EVIDENCE_COLUMNS if c not in input_fields]


def fill_row(row: dict, methods: list[str], outcome) -> dict:
    out = dict(row)
    for m in methods:
        scored = outcome.scored.get(m)
        out[f"{m}{METHOD_SUFFIX}"] = ACCEPTANCE_VALUE[scored["status"]] if scored else ""
    out["validation_status"]   = outcome.status
    out["reason"]              = outcome.reason
    out["reason_detail"]       = outcome.reason_detail
    out["matched_address"]     = outcome.matched_address
    out["match_score"]         = f"{outcome.match_score:.2f}" if outcome.match_score is not None else ""
    out["evidence_pages"]      = "; ".join(
        f"{m}: {'|'.join(pages)}" for m, pages in outcome.evidence_pages.items() if pages and m in methods
    )
    out["evidence_screenshot"] = outcome.screenshot or ""
    out["validated_at"]        = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return out


class CheckpointWriter:
    """Thread-safe append-only CSV writer; rows already present are skipped on resume."""

    def __init__(self, path: str, fieldnames: list[str]):
        self.path = path
        self.fieldnames = fieldnames
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            with open(path, "w", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, fieldnames=fieldnames).writeheader()

    def done_ids(self, retry_reasons: frozenset[str] = frozenset()) -> set[str]:
        """Records already in the checkpoint, except those whose latest reason is
        in retry_reasons (they are validated again; the newer row wins)."""
        with open(self.path, newline="", encoding="utf-8") as f:
            latest = {r["record_id"]: r.get("reason", "") for r in csv.DictReader(f)}
        return {rid for rid, reason in latest.items() if reason not in retry_reasons}

    def append(self, row: dict):
        with self._lock, open(self.path, "a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=self.fieldnames, extrasaction="ignore").writerow(row)

    def finalize(self, order: list[str]) -> list[dict]:
        """Rewrite the checkpoint in master-dataset order, keeping the latest row
        per record (a retried record's newer result replaces the old one)."""
        with open(self.path, newline="", encoding="utf-8") as f:
            rows = {r["record_id"]: r for r in csv.DictReader(f)}
        ordered = [rows[i] for i in order if i in rows]
        with open(self.path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=self.fieldnames)
            writer.writeheader()
            writer.writerows(ordered)
        return ordered


def summarize(rows: list[dict], methods: list[str]) -> dict:
    return {
        "records":   len(rows),
        "by_status": dict(Counter(r["validation_status"] for r in rows)),
        "by_reason": dict(Counter(r["reason"] for r in rows if r["reason"])),
        "accepted":  {m: sum(r[f"{m}{METHOD_SUFFIX}"] == "Yes" for r in rows) for m in methods},
    }
