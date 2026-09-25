"""
StoreLocatorPage / StoreDetailPage
==================================
Profile-driven page objects for chain store locators: search an address,
read the candidate list, open one store, read its details.
"""

from dataclasses import dataclass

from playwright.sync_api import Locator, Page

from pages.site_profile import SiteProfile


@dataclass
class StoreSearchResult:
    name:    str
    address: str
    index:   int


class StoreLocatorPage:

    def __init__(self, page: Page, profile: SiteProfile, site_url: str):
        self.page     = page
        self.profile  = profile
        self.site_url = site_url.rstrip("/")

        self.search_input = page.locator(profile.search_input)
        self.search_button = page.locator(profile.search_submit)
        self.results_panel = page.locator(profile.results_container)
        self.status        = page.locator(profile.results_status)
        self.results       = self.results_panel.locator(profile.result_item)

    def open(self, timeout: float = 15000):
        response = self.page.goto(self.site_url + self.profile.locator_path, wait_until="domcontentloaded")
        self.search_input.wait_for(timeout=timeout)
        return response

    def search(self, query: str, timeout: float = 15000):
        self.search_input.fill(query)
        self.search_button.click()
        self.wait_for_results(timeout)
        return self

    def wait_for_results(self, timeout: float = 15000):
        """Results render async; wait for the panel's busy state to clear."""
        self.page.wait_for_function(
            "([panel, busy]) => { const el = document.querySelector(panel);"
            " return el && !el.matches(busy) && !el.querySelector(busy); }",
            arg=[self.profile.results_container, self.profile.busy_selector],
            timeout=timeout,
        )
        return self

    def result_list(self, timeout: float = 15000) -> list[StoreSearchResult]:
        items = []
        for i in range(self.results.count()):
            item = self.results.nth(i)
            items.append(StoreSearchResult(
                name=item.locator(self.profile.result_name).inner_text(timeout=timeout).strip(),
                address=item.locator(self.profile.result_address).inner_text(timeout=timeout).strip(),
                index=i,
            ))
        return items

    def result(self, index: int) -> Locator:
        return self.results.nth(index)

    def open_result(self, index: int, timeout: float = 15000) -> "StoreDetailPage":
        self.results.nth(index).locator(self.profile.result_link).click(timeout=timeout)
        detail = StoreDetailPage(self.page, self.profile)
        detail.content.wait_for(timeout=timeout)
        return detail


class StoreDetailPage:

    def __init__(self, page: Page, profile: SiteProfile):
        self.page     = page
        self.profile  = profile
        self.content  = page.locator(profile.store_content)
        self.address  = page.locator(profile.store_address)
        self.closed   = page.locator(profile.store_closed) if profile.store_closed else None
        self.payments = page.locator(profile.store_payments) if profile.store_payments else None

    def address_text(self) -> str:
        return self.address.inner_text().strip()

    def is_temporarily_closed(self) -> bool:
        return bool(self.closed and self.closed.count() and self.closed.first.is_visible())

    def payment_methods(self) -> list[str]:
        """Method codes tagged in the store's payment section (UI checks only;
        the pipeline uses the scraper's layered detection instead)."""
        if not self.payments or not self.payments.count():
            return []
        return self.payments.locator("[data-payment-method]").evaluate_all(
            "els => els.map(e => e.dataset.paymentMethod)"
        )
