"""
Store Locator UI Tests (profile-driven POM)
===========================================
TC_120  Address + postcode search ranks the matching store first
TC_121  Unknown area shows a no-results message
TC_122  Async search: busy state is awaited, never a fixed delay
TC_123  Store page shows its address and the payments accepted at that store
TC_124  Store without published payment info has no payments section
TC_125  Temporarily closed store shows a closed notice
TC_126  Footer logos are brand-wide and differ from the store's own methods
TC_127  Info pages (privacy, terms, contact, FAQ) reachable from the footer
"""

import pytest
from playwright.sync_api import Page, expect

from mock_merchant import chain
from mock_merchant.server import rewrite_url
from pages.components.site_footer import SiteFooter
from pages.site_profile import profile_for_url
from pages.store_locator_page import StoreLocatorPage

SITES = {
    "us": "https://us.crustandco.example",
    "uk": "https://uk.crustandco.example",
    "in": "https://in.crustandco.example",
}


@pytest.fixture
def locator_for(page: Page, base_url):
    def _open(region: str) -> StoreLocatorPage:
        real = SITES[region]
        locator = StoreLocatorPage(page, profile_for_url(real), rewrite_url(real, base_url))
        locator.open()
        return locator
    return _open


def _store(store_id: str) -> dict:
    return chain.get_store(store_id)


@pytest.mark.parametrize("region, query, expected_first", [
    ("us", "1450 Market St, San Francisco 94103", "1450 Market Street, San Francisco, CA 94103"),
    ("uk", "Oxford Street London W1D 1BS",        "10 Oxford Street, London W1D 1BS"),
    ("in", "MG Road Bengaluru 560001",            "44 MG Road, Bengaluru, Karnataka 560001"),
])
def test_search_ranks_matching_store_first(locator_for, region, query, expected_first):
    """TC_120"""
    locator = locator_for(region).search(query)
    results = locator.result_list()
    assert results, "expected at least one candidate"
    assert results[0].address == expected_first
    expect(locator.status).to_have_text(f"{len(results)} store{'s' if len(results) > 1 else ''} found")


def test_no_results_message(locator_for):
    """TC_121"""
    locator = locator_for("us").search("Anchorage AK 99501")
    expect(locator.results).to_have_count(0)
    expect(locator.status).to_have_text('No stores found near "Anchorage AK 99501"')


def test_async_search_is_awaited(locator_for):
    """TC_122 — search API answers after ~600 ms."""
    locator = locator_for("us")
    locator.search_input.fill("San Francisco")
    locator.search_button.click()
    expect(locator.results_panel).to_have_attribute("aria-busy", "true")
    expect(locator.status).to_have_text("Searching…")

    locator.wait_for_results()

    expect(locator.results_panel).to_have_attribute("aria-busy", "false")
    assert len(locator.result_list()) == 2


@pytest.mark.parametrize("region, query, store_id", [
    ("us", "1450 Market Street 94103", "US-1001"),
    ("in", "Connaught Place 110001",   "IN-3001"),
])
def test_store_page_shows_payments(locator_for, region, query, store_id):
    """TC_123"""
    detail = locator_for(region).search(query).open_result(0)
    store = _store(store_id)
    expect(detail.address).to_have_text(store["address"])
    expect(detail.payments).to_be_visible()
    assert detail.payment_methods() == store["accepted_methods"]
    assert not detail.is_temporarily_closed()


def test_store_without_payment_info(locator_for):
    """TC_124"""
    detail = locator_for("us").search("800 Congress Avenue Austin 78701").open_result(0)
    expect(detail.address).to_have_text(_store("US-1004")["address"])
    expect(detail.payments).to_have_count(0)
    assert detail.payment_methods() == []


def test_temporarily_closed_store(locator_for):
    """TC_125"""
    detail = locator_for("us").search("1200 Pine Street Seattle 98101").open_result(0)
    expect(detail.closed).to_have_text("Temporarily closed")
    assert detail.is_temporarily_closed()


def test_footer_logos_are_brand_wide(locator_for, page: Page):
    """TC_126 — US footer shows Amex; the Embarcadero store does not take it."""
    detail = locator_for("us").search("2 Embarcadero Center 94111").open_result(0)
    footer = SiteFooter(page)
    assert "amex" in footer.payment_methods()
    assert "amex" not in detail.payment_methods()


@pytest.mark.parametrize("link, heading", [
    ("Privacy Policy",     "Privacy Policy"),
    ("Terms & Conditions", "Terms & Conditions"),
    ("Contact Us",         "Contact Us"),
    ("FAQ",                "FAQ"),
])
def test_info_pages_linked_from_footer(locator_for, page: Page, link, heading):
    """TC_127"""
    locator_for("uk")
    SiteFooter(page).open(link)
    expect(page.get_by_role("heading", level=1)).to_have_text(heading)
    expect(page).to_have_title(f"{heading} | Crust & Co United Kingdom")
