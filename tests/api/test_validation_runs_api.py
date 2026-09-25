"""
Validation Runs API Tests
=========================
TC_220  POST valid run → 201, schema, Location header, summary counts
TC_221  POST run → mismatches flag results that disagree with ground truth
TC_222  GET run by Location → 200, identical payload
TC_223  POST without X-API-Key → 401; wrong key → 403
TC_224  POST invalid payloads → 422 with field-level details
TC_225  POST for unknown merchant → 404
TC_226  GET unknown run → 404 RUN_NOT_FOUND
"""

import copy

import pytest

VALID_RUN = {
    "merchant_id": "M002",
    "results": [
        {"payment_method": "visa",   "detected": True,  "confidence_score": 1.0,
         "status": "AUTO_ACCEPT", "signals": ["network", "img", "text", "alt", "css_class"]},
        {"payment_method": "paypal", "detected": False, "confidence_score": 0.55,
         "status": "REVIEW",      "signals": ["img", "text"]},
        {"payment_method": "amex",   "detected": False, "confidence_score": 0.0,
         "status": "AUTO_REJECT", "signals": []},
    ],
}


def _run(**overrides) -> dict:
    run = copy.deepcopy(VALID_RUN)
    run.update(overrides)
    return run


def _result(index: int, **overrides) -> dict:
    run = copy.deepcopy(VALID_RUN)
    run["results"][index].update(overrides)
    return run


@pytest.fixture
def post_run(api, api_key):
    def _post(payload, key=api_key):
        headers = {"X-API-Key": key} if key is not None else {}
        return api.post("/api/v1/validation-runs", data=payload, headers=headers)
    return _post


def test_create_run(post_run, assert_schema):
    """TC_220"""
    resp = post_run(VALID_RUN)
    assert resp.status == 201
    body = resp.json()
    assert_schema(body, "validation_run")
    assert resp.headers["location"] == f"/api/v1/validation-runs/{body['run_id']}"
    assert body["merchant_id"] == "M002"
    assert body["summary"] == {"total": 3, "AUTO_ACCEPT": 1, "REVIEW": 1, "AUTO_REJECT": 1}
    assert body["mismatches"] == []
    assert body["results"] == VALID_RUN["results"]


def test_create_run_reports_ground_truth_mismatches(post_run):
    """TC_221 — M002 accepts Klarna but not Amex."""
    payload = _run(results=[
        {"payment_method": "amex",   "detected": True,  "confidence_score": 0.9,
         "status": "AUTO_ACCEPT", "signals": ["network", "img", "alt"]},
        {"payment_method": "klarna", "detected": False, "confidence_score": 0.2,
         "status": "AUTO_REJECT", "signals": ["text"]},
    ])
    body = post_run(payload).json()
    assert body["mismatches"] == [
        {"payment_method": "amex",   "reported": True,  "expected": False},
        {"payment_method": "klarna", "reported": False, "expected": True},
    ]


def test_get_run_by_location(api, post_run, assert_schema):
    """TC_222"""
    created = post_run(VALID_RUN)
    resp = api.get(created.headers["location"])
    assert resp.status == 200
    assert_schema(resp.json(), "validation_run")
    assert resp.json() == created.json()


@pytest.mark.parametrize("key, status, code", [
    (None,        401, "UNAUTHORIZED"),
    ("wrong-key", 403, "FORBIDDEN"),
])
def test_create_run_auth(post_run, assert_schema, key, status, code):
    """TC_223"""
    resp = post_run(VALID_RUN, key=key)
    assert resp.status == status
    assert_schema(resp.json(), "error")
    assert resp.json()["error"]["code"] == code


@pytest.mark.parametrize("payload, field", [
    pytest.param(_run(merchant_id="merchant-2"),               "merchant_id",         id="bad-merchant-id"),
    pytest.param(_run(results=[]),                             "results",             id="empty-results"),
    pytest.param({"results": VALID_RUN["results"]},           "merchant_id",         id="missing-merchant"),
    pytest.param(_result(0, confidence_score=1.2),             "results.0.confidence_score", id="score-above-1"),
    pytest.param(_result(0, status="ACCEPTED"),                "results.0.status",    id="unknown-status"),
    pytest.param(_result(0, payment_method="bitcoin"),         "results.0.payment_method", id="unknown-method"),
    pytest.param(_result(0, signals=["dom"]),                  "results.0.signals.0", id="unknown-signal"),
    pytest.param(_result(1, status="AUTO_ACCEPT", detected=True), "results.1",        id="status-vs-score"),
    pytest.param(_result(0, detected=False),                   "results.0",           id="detected-vs-status"),
    pytest.param(_result(2, payment_method="visa"),            "",                    id="duplicate-method"),
])
def test_create_run_validation_errors(post_run, assert_schema, payload, field):
    """TC_224"""
    resp = post_run(payload)
    assert resp.status == 422
    body = resp.json()
    assert_schema(body, "error")
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert field in [d["field"] for d in body["error"]["details"]]


def test_create_run_unknown_merchant(post_run, assert_schema):
    """TC_225"""
    resp = post_run(_run(merchant_id="M999"))
    assert resp.status == 404
    assert_schema(resp.json(), "error")
    assert resp.json()["error"]["code"] == "MERCHANT_NOT_FOUND"


def test_get_unknown_run(api, assert_schema):
    """TC_226"""
    resp = api.get("/api/v1/validation-runs/00000000-0000-0000-0000-000000000000")
    assert resp.status == 404
    assert_schema(resp.json(), "error")
    assert resp.json()["error"]["code"] == "RUN_NOT_FOUND"
