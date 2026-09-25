"""
Merchant API Tests
==================
TC_201  GET /health → 200, version, X-Request-ID echoed
TC_202  GET /merchants → 200, list schema, all 12 merchants
TC_203  GET /merchants?region= filters, case-insensitive
TC_204  GET /merchants pagination (limit/offset) is stable and non-overlapping
TC_205  GET /merchants invalid query params → 422 with field details
TC_206  GET /merchants/{id} → 200, schema, payload matches merchants.csv
TC_207  GET /merchants/{id} unknown → 404 MERCHANT_NOT_FOUND
TC_208  GET /merchants/{id}/payment-methods matches ground truth (all merchants)
TC_209  GET /merchants/{id}/payment-methods?context=footer returns cards only
TC_210  GET /merchants/{id}/products → prices converted to merchant currency
"""

import pytest

from utils.csv_reader import load_merchants

MERCHANTS = {m["merchant_id"]: m for m in load_merchants()}


def test_health(api):
    """TC_201"""
    resp = api.get("/health", headers={"X-Request-ID": "qa-trace-201"})
    assert resp.status == 200
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.headers["x-request-id"] == "qa-trace-201"
    assert resp.json() == {"status": "ok", "version": "1.0.0"}


def test_list_merchants(api, assert_schema):
    """TC_202"""
    resp = api.get("/api/v1/merchants")
    assert resp.status == 200
    body = resp.json()
    assert_schema(body, "merchant_list")
    assert body["total"] == len(MERCHANTS)
    assert {m["merchant_id"] for m in body["items"]} == set(MERCHANTS)


@pytest.mark.parametrize("region, expected_ids", [
    ("US", {"M001", "M010"}),
    ("gb", {"M002", "M011"}),
    ("SG", {"M007", "M012"}),
    ("BR", set()),
])
def test_list_merchants_region_filter(api, assert_schema, region, expected_ids):
    """TC_203"""
    resp = api.get("/api/v1/merchants", params={"region": region})
    assert resp.status == 200
    body = resp.json()
    assert_schema(body, "merchant_list")
    assert {m["merchant_id"] for m in body["items"]} == expected_ids
    assert body["total"] == len(expected_ids)
    assert all(m["region"] == region.upper() for m in body["items"])


def test_list_merchants_pagination(api):
    """TC_204"""
    pages = [api.get("/api/v1/merchants", params={"limit": 5, "offset": o}).json() for o in (0, 5, 10)]
    ids = [m["merchant_id"] for p in pages for m in p["items"]]

    assert [len(p["items"]) for p in pages] == [5, 5, 2]
    assert all(p["total"] == 12 and p["limit"] == 5 for p in pages)
    assert len(ids) == len(set(ids)) == 12


@pytest.mark.parametrize("params, field", [
    ({"limit": 0},        "limit"),
    ({"limit": 101},      "limit"),
    ({"offset": -1},      "offset"),
    ({"region": "USA"},   "region"),
    ({"limit": "ten"},    "limit"),
])
def test_list_merchants_invalid_params(api, assert_schema, params, field):
    """TC_205"""
    resp = api.get("/api/v1/merchants", params=params)
    assert resp.status == 422
    body = resp.json()
    assert_schema(body, "error")
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert field in [d["field"] for d in body["error"]["details"]]


@pytest.mark.parametrize("merchant_id", ["M001", "M004", "M008"])
def test_get_merchant(api, assert_schema, merchant_id):
    """TC_206"""
    resp = api.get(f"/api/v1/merchants/{merchant_id}")
    assert resp.status == 200
    body = resp.json()
    assert_schema(body, "merchant")

    source = MERCHANTS[merchant_id]
    for field in ("merchant_id", "merchant_name", "region", "country_code", "platform", "segment"):
        assert body[field] == source[field], field
    assert body["storefront_url"] == f"/store/{merchant_id}/"


@pytest.mark.parametrize("path", [
    "/api/v1/merchants/M999",
    "/api/v1/merchants/M999/payment-methods",
    "/api/v1/merchants/M999/products",
])
def test_unknown_merchant_404(api, assert_schema, path):
    """TC_207"""
    resp = api.get(path)
    assert resp.status == 404
    body = resp.json()
    assert_schema(body, "error")
    assert body["error"]["code"] == "MERCHANT_NOT_FOUND"
    assert "M999" in body["error"]["message"]


@pytest.mark.parametrize("merchant_id", sorted(MERCHANTS))
def test_payment_methods_match_ground_truth(api, assert_schema, ground_truth, merchant_id):
    """TC_208"""
    resp = api.get(f"/api/v1/merchants/{merchant_id}/payment-methods", params={"latency_ms": 0})
    assert resp.status == 200
    body = resp.json()
    assert_schema(body, "payment_methods")
    assert body["merchant_id"] == merchant_id
    assert [m["code"] for m in body["methods"]] == ground_truth[merchant_id]


def test_payment_methods_footer_context(api, assert_schema):
    """TC_209"""
    resp = api.get("/api/v1/merchants/M001/payment-methods", params={"context": "footer"})
    assert resp.status == 200
    body = resp.json()
    assert_schema(body, "payment_methods")
    assert {m["type"] for m in body["methods"]} == {"card"}
    assert [m["code"] for m in body["methods"]] == ["amex", "visa", "mastercard", "discover"]


@pytest.mark.parametrize("merchant_id, currency, headphones_price", [
    ("M001", "USD", 129.00),
    ("M002", "GBP", 101.91),
    ("M004", "JPY", 19221),
])
def test_products_in_merchant_currency(api, assert_schema, merchant_id, currency, headphones_price):
    """TC_210"""
    resp = api.get(f"/api/v1/merchants/{merchant_id}/products")
    assert resp.status == 200
    body = resp.json()
    assert_schema(body, "products")
    assert body["currency"] == currency
    assert all(p["currency"] == currency for p in body["items"])
    headphones = next(p for p in body["items"] if p["name"] == "Wireless Headphones")
    assert headphones["price"] == pytest.approx(headphones_price)
