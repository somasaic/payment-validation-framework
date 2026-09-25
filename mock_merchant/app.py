"""
Mock Merchant Service
=====================
Local stand-in for the merchant sites and the validation results API, so UI,
API and pipeline tests run against a deterministic target in CI.

Storefront (HTML):
  GET  /store/{merchant_id}/            product listing + footer payment icons
  GET  /store/{merchant_id}/cart        cart
  GET  /store/{merchant_id}/checkout    checkout with async payment-methods widget

REST API (JSON, /api/v1):
  GET  /health
  GET  /api/v1/merchants                       ?region=&limit=&offset=
  GET  /api/v1/merchants/{merchant_id}
  GET  /api/v1/merchants/{merchant_id}/products
  GET  /api/v1/merchants/{merchant_id}/payment-methods   ?context=checkout|footer
  POST /api/v1/validation-runs                 (requires X-API-Key)
  GET  /api/v1/validation-runs/{run_id}

The checkout payment widget is served with configurable latency
(MOCK_PAYMENT_LATENCY_MS) to reproduce slow third-party payment iframes.
"""

import asyncio
import os
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator, model_validator

from mock_merchant import catalog
from utils.csv_reader import load_detection_rules

API_VERSION = "1.0.0"
STATIC_DIR  = os.path.join(os.path.dirname(__file__), "static")

_thresholds     = load_detection_rules()["confidence_thresholds"]
AUTO_ACCEPT_MIN = _thresholds["auto_accept"]
REVIEW_MIN      = _thresholds["review_required"]

Status = Literal["AUTO_ACCEPT", "REVIEW", "AUTO_REJECT"]
Signal = Literal["network", "img", "text", "alt", "css_class"]


# ── Models ────────────────────────────────────────────────────────────────────

class ValidationResultIn(BaseModel):
    payment_method:   str
    detected:         bool
    confidence_score: float = Field(ge=0.0, le=1.0)
    status:           Status
    signals:          list[Signal] = []

    @field_validator("payment_method")
    @classmethod
    def known_method(cls, v: str) -> str:
        if v not in catalog.payment_method_catalog():
            raise ValueError(f"unknown payment method '{v}'")
        return v

    @model_validator(mode="after")
    def status_matches_score(self):
        expected = (
            "AUTO_ACCEPT" if self.confidence_score >= AUTO_ACCEPT_MIN
            else "REVIEW" if self.confidence_score >= REVIEW_MIN
            else "AUTO_REJECT"
        )
        if self.status != expected:
            raise ValueError(
                f"status {self.status} inconsistent with confidence_score "
                f"{self.confidence_score} (expected {expected})"
            )
        if self.detected != (self.status == "AUTO_ACCEPT"):
            raise ValueError("detected must be true only for AUTO_ACCEPT results")
        return self


class ValidationRunIn(BaseModel):
    merchant_id: str = Field(pattern=r"^M\d{3}$")
    results:     list[ValidationResultIn] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_methods(self):
        methods = [r.payment_method for r in self.results]
        if len(methods) != len(set(methods)):
            raise ValueError("duplicate payment_method in results")
        return self


# ── Errors ────────────────────────────────────────────────────────────────────

class ApiError(HTTPException):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(status_code=status_code, detail=message)
        self.code = code


def _error(status_code: int, code: str, message: str, details: list | None = None) -> JSONResponse:
    body = {"error": {"code": code, "message": message}}
    if details:
        body["error"]["details"] = details
    return JSONResponse(status_code=status_code, content=body)


# ── App factory ───────────────────────────────────────────────────────────────

