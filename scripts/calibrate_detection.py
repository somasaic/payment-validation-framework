"""
Detection calibration report + quality gate
===========================================
Evaluates the confidence weights/thresholds in data/detection_rules.json on
labelled evidence, reports per-signal reliability, suggests data-driven
weights and sweeps the AUTO_ACCEPT threshold. Suggestions are never applied
automatically — a person reviews them and edits detection_rules.json.

Exit code 1 if the CURRENT configuration misses the quality gate
(--min-precision / --max-fpr), so CI can block a weight change that makes
automatic "Yes" answers less trustworthy.

Usage:
  python scripts/calibrate_detection.py
  python scripts/calibrate_detection.py --review-queue reports/review_queue_*.csv
  python scripts/calibrate_detection.py --labels my_labels.csv --out reports/calibration_report.md
"""

import argparse
import glob
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.csv_reader import load_detection_rules                     # noqa: E402
from validators.calibration import (                                   # noqa: E402
    SIGNALS, Metrics, evaluate, fit_weights, load_labelled_evidence, load_review_decisions,
    rules_with_weights, signal_reliability, threshold_sweep,
)

DEFAULT_LABELS = Path(__file__).resolve().parents[1] / "data" / "calibration" / "labelled_evidence.csv"


def pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def metrics_row(name: str, m: Metrics) -> str:
    return (f"| {name} | {m.total} | {pct(m.precision)} | {pct(m.recall)} | "
            f"{pct(m.false_positive_rate)} | {pct(m.review_rate)} | {pct(m.auto_accuracy)} |")


def build_report(rows, rules, min_precision, max_fpr, sources) -> tuple[str, bool]:
    current = evaluate(rows, rules)
    fitted = fit_weights(rows)
    suggested = evaluate(rows, rules_with_weights(rules, fitted))
    review_min = rules["confidence_thresholds"]["review_required"]
    gate_ok = current.precision >= min_precision and current.false_positive_rate <= max_fpr
    header = "| Config | Rows | Precision (Yes) | Recall (Yes) | False-positive rate | Review rate | Auto-decision accuracy |"
    sep = "|---|---:|---:|---:|---:|---:|---:|"

    lines = [
        "# Detection Calibration Report", "",
        f"Labelled rows: **{len(rows)}** ({sum(r.accepted for r in rows)} accepted / "
        f"{sum(not r.accepted for r in rows)} not accepted)", "",
        "| Source | Rows |", "|---|---:|",
        *[f"| {s} | {n} |" for s, n in sources.most_common()], "",
        "> Metrics are only as representative as the labels. Synthetic example rows illustrate the method; "
        "decisions about real weights need QA-reviewed labels from real scans.", "",
        "## 1. Current configuration (`data/detection_rules.json`)", "",
        header, sep, metrics_row("current", current), "",
        "| | Accepted | Not accepted |", "|---|---:|---:|",
        f"| AUTO_ACCEPT (Yes) | {current.tp} | **{current.fp}** |",
        f"| REVIEW (human) | {current.review_pos} | {current.review_neg} |",
        f"| AUTO_REJECT (No) | {current.fn} | {current.tn} |", "",
        "### Per method", "", header.replace("Config", "Method"), sep,
        *[metrics_row(m, per) for m, per in sorted(current.by_method.items())], "",
        "## 2. Signal reliability", "",
        "| Signal | Fired | Precision when fired | Recall | Fires on non-accepted |", "|---|---:|---:|---:|---:|",
        *[f"| {s} | {v['fired']} | {pct(v['precision'])} | {pct(v['recall'])} | {pct(v['false_fire'])} |"
          for s, v in signal_reliability(rows).items()], "",
        "## 3. Suggested weights (log-likelihood ratio, scaled to sum 1.0)", "",
        "| Signal | " + " | ".join(SIGNALS) + " |", "|---|" + "---:|" * len(SIGNALS),
        "| suggested | " + " | ".join(f"{fitted[s]:.3f}" for s in SIGNALS) + " |", "",
        header, sep, metrics_row("current", current), metrics_row("suggested", suggested), "",
        f"Guardrail — no single signal may reach REVIEW on its own (max weight < {review_min}): "
        + ("✅ holds" if max(fitted.values()) < review_min else "❌ violated — do not adopt"), "",
        "## 4. AUTO_ACCEPT threshold sweep (current weights)", "",
        "| auto_accept | Precision | Recall | False-positive rate | Review rate |", "|---:|---:|---:|---:|---:|",
        *[f"| {r['auto_accept']:.2f} | {pct(r['precision'])} | {pct(r['recall'])} | "
          f"{pct(r['false_positive_rate'])} | {pct(r['review_rate'])} |" for r in threshold_sweep(rows, rules)], "",
        "## 5. Quality gate", "",
        f"Precision ≥ {pct(min_precision)} and false-positive rate ≤ {pct(max_fpr)}: "
        + (f"✅ **PASS** ({pct(current.precision)}, {pct(current.false_positive_rate)})" if gate_ok else
           f"❌ **FAIL** ({pct(current.precision)}, {pct(current.false_positive_rate)})"), "",
    ]
    return "\n".join(lines), gate_ok


def main():
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Evaluate and calibrate detection weights")
    parser.add_argument("--labels", nargs="*", default=[str(DEFAULT_LABELS)],
                        help="Labelled evidence CSV(s)")
    parser.add_argument("--review-queue", nargs="*", default=[],
                        help="Reviewed review_queue_*.csv files (reviewer_decision Yes/No); globs allowed")
    parser.add_argument("--out", default="reports/calibration_report.md")
    parser.add_argument("--min-precision", type=float, default=0.95)
    parser.add_argument("--max-fpr", type=float, default=0.05)
    args = parser.parse_args()

    rows = [r for path in args.labels for r in load_labelled_evidence(path)]
    for pattern in args.review_queue:
        for path in glob.glob(pattern):
            rows += load_review_decisions(path)
    if not rows:
        sys.exit("No labelled rows found")

    sources = Counter(r.source or "unlabelled source" for r in rows)
    report, gate_ok = build_report(rows, load_detection_rules(), args.min_precision, args.max_fpr, sources)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(report, encoding="utf-8")
    print(report)
    print(f"\nReport written to {args.out}")
    sys.exit(0 if gate_ok else 1)


if __name__ == "__main__":
    main()
