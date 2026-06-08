# 🌍 Global Payment Method Validation Framework

> **Automated detection of accepted payment methods (Amex, Visa, Mastercard, Discover, PayPal, wallets) across multi-region merchant websites — built to replace a multi-day manual QA process.**

![Python](https://img.shields.io/badge/Python-3.11-blue?logo=python)
![Playwright](https://img.shields.io/badge/Playwright-1.44-green?logo=playwright)
![pytest](https://img.shields.io/badge/pytest-8.2-orange)
![CI](https://github.com/somasaic/payment-validation-framework/actions/workflows/validation_pipeline.yml/badge.svg)
![Merchants](https://img.shields.io/badge/Merchants-12-purple)
![Regions](https://img.shields.io/badge/Regions-US_GB_DE_JP_AU_SG_HK_CA-red)

---

## 🎯 Background & Problem

As part of a payment data validation project for a global card network client, the team manually validated **which payment methods each merchant website accepts** — covering Amex, Visa, Mastercard, Discover, PayPal, Apple Pay, Google Pay, and region-specific wallets.

**Manual process (before automation):**
1. Open each merchant URL in browser
2. Navigate through cart → checkout → payment step
3. Visually identify payment icons and labels
4. Screenshot evidence and record to spreadsheet
5. QA-verify → clean data → deliver to client

**The problem:**
- 10+ merchants × 6–8 methods each = 80+ manual checks per batch
- Repeated across multiple release cycles
- High error rate from human fatigue
- No audit trail linking evidence to data

**Total cycle time: 3+ days per batch.**

---

## ✅ What This Framework Does

Automates the full detection pipeline — **read-only, non-destructive**:

| Step | What happens |
|------|-------------|
| **1. Load** | Reads merchant list from CSV (region, URL, platform) |
| **2. Scan — Landing** | Playwright navigates to merchant site, extracts DOM text, image src/alt, CSS classes, network URLs |
| **3. Scan — Checkout** | Auto-discovers checkout URL, repeats evidence extraction |
| **4. Score** | Confidence engine weights each signal source → score 0.0–1.0 per method |
| **5. Classify** | AUTO_ACCEPT (≥0.80) / REVIEW (0.50–0.79) / AUTO_REJECT (<0.50) |
| **6. Report** | Writes structured CSV dataset + review queue CSV + screenshots as evidence |

**Result: Multi-day manual process → under 2 hours automated.**

---

## 🔍 Detection Engine (4-Layer Approach)

```
Layer 1: Network interception   — captures resource URLs containing method identifiers
Layer 2: Image src/alt scan     — payment icon filenames and alt text
Layer 3: DOM text match         — keyword detection near payment context
Layer 4: CSS class scan         — payment-related class names on elements

Confidence score = W_network×signal + W_img×signal + W_text×signal + W_alt×signal
```

**Why layered?** Each source has false-positive risk alone. Combined scoring + confidence thresholds prevents incorrect data reaching the client.

---

## 📊 Output: Structured Dataset

**`reports/validation_results_<timestamp>.csv`** — Client-deliverable dataset:

| merchant_id | merchant_name | region | payment_method | detected | confidence_score | status | signals_fired | screenshot_landing |
|-------------|--------------|--------|----------------|----------|-----------------|--------|--------------|-------------------|
| M001 | DemoStore US | US | amex | True | 0.900 | AUTO_ACCEPT | network\|img | reports/screenshots/M001_landing.png |
| M001 | DemoStore US | US | jcb | False | 0.000 | AUTO_REJECT | | |
| M002 | TechRetail UK | GB | amex | False | 0.200 | AUTO_REJECT | text | |

**`reports/review_queue_<timestamp>.csv`** — Manual QA triage for uncertain rows (0.50–0.79 confidence):

| merchant_id | payment_method | confidence_score | signals_fired | reviewer_decision | notes |
|-------------|----------------|-----------------|--------------|-------------------|-------|
| M002 | paypal | 0.650 | img\|text | *(QA fills)* | |

**`reports/screenshots/`** — Evidence screenshots (landing + checkout per merchant)

---

## 🗂️ Project Structure

```
payment-validation-framework/
│
├── data/
│   ├── merchants.csv            # 12 merchants — US, GB, DE, JP, AU, SG, HK, CA, FR
│   └── detection_rules.json     # Keywords, img patterns, weights per payment method
│
├── scrapers/
│   └── payment_scraper.py       # Core: navigate → DOM extract → network intercept → screenshot
│
├── validators/
│   └── confidence_scorer.py     # Weighted scoring → AUTO_ACCEPT / REVIEW / AUTO_REJECT
│
├── utils/
│   ├── csv_reader.py            # Load merchants.csv + detection_rules.json
│   └── report_writer.py         # Write full CSV + review queue + console summary
│
├── tests/
│   └── test_validation_pipeline.py  # TC_001–TC_008: scorer + CSV + report unit tests
│
├── reports/                     # Auto-generated outputs
│   ├── validation_results_*.csv
│   ├── review_queue_*.csv
│   └── screenshots/
│
├── run_validation.py            # Main pipeline runner (CLI: --region, --merchant)
├── .github/workflows/
│   └── validation_pipeline.yml  # CI: unit tests → parallel per-region → nightly full run
├── pytest.ini
└── requirements.txt
```

---

## 🧪 Test Coverage

| TC | Description | Type |
|----|-------------|------|
| TC_001 | All signals → AUTO_ACCEPT (score ≥ 0.80) | Unit |
| TC_002 | No signals → AUTO_REJECT (score = 0.0) | Unit |
| TC_003 | Partial signals → REVIEW or AUTO_REJECT | Unit |
| TC_004 | Network signal weight correctly applied | Unit |
| TC_005 | merchants.csv loads correct columns + count | Unit |
| TC_006 | detection_rules.json has all major methods | Unit |
| TC_007 | Full report CSV has correct columns and row count | Unit |
| TC_008 | Review queue contains only REVIEW-status rows | Unit |

---

## 🌍 Region & Payment Method Coverage

| Region | Key Methods Validated |
|--------|-----------------------|
| US | Amex, Visa, MC, Discover, PayPal, Apple Pay, Google Pay, Shop Pay |
| UK | Visa, MC, PayPal, Klarna, Clearpay |
| Germany | Visa, MC, PayPal |
| Japan | Visa, MC, JCB, Amex |
| Australia | Visa, MC, PayPal, AfterPay, Apple Pay |
| Singapore | Visa, MC, Amex, PayNow, GrabPay |
| Hong Kong | Visa, MC, Amex, UnionPay |
| Canada | Visa, MC, Amex, PayPal |

---

## ⚙️ Tech Stack

| Tool | Role |
|------|------|
| **Playwright 1.44** | Browser automation — handles JS-heavy merchant sites |
| **Python 3.11** | Pipeline orchestration + scoring logic |
| **pytest** | Unit + integration test runner |
| **CSV / JSON** | Merchant config input + structured output |
| **GitHub Actions** | CI — unit tests → per-region parallel → nightly full run |
| **Network Interception** | Playwright request listener for passive URL capture |

---

## 🚀 Run Locally

```bash
# Clone & install
git clone https://github.com/somasaic/payment-validation-framework.git
cd payment-validation-framework
pip install -r requirements.txt
playwright install chromium

# Run all merchants
python run_validation.py

# Run specific region
python run_validation.py --region US
python run_validation.py --region SG

# Run single merchant
python run_validation.py --merchant M001

# Run tests
pytest tests/ -v
```

---

## 📈 Sample Console Output

```
=================================================================
  GLOBAL PAYMENT METHOD VALIDATION — RUN SUMMARY
=================================================================
  Merchants scanned  : 12
  Total checks       : 144
  ✅ AUTO ACCEPT     : 89  (61.8%)
  🔍 NEEDS REVIEW    : 31
  ❌ AUTO REJECT     : 24
=================================================================

  AUTO-ACCEPTED (High confidence detected):
  [US] DemoStore US: amex, visa, mastercard, paypal, apple_pay
  [JP] NipponMart JP: visa, mastercard, jcb
  [AU] OzBuy AU: visa, mastercard, paypal, afterpay
  [SG] SingaMall SG: amex, visa, mastercard
```

---

## 📝 Disclaimer

All merchant URLs in `merchants.csv` are demo/placeholder domains created for portfolio demonstration purposes.  
No real merchant credentials, live payment flows, or proprietary client data is included.  
This framework demonstrates the architecture and detection approach applied in real fintech payment data validation work.  
Designed to never fill or submit payment forms — read-only evidence collection only.

---

## 🔗 Related Projects

- [playwright-web-automation-python](https://github.com/somasaic/playwright-web-automation-python) — Playwright + Python web automation
- [sdet-stlc-portfolio](https://github.com/somasaic/sdet-stlc-portfolio) — Full STLC: Manual → Playwright TypeScript → AI Agents

---

*Built by [Soma Sai Dinesh](https://www.linkedin.com/in/somasaidinesh/) — SDET | Playwright | Python | Fintech Payments QA*
