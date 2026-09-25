"""
Prepare merged allure-results before `allure generate`:
  - group Newman (Postman) results under the same "REST API" epic/suite as pytest API tests
  - add defect categories (allure/categories.json)
  - add executor info (GitHub Actions build link) when running in CI

Usage: python scripts/prepare_allure_results.py allure-results
"""

import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def label_postman_results(results: Path) -> int:
    count = 0
    for path in results.glob("*-result.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        labels = data.get("labels", [])
        if not any(l["name"] == "framework" and l["value"] == "newman" for l in labels):
            continue
        folder = next((l["value"] for l in labels if l["name"] == "suite"), "Collection")
        keep = [l for l in labels if l["name"] not in ("parentSuite", "suite", "epic", "feature", "host")]
        data["labels"] = keep + [
            {"name": "parentSuite", "value": "REST API"},
            {"name": "suite", "value": f"Postman: {folder}"},
            {"name": "epic", "value": "REST API"},
            {"name": "feature", "value": f"Postman: {folder}"},
            {"name": "tag", "value": "postman"},
        ]
        path.write_text(json.dumps(data), encoding="utf-8")
        count += 1
    return count


def write_executor(results: Path):
    if not os.getenv("GITHUB_ACTIONS"):
        return
    server = os.environ["GITHUB_SERVER_URL"]
    repo = os.environ["GITHUB_REPOSITORY"]
    run_id = os.environ["GITHUB_RUN_ID"]
    executor = {
        "name": "GitHub Actions",
        "type": "github",
        "url": f"{server}/{repo}/actions",
        "buildOrder": int(os.environ.get("GITHUB_RUN_NUMBER", "0")),
        "buildName": f"{os.environ.get('GITHUB_WORKFLOW', 'CI')} #{os.environ.get('GITHUB_RUN_NUMBER', '')}",
        "buildUrl": f"{server}/{repo}/actions/runs/{run_id}",
        "reportUrl": os.getenv("ALLURE_REPORT_URL", ""),
        "reportName": "Payment Validation Framework",
    }
    (results / "executor.json").write_text(json.dumps(executor, indent=2), encoding="utf-8")


def main():
    results = Path(sys.argv[1] if len(sys.argv) > 1 else "allure-results")
    results.mkdir(parents=True, exist_ok=True)
    labelled = label_postman_results(results)
    shutil.copy(ROOT / "allure" / "categories.json", results / "categories.json")
    write_executor(results)
    print(f"allure-results ready: {labelled} Postman results labelled")


if __name__ == "__main__":
    main()
