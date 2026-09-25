"""
PaymentMethodsPanel
===================
Checkout payment widget. Methods load asynchronously (like third-party
payment iframes), so every read goes through wait_until_loaded(), which waits
on the widget's own aria-busy state rather than a fixed delay.
"""

from playwright.sync_api import Locator, Page


class PaymentMethodsPanel:

    def __init__(self, page: Page):
        self.page         = page
        self.root         = page.get_by_test_id("payment-methods")
        self.loading      = self.root.get_by_test_id("payment-loading")
        self.error        = self.root.get_by_role("alert")
        self.retry_button = self.root.get_by_role("button", name="Retry")
        self.options      = self.root.get_by_role("radio")
        self.items        = self.root.locator("[data-payment-method]")
        self.selected     = self.root.get_by_test_id("selected-payment-method")

    def wait_until_loaded(self, timeout: float | None = None):
        self.page.locator("[data-testid='payment-methods'][aria-busy='false']").wait_for(timeout=timeout)
        return self

    def available_methods(self) -> list[str]:
        """Method codes as rendered, e.g. ['visa', 'mastercard', 'paypal']."""
        self.wait_until_loaded()
        return self.items.evaluate_all("els => els.map(e => e.dataset.paymentMethod)")

    def methods_of_type(self, method_type: str) -> list[str]:
        self.wait_until_loaded()
        return self.root.locator(f"[data-method-type='{method_type}']").evaluate_all(
            "els => els.map(e => e.dataset.paymentMethod)"
        )

    def option(self, display_name: str) -> Locator:
        return self.root.get_by_role("radio", name=display_name, exact=True)

    def select(self, display_name: str):
        self.wait_until_loaded()
        self.option(display_name).check()
        return self

    def retry(self):
        self.retry_button.click()
        return self
