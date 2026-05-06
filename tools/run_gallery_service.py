from __future__ import annotations

import hashlib
import os
import signal
import subprocess
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parent.parent
BUILD_SCRIPT = APP_ROOT / "tools" / "build_icloud_gallery.py"
DEFAULT_OUTPUT_DIR = Path("/data/dist")
DEFAULT_CACHE_DIR = Path("/data/cache")
DEFAULT_REFRESH_INTERVAL_SECONDS = 43200
DEFAULT_PORT = 8585


def env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return int(value)


def env_path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    if not value:
        return default
    return Path(value)


def redact_album_url(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"sha256:{digest}"


def build_command() -> list[str]:
    album_url = os.environ.get("ALBUM_URL", "").strip()
    if not album_url:
        raise RuntimeError("ALBUM_URL is required.")

    output_dir = env_path("OUTPUT_DIR", DEFAULT_OUTPUT_DIR)
    cache_dir = env_path("CACHE_DIR", DEFAULT_CACHE_DIR)
    workers = env_int("WORKERS", 8)
    max_items = os.environ.get("MAX_ITEMS", "").strip()

    command = [
        sys.executable,
        str(BUILD_SCRIPT),
        "--album-url",
        album_url,
        "--output-dir",
        str(output_dir),
        "--cache-dir",
        str(cache_dir),
        "--workers",
        str(workers),
    ]

    if max_items:
        command.extend(["--max-items", max_items])

    return command


def run_build() -> bool:
    command = build_command()
    album_label = redact_album_url(os.environ.get("ALBUM_URL", "").strip())
    output_dir = env_path("OUTPUT_DIR", DEFAULT_OUTPUT_DIR)
    cache_dir = env_path("CACHE_DIR", DEFAULT_CACHE_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    print(
        f"[gallery] running build for album {album_label} into {output_dir}",
        flush=True,
    )
    result = subprocess.run(command, cwd=APP_ROOT, check=False)
    print(f"[gallery] build exit code: {result.returncode}", flush=True)
    return result.returncode == 0


def refresh_loop(stop_event: threading.Event) -> None:
    interval = env_int("REFRESH_INTERVAL_SECONDS", DEFAULT_REFRESH_INTERVAL_SECONDS)
    if interval <= 0:
        print("[gallery] periodic refresh disabled", flush=True)
        return

    print(f"[gallery] refresh interval: {interval} seconds", flush=True)
    while not stop_event.wait(interval):
        print("[gallery] starting scheduled refresh", flush=True)
        run_build()


def main() -> int:
    output_dir = env_path("OUTPUT_DIR", DEFAULT_OUTPUT_DIR)
    port = env_int("PORT", DEFAULT_PORT)

    stop_event = threading.Event()

    def handle_signal(signum: int, _frame: object) -> None:
        print(f"[gallery] received signal {signum}, shutting down", flush=True)
        stop_event.set()
        server.shutdown()

    initial_ok = run_build()
    if not initial_ok:
        print("[gallery] initial build failed; serving any existing output", flush=True)

    handler_class = partial(SimpleHTTPRequestHandler, directory=str(output_dir))
    server = ThreadingHTTPServer(("0.0.0.0", port), handler_class)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    refresh_thread = threading.Thread(target=refresh_loop, args=(stop_event,), daemon=True)
    refresh_thread.start()

    print(f"[gallery] serving {output_dir} on port {port}", flush=True)
    try:
        server.serve_forever()
    finally:
        stop_event.set()
        server.server_close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
