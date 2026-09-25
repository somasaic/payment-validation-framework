"""
Detection calibration
=====================
Measures how well the confidence weights and thresholds separate "method is
accepted" from "method is not accepted" on labelled evidence, and suggests
data-driven weights.

Labelled evidence = one row per (page scan, payment method): which signals
fired and whether the method is really accepted. Sources:
  - data/calibration/labelled_evidence.csv (curated examples, see its README)
  - reviewed review queues: review_queue_*.csv rows where a QA reviewer filled
    reviewer_decision (Yes/No) — the production feedback loop

Decision semantics (same as the pipeline):
  AUTO_ACCEPT → predicted Yes · REVIEW → routed to a human · AUTO_REJECT → No

Suggested weights: per-signal log-likelihood ratio
    llr(s) = ln( P(s fired | accepted) / P(s fired | not accepted) )   (Laplace-smoothed)
clipped at 0 (a signal that is not more common on accepted pages adds no
confidence) and scaled so all signals together sum to 1.0 — the same scale the
thresholds use. This is the naive-Bayes view of the scorer: independent
signals, each adding evidence in proportion to how discriminative it is.
"""

import csv
import math
from dataclasses import dataclass, field
from pathlib import Path

from validators.confidence_scorer import ConfidenceScorer

SIGNALS = ("network", "img", "alt", "text", "css_class")
TRUE_VALUES = {"1", "true", "yes", "y"}


@dataclass
class LabelledEvidence:
    method:   str
    signals:  dict[str, bool]
    accepted: bool
    source:   str = ""


@dataclass
class Metrics:
    total: int = 0
    tp: int = 0                  # AUTO_ACCEPT and accepted
    fp: int = 0                  # AUTO_ACCEPT but not accepted   ← the costly error
    fn: int = 0                  # AUTO_REJECT but accepted
    tn: int = 0                  # AUTO_REJECT and not accepted
    review_pos: int = 0          # REVIEW and accepted
    review_neg: int = 0          # REVIEW and not accepted
    by_method: dict = field(default_factory=dict)

    @property
    def positives(self) -> int:
        return self.tp + self.fn + self.review_pos

    @property
    def negatives(self) -> int:
        return self.fp + self.tn + self.review_neg

    @property
    def precision(self) -> float:
        """Of the automatic Yes answers, how many are right."""
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else 1.0

    @property
    def recall(self) -> float:
        """Of the accepted methods, how many got an automatic Yes."""
        return self.tp / self.positives if self.positives else 1.0

    @property
    def false_positive_rate(self) -> float:
        return self.fp / self.negatives if self.negatives else 0.0

    @property
    def review_rate(self) -> float:
        return (self.review_pos + self.review_neg) / self.total if self.total else 0.0

    @property
    def auto_accuracy(self) -> float:
        """Accuracy of the decisions made without a human (excludes REVIEW)."""
        decided = self.tp + self.fp + self.fn + self.tn
        return (self.tp + self.tn) / decided if decided else 1.0


# ── Loading ───────────────────────────────────────────────────────────────────

def _truthy(value: str) -> bool:
    return str(value).strip().lower() in TRUE_VALUES


def load_labelled_evidence(path: str | Path) -> list[LabelledEvidence]:
    """Columns: method, network, img, alt, text, css_class (0/1), accepted (0/1), [source]."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        return [
            LabelledEvidence(
                method=row["method"].strip(),
                signals={s: _truthy(row[s]) for s in SIGNALS},
                accepted=_truthy(row["accepted"]),
                source=row.get("source", ""),
            )
            for row in csv.DictReader(f)
        ]


def load_review_decisions(path: str | Path) -> list[LabelledEvidence]:
    """Reviewed rows of a review_queue_*.csv (reviewer_decision Yes/No; blanks skipped)."""
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            decision = row.get("reviewer_decision", "").strip().lower()
            if decision not in ("yes", "no"):
                continue
            fired = set(filter(None, row.get("signals_fired", "").split("|")))
            rows.append(LabelledEvidence(
                method=row["payment_method"],
                signals={s: s in fired for s in SIGNALS},
                accepted=decision == "yes",
                source=f"review:{Path(path).name}",
            ))
    return rows


# ── Evaluation ────────────────────────────────────────────────────────────────

def evaluate(rows: list[LabelledEvidence], rules: dict) -> Metrics:
    scorer = ConfidenceScorer(rules)
    metrics = Metrics()
    for row in rows:
        status = scorer.score_merchant({row.method: row.signals})[row.method]["status"]
        key = {
            ("AUTO_ACCEPT", True): "tp", ("AUTO_ACCEPT", False): "fp",
            ("AUTO_REJECT", True): "fn", ("AUTO_REJECT", False): "tn",
            ("REVIEW", True): "review_pos", ("REVIEW", False): "review_neg",
        }[(status, row.accepted)]
        setattr(metrics, key, getattr(metrics, key) + 1)
        metrics.total += 1
        per = metrics.by_method.setdefault(row.method, Metrics())
        setattr(per, key, getattr(per, key) + 1)
        per.total += 1
    return metrics


def signal_reliability(rows: list[LabelledEvidence]) -> dict[str, dict]:
    """How often each signal fires, and how trustworthy it is when it does."""
    pos = [r for r in rows if r.accepted]
    neg = [r for r in rows if not r.accepted]
    out = {}
    for s in SIGNALS:
        fired_pos = sum(r.signals[s] for r in pos)
        fired_neg = sum(r.signals[s] for r in neg)
        fired = fired_pos + fired_neg
        out[s] = {
            "fired":      fired,
            "precision":  fired_pos / fired if fired else 0.0,               # P(accepted | fired)
            "recall":     fired_pos / len(pos) if pos else 0.0,              # P(fired | accepted)
            "false_fire": fired_neg / len(neg) if neg else 0.0,              # P(fired | not accepted)
        }
    return out


def fit_weights(rows: list[LabelledEvidence], smoothing: float = 1.0) -> dict[str, float]:
    """Log-likelihood-ratio weights, clipped at 0, scaled to sum to 1.0."""
    pos = [r for r in rows if r.accepted]
    neg = [r for r in rows if not r.accepted]
    llr = {}
    for s in SIGNALS:
        p_pos = (sum(r.signals[s] for r in pos) + smoothing) / (len(pos) + 2 * smoothing)
        p_neg = (sum(r.signals[s] for r in neg) + smoothing) / (len(neg) + 2 * smoothing)
        llr[s] = max(0.0, math.log(p_pos / p_neg))
    total = sum(llr.values())
    if total == 0:
        return {s: 0.0 for s in SIGNALS}
    return {s: round(v / total, 3) for s, v in llr.items()}


def rules_with_weights(rules: dict, weights: dict[str, float]) -> dict:
    """Copy of the rules with the same weights applied to every method."""
    methods = {m: {**r, "confidence_weights": dict(weights)} for m, r in rules["payment_methods"].items()}
    return {**rules, "payment_methods": methods}


def threshold_sweep(rows: list[LabelledEvidence], rules: dict,
                    accept_thresholds=(0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95)) -> list[dict]:
    """Precision / recall / review rate for alternative AUTO_ACCEPT thresholds."""
    results = []
    review = rules["confidence_thresholds"]["review_required"]
    for t in accept_thresholds:
        variant = {**rules, "confidence_thresholds": {"auto_accept": t, "review_required": min(review, t)}}
        m = evaluate(rows, variant)
        results.append({"auto_accept": t, "precision": m.precision, "recall": m.recall,
                        "false_positive_rate": m.false_positive_rate, "review_rate": m.review_rate})
    return results
