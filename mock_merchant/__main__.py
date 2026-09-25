"""python -m mock_merchant [--port 8000]"""

import argparse

import uvicorn

parser = argparse.ArgumentParser(description="Run the mock merchant service")
parser.add_argument("--host", default="127.0.0.1")
parser.add_argument("--port", type=int, default=8000)
args = parser.parse_args()

uvicorn.run("mock_merchant.app:app", host=args.host, port=args.port, log_level="info")
