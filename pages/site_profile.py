"""
SiteProfile
===========
Per-brand selector map for store-locator sites. Every chain builds its
locator differently; keeping selectors in data/site_profiles.json lets one
set of page objects drive all of them, and onboarding a new brand is a
config change rather than new code.
"""

import json
import os
from dataclasses import dataclass, field
from functools import lru_cache
from urllib.parse import urlparse

PROFILES_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "site_profiles.json")


@dataclass(frozen=True)
class SiteProfile:
    name:              str
    domains:           tuple[str, ...]
    locator_path:      str
    search_input:      str
    search_submit:     str
    results_container: str
    results_status:    str
    result_item:       str
    result_name:       str
    result_address:    str
    result_link:       str
    store_content:     str
    store_address:     str
    store_closed:      str | None = None
    store_payments:    str | None = None
    busy_selector:     str = "[aria-busy='true']"
    extra:             dict = field(default_factory=dict)


@lru_cache(maxsize=1)
def load_site_profiles(path: str = PROFILES_FILE) -> dict[str, SiteProfile]:
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)["profiles"]
    return {
        name: SiteProfile(name=name, **{**cfg, "domains": tuple(cfg["domains"])})
        for name, cfg in raw.items()
    }


def profile_for_url(url: str) -> SiteProfile | None:
    host = urlparse(url).netloc.lower()
    return next((p for p in load_site_profiles().values() if host in p.domains), None)
