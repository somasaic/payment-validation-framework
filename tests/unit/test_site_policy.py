"""
Site Policy Unit Tests (live-run safeguards)
============================================
TC_026  Allowlist: None allows all, empty allows none, exact hosts, "*." subdomains
TC_027  Allowlist file parsing; the shipped allowlist is empty (safe by default)
TC_028  robots.txt rules are obeyed, including a group for our user agent
TC_029  robots.txt HTTP handling: 401/403 and 5xx block, 404 allows, network error allows
TC_030  robots.txt is fetched once per origin
"""

import pytest

from utils.site_policy import DEFAULT_ALLOWLIST, USER_AGENT, SitePolicy, load_allowlist

ROBOTS = f"""
User-agent: *
Disallow: /private/

User-agent: {USER_AGENT}
Disallow: /stores/secret
"""


def fetcher(status, body="", calls=None):
    def _fetch(url):
        if calls is not None:
            calls.append(url)
        return status, body
    return _fetch


@pytest.mark.parametrize("allowlist, url, allowed", [
    (None,                     "https://anything.example/x",       True),
    ([],                       "https://shop.example/",            False),
    (["shop.example"],         "https://shop.example/checkout",    True),
    (["shop.example"],         "https://www.shop.example/",        False),
    (["*.brand.example"],      "https://us.brand.example/stores",  True),
    (["*.brand.example"],      "https://brand.example/",           False),
    (["Shop.Example"],         "https://SHOP.example/",            True),
])
def test_allowlist(allowlist, url, allowed):
    """TC_026"""
    assert SitePolicy(allowlist=allowlist).is_allowlisted(url) is allowed


def test_allowlist_file(tmp_path):
    """TC_027"""
    path = tmp_path / "allow.txt"
    path.write_text("# approved\n\nShop.Example   # contract 42\n*.brand.example\n", encoding="utf-8")
    assert load_allowlist(path) == ["shop.example", "*.brand.example"]
    assert load_allowlist(DEFAULT_ALLOWLIST) == [], "shipped allowlist must stay empty"


def test_robots_rules():
    """TC_028"""
    policy = SitePolicy(fetch=fetcher(200, ROBOTS))
    assert policy.robots_block_reason("https://shop.example/products") is None
    assert policy.robots_block_reason("https://shop.example/stores/secret") is not None     # our agent's group
    assert "disallows /stores/secret" in policy.robots_block_reason("https://shop.example/stores/secret")


@pytest.mark.parametrize("status, blocked", [
    (401, True), (403, True),      # access-restricted robots.txt → disallow all
    (404, False), (410, False),    # no robots.txt → allow
    (500, True), (503, True),      # server error → disallow (RFC 9309)
    (None, False),                 # unreachable → allow; navigation reports SITE_UNREACHABLE
])
def test_robots_http_status(status, blocked):
    """TC_029"""
    reason = SitePolicy(fetch=fetcher(status)).robots_block_reason("https://shop.example/")
    assert (reason is not None) is blocked


def test_robots_cached_per_origin():
    """TC_030"""
    calls = []
    policy = SitePolicy(fetch=fetcher(200, ROBOTS, calls))
    for path in ("/", "/a", "/private/b"):
        policy.robots_block_reason(f"https://shop.example{path}")
    policy.robots_block_reason("https://other.example/")
    assert calls == ["https://shop.example/robots.txt", "https://other.example/robots.txt"]
