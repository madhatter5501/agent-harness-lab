"""Launch the same three services without Docker. Ctrl-C shuts down all children."""

import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
children = []
env = {
    **os.environ,
    "DATA_DIR": str(ROOT / ".data"),
    "ORDERS_URL": "http://127.0.0.1:8766",
    "DOCUMENTS_URL": "http://127.0.0.1:8767",
}
env.setdefault("MODEL_BASE_URL", "http://127.0.0.1:1234/v1")


def start(module, port):
    children.append(
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                module + ":app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            env=env,
        )
    )
    for _ in range(50):
        if children[-1].poll() is not None:
            raise RuntimeError(
                module + " exited; check whether Compose is already using its port"
            )
        try:
            urllib.request.urlopen(
                "http://127.0.0.1:" + str(port) + "/health", timeout=1
            ).close()
            return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError(module + " did not become healthy")


if __name__ == "__main__":
    try:
        start("lab.orders", 8766)
        start("lab.documents", 8767)
        start("lab.harness", 8765)
        print("\nOpen http://localhost:8765 — Ctrl-C to stop.\n", flush=True)
        while all(p.poll() is None for p in children):
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for p in reversed(children):
            p.send_signal(signal.SIGTERM) if p.poll() is None else None
        for p in children:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
