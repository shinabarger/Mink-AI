"""Start Mink: python -m mink"""

import logging
import socket
import threading
import time
import webbrowser

import uvicorn

HOST = "127.0.0.1"  # Local only. Nothing is reachable from other machines.
PORT = 8765
URL = f"http://{HOST}:{PORT}"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    threading.Thread(target=_open_browser_when_ready, daemon=True).start()
    uvicorn.run("mink.server:app", host=HOST, port=PORT, log_level="warning")


def _open_browser_when_ready(timeout_seconds: float = 60) -> None:
    """Only announce and open the browser once the server is actually accepting connections."""
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            with socket.create_connection((HOST, PORT), timeout=1):
                print(
                    f"\n  Mink is running at {URL}\n  Leave this window open while you use it. Press Ctrl+C to quit.\n"
                )
                webbrowser.open(URL)
                return
        except OSError:
            time.sleep(0.5)


if __name__ == "__main__":
    main()
