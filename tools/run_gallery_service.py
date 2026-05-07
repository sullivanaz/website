from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
from datetime import datetime
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from site_assets import copy_static_site


APP_ROOT = Path(__file__).resolve().parent.parent
BUILD_SCRIPT = APP_ROOT / "tools" / "build_icloud_gallery.py"
DEFAULT_OUTPUT_DIR = Path("/data/dist")
DEFAULT_CACHE_DIR = Path("/data/cache")
DEFAULT_REFRESH_INTERVAL_SECONDS = 43200
DEFAULT_PORT = 8585
DEFAULT_MAX_DOWNLOAD_BYTES = 4 * 1024 * 1024 * 1024
BUILD_LOCK = threading.Lock()


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


class GalleryRequestHandler(SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        request_path = urlsplit(self.path).path or "/"
        normalized_path = "/index.html" if request_path == "/" else request_path

        if normalized_path.endswith(".html") or normalized_path == "/album.json":
            self.send_header("Cache-Control", "no-store")
        elif normalized_path in ("/app.js", "/styles.css"):
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")

        super().end_headers()


def build_command() -> list[str]:
    album_url = os.environ.get("ALBUM_URL", "").strip()
    if not album_url:
        raise RuntimeError("ALBUM_URL is required.")

    output_dir = env_path("OUTPUT_DIR", DEFAULT_OUTPUT_DIR)
    cache_dir = env_path("CACHE_DIR", DEFAULT_CACHE_DIR)
    workers = env_int("WORKERS", 8)
    max_download_bytes = env_int("MAX_DOWNLOAD_BYTES", DEFAULT_MAX_DOWNLOAD_BYTES)
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
        "--max-download-bytes",
        str(max_download_bytes),
    ]

    if max_items:
        command.extend(["--max-items", max_items])

    return command


def sync_static_site(output_dir: Path) -> str:
    version = copy_static_site(output_dir)
    print(f"[gallery] synced static shell version {version} into {output_dir}", flush=True)
    return version


def write_status_manifest(output_dir: Path, status: str, message: str) -> None:
    manifest_path = output_dir / "album.json"
    payload = {
        "status": status,
        "message": message,
        "albumTitle": "Shared Album",
        "generatedAt": datetime.now().astimezone().isoformat(),
        "counts": {
            "all": 0,
            "photos": 0,
            "videos": 0,
            "contributors": 0,
            "skipped": 0,
        },
        "dateRange": {
            "oldest": None,
            "newest": None,
        },
        "contributors": [],
        "skippedItems": [],
        "items": [],
    }
    temp_path = manifest_path.with_suffix(".json.part")
    temp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temp_path.replace(manifest_path)


def read_manifest_status(output_dir: Path) -> str:
    manifest_path = output_dir / "album.json"
    if not manifest_path.exists():
        return ""
    try:
        return str(json.loads(manifest_path.read_text(encoding="utf-8")).get("status", ""))
    except (OSError, json.JSONDecodeError):
        return ""


def prepare_site_for_serving(output_dir: Path, cache_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    sync_static_site(output_dir)
    if not (output_dir / "album.json").exists():
        write_status_manifest(
            output_dir,
            "building",
            "The gallery is downloading media and will appear automatically when it is ready.",
        )


def run_build() -> bool:
    output_dir = env_path("OUTPUT_DIR", DEFAULT_OUTPUT_DIR)
    cache_dir = env_path("CACHE_DIR", DEFAULT_CACHE_DIR)
    with BUILD_LOCK:
        command = build_command()
        album_label = redact_album_url(os.environ.get("ALBUM_URL", "").strip())
        prepare_site_for_serving(output_dir, cache_dir)

        print(
            f"[gallery] running build for album {album_label} into {output_dir}",
            flush=True,
        )
        result = subprocess.run(command, cwd=APP_ROOT, check=False)
        print(f"[gallery] build exit code: {result.returncode}", flush=True)
        if result.returncode != 0 and read_manifest_status(output_dir) == "building":
            write_status_manifest(
                output_dir,
                "error",
                "The gallery build failed. Check the container logs for details.",
            )
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
    cache_dir = env_path("CACHE_DIR", DEFAULT_CACHE_DIR)
    port = env_int("PORT", DEFAULT_PORT)

    stop_event = threading.Event()

    def handle_signal(signum: int, _frame: object) -> None:
        print(f"[gallery] received signal {signum}, shutting down", flush=True)
        stop_event.set()
        server.shutdown()

    prepare_site_for_serving(output_dir, cache_dir)

    handler_class = partial(GalleryRequestHandler, directory=str(output_dir))
    server = ThreadingHTTPServer(("0.0.0.0", port), handler_class)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    initial_build_thread = threading.Thread(target=run_build, daemon=True)
    initial_build_thread.start()

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
