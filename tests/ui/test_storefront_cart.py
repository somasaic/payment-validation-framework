"""
Storefront + Cart UI Tests
==========================
TC_101  Product prices render in the merchant's local currency
TC_102  Adding products updates the header cart badge
TC_103  Footer "We accept" shows only card brands the merchant accepts
TC_104  Empty cart shows empty state and blocks checkout
TC_105  Quantity changes recalculate line total and subtotal
TC_106  Decreasing quantity to zero removes the line
TC_107  Cart persists across page reloads
"""

import pytest
from playwright.sync_api import Page, expect

from pages.cart_page import CartPage
from pages.storefront_page import StorefrontPage

HEADPHONES = "Wireless Headphones"
BACKPACK   = "Travel Backpack"


@pytest.mark.parametrize("merchant_id, expected_price", [
    ("M001", "$129.00"),
    ("M002", "£101.91"),
    ("M004", "¥19,221"),
    ("M008", "HK$1,007.49"),
])
def test_prices_in_local_currency(page: Page, merchant_id, expected_price):
    """TC_101"""
    store = StorefrontPage(page, merchant_id).open()
    expect(store.price_of(HEADPHONES)).to_have_text(expected_price)


def test_add_to_cart_updates_badge(page: Page):
    """TC_102"""
    store = StorefrontPage(page, "M001").open()
    expect(store.cart_count).to_have_text("0")

    store.add_to_cart(HEADPHONES, quantity=2).add_to_cart(BACKPACK)

    expect(store.cart_count).to_have_text("3")
    expect(store.toast).to_have_text(f"{BACKPACK} added to cart")


@pytest.mark.parametrize("merchant_id", ["M001", "M002", "M008"])
def test_footer_shows_accepted_card_brands(page: Page, merchant_id, ground_truth):
    """TC_103"""
    store = StorefrontPage(page, merchant_id).open()
    expect(store.footer_icons.first).to_be_visible()

    cards = {"amex", "visa", "mastercard", "discover", "jcb", "unionpay"}
    expected = [m for m in ground_truth[merchant_id] if m in cards]
    assert store.footer_card_brands() == expected


def test_empty_cart_blocks_checkout(page: Page):
    """TC_104"""
    cart = CartPage(page, "M001").open()
    expect(cart.empty_message).to_be_visible()
    expect(cart.items).to_have_count(0)
    expect(cart.checkout_button).to_be_disabled()


def test_quantity_change_recalculates_totals(page: Page):
    """TC_105"""
    cart = StorefrontPage(page, "M001").open() \
        .add_to_cart(HEADPHONES).add_to_cart(BACKPACK).go_to_cart()
    expect(cart.subtotal).to_have_text("$203.50")

    cart.increase(HEADPHONES)

    expect(cart.quantity_of(HEADPHONES)).to_have_text("2")
    expect(cart.line_total_of(HEADPHONES)).to_have_text("$258.00")
    expect(cart.subtotal).to_have_text("$332.50")
    expect(cart.cart_count).to_have_text("3")


def test_decrease_to_zero_removes_line(page: Page):
    """TC_106"""
    cart = StorefrontPage(page, "M001").open() \
        .add_to_cart(HEADPHONES).add_to_cart(BACKPACK).go_to_cart()

    cart.decrease(BACKPACK)

    expect(cart.item(BACKPACK)).to_have_count(0)
    expect(cart.items).to_have_count(1)
    expect(cart.subtotal).to_have_text("$129.00")


def test_cart_persists_across_reload(page: Page):
    """TC_107"""
    cart = StorefrontPage(page, "M002").open().add_to_cart(HEADPHONES).go_to_cart()

    page.reload()
    cart.wait_until_ready()

    expect(cart.quantity_of(HEADPHONES)).to_have_text("1")
    expect(cart.checkout_button).to_be_enabled()
