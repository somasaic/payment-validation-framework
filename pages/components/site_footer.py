from playwright.sync_api import Locator, Page


class SiteFooter:
    """Site-wide footer: legal/info links and brand-level payment logos."""

    def __init__(self, page: Page):
        self.page  = page
        self.root  = page.locator("footer")
        self.links = self.root.get_by_role("navigation", name="Legal").get_by_role("link")
        self.payment_icons = self.root.locator("[data-payment-method]")

    def link(self, name: str) -> Locator:
        return self.root.get_by_role("link", name=name, exact=True)

    def open(self, name: str):
        self.link(name).click()
        self.page.wait_for_load_state()
        return self

    def payment_methods(self) -> list[str]:
        return self.payment_icons.evaluate_all("els => els.map(e => e.dataset.paymentMethod)")
