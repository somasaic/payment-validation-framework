"""
Checkout Payment Method UI Tests
================================
TC_110  Checkout offers exactly the merchant's accepted methods (all 12 merchants)
TC_111  Amex is not offered where the merchant does not accept it
TC_112  Methods are grouped by type (card / wallet / bnpl)
TC_113  Slow payment widget (4s) — conditional waits, no fixed sleeps
TC_114  Payment API failure shows an error and Retry recovers
TC_115  Continue is gated on email + selected method; review shows the method
TC_116  Checkout with an empty cart cannot continue

Read-only: tests stop at order review. No card data is entered.
"""

import re

import pytest
from playwright.sync_api import Page, Route, expect

from mock_merchant import catalog
from pages.checkout_page import CheckoutPage
from pages.storefront_page import StorefrontPage

PAYMENT_API = re.compile(r"/api/v1/merchants/\w+/payment-methods\?context=checkout")


def _checkout_with_item(page: Page, merchant_id: str, query: str = "") -> CheckoutPage:
    StorefrontPage(page, merchant_id).open().add_to_cart("Smart Watch")
    return CheckoutPage(page, merchant_id).open(query)


@pytest.mark.parametrize("merchant_id", sorted(catalog.merchants()))
def test_checkout_offers_accepted_methods(page: Page, merchant_id, ground_truth):
    """TC_110"""
    checkout = CheckoutPage(page, merchant_id).open()
    assert checkout.payment.available_methods() == ground_truth[merchant_id]


@pytest.mark.parametrize("merchant_id", ["M002", "M003", "M005", "M009", "M011"])
def test_amex_not_offered_when_not_accepted(page: Page, merchant_id):
    """TC_111"""
    checkout = CheckoutPage(page, merchant_id).open()
    checkout.payment.wait_until_loaded()
    expect(checkout.payment.option("American Express")).to_have_count(0)
    expect(checkout.payment.option("Visa")).to_be_visible()


def test_methods_grouped_by_type(page: Page):
    """TC_112"""
    payment = CheckoutPage(page, "M002").open().payment
    assert payment.methods_of_type("card") == ["visa", "mastercard"]
    assert payment.methods_of_type("wallet") == ["paypal"]
    assert payment.methods_of_type("bnpl") == ["klarna", "afterpay"]


def test_slow_payment_widget_is_awaited(page: Page, ground_truth):
    """TC_113 — widget takes 4s; a 2s fixed sleep would read an empty list."""
    checkout = CheckoutPage(page, "M001").open("payment_latency_ms=4000")
    expect(checkout.payment.loading).to_be_visible()

    assert checkout.payment.available_methods() == ground_truth["M001"]
    expect(checkout.payment.loading).to_be_hidden()


def test_payment_api_failure_then_retry(page: Page, ground_truth):
    """TC_114"""
    def fail(route: Route):
        route.fulfill(status=503, json={"error": {"code": "UNAVAILABLE", "message": "down"}})

    page.route(PAYMENT_API, fail)
    checkout = CheckoutPage(page, "M006").open()

    expect(checkout.payment.error).to_be_visible()
    expect(checkout.payment.error).to_contain_text("could not be loaded")
    expect(checkout.payment.options).to_have_count(0)

    page.unroute(PAYMENT_API, fail)
    checkout.payment.retry()

    expect(checkout.payment.error).to_be_hidden()
    assert checkout.payment.available_methods() == ground_truth["M006"]


def test_continue_requires_email_and_method(page: Page):
    """TC_115"""
    checkout = _checkout_with_item(page, "M010")
    expect(checkout.continue_button).to_be_disabled()

    checkout.payment.select("American Express")
    expect(checkout.payment.selected).to_have_text("Selected: American Express")
    expect(checkout.continue_button).to_be_disabled()

    checkout.fill_contact("not-an-email")
    expect(checkout.continue_button).to_be_disabled()

    checkout.fill_contact("qa.reviewer@example.com", country="United States")
    expect(checkout.continue_button).to_be_enabled()

    checkout.continue_to_review()
    expect(checkout.order_review).to_be_visible()
    expect(checkout.review_method).to_have_text("American Express")


def test_empty_cart_cannot_continue(page: Page):
    """TC_116"""
    checkout = CheckoutPage(page, "M001").open()
    expect(checkout.summary_empty).to_be_visible()

    checkout.payment.select("Visa")
    checkout.fill_contact("qa.reviewer@example.com")

    expect(checkout.continue_button).to_be_disabled()
