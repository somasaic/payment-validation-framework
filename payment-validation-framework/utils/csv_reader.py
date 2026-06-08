"""CSV reader for merchants.csv"""
import csv, os

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

def load_merchants(csv_file: str = "merchants.csv") -> list[dict]:
    with open(os.path.join(DATA_DIR, csv_file), newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))

def load_detection_rules(json_file: str = "detection_rules.json") -> dict:
    import json
    with open(os.path.join(DATA_DIR, json_file), encoding="utf-8") as f:
        return json.load(f)
