"""
Detection Calibration Unit Tests
================================
TC_031  evaluate() counts TP / FP / FN / TN / REVIEW exactly and derives precision, recall, FPR
TC_032  signal_reliability() computes precision, recall and false-fire rate per signal
TC_033  fit_weights(): non-negative, sums to 1.0, ranks discriminative signals higher, ignores useless ones
TC_034  threshold_sweep(): raising AUTO_ACCEPT never increases recall
TC_035  Reviewed review-queue rows become labels; undecided rows are skipped
TC_036  CLI quality gate: exit 0 when the gate passes, 1 when it fails; report written
"""

import csv
import subprocess
import sys
from pathlib import Path

import pytest

from utils.csv_reader import load_detection_rules
from validators.calibration import (
    SIGNALS, LabelledEvidence, evaluate, fit_weights, load_labelled_evidence,
    load_review_decisions, signal_reliability, threshold_sweep,
)

ROOT = Path(__file__).resolve().parents[2]
RULES = load_detection_rules()


def row(accepted: bool, *fired: str, method: str = "amex") -> LabelledEvidence:
    return LabelledEvidence(method, {s: s in fired for s in SIGNALS}, accepted)


def test_evaluate_confusion():
    """TC_031 — amex weights: all signals 1.0, network+img 0.7, text 0.2."""
    rows = [
        row(True, *SIGNALS),            # AUTO_ACCEPT, accepted     → TP
        row(False, *SIGNALS),           # AUTO_ACCEPT, not accepted → FP
        row(True, "network", "img"),    # REVIEW, accepted
        row(False, "network", "img"),   # REVIEW, not accepted
        row(True, "text"),              # AUTO_REJECT, accepted     → FN
        row(False),                     # AUTO_REJECT, not accepted → TN
    ]
    m = evaluate(rows, RULES)
    assert (m.tp, m.fp, m.review_pos, m.review_neg, m.fn, m.tn) == (1, 1, 1, 1, 1, 1)
    assert m.precision == 0.5
    assert m.recall == pytest.approx(1 / 3)
    assert m.false_positive_rate == pytest.approx(1 / 3)
    assert m.review_rate == pytest.approx(2 / 6)
    assert m.by_method["amex"].total == 6


def test_signal_reliability():
    """TC_032"""
    rows = [row(True, "img", "text"), row(True, "img"), row(False, "text"), row(False)]
    rel = signal_reliability(rows)
    assert rel["img"] == {"fired": 2, "precision": 1.0, "recall": 1.0, "false_fire": 0.0}
    assert rel["text"]["precision"] == 0.5 and rel["text"]["false_fire"] == 0.5
    assert rel["network"]["fired"] == 0


def test_fit_weights_properties():
    """TC_033"""
    rows = (                                        # 25 accepted, 25 not accepted
        [row(True, "img", "alt", "text")] * 20      # img + alt only ever on accepted pages
        + [row(True, "network")] * 5
        + [row(False, "text")] * 20                 # text on 80% of both → carries no information
        + [row(False, "network")] * 5               # network on 20% of both → no information
    )
    w = fit_weights(rows)
    assert all(v >= 0 for v in w.values())
    assert sum(w.values()) == pytest.approx(1.0, abs=0.005)
    assert w["img"] > 0 and w["alt"] > 0
    assert w["text"] == 0.0 and w["network"] == 0.0 and w["css_class"] == 0.0


def test_threshold_sweep_monotonic():
    """TC_034"""
    rows = load_labelled_evidence(ROOT / "data" / "calibration" / "labelled_evidence.csv")
    recalls = [r["recall"] for r in threshold_sweep(rows, RULES)]
    assert recalls == sorted(recalls, reverse=True)


def test_review_queue_labels(tmp_path):
    """TC_035"""
    path = tmp_path / "review_queue_x.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["payment_method", "signals_fired", "reviewer_decision"])
        w.writeheader()
        w.writerows([
            {"payment_method": "paypal", "signals_fired": "img|text", "reviewer_decision": "Yes"},
            {"payment_method": "visa",   "signals_fired": "network",  "reviewer_decision": "no"},
            {"payment_method": "amex",   "signals_fired": "img",      "reviewer_decision": ""},
        ])
    rows = load_review_decisions(path)
    assert [(r.method, r.accepted) for r in rows] == [("paypal", True), ("visa", False)]
    assert rows[0].signals == {"network": False, "img": True, "alt": False, "text": True, "css_class": False}


@pytest.mark.parametrize("min_precision, expected_exit", [(0.95, 0), (0.99, 1)])
def test_cli_quality_gate(tmp_path, min_precision, expected_exit):
    """TC_036"""
    out = tmp_path / "report.md"
    proc = subprocess.run([sys.executable, "scripts/calibrate_detection.py", "--out", str(out),
                           "--min-precision", str(min_precision)], cwd=ROOT, capture_output=True)
    assert proc.returncode == expected_exit
    report = out.read_text(encoding="utf-8")
    assert "# Detection Calibration Report" in report and "## 5. Quality gate" in report
