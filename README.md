# 🌍 Global Payment Method Validation Framework

> **Automated validation of accepted payment methods (Amex, Visa, Mastercard, Discover, PayPal, wallets, RuPay/UPI and more) across multi-region merchant websites and individual store locations — built to replace a multi-day manual QA process.**

![Python](https://img.shields.io/badge/Python-3.11-blue?logo=python)
![Playwright](https://img.shields.io/badge/Playwright-1.55-green?logo=playwright)
![pytest](https://img.shields.io/badge/pytest-8.2-orange)
![Postman](https://img.shields.io/badge/Postman-Newman-ff6c37?logo=postman)
![CI](https://github.com/somasaic/payment-validation-framework/actions/workflows/validation_pipeline.yml/badge.svg)
![Tests](https://img.shields.io/badge/tests-168_pytest_%2B_85_Postman-brightgreen)
![Allure](https://img.shields.io/badge/report-Allure-orange)
![Regions](https://img.shields.io/badge/Regions-US_GB_DE_JP_AU_SG_HK_CA_FR_IN-red)

---

## 🎯 Background & Problem

For a global card network client, the team maintained a **master dataset of 5,000–10,000 merchant records**: website URL, merchant name, region, and (for chains) the **street address of each location**, plus one Yes/No column per payment method. Each record had to be validated against what the merchant **publicly shows** on its website: home page, cart and checkout, store locator, FAQ, terms, privacy and contact pages.

For multi-location brands the question is per store: *does the location at this address advertise Amex / Visa / Mastercard…?* That means opening the brand's regional site, using its **store locator** to find that exact address, and checking the store's own page.

**Manual process (before automation):**
1. Open the merchant website (or the brand's regional site)
2. For chains: search the store locator for the address, open the matching store
3. Visually identify payment icons and text across the relevant pages
4. Screenshot evidence, fill Yes/No per method, write a reason when the site can't be checked
5. QA-verify → clean data → deliver to client

**The problem:** thousands of records × 10+ methods, repeated every release cycle, high error rate from fatigue, and no audit trail linking a Yes/No back to evidence.

**Total cycle time: 3+ days per batch.**

---

## ✅ What This Framework Does

Two read-only, non-destructive pipelines:

| Pipeline | Input | Output |
|----------|-------|--------|
| **Master dataset validation** (`run_master_validation.py`) | `data/master_dataset.csv`: one row per merchant or store location | Same dataset with every `<method>_accepted` filled **Yes / No / Review**, plus `validation_status`, `reason` and evidence columns |
| **Site-level validation** (`run_validation.py`) | `data/merchants.csv` | Per-method confidence dataset, review queue, screenshots |

**Store-location flow** (records with a `store_address`):

| Step | What happens |
|------|-------------|
| **1. Locator** | Opens the brand's regional site → store locator (selectors from `data/site_profiles.json`) |
| **2. Search** | Searches the record's address + postcode, waits for async results |
| **3. Match** | Normalises addresses (`St.`→`street`, `M.G.`→`mg`…) and picks the candidate that matches, gated on postcode and street number |
| **4. Store page** | Opens the store, re-verifies the address, detects "temporarily closed" notices |
| **5. Scan (scoped)** | Scans **only the store's own content**. Site-wide footer logos are brand claims, not proof that a given location accepts a card |
| **6. Fill** | Scores each method → Yes / No / Review, captures an element screenshot as evidence |

**Site-level flow** (records without an address): landing page → public pages discovered from the site's own links (store locator, payment/help, FAQ, terms, privacy, contact) → checkout. Records which page each method was seen on.

**Result: Multi-day manual process → under 2 hours automated.**

---

## 🔍 Detection Engine (4-Layer Approach)

```
Layer 1: Network interception   — resource URLs containing method identifiers
                                  (scoped scans: only icons inside the store content that actually loaded)
Layer 2: Image src/alt scan     — payment icon filenames and alt text
Layer 3: DOM text match         — keyword detection in visible text
Layer 4: CSS class scan         — payment-related class names on elements

Confidence score = W_network×signal + W_img×signal + W_text×signal + W_alt×signal + W_css×signal
AUTO_ACCEPT (≥0.80) → Yes    REVIEW (0.50–0.79) → Review    AUTO_REJECT (<0.50) → No
```

**Why layered?** Each source has false-positive risk alone. Combined scoring + confidence thresholds prevents incorrect data reaching the client.

### Reason codes

Every record that can't be fully validated says why:

| Reason | Meaning |
|--------|---------|
| `SITE_UNREACHABLE` | DNS / connection / timeout / HTTP error (retried once) |
| `BLOCKED_BY_BOT_PROTECTION` | 403/429 or a captcha / "verify you are human" interstitial |
| `LOCATOR_NOT_SUPPORTED` | Record has an address but the brand has no site profile, so it gets a site-level result, flagged for review |
| `LOCATOR_PAGE_CHANGED` | The brand's locator moved (404) or its selectors no longer match; the site profile needs updating (not retried) |
| `STORE_NOT_FOUND` | Locator returned no candidate matching the address + postcode |
| `STORE_TEMPORARILY_CLOSED` | Unable to verify: the store page shows a temporarily closed notice. Method columns are left blank |
| `NO_PAYMENT_INFO_DISPLAYED` | Page loaded fine but shows no payment acceptance, so every method is `No` |
| `LOW_CONFIDENCE` | At least one method scored in the Review band |

---

## ⏱️ Reliability: Conditional Waits, Not Sleeps

The scraper originally paused a fixed 2 seconds after each navigation. Checkout payment widgets that render asynchronously (third-party iframes, slow payment APIs) were scanned **before they appeared**. Checkout-only methods such as wallets and BNPL were then silently scored `AUTO_REJECT`, and the failure depended on how fast the network was.

The scraper now waits on page state, configured under `page_readiness` in `detection_rules.json`:

1. `load` event fired
2. no visible busy indicator (`aria-busy="true"`, spinners, skeletons)
3. a payment-context element is visible (only on pages expected to carry payment UI)

Each wait is soft. A timeout is logged and the scan continues, so one slow site never blocks a batch. The regression test runs the real scraper against a checkout widget that takes **4 s** to render (`tests/integration`, TC_302).

The same rule applies to the UI suite: page objects wait on the app's own signals (`body[data-ready]`, the widget's `aria-busy`, the locator's results panel). There are **zero** `wait_for_timeout` calls in tests.

---

## 🧱 Page Object Model

```
pages/
├── base_page.py                 # shared chrome, navigation, app-ready wait
├── storefront_page.py           # products, add to cart
├── cart_page.py                 # quantities, totals, proceed to checkout
├── checkout_page.py             # contact, payment panel, continue to review (never submits)
├── store_locator_page.py        # StoreLocatorPage / StoreDetailPage — profile-driven
├── site_profile.py              # per-brand selector map loader
└── components/
    ├── payment_methods_panel.py # async payment widget
    └── site_footer.py           # legal links + brand-level logos
```

**Locator policy:** role + accessible name first (`get_by_role`, `get_by_label`), then `data-testid` for non-semantic containers, then `data-*` domain attributes. No XPath, no positional CSS. Page objects expose locators and actions only; assertions live in tests.

**Site profiles:** every chain builds its store locator differently. Selectors live in `data/site_profiles.json` (Playwright selector syntax), so one set of page objects drives every brand and onboarding a new brand is a config change:

```json
"crust_and_co": {
  "domains": ["us.crustandco.example", "uk.crustandco.example", "in.crustandco.example"],
  "locator_path": "/stores",
  "search_input": "role=searchbox[name=\"Find a store near you\"]",
  "result_item": "[data-testid='store-result']",
  "store_content": "[data-testid='store-detail']"
}
```

---

## 🔌 REST API Testing

The mock service exposes a versioned API (`/api/v1`) for merchants, payment methods, products, store-locator search and **validation runs** (validated results are POSTed back, and the API flags mismatches against ground truth).

| Suite | Tool | Checks |
|-------|------|--------|
| `tests/api/` | pytest + Playwright `APIRequestContext` + `jsonschema` | JSON Schema (draft 2020-12) on every response, status codes 200/201/401/403/404/422, `Location` / `X-Request-ID` headers, filtering, pagination, field-level 422 details, business rules (status must match score) |
| `postman/` | Postman collection + Newman in CI | 17 requests / 85 assertions: schemas, status codes, payloads, auth, chained requests (`runId`) |

---

## 🧪 Test Coverage

| Suite | IDs | What it covers | Count |
|-------|-----|----------------|-------|
| Unit | TC_001–TC_017 | Scorer, CSV/rules loading, report writer, address normalisation & matching, master dataset I/O, checkpoint resume | 21 |
| UI (POM) | TC_101–TC_127 | Currency, cart, footer brands, accepted methods at checkout for all 12 merchants, slow widget, API 503 + retry (route interception), checkout gating, store-locator search / results / store page / closed / no-payments / footer pages | 48 |
| API | TC_201–TC_234 | Merchants, payment methods, products, validation runs, store-locator search, 404/405 error envelopes | 59 |
| Integration | TC_301–TC_315 | Real scraper + scorer vs ground truth for every merchant and every master-dataset record, scoped scans, publish to API, CLI resume, stale site profiles, maintenance pages | 40 |
| Postman | n/a | 17 requests, 85 assertions | 85 |

```bash
pytest -m unit            # fast, no browser
pytest -m api
pytest -m ui --tracing retain-on-failure
pytest -m integration
TARGET_BASE_URL=https://staging.example.com pytest -m api   # point suites at a deployed environment
```

---

## 📑 Test Reports & CI/CD

Every run in GitHub Actions produces three report formats and publishes them:

| Report | Source | Where |
|--------|--------|-------|
| **Allure Report** | pytest (`allure-pytest`) + Postman (`newman-reporter-allure`), merged | GitHub Pages: **https://somasaic.github.io/payment-validation-framework/** |
| **HTML reports** | `pytest-html` per suite (self-contained), Newman `htmlextra` for Postman | GitHub Pages `/html/` + `test-report` artifact |
| **JUnit XML** | pytest + Newman | job artifacts |

**Allure setup:**
- **Behaviors**: epics per suite (Scoring & Data, Storefront & Store Locator UI, REST API, Validation Pipelines), features per test module, and titles taken from the TC IDs (`TC_110 · Checkout offers exactly the merchant's accepted methods [M002]`)
- **Suites**: Postman folders and pytest API tests grouped together under *REST API*
- **Categories**: timeouts, schema/contract violations, detection accuracy vs ground truth, product vs test defects (`allure/categories.json`)
- **Trend history**: carried across runs from the `gh-pages` branch
- **Environment + executor**: Python / Playwright versions, target, branch, commit, link back to the Actions run
- **Failure evidence**: failed UI tests attach a full-page screenshot and the page URL, both in Allure and embedded in the pytest-html report; Playwright traces are uploaded as `playwright-traces`

**Pipeline** (`.github/workflows/validation_pipeline.yml`):

```
unit-tests ─┬─ api-tests (pytest + Newman) ──┐
            ├─ ui-tests (POM, traces) ────────┼─ test-report (Allure + HTML → GitHub Pages, job summary)
            └─ integration-tests ─────────────┘
                     └─ validation-pipeline (6-region matrix) + master-dataset  ──  artifacts (CSV + screenshots)
nightly / manual: full-run (mock or live target)
```

The run summary page shows total / passed / failed / pass rate and links to the published reports. Reports are published from `main`; pull requests get them as the `test-report` artifact.

**Generate locally:**

```bash
pytest --alluredir=allure-results --html=reports/html/report.html --self-contained-html
python scripts/prepare_allure_results.py allure-results
npx -p allure-commandline allure serve allure-results        # needs Java 8+
```

---

## 🧪 Test Target: Mock Merchant Service

Real merchant sites change daily and must not be load-tested from CI, so every suite runs against `mock_merchant/`, a FastAPI service that stands in for them:

- **12 storefronts** (store → cart → checkout) with an async payment widget whose latency is configurable (`MOCK_PAYMENT_LATENCY_MS`)
- **A multi-region restaurant chain** ("Crust & Co": US, UK, IN, AU, CA sites) with an async store locator, per-location payment sections, a closed store, a store that publishes no payment info, and a bot-protected region
- **Ground truth** (`mock_merchant/data/*.json`) so tests assert exact detection accuracy, not just "something was found"

`--mock` on either runner maps each record's real website onto the mock service. Unknown domains pass through untouched, which is how `SITE_UNREACHABLE` is exercised.

---

## 📊 Output: Master Dataset (filled)

`reports/master_validated_<timestamp>.csv`

| record_id | website_url | store_address | amex_accepted | visa_accepted | mastercard_accepted | upi_accepted | validation_status | reason | matched_address | match_score |
|-----------|-------------|---------------|:---:|:---:|:---:|:---:|-------------------|--------|-----------------|:---:|
| R0001 | us.crustandco.example | 1450 Market St, San Francisco, CA | Yes | Yes | Yes | No | VALIDATED | | 1450 Market Street, San Francisco, CA 94103 | 1.00 |
| R0002 | us.crustandco.example | 2 Embarcadero Ctr, San Francisco, CA | **No** | Yes | Yes | No | VALIDATED | | 2 Embarcadero Center, San Francisco, CA 94111 | 1.00 |
| R0004 | us.crustandco.example | 800 Congress Ave, Austin, TX | No | No | No | No | VALIDATED | NO_PAYMENT_INFO_DISPLAYED | 800 Congress Avenue, Austin, TX 78701 | 1.00 |
| R0006 | us.crustandco.example | 999 Harbor Blvd, Long Beach, CA | | | | | NOT_VALIDATED | STORE_NOT_FOUND | | |
| R0009 | in.crustandco.example | A-12 Connaught Place, New Delhi | No | Yes | Yes | Yes | VALIDATED | | A-12 Connaught Place, New Delhi, Delhi 110001 | 1.00 |
| R0013 | ca.crustandco.example | 220 Yonge St, Toronto, ON | | | | | NOT_VALIDATED | BLOCKED_BY_BOT_PROTECTION | | |

R0002 is **No** for Amex even though the US site's footer shows the Amex logo: the scan is scoped to the store's own content.

Also written: `evidence_pages` (which page each method was seen on), `evidence_screenshot`, `reason_detail`, `validated_at`.

**Site-level outputs** (`run_validation.py`): `validation_results_<ts>.csv` (client dataset), `review_queue_<ts>.csv` (0.50–0.79 confidence rows for manual QA), `screenshots/`.

---

## ⚡ Built for 5k–10k Records

| Concern | Approach |
|---------|----------|
| Throughput | `--workers N` threads, each with its own browser; fresh context per record |
| Politeness | Per-domain throttle (`--domain-interval`) so one brand's site is never hammered |
| Crash safety | Rows appended to the output as they finish; `--resume <file>` skips completed rows |
| Transient failures | Unreachable / unexpected errors retried (`--retries`) |
| One bad site | Every exception becomes a row with a reason code; the batch never stops |
| Browser crash | Worker detects the disconnected browser and relaunches it before the next record |

---

## 🗂️ Project Structure

```
payment-validation-framework/
│
├── data/
│   ├── master_dataset.csv       # Master dataset: merchants + store locations, Yes/No per method
│   ├── merchants.csv            # 12 merchants — US, GB, DE, JP, AU, SG, HK, CA, FR
│   ├── detection_rules.json     # Keywords, patterns, weights, readiness waits, page discovery
│   └── site_profiles.json       # Per-brand store-locator selectors
│
├── scrapers/
│   ├── payment_scraper.py       # Navigate → readiness waits → layered scan (full page or scoped)
│   └── record_validator.py      # One master record → locator flow or site-level scan → outcome + reason
│
├── validators/
│   └── confidence_scorer.py     # Weighted scoring → AUTO_ACCEPT / REVIEW / AUTO_REJECT
│
├── pages/                       # Page Object Model (see above)
│
├── utils/
│   ├── address_matcher.py       # Address normalisation + candidate matching
│   ├── master_dataset.py        # Master CSV load/validate, fill, checkpoint writer, reason codes
│   ├── csv_reader.py            # Load merchants.csv + detection_rules.json
│   └── report_writer.py         # Site-level CSV + review queue + console summary
│
├── mock_merchant/               # FastAPI test target: storefronts, chain sites, REST API, ground truth
│
├── tests/
│   ├── unit/                    # TC_001–TC_017
│   ├── ui/                      # TC_101–TC_127  (POM)
│   ├── api/                     # TC_201–TC_234  (+ schemas/)
│   └── integration/             # TC_301–TC_315
│
├── postman/                     # Collection + environment (Newman in CI)
├── allure/categories.json       # Allure defect categories
├── scripts/
│   └── prepare_allure_results.py  # Merge Postman results, categories, CI executor info
│
├── run_master_validation.py     # Master dataset runner (--workers, --resume, --region, --mock)
├── run_validation.py            # Site-level runner (--region, --merchant, --mock)
├── .github/workflows/
│   └── validation_pipeline.yml  # tests → Allure + HTML reports on Pages → validation runs → nightly
├── pytest.ini
└── requirements.txt
```

---

## 🌍 Region & Payment Method Coverage

| Region | Key Methods Validated |
|--------|-----------------------|
| US | Amex, Visa, MC, Discover, PayPal, Apple Pay, Google Pay, Shop Pay |
| UK | Visa, MC, Amex, PayPal, Klarna, Clearpay, Apple Pay, Google Pay |
| Germany / France | Visa, MC, PayPal |
| Japan | Visa, MC, JCB, Amex |
| Australia | Visa, MC, Amex, PayPal, Afterpay, Apple Pay |
| Singapore | Visa, MC, Amex, Apple Pay |
| Hong Kong | Visa, MC, Amex, UnionPay |
| Canada | Visa, MC, Amex, PayPal |
| India | Visa, MC, Amex, RuPay, UPI, Paytm |

---

## ⚙️ Tech Stack

| Tool | Role |
|------|------|
| **Playwright 1.55 (Python)** | Browser automation, network interception, API request context |
| **Python 3.11** | Pipelines, scoring, address matching |
| **pytest + pytest-playwright** | Unit, UI, API and integration suites; tracing & screenshots on failure |
| **jsonschema** | Response contract validation (draft 2020-12) |
| **Postman + Newman** | API collection run in CI (htmlextra + Allure reporters) |
| **Allure Report / pytest-html** | Merged test reporting with history, categories and failure evidence |
| **FastAPI + Uvicorn** | Mock merchant service (test target) |
| **GitHub Actions + Pages** | CI: unit → API / UI / integration → reports published to Pages → regional matrix + master dataset → nightly |

---

## 🚀 Run Locally

```bash
# Clone & install
git clone https://github.com/somasaic/payment-validation-framework.git
cd payment-validation-framework
pip install -r requirements.txt
playwright install chromium

# Master dataset (store locations + merchants) against the mock sites
python run_master_validation.py --mock
python run_master_validation.py --mock --region IN --workers 2
python run_master_validation.py --resume reports/master_validated_<ts>.csv

# Site-level pipeline
python run_validation.py --mock
python run_validation.py --mock --region SG
python run_validation.py --mock --merchant M001     # single merchant
python run_validation.py --region US                # live mode: real URLs from merchants.csv

# Tests
pytest                                              # everything
pytest -m "unit or api"                             # no browser needed for unit

# Postman collection
python -m mock_merchant --port 8000                 # in another terminal
npx newman run postman/payment-validation-api.postman_collection.json -e postman/local.postman_environment.json
```

---

## 📈 Sample Console Output

```
=================================================================
  GLOBAL PAYMENT METHOD VALIDATION — RUN SUMMARY
=================================================================
  Merchants scanned  : 12
  Total checks       : 180
  ✅ AUTO ACCEPT     : 50  (27.8%)
  🔍 NEEDS REVIEW    : 0
  ❌ AUTO REJECT     : 130
=================================================================

  AUTO-ACCEPTED (High confidence detected):
  [US] DemoStore US: amex, visa, mastercard, discover, paypal, apple_pay, google_pay, shop_pay
  [GB] TechRetail UK: visa, mastercard, paypal, klarna, afterpay
  [JP] NipponMart JP: amex, visa, mastercard, jcb
  [HK] HKBazaar HK: amex, visa, mastercard, unionpay
```

```
MASTER DATASET VALIDATION — RUN SUMMARY
  records: 18   VALIDATED: 13   NEEDS_REVIEW: 1   NOT_VALIDATED: 4
  reasons: NO_PAYMENT_INFO_DISPLAYED 1, STORE_TEMPORARILY_CLOSED 1, STORE_NOT_FOUND 1,
           BLOCKED_BY_BOT_PROTECTION 1, SITE_UNREACHABLE 1, LOCATOR_NOT_SUPPORTED 1
```

---

## 📝 Disclaimer

All merchant URLs, brands ("Crust & Co" and the `demo-*` stores), addresses and datasets in this repository are fictional placeholders created for portfolio demonstration. `.example` domains are reserved and never resolve.
No real merchant data, credentials, live payment flows, or proprietary client data is included.
This framework demonstrates the architecture and detection approach applied in real fintech payment data validation work.
Designed to never fill or submit payment forms: read-only evidence collection only.

---

## 🔗 Related Projects

- [playwright-web-automation-python](https://github.com/somasaic/playwright-web-automation-python) — Playwright + Python web automation
- [sdet-stlc-portfolio](https://github.com/somasaic/sdet-stlc-portfolio) — Full STLC: Manual → Playwright TypeScript → AI Agents

---

*Built by [Soma Sai Dinesh](https://www.linkedin.com/in/somasaidinesh/) — SDET | Playwright | Python | Fintech Payments QA*
