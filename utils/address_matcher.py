"""
Address matching
================
Master-dataset addresses and store-locator addresses rarely match as strings
("1450 Market St., San Francisco CA" vs "1450 Market Street, San Francisco,
CA 94103"). Normalise both, then require:
  - postal code present in the candidate (when the record has one)
  - same street number (when both have one)
  - token overlap ≥ threshold
and pick the best-scoring candidate.
"""

import re
from dataclasses import dataclass

ABBREVIATIONS = {
    "st": "street", "str": "street", "rd": "road", "ave": "avenue", "av": "avenue",
    "blvd": "boulevard", "dr": "drive", "ln": "lane", "ct": "court", "pl": "place",
    "hwy": "highway", "pkwy": "parkway", "sq": "square", "ctr": "center", "centre": "center",
    "n": "north", "s": "south", "e": "east", "w": "west",
    "ste": "suite", "fl": "floor",
}

MATCH_THRESHOLD = 0.6


@dataclass
class AddressMatch:
    index:   int
    address: str
    score:   float


def normalize(address: str) -> list[str]:
    # Drop periods first so initials and abbreviations stay one token: "M.G." → "mg", "St." → "st"
    tokens = re.split(r"[^a-z0-9]+", address.lower().replace(".", ""))
    return [ABBREVIATIONS.get(t, t) for t in tokens if t]


def _postal(code: str) -> str:
    return re.sub(r"[^a-z0-9]", "", code.lower())


def _street_number(tokens: list[str]) -> str | None:
    return next((t for t in tokens[:3] if re.fullmatch(r"[a-z]?-?\d+[a-z]?", t)), None)


def score(record_address: str, candidate: str, postal_code: str = "") -> float:
    rec, cand = normalize(record_address), normalize(candidate)
    if not rec or not cand:
        return 0.0
    if postal_code and _postal(postal_code) not in _postal(candidate):
        return 0.0
    rec_no, cand_no = _street_number(rec), _street_number(cand)
    if rec_no and cand_no and rec_no != cand_no:
        return 0.0
    rec_set, cand_set = set(rec), set(cand)
    return len(rec_set & cand_set) / len(rec_set)


def best_match(record_address: str, candidates: list[str], postal_code: str = "",
               threshold: float = MATCH_THRESHOLD) -> AddressMatch | None:
    scored = [AddressMatch(i, c, score(record_address, c, postal_code)) for i, c in enumerate(candidates)]
    scored = [m for m in scored if m.score >= threshold]
    return max(scored, key=lambda m: m.score, default=None)
