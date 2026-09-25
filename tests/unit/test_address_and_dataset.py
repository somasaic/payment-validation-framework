"""
Address Matcher + Master Dataset Unit Tests
===========================================
TC_010  Abbreviations and punctuation normalise to the same tokens
TC_011  Formatting differences still match (St./Street, M.G./MG, missing state)
TC_012  Different postal code or street number never matches
TC_013  best_match picks the right store among nearby candidates
TC_014  Master dataset: method columns resolved; unknown method rejected
TC_015  Master dataset: duplicate record_id / missing column rejected
TC_016  fill_row maps statuses to Yes / No / Review and records evidence
TC_017  Checkpoint writer supports resume and restores input order
TC_018  Resume retries transient failures; the newer result replaces the old row
"""

import csv

import pytest

from scrapers.record_validator import ValidationOutcome
from utils.address_matcher import best_match, normalize, score
from utils.master_dataset import RETRY_ON_RESUME, CheckpointWriter, Status, fill_row, load_master

KNOWN = {"amex", "visa", "mastercard", "paypal"}
HEADER = "record_id,merchant_name,website_url,store_address,postal_code,amex_accepted,visa_accepted,validation_status,reason\n"


def test_normalize():
    """TC_010"""
    assert normalize("225 W. 34th St., New York") == ["225", "west", "34th", "street", "new", "york"]
    assert normalize("2 Embarcadero Ctr") == normalize("2 Embarcadero Center")


@pytest.mark.parametrize("record, candidate, postal", [
    ("1450 Market St, San Francisco, CA", "1450 Market Street, San Francisco, CA 94103", "94103"),
    ("44 M.G. Road, Bengaluru",           "44 MG Road, Bengaluru, Karnataka 560001",     "560001"),
    ("10 Oxford St, London",              "10 Oxford Street, London W1D 1BS",            "W1D 1BS"),
    ("10 Oxford St, London",              "10 Oxford Street, London W1D 1BS",            "w1d1bs"),
])
def test_formatting_differences_match(record, candidate, postal):
    """TC_011"""
    assert score(record, candidate, postal) >= 0.8


@pytest.mark.parametrize("record, candidate, postal", [
    ("1450 Market St, San Francisco", "1450 Market Street, San Francisco, CA 94103", "94111"),
    ("1460 Market St, San Francisco", "1450 Market Street, San Francisco, CA 94103", "94103"),
])
def test_postal_or_number_mismatch_rejected(record, candidate, postal):
    """TC_012"""
    assert score(record, candidate, postal) == 0.0


def test_best_match_among_candidates():
    """TC_013"""
    candidates = [
        "2 Embarcadero Center, San Francisco, CA 94111",
        "1450 Market Street, San Francisco, CA 94103",
    ]
    match = best_match("1450 Market St, San Francisco", candidates, "94103")
    assert match.index == 1 and match.score == 1.0
    assert best_match("999 Harbor Blvd, Long Beach", candidates, "90802") is None


def test_load_master_methods(tmp_path):
    """TC_014"""
    path = tmp_path / "master.csv"
    path.write_text(HEADER + "R1,Shop,https://a.example,,,,,,\n", encoding="utf-8")
    rows, fields, methods = load_master(str(path), KNOWN)
    assert methods == ["amex", "visa"] and len(rows) == 1

    path.write_text(HEADER.replace("visa_accepted", "bitcoin_accepted") + "R1,Shop,https://a.example,,,,,,\n")
    with pytest.raises(ValueError, match="bitcoin"):
        load_master(str(path), KNOWN)


@pytest.mark.parametrize("content, error", [
    (HEADER + "R1,A,https://a.example,,,,,,\nR1,B,https://b.example,,,,,,\n", "duplicate record_id"),
    (HEADER.replace(",reason", ""), "missing required column: reason"),
])
def test_load_master_rejects_bad_input(tmp_path, content, error):
    """TC_015"""
    path = tmp_path / "master.csv"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match=error):
        load_master(str(path), KNOWN)


def test_fill_row():
    """TC_016"""
    outcome = ValidationOutcome(
        Status.NEEDS_REVIEW, "LOW_CONFIDENCE", "Manual review: visa",
        scored={
            "amex": {"status": "AUTO_ACCEPT", "score": 0.9, "detected": True, "signals": ["img"]},
            "visa": {"status": "REVIEW", "score": 0.6, "detected": False, "signals": ["text"]},
        },
        evidence_pages={"amex": ["store_page"], "visa": ["store_page"], "jcb": ["store_page"]},
        matched_address="1 Main Street", match_score=0.875,
    )
    row = fill_row({"record_id": "R1", "amex_accepted": "", "visa_accepted": ""}, ["amex", "visa"], outcome)
    assert row["amex_accepted"] == "Yes"
    assert row["visa_accepted"] == "Review"
    assert row["validation_status"] == "NEEDS_REVIEW"
    assert row["match_score"] == "0.88"
    assert row["evidence_pages"] == "amex: store_page; visa: store_page"


def test_checkpoint_resume_and_order(tmp_path):
    """TC_017"""
    path = str(tmp_path / "out.csv")
    writer = CheckpointWriter(path, ["record_id", "value"])
    writer.append({"record_id": "R2", "value": "b"})

    resumed = CheckpointWriter(path, ["record_id", "value"])
    assert resumed.done_ids() == {"R2"}
    resumed.append({"record_id": "R1", "value": "a"})

    rows = resumed.finalize(["R1", "R2", "R3"])
    assert [r["record_id"] for r in rows] == ["R1", "R2"]
    with open(path, newline="", encoding="utf-8") as f:
        assert [r["record_id"] for r in csv.DictReader(f)] == ["R1", "R2"]


def test_resume_retries_transient_failures(tmp_path):
    """TC_018"""
    path = str(tmp_path / "out.csv")
    writer = CheckpointWriter(path, ["record_id", "reason", "value"])
    writer.append({"record_id": "R1", "reason": "", "value": "done"})
    writer.append({"record_id": "R2", "reason": "SITE_UNREACHABLE", "value": ""})
    writer.append({"record_id": "R3", "reason": "STORE_NOT_FOUND", "value": ""})

    assert writer.done_ids(retry_reasons=RETRY_ON_RESUME) == {"R1", "R3"}   # R2 is tried again

    writer.append({"record_id": "R2", "reason": "", "value": "retried"})
    rows = writer.finalize(["R1", "R2", "R3"])
    assert [(r["record_id"], r["value"]) for r in rows] == [("R1", "done"), ("R2", "retried"), ("R3", "")]