def create_app(payment_latency_ms: int | None = None, api_key: str | None = None) -> FastAPI:
    if payment_latency_ms is None:
        payment_latency_ms = int(os.getenv("MOCK_PAYMENT_LATENCY_MS", "800"))
    api_key = api_key or os.getenv("MOCK_API_KEY", "local-dev-key")

    app = FastAPI(title="Mock Merchant Service", version=API_VERSION, docs_url="/docs")
    app.state.validation_runs = {}

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        return response

    @app.exception_handler(ApiError)
    async def api_error_handler(_: Request, exc: ApiError):
        return _error(exc.status_code, exc.code, exc.detail)

    @app.exception_handler(HTTPException)
    async def http_error_handler(_: Request, exc: HTTPException):
        return _error(exc.status_code, "HTTP_ERROR", str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_: Request, exc: RequestValidationError):
        details = [
            {"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]}
            for e in exc.errors()
        ]
        return _error(422, "VALIDATION_ERROR", "Request validation failed", details)

    def _merchant_or_404(merchant_id: str) -> dict:
        merchant = catalog.get_merchant(merchant_id)
        if merchant is None:
            raise ApiError(404, "MERCHANT_NOT_FOUND", f"Merchant '{merchant_id}' not found")
        return merchant

    def _public_merchant(m: dict) -> dict:
        return {
            "merchant_id":    m["merchant_id"],
            "merchant_name":  m["merchant_name"],
            "region":         m["region"],
            "country_code":   m["country_code"],
            "platform":       m["platform"],
            "segment":        m["segment"],
            "currency":       m["currency"],
            "storefront_url": f"/store/{m['merchant_id']}/",
        }

    # ── Storefront pages ──────────────────────────────────────────────────

    def _page(merchant_id: str, name: str) -> FileResponse:
        _merchant_or_404(merchant_id)
        return FileResponse(os.path.join(STATIC_DIR, f"{name}.html"))

    @app.get("/store/{merchant_id}/", include_in_schema=False)
    def store_home(merchant_id: str):
        return _page(merchant_id, "store")

    @app.get("/store/{merchant_id}/cart", include_in_schema=False)
    def store_cart(merchant_id: str):
        return _page(merchant_id, "cart")

    @app.get("/store/{merchant_id}/checkout", include_in_schema=False)
    def store_checkout(merchant_id: str):
        return _page(merchant_id, "checkout")

    # ── API ───────────────────────────────────────────────────────────────

    @app.get("/health")
    def health():
        return {"status": "ok", "version": API_VERSION}

    @app.get("/api/v1/merchants")
    def list_merchants(
        region: str | None = Query(None, pattern=r"^[A-Za-z]{2}$"),
        limit:  int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0),
    ):
        items = list(catalog.merchants().values())
        if region:
            items = [m for m in items if m["region"] == region.upper()]
        page = items[offset:offset + limit]
        return {
            "items":  [_public_merchant(m) for m in page],
            "total":  len(items),
            "limit":  limit,
            "offset": offset,
        }

    @app.get("/api/v1/merchants/{merchant_id}")
    def get_merchant(merchant_id: str):
        return _public_merchant(_merchant_or_404(merchant_id))

    @app.get("/api/v1/merchants/{merchant_id}/products")
    def get_products(merchant_id: str):
        merchant = _merchant_or_404(merchant_id)
        currency = catalog.currency_info(merchant["currency"])
        return {
            "merchant_id": merchant["merchant_id"],
            "currency":    merchant["currency"],
            "symbol":      currency["symbol"],
            "decimals":    currency["decimals"],
            "items":       catalog.products(merchant_id),
        }

    @app.get("/api/v1/merchants/{merchant_id}/payment-methods")
    async def get_payment_methods(
        merchant_id: str,
        context:    Literal["checkout", "footer"] = "checkout",
        latency_ms: int | None = Query(None, ge=0, le=15000),
    ):
        merchant = _merchant_or_404(merchant_id)
        if context == "checkout":
            delay = payment_latency_ms if latency_ms is None else latency_ms
            if delay:
                await asyncio.sleep(delay / 1000)
        methods = catalog.accepted_methods(merchant_id)
        if context == "footer":
            methods = [m for m in methods if m["type"] == "card"]
        return {
            "merchant_id": merchant["merchant_id"],
            "currency":    merchant["currency"],
            "methods":     methods,
        }

    @app.post("/api/v1/validation-runs", status_code=201)
    def create_validation_run(
        payload:   ValidationRunIn,
        x_api_key: str | None = Header(None),
    ):
        if x_api_key is None:
            raise ApiError(401, "UNAUTHORIZED", "Missing X-API-Key header")
        if x_api_key != api_key:
            raise ApiError(403, "FORBIDDEN", "Invalid API key")
        merchant = _merchant_or_404(payload.merchant_id)

        accepted = set(merchant["accepted_methods"])
        summary = {"total": len(payload.results), "AUTO_ACCEPT": 0, "REVIEW": 0, "AUTO_REJECT": 0}
        mismatches = []
        for r in payload.results:
            summary[r.status] += 1
            if r.status != "REVIEW" and r.detected != (r.payment_method in accepted):
                mismatches.append({
                    "payment_method": r.payment_method,
                    "reported":       r.detected,
                    "expected":       r.payment_method in accepted,
                })

        run = {
            "run_id":      str(uuid.uuid4()),
            "merchant_id": merchant["merchant_id"],
            "created_at":  datetime.now(timezone.utc).isoformat(),
            "summary":     summary,
            "mismatches":  mismatches,
            "results":     [r.model_dump() for r in payload.results],
        }
        app.state.validation_runs[run["run_id"]] = run
        return JSONResponse(
            status_code=201,
            content=run,
            headers={"Location": f"/api/v1/validation-runs/{run['run_id']}"},
        )

    @app.get("/api/v1/validation-runs/{run_id}")
    def get_validation_run(run_id: str):
        run = app.state.validation_runs.get(run_id)
        if run is None:
            raise ApiError(404, "RUN_NOT_FOUND", f"Validation run '{run_id}' not found")
        return run

    return app


app = create_app()
