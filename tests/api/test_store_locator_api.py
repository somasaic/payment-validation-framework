"""
Store Locator API Tests
=======================
TC_230  GET /chain/{region}/stores?q= → 200, schema, ranked results
TC_231  Query that matches nothing → 200 with empty items
TC_232  Missing / too-short q → 422 VALIDATION_ERROR
TC_233  Bot-protected region → 403
TC_234  Unknown region → 404 error envelope
"""

import pytest


def test_search(api, assert_schema):
    """TC_230"""
    resp = api.get("/api/v1/chain/us/stores", params={"q": "San Francisco CA 94103"})
    assert resp.status == 200
    body = resp.json()
    assert_schema(body, "store_search")
    assert body["region"] == "us" and body["total"] == len(body["items"]) == 2
    assert body["items"][0]["store_id"] == "US-1001"
    assert body["items"][0]["url"] == "/chain/us/stores/US-1001"


def test_search_no_results(api, assert_schema):
    """TC_231"""
    resp = api.get("/api/v1/chain/uk/stores", params={"q": "Edinburgh EH1"})
    assert resp.status == 200
    assert_schema(resp.json(), "store_search")
    assert resp.json()["items"] == []


@pytest.mark.parametrize("params", [{}, {"q": "a"}])
def test_search_invalid_query(api, assert_schema, params):
    """TC_232"""
    resp = api.get("/api/v1/chain/us/stores", params=params)
    assert resp.status == 422
    assert_schema(resp.json(), "error")
    assert resp.json()["error"]["details"][0]["field"] == "q"


def test_bot_protected_region(api):
    """TC_233"""
    resp = api.get("/api/v1/chain/ca/stores", params={"q": "Toronto"})
    assert resp.status == 403
    assert "verify you are human" in resp.text().lower()


def test_unknown_region(api, assert_schema):
    """TC_234"""
    resp = api.get("/api/v1/chain/fr/stores", params={"q": "Paris"})
    assert resp.status == 404
    assert_schema(resp.json(), "error")
