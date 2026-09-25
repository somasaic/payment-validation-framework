"""
Configuration
=============
Settings come from environment variables. For local runs, put them in a
`.env` file at the repo root (copy `.env.example`); load_env() reads it.
Variables already set in the environment (e.g. in CI) win over .env.

  MOCK_API_KEY             API key the mock service expects on POST requests
  MOCK_PAYMENT_LATENCY_MS  delay before the checkout payment widget renders
  MOCK_SEARCH_LATENCY_MS   delay before store-locator search results return
  TARGET_BASE_URL          run the test suites against an already-running
                           deployment instead of the local mock service
"""

from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]



def load_env() -> None:
    """Load <repo>/.env into os.environ without overriding existing variables."""
    load_dotenv(ROOT / ".env", override=False)
