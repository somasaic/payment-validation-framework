"""
Confidence Scoring Engine
=========================
Takes raw evidence dict from scraper.
Computes confidence score (0.0 → 1.0) per payment method.
Assigns status: AUTO_ACCEPT | REVIEW | AUTO_REJECT

Scoring formula:
  score = W_network * network + W_img * img + W_text * text + W_alt * alt + W_css * css_class
  (weights from detection_rules.json per method)
"""

import logging

logger = logging.getLogger(__name__)

# Default weights if not in rules
DEFAULT_WEIGHTS = {
    "network":   0.40,
    "img":       0.30,
    "text":      0.15,
    "alt":       0.10,
    "css_class": 0.05,
}

AUTO_ACCEPT_THRESHOLD = 0.80
REVIEW_THRESHOLD      = 0.50


class ConfidenceScorer:

    def __init__(self, detection_rules: dict, thresholds: dict = None):
        self.rules = detection_rules["payment_methods"]
        thresholds = thresholds or detection_rules.get("confidence_thresholds", {})
        self.auto_accept_threshold = thresholds.get("auto_accept", AUTO_ACCEPT_THRESHOLD)
        self.review_threshold      = thresholds.get("review_required", REVIEW_THRESHOLD)

    def score_merchant(self, evidence: dict) -> dict:
        """
        Score all payment methods for one merchant.

        Returns: {
            method: {
                "score": float,
                "status": "AUTO_ACCEPT" | "REVIEW" | "AUTO_REJECT",
                "detected": bool,
                "signals": [str]   # which signals fired
            }
        }
        """
        results = {}
        for method, ev in evidence.items():
            score, signals = self._compute_score(method, ev)
            status = self._classify(score)
            results[method] = {
                "score":    round(score, 3),
                "status":   status,
                "detected": status == "AUTO_ACCEPT",
                "signals":  signals,
            }
            if signals:
                logger.info(
                    f"  {method}: score={score:.2f} [{status}] signals={signals}"
                )
        return results

    def _compute_score(self, method: str, evidence: dict) -> tuple[float, list[str]]:
        rule    = self.rules.get(method, {})
        weights = rule.get("confidence_weights", DEFAULT_WEIGHTS)
        score   = 0.0
        signals = []

        for signal, weight_key in [
            ("network",   "network"),
            ("img",       "img"),
            ("text",      "text"),
            ("alt",       "alt"),
            ("css_class", "css_class"),
        ]:
            if evidence.get(signal):
                w = weights.get(weight_key, DEFAULT_WEIGHTS.get(weight_key, 0))
                score += w
                signals.append(signal)

        return min(score, 1.0), signals

    def _classify(self, score: float) -> str:
        if score >= self.auto_accept_threshold:
            return "AUTO_ACCEPT"
        elif score >= self.review_threshold:
            return "REVIEW"
        else:
            return "AUTO_REJECT"

    def get_summary(self, scored_results: dict) -> dict:
        """High-level pass/review/fail counts."""
        counts = {"AUTO_ACCEPT": 0, "REVIEW": 0, "AUTO_REJECT": 0}
        for r in scored_results.values():
            counts[r["status"]] += 1
        return counts
