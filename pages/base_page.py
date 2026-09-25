"""
BasePage
========
Shared storefront chrome (header, cart badge, footer card icons) and
navigation. Page objects expose locators and user actions only — assertions
live in the tests.

Locator policy (in priority order):
  1. Role + accessible name  (get_by_role / get_by_label)
  2. data-testid             (get_by_test_id) for non-semantic containers
  3. data-* attributes        for domain data, e.g. [data-payment-method]
No XPath, no positional CSS, no text-only selectors for dynamic content.
"""

from playwright.sync_api import Locator, Page


class BasePage:
    PATH = ""

    def __init__(self, page: Page, merchant_id: str):
        self.page = page
        self.merchant_id = merchant_id

        self.store_name  = page.get_by_test_id("store-name")
        self.cart_link   = page.get_by_test_id("cart-link")
        self.cart_count  = page.get_by_test_id("cart-count")
        self.footer_icons: Locator = page.get_by_test_id("footer-payment-icons").locator("[data-payment-method]")

    @property
    def url_path(self) -> str:
        return f"/store/{self.merchant_id}/{self.PATH}"

    def open(self, query: str = ""):
        self.page.goto(self.url_path + (f"?{query}" if query else ""))
        self.wait_until_ready()
        return self

    def wait_until_ready(self):
        """App sets body[data-ready] once its initial data has rendered."""
        self.page.locator("body[data-ready='true']").wait_for()

    def footer_card_brands(self) -> list[str]:
        return self.footer_icons.evaluate_all("els => els.map(e => e.dataset.paymentMethod)")

    def go_to_cart(self):
        from pages.cart_page import CartPage
        self.cart_link.click()
        return CartPage(self.page, self.merchant_id).wait_for_page()

    def wait_for_page(self):
        self.page.wait_for_url(f"**{self.url_path}")
        self.wait_until_ready()
        return self
