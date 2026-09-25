"""
Storefront catalog
==================
Joins data/merchants.csv with mock_merchant/data/storefronts.json so the mock
storefronts and API serve the same merchants the pipeline scans.

storefronts.json is also the ground truth: `accepted_methods` is what each
merchant really accepts, which lets tests measure detection accuracy.
"""

import json
import os
from functools import lru_cache

from utils.csv_reader import load_merchants

STOREFRONTS_FILE = os.path.join(os.path.dirname(__file__), "data", "storefronts.json")


@lru_cache(maxsize=1)
def _load() -> dict:
    with open(STOREFRONTS_FILE, encoding="utf-8") as f:
        return json.load(f)


def payment_method_catalog() -> dict:
    return _load()["payment_method_catalog"]


@lru_cache(maxsize=1)
def merchants() -> dict[str, dict]:
    """merchant_id → merchant record (CSV columns + currency + accepted_methods)."""
    storefronts = _load()["storefronts"]
    result = {}
    for row in load_merchants():
        store = storefronts.get(row["merchant_id"])
        if store is None:
            continue
        result[row["merchant_id"]] = {**row, **store}
    return result


def get_merchant(merchant_id: str) -> dict | None:
    return merchants().get(merchant_id.upper())


def accepted_methods(merchant_id: str) -> list[dict]:
    merchant = get_merchant(merchant_id)
    if merchant is None:
        return []
    catalog = payment_method_catalog()
    return [
        {
            "code":         code,
            "display_name": catalog[code]["display_name"],
            "type":         catalog[code]["type"],
            "icon_url":     f"/static/icons/{catalog[code]['icon']}",
        }
        for code in merchant["accepted_methods"]
    ]


def products(merchant_id: str) -> list[dict]:
    merchant = get_merchant(merchant_id)
    if merchant is None:
        return []
    data = _load()
    currency = data["currencies"][merchant["currency"]]
    return [
        {
            "sku":      p["sku"],
            "name":     p["name"],
            "price":    round(p["base_price_usd"] * currency["usd_rate"], currency["decimals"]),
            "currency": merchant["currency"],
        }
        for p in data["products"]
    ]


def currency_info(code: str) -> dict:
    return _load()["currencies"][code]
