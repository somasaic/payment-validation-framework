"""
Run the mock merchant service in a background thread (for pytest fixtures and
`run_validation.py --mock`), bound to a free local port.
"""

import socket
import threading
import time

import uvicorn

from mock_merchant import catalog, chain
from mock_merchant.app import create_app


def mock_routes() -> dict[str, str]:
    """Real website origin → path on the mock server that stands in for it."""
    routes = {m["base_url"].rstrip("/"): f"/store/{mid}" for mid, m in catalog.merchants().items()}
    routes.update(chain.data()["domain_routes"])
    return routes


def rewrite_url(url: str, server_url: str) -> str:
    """Map a real website URL onto the mock server; unknown origins pass through."""
    origin = url.rstrip("/")
    path = mock_routes().get(origin)
    return f"{server_url}{path}" if path else url


class MockMerchantServer:

    def __init__(self, host: str = "127.0.0.1", port: int = 0, **app_kwargs):
        self.host = host
        self.port = port
        self._app = create_app(**app_kwargs)
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self, timeout: float = 10.0) -> str:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.host, self.port))
        self.port = sock.getsockname()[1]

        config = uvicorn.Config(self._app, log_level="warning", access_log=False)
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(
            target=self._server.run, kwargs={"sockets": [sock]}, daemon=True
        )
        self._thread.start()

        deadline = time.monotonic() + timeout
        while not self._server.started:
            if time.monotonic() > deadline or not self._thread.is_alive():
                raise RuntimeError("Mock merchant server failed to start")
            time.sleep(0.05)
        return self.url

    def stop(self):
        if self._server:
            self._server.should_exit = True
        if self._thread:
            self._thread.join(timeout=10)

    def __enter__(self) -> "MockMerchantServer":
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()
