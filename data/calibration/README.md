# Calibration data

Labelled evidence for evaluating and calibrating the detection weights
(`validators/calibration.py`, `scripts/calibrate_detection.py`).

## Format — `labelled_evidence.csv`

| Column | Meaning |
|--------|---------|
| `method` | payment method code (`amex`, `visa`, …) |
| `network`, `img`, `alt`, `text`, `css_class` | `1` if that evidence signal fired on the scanned page, else `0` |
| `accepted` | `1` if the merchant/location really accepts the method, else `0` |
| `source` | where the label came from |

## What is in this file

**All rows are synthetic** (`source` = `synthetic:<scenario>`). They were written
to cover the evidence patterns the engine meets on real pages — full checkout
icon sets, icons without alt text, cached icons with no network request, text-only
statements, and the traps: blog/news mentions, leftover CSS classes,
third-party script requests, "coming soon" promo banners, sponsor logo strips,
brand-wide footer logos seen by an unscoped scan.

They exist to demonstrate and test the calibration method. **Their metrics are
not accuracy claims**, and weights must not be tuned to them.

## Real labels

1. Run the pipeline; QA reviewers fill `reviewer_decision` (Yes/No) in
   `reports/review_queue_<ts>.csv`.
2. Add reviewed spot-checks of AUTO_ACCEPT / AUTO_REJECT rows (a random sample)
   to a labelled CSV in the format above — review-queue rows alone are biased
   towards uncertain cases.
3. `python scripts/calibrate_detection.py --labels real_labels.csv --review-queue "reports/review_queue_*.csv"`
4. Read the report; if a weight or threshold change is justified, edit
   `data/detection_rules.json` and re-run the tests (TC_025 guards the
   "no single signal decides alone" invariant).
