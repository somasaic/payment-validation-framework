from playwright.sync_api import Locator, Page

from pages.base_page import BasePage


class StorefrontPage(BasePage):
    PATH = ""

    def __init__(self, page: Page, merchant_id: str):
        super().__init__(page, merchant_id)
        self.heading       = page.get_by_role("heading", name="Shop")
        self.product_cards = page.get_by_test_id("product-card")
        self.toast         = page.get_by_test_id("toast")

    def product(self, name: str) -> Locator:
        return self.product_cards.filter(has=self.page.get_by_role("heading", name=name))

    def price_of(self, name: str) -> Locator:
        return self.product(name).get_by_test_id("product-price")

    def add_to_cart(self, name: str, quantity: int = 1):
        button = self.product(name).get_by_role("button", name="Add to cart")
        for _ in range(quantity):
            button.click()
        return self
