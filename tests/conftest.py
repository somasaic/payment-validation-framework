"""
Shared fixtures
===============
- Target environment: a local mock merchant service is started once per
  session, unless TARGET_BASE_URL points at an already-running deployment.
- `base_url` feeds pytest-playwright, so pages navigate with relative paths.
- Tests are marked by folder (unit / ui / api / integration) for `-m` filtering.
"""

import json
import os
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from mock_merchant import catalog
from mock_merchant.server import MockMerchantServer

TESTS_DIR   = Path(__file__).parent
SCHEMAS_DIR = TESTS_DIR / "api" / "schemas"
API_KEY     = os.getenv("MOCK_API_KEY", "local-dev-key")


def pytest_collection_modifyitems(items):
    for item in items:
        suite = Path(item.fspath).relative_to(TESTS_DIR).parts[0]
        if suite in ("unit", "ui", "api", "integration"):
            item.add_marker(suite)


# ── Environment ───────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def base_url():
    external = os.getenv("TARGET_BASE_URL")
    if external:
        yield external.rstrip("/")
        return
    with MockMerchantServer(payment_latency_ms=300, api_key=API_KEY) as server:
        yield server.url


@pytest.fixture(scope="session")
def ground_truth() -> dict[str, list[str]]:
    """merchant_id → payment method codes the merchant really accepts."""
    return {mid: m["accepted_methods"] for mid, m in catalog.merchants().items()}


# ── Browser ───────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    return {**browser_context_args, "viewport": {"width": 1280, "height": 800}, "locale": "en-US"}


# ── API ───────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def api(playwright, base_url):
    context = playwright.request.new_context(
        base_url=base_url,
        extra_http_headers={"Accept": "application/json"},
    )
    yield context
    context.dispose()


@pytest.fixture(scope="session")
def api_key() -> str:
    return API_KEY


@pytest.fixture(scope="session")
def assert_schema():
    validators = {}

    def _assert(body: dict, schema_name: str):
        if schema_name not in validators:
            schema = json.loads((SCHEMAS_DIR / f"{schema_name}.json").read_text(encoding="utf-8"))
            validators[schema_name] = Draft202012Validator(schema)
        errors = sorted(validators[schema_name].iter_errors(body), key=lambda e: list(e.path))
        assert not errors, f"{schema_name} schema violations:\n" + "\n".join(
            f"  {'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in errors
        )

    return _assert
