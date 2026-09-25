"""
Shared fixtures
===============
- Target environment: a local mock merchant service is started once per
  session, unless TARGET_BASE_URL points at an already-running deployment.
- `base_url` feeds pytest-playwright, so pages navigate with relative paths.
- Tests are marked by folder (unit / ui / api / integration) for `-m` filtering.
- Reporting: Allure labels + titles from TC ids, pytest-html "Test case"
  column, and screenshot + URL attached to both reports when a UI test fails.
"""

import base64
import json
import os
import platform
import re
from pathlib import Path

import allure
import pytest
from jsonschema import Draft202012Validator

from mock_merchant import catalog
from mock_merchant.server import MockMerchantServer

TESTS_DIR   = Path(__file__).parent
SCHEMAS_DIR = TESTS_DIR / "api" / "schemas"
API_KEY     = os.getenv("MOCK_API_KEY", "local-dev-key")

SUITES = {
    "unit":        "Scoring & Data",
    "ui":          "Storefront & Store Locator UI",
    "api":         "REST API",   # Postman results join this epic (scripts/prepare_allure_results.py)
    "integration": "Validation Pipelines",
}
TC_LINE = re.compile(r"^(TC_\d+)\s+(.+)$", re.MULTILINE)


def _suite(item) -> str | None:
    suite = Path(item.fspath).relative_to(TESTS_DIR).parts[0]
    return suite if suite in SUITES else None


def pytest_collection_modifyitems(items):
    for item in items:
        suite = _suite(item)
        if suite:
            item.add_marker(suite)


# ── Reporting ─────────────────────────────────────────────────────────────────

def _test_case(item) -> tuple[str, str]:
    """(TC id, description) from the test docstring + the module's TC index."""
    doc = (getattr(item, "function", None) and item.function.__doc__) or ""
    match = re.match(r"\s*(TC_\d+)", doc)
    if not match:
        return "", ""
    index = dict(TC_LINE.findall(item.module.__doc__ or ""))
    return match.group(1), index.get(match.group(1), "")


@pytest.fixture(autouse=True)
def _allure_labels(request):
    item = request.node
    suite = _suite(item)
    if suite:
        allure.dynamic.epic(SUITES[suite])
        allure.dynamic.tag(suite)
    module_title = (item.module.__doc__ or "").strip().splitlines()
    if suite:
        allure.dynamic.parent_suite(SUITES[suite])
    if module_title:
        allure.dynamic.feature(module_title[0])
        allure.dynamic.suite(module_title[0])
    tc, _ = _test_case(item)
    if tc:
        allure.dynamic.label("testId", tc)


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_call(item):
    # allure-pytest names the result when the call phase starts; title it after that
    tc, description = _test_case(item)
    if tc:
        params = f" [{item.callspec.id}]" if hasattr(item, "callspec") else ""
        allure.dynamic.title(f"{tc} · {description or item.originalname}{params}")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    report.test_case = " ".join(_test_case(item)).strip()
    page = item.funcargs.get("page") if hasattr(item, "funcargs") else None
    if report.when != "call" or not report.failed or page is None:
        return
    try:
        png = page.screenshot(full_page=True)
    except Exception:
        return
    allure.attach(png, name="screenshot on failure", attachment_type=allure.attachment_type.PNG)
    allure.attach(page.url, name="page url", attachment_type=allure.attachment_type.URI_LIST)
    html_plugin = item.config.pluginmanager.getplugin("html")
    if html_plugin:
        extras = getattr(report, "extras", [])
        extras.append(html_plugin.extras.url(page.url))
        extras.append(html_plugin.extras.png(base64.b64encode(png).decode()))
        report.extras = extras


def pytest_html_report_title(report):
    report.title = "Payment Validation Framework — Test Report"


def pytest_html_results_table_header(cells):
    cells.insert(1, "<th>Test case</th>")


def pytest_html_results_table_row(report, cells):
    cells.insert(1, f"<td>{getattr(report, 'test_case', '')}</td>")


def pytest_sessionstart(session):
    """HTML report environment table (runs after plugins add their own keys)."""
    try:
        from pytest_metadata.plugin import metadata_key
        metadata = session.config.stash[metadata_key]
        for noisy in ("JAVA_HOME", "Base URL"):
            metadata.pop(noisy, None)
        metadata.update({
            "Target": os.getenv("TARGET_BASE_URL", "local mock merchant service"),
            "Playwright": _version("playwright"),
        })
    except (ImportError, KeyError):
        pass


def pytest_sessionfinish(session):
    """Allure environment widget."""
    results_dir = getattr(session.config.option, "allure_report_dir", None)
    if not results_dir:
        return
    os.makedirs(results_dir, exist_ok=True)
    env = {
        "Python": platform.python_version(),
        "Playwright": _version("playwright"),
        "OS": f"{platform.system()} {platform.release()}",
        "Target": os.getenv("TARGET_BASE_URL", "local mock merchant service"),
        "Branch": os.getenv("GITHUB_REF_NAME", "local"),
        "Commit": os.getenv("GITHUB_SHA", "")[:7] or "local",
    }
    Path(results_dir, "environment.properties").write_text(
        "\n".join(f"{k}={v}" for k, v in env.items()) + "\n", encoding="utf-8"
    )


def _version(package: str) -> str:
    from importlib.metadata import PackageNotFoundError, version
    try:
        return version(package)
    except PackageNotFoundError:
        return "n/a"


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
