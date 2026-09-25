"""
Confidence Scorer Unit Tests
============================
TC_001  All signals → AUTO_ACCEPT
TC_002  No signals → score 0.0 → AUTO_REJECT
TC_003  Text only → 0.20 → AUTO_REJECT
TC_004  Network only → exactly the method's network weight
TC_009  Rounded score decides the status (0.4 + 0.3 + 0.1 floats to 0.7999…)
TC_019  Exact threshold boundaries: 0.80 / 0.799 / 0.50 / 0.499
TC_020  Score is capped at 1.0
TC_021  Missing weights fall back to DEFAULT_WEIGHTS; unknown methods use defaults entirely
TC_022  Thresholds come from the rules file, and an explicit argument overrides them
TC_023  signals lists only fired signals in a fixed order; detected only for AUTO_ACCEPT
TC_024  get_summary counts statuses
TC_025  Shipped rules: no single signal can reach REVIEW; all signals always reach AUTO_ACCEPT
"""

import pytest

from utils.csv_reader import load_detection_rules
from validators.confidence_scorer import DEFAULT_WEIGHTS, ConfidenceScorer

SIGNALS = ("network", "img", "alt", "text", "css_class")
RULES = load_detection_rules()


def evidence(*fired: str) -> dict:
    return {s: s in fired for s in SIGNALS}


def rules_with(weights: dict, thresholds: dict | None = None) -> dict:
    """Minimal rules file with one method 'm' using the given weights."""
    return {
        "payment_methods": {"m": {"confidence_weights": weights}},
        "confidence_thresholds": thresholds or {"auto_accept": 0.80, "review_required": 0.50},
    }


@pytest.fixture(scope="module")
def scorer():
    return ConfidenceScorer(RULES)


# ── Shipped rules ─────────────────────────────────────────────────────────────

def test_all_signals_auto_accept(scorer):
    """TC_001"""
    r = scorer.score_merchant({"amex": evidence(*SIGNALS)})["amex"]
    assert (r["score"], r["status"], r["detected"]) == (1.0, "AUTO_ACCEPT", True)


def test_no_signals_auto_reject(scorer):
    """TC_002"""
    r = scorer.score_merchant({"visa": evidence()})["visa"]
    assert (r["score"], r["status"], r["detected"], r["signals"]) == (0.0, "AUTO_REJECT", False, [])


def test_text_only_rejected(scorer):
    """TC_003"""
    r = scorer.score_merchant({"mastercard": evidence("text")})["mastercard"]
    assert (r["score"], r["status"]) == (0.2, "AUTO_REJECT")


@pytest.mark.parametrize("method", ["amex", "paypal", "upi"])
def test_network_only_uses_network_weight(scorer, method):
    """TC_004"""
    r = scorer.score_merchant({method: evidence("network")})[method]
    assert r["score"] == RULES["payment_methods"][method]["confidence_weights"]["network"]
    assert r["signals"] == ["network"]


@pytest.mark.parametrize("fired, expected_score, expected_status", [
    (("network", "img", "alt"), 0.80, "AUTO_ACCEPT"),
    (("network", "text"),       0.60, "REVIEW"),
    (("img", "text"),           0.50, "REVIEW"),
])
def test_rounded_score_decides_status(scorer, fired, expected_score, expected_status):
    """TC_009"""
    r = scorer.score_merchant({"amex": evidence(*fired)})["amex"]
    assert (r["score"], r["status"]) == (expected_score, expected_status)


# ── Boundaries and mechanics (controlled weights) ─────────────────────────────

@pytest.mark.parametrize("weight, expected", [
    (0.80,  "AUTO_ACCEPT"),   # exactly at auto_accept
    (0.799, "REVIEW"),        # just below
    (0.50,  "REVIEW"),        # exactly at review_required
    (0.499, "AUTO_REJECT"),   # just below
    (0.0,   "AUTO_REJECT"),
])
def test_threshold_boundaries(weight, expected):
    """TC_019"""
    r = ConfidenceScorer(rules_with({"network": weight})).score_merchant({"m": evidence("network")})["m"]
    assert (r["score"], r["status"]) == (weight, expected)


def test_score_capped_at_one():
    """TC_020"""
    weights = {"network": 0.6, "img": 0.5, "text": 0.4, "alt": 0.3, "css_class": 0.2}
    r = ConfidenceScorer(rules_with(weights)).score_merchant({"m": evidence(*SIGNALS)})["m"]
    assert r["score"] == 1.0


def test_default_weights():
    """TC_021"""
    scorer = ConfidenceScorer(rules_with({"network": 0.4}))           # css_class not configured
    assert scorer.score_merchant({"m": evidence("css_class")})["m"]["score"] == DEFAULT_WEIGHTS["css_class"]

    unknown = scorer.score_merchant({"not_in_rules": evidence("img", "text")})["not_in_rules"]
    assert unknown["score"] == round(DEFAULT_WEIGHTS["img"] + DEFAULT_WEIGHTS["text"], 3)


def test_thresholds_from_rules_and_override():
    """TC_022"""
    strict = ConfidenceScorer(rules_with({"network": 0.85}, {"auto_accept": 0.9, "review_required": 0.6}))
    assert strict.score_merchant({"m": evidence("network")})["m"]["status"] == "REVIEW"

    lenient = ConfidenceScorer(rules_with({"network": 0.85}), thresholds={"auto_accept": 0.7, "review_required": 0.3})
    assert lenient.score_merchant({"m": evidence("network")})["m"]["status"] == "AUTO_ACCEPT"


def test_signals_order_and_detected_flag(scorer):
    """TC_023"""
    r = scorer.score_merchant({"amex": evidence("css_class", "alt", "network")})["amex"]
    assert r["signals"] == ["network", "alt", "css_class"]          # scorer's fixed order
    assert r["status"] == "REVIEW" and r["detected"] is False       # 0.4 + 0.1 + 0.05 = 0.55

    accepted = scorer.score_merchant({"amex": evidence("network", "img", "text")})["amex"]
    assert accepted["detected"] is True


def test_summary_counts(scorer):
    """TC_024"""
    scored = scorer.score_merchant({
        "amex": evidence(*SIGNALS), "visa": evidence("network", "img"), "jcb": evidence(),
    })
    assert scorer.get_summary(scored) == {"AUTO_ACCEPT": 1, "REVIEW": 1, "AUTO_REJECT": 1}


# ── Invariants of the shipped configuration ───────────────────────────────────

@pytest.mark.parametrize("method", sorted(RULES["payment_methods"]))
def test_shipped_weights_invariants(scorer, method):
    """TC_025 — guards against a weight edit that lets one weak signal decide alone."""
    for signal in SIGNALS:
        r = scorer.score_merchant({method: evidence(signal)})[method]
        assert r["status"] == "AUTO_REJECT", f"{method}: '{signal}' alone reached {r['status']}"
    assert scorer.score_merchant({method: evidence(*SIGNALS)})[method]["status"] == "AUTO_ACCEPT"
