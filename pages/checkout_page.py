from playwright.sync_api import Page

from pages.base_page import BasePage
from pages.components.payment_methods_panel import PaymentMethodsPanel


class CheckoutPage(BasePage):
    """Checkout up to order review. Never places an order or enters card data."""

    PATH = "checkout"

    def __init__(self, page: Page, merchant_id: str):
        super().__init__(page, merchant_id)
        self.email           = page.get_by_label("Email")
        self.country         = page.get_by_label("Country")
        self.payment         = PaymentMethodsPanel(page)
        self.continue_button = page.get_by_role("button", name="Continue to review")
        self.summary_items   = page.get_by_test_id("summary-item")
        self.summary_empty   = page.get_by_test_id("summary-empty")
        self.order_total     = page.get_by_test_id("order-total")
        self.order_review    = page.get_by_role("region", name="Order review")
        self.review_method   = page.get_by_test_id("review-method")

    def fill_contact(self, email: str, country: str | None = None):
        self.email.fill(email)
        if country:
            self.country.select_option(label=country)
        return self

    def continue_to_review(self):
        self.continue_button.click()
        return self
