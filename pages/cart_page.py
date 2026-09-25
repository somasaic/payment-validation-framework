from playwright.sync_api import Locator, Page

from pages.base_page import BasePage


class CartPage(BasePage):
    PATH = "cart"

    def __init__(self, page: Page, merchant_id: str):
        super().__init__(page, merchant_id)
        self.items           = page.get_by_test_id("cart-item")
        self.empty_message   = page.get_by_test_id("cart-empty")
        self.subtotal        = page.get_by_test_id("cart-subtotal")
        self.checkout_button = page.get_by_role("button", name="Proceed to checkout")

    def item(self, name: str) -> Locator:
        return self.items.filter(has_text=name)

    def quantity_of(self, name: str) -> Locator:
        return self.item(name).get_by_test_id("item-qty")

    def line_total_of(self, name: str) -> Locator:
        return self.item(name).get_by_test_id("item-total")

    def increase(self, name: str):
        self.page.get_by_role("button", name=f"Increase quantity of {name}").click()
        return self

    def decrease(self, name: str):
        self.page.get_by_role("button", name=f"Decrease quantity of {name}").click()
        return self

    def remove(self, name: str):
        self.item(name).get_by_role("button", name="Remove").click()
        return self

    def proceed_to_checkout(self):
        from pages.checkout_page import CheckoutPage
        self.checkout_button.click()
        return CheckoutPage(self.page, self.merchant_id).wait_for_page()
