from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import re
import shutil
import socket
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from PIL import Image, ImageOps, UnidentifiedImageError
from site_assets import copy_static_site


DEFAULT_SHARED_HOST = "p23-sharedstreams.icloud.com"
REQUEST_TIMEOUT = 60
ASSET_BATCH_SIZE = 128
IMAGE_MAX_EDGE = 1600
POSTER_MAX_EDGE = 1280
THUMB_MAX_EDGE = 480
DEFAULT_MAX_DOWNLOAD_BYTES = 4 * 1024 * 1024 * 1024
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
SAFE_GUID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class SkipItemError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a static gallery from a public iCloud Shared Album URL."
    )
    parser.add_argument("--album-url", required=True, help="Public iCloud Shared Album URL")
    parser.add_argument(
        "--output-dir",
        default="dist",
        help="Directory where the static site will be written",
    )
    parser.add_argument(
        "--cache-dir",
        default="_build/cache",
        help="Directory used for downloaded source assets",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="Concurrent download workers",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=0,
        help="Limit items for a quick test run",
    )
    parser.add_argument(
        "--max-download-bytes",
        type=int,
        default=DEFAULT_MAX_DOWNLOAD_BYTES,
        help="Maximum size for a single downloaded source asset",
    )
    return parser.parse_args()


def parse_album_token(value: str) -> str:
    if "sharedalbum/#" in value:
        token = value.split("#", 1)[1]
    else:
        token = value

    token = token.split(";", 1)[0].strip()
    if not token:
        raise ValueError("Could not extract a shared album token from the supplied value.")
    return token


def redact_token(token: str) -> str:
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]
    return f"sha256:{digest}"


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "item"


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def ensure_within_root(path: Path, root: Path) -> Path:
    resolved_root = root.resolve()
    resolved_path = path.resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as error:
        raise ValueError(f"Refusing to write outside {resolved_root}: {resolved_path}") from error
    return resolved_path


def validate_public_host(hostname: str) -> None:
    if not hostname:
        raise ValueError("Asset URL is missing a host.")
    if hostname.lower() == "localhost":
        raise ValueError("Asset URL must not target localhost.")

    addresses = {info[4][0] for info in socket.getaddrinfo(hostname, None)}
    for address in addresses:
        parsed = ipaddress.ip_address(address)
        if (
            parsed.is_loopback
            or parsed.is_private
            or parsed.is_link_local
            or parsed.is_multicast
            or parsed.is_reserved
            or parsed.is_unspecified
        ):
            raise ValueError(f"Asset URL resolves to a non-public address: {hostname}")


def validate_bare_public_host(value: str) -> str:
    host = value.strip()
    parsed = urlsplit(f"https://{host}")
    if "://" in host or parsed.path or parsed.query or parsed.fragment or not parsed.hostname:
        raise ValueError("Expected a bare HTTPS host.")
    validate_public_host(parsed.hostname)
    return parsed.netloc


def build_safe_asset_url(meta: dict[str, Any]) -> str:
    location = str(meta.get("url_location", "")).strip()
    path = str(meta.get("url_path", "")).strip()
    if not location or not path:
        raise ValueError("Asset URL metadata is incomplete.")

    netloc = validate_bare_public_host(location)
    parsed_path = urlsplit(path)
    if parsed_path.scheme or parsed_path.netloc or not parsed_path.path.startswith("/"):
        raise ValueError("Asset URL path must be a relative absolute path.")

    return urlunsplit(("https", netloc, parsed_path.path, parsed_path.query, ""))


def post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            return json.load(response)
    except HTTPError as error:
        if error.fp is None:
            raise
        return json.load(error.fp)


def download_file(url: str, destination: Path, max_download_bytes: int) -> None:
    if destination.exists():
        return

    ensure_parent(destination)
    temp_path = destination.with_suffix(destination.suffix + ".part")

    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT) as response, temp_path.open("wb") as handle:
            content_length = response.headers.get("Content-Length")
            try:
                declared_size = int(content_length) if content_length else 0
            except ValueError as error:
                raise SkipItemError("Asset response has an invalid Content-Length") from error
            if declared_size > max_download_bytes:
                raise SkipItemError(f"Asset is larger than {max_download_bytes} bytes")

            bytes_written = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                bytes_written += len(chunk)
                if bytes_written > max_download_bytes:
                    raise SkipItemError(f"Asset exceeded {max_download_bytes} bytes while downloading")
                handle.write(chunk)

        temp_path.replace(destination)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def safe_photo_guid(item: dict[str, Any]) -> str:
    guid = str(item.get("photoGuid", "")).strip()
    if not SAFE_GUID_RE.fullmatch(guid):
        raise SkipItemError("Invalid photoGuid in album metadata")
    return guid


def safe_date_part(item: dict[str, Any]) -> str:
    value = str(item.get("dateCreated", "")).strip()
    timestamp = parse_timestamp(value)
    return timestamp.date().isoformat()


def choose_image_derivative(item: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    best_key = ""
    best_meta: dict[str, Any] = {}
    best_size = -1

    for key, meta in item["derivatives"].items():
        file_size = int(meta.get("fileSize", 0))
        if file_size > best_size:
            best_key = key
            best_meta = meta
            best_size = file_size

    if not best_key:
        raise SkipItemError(f"No usable image derivative found for {item['photoGuid']}")
    return best_key, best_meta


def choose_video_derivatives(item: dict[str, Any]) -> tuple[tuple[str, dict[str, Any]], tuple[str, dict[str, Any]]]:
    poster_meta = item["derivatives"].get("PosterFrame")
    if not poster_meta:
        raise SkipItemError(f"No poster frame found for {item['photoGuid']}")

    best_video_key = ""
    best_video_meta: dict[str, Any] = {}
    best_size = -1

    for key, meta in item["derivatives"].items():
        if key == "PosterFrame":
            continue
        if meta.get("state") not in (None, "available"):
            continue
        file_size = int(meta.get("fileSize", 0))
        if file_size > best_size:
            best_video_key = key
            best_video_meta = meta
            best_size = file_size

    if not best_video_key:
        raise SkipItemError(f"No usable video derivative found for {item['photoGuid']}")

    return (best_video_key, best_video_meta), ("PosterFrame", poster_meta)


def build_asset_url_map(host: str, token: str, items: list[dict[str, Any]]) -> dict[str, str]:
    url_map: dict[str, str] = {}
    safe_host = validate_bare_public_host(host)
    photo_guids = [item["photoGuid"] for item in items]

    for start in range(0, len(photo_guids), ASSET_BATCH_SIZE):
        batch = photo_guids[start : start + ASSET_BATCH_SIZE]
        payload = {"photoGuids": batch}
        data = post_json(f"https://{safe_host}/{token}/sharedstreams/webasseturls", payload)
        for checksum, meta in data.get("items", {}).items():
            url_map[checksum] = build_safe_asset_url(meta)

    return url_map


def resize_to_limit(image: Image.Image, max_edge: int) -> Image.Image:
    resized = image.copy()
    resized.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
    return resized


def normalize_image(source_path: Path) -> Image.Image:
    with Image.open(source_path) as source:
        image = ImageOps.exif_transpose(source)
        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGB")
        else:
            image = image.copy()
    return image


def save_webp_variant(source_path: Path, destination: Path, max_edge: int, quality: int) -> tuple[int, int]:
    if destination.exists():
        try:
            with Image.open(destination) as image:
                image.load()
                return image.size
        except (UnidentifiedImageError, OSError):
            destination.unlink(missing_ok=True)

    image = normalize_image(source_path)
    resized = resize_to_limit(image, max_edge)
    ensure_parent(destination)
    temp_path = destination.with_suffix(destination.suffix + ".part")
    resized.save(temp_path, format="WEBP", quality=quality, method=6)
    temp_path.replace(destination)
    return resized.size


def build_file_stem(item: dict[str, Any]) -> str:
    date_part = safe_date_part(item)
    guid_part = safe_photo_guid(item).lower()
    return f"{date_part}-{guid_part}"


def legacy_file_glob(item: dict[str, Any], suffix: str) -> str:
    date_part = safe_date_part(item)
    guid_part = safe_photo_guid(item)[:8].lower()
    return f"*-{date_part}-{guid_part}{suffix}"


def migrate_legacy_file(item: dict[str, Any], destination: Path) -> None:
    if destination.exists():
        return

    ensure_parent(destination)
    matches = sorted(destination.parent.glob(legacy_file_glob(item, destination.suffix)))
    if not matches:
        return

    matches[0].replace(destination)


def contributor_name(item: dict[str, Any]) -> str:
    return item.get("contributorFullName") or " ".join(
        part for part in [item.get("contributorFirstName"), item.get("contributorLastName")] if part
    ).strip() or "Unknown"


def ext_from_url(url: str, fallback: str) -> str:
    suffix = Path(urlsplit(url).path).suffix.lower()
    return suffix or fallback


def process_item(
    item: dict[str, Any],
    asset_urls: dict[str, str],
    output_dir: Path,
    cache_dir: Path,
    max_download_bytes: int,
) -> dict[str, Any]:
    stem = build_file_stem(item)
    media_type = "video" if item.get("mediaAssetType") == "video" else "photo"
    caption = (item.get("caption") or "").strip()
    output_root = output_dir.resolve()
    cache_root = cache_dir.resolve()

    if media_type == "photo":
        image_key, image_meta = choose_image_derivative(item)
        image_url = asset_urls.get(image_meta["checksum"])
        if not image_url:
            raise SkipItemError(f"Missing asset URL for {item['photoGuid']}")
        source_ext = ext_from_url(image_url, ".jpg")
        source_path = ensure_within_root(cache_dir / "images" / f"{stem}{source_ext}", cache_root)
        migrate_legacy_file(item, source_path)
        download_file(image_url, source_path, max_download_bytes)

        image_rel = Path("assets/images") / f"{stem}.webp"
        thumb_rel = Path("assets/thumbs") / f"{stem}.webp"
        image_path = ensure_within_root(output_dir / image_rel, output_root)
        thumb_path = ensure_within_root(output_dir / thumb_rel, output_root)
        migrate_legacy_file(item, image_path)
        migrate_legacy_file(item, thumb_path)
        image_size = save_webp_variant(source_path, image_path, IMAGE_MAX_EDGE, 82)
        thumb_size = save_webp_variant(source_path, thumb_path, THUMB_MAX_EDGE, 72)

        return {
            "id": item["photoGuid"],
            "type": media_type,
            "caption": caption,
            "contributor": contributor_name(item),
            "dateCreated": item["dateCreated"],
            "width": image_size[0],
            "height": image_size[1],
            "thumbWidth": thumb_size[0],
            "thumbHeight": thumb_size[1],
            "src": image_rel.as_posix(),
            "thumb": thumb_rel.as_posix(),
        }

    (video_key, video_meta), (poster_key, poster_meta) = choose_video_derivatives(item)
    video_url = asset_urls.get(video_meta["checksum"])
    poster_url = asset_urls.get(poster_meta["checksum"])
    if not video_url or not poster_url:
        raise SkipItemError(f"Missing video asset URL for {item['photoGuid']}")

    video_ext = ext_from_url(video_url, ".mp4")
    poster_ext = ext_from_url(poster_url, ".jpg")

    video_rel = Path("assets/videos") / f"{stem}{video_ext}"
    poster_rel = Path("assets/posters") / f"{stem}.webp"
    thumb_rel = Path("assets/thumbs") / f"{stem}.webp"

    video_path = ensure_within_root(output_dir / video_rel, output_root)
    poster_source = ensure_within_root(cache_dir / "posters" / f"{stem}{poster_ext}", cache_root)
    poster_path = ensure_within_root(output_dir / poster_rel, output_root)
    thumb_path = ensure_within_root(output_dir / thumb_rel, output_root)
    migrate_legacy_file(item, video_path)
    migrate_legacy_file(item, poster_source)
    migrate_legacy_file(item, poster_path)
    migrate_legacy_file(item, thumb_path)
    download_file(video_url, video_path, max_download_bytes)
    download_file(poster_url, poster_source, max_download_bytes)

    poster_size = save_webp_variant(poster_source, poster_path, POSTER_MAX_EDGE, 80)
    thumb_size = save_webp_variant(poster_source, thumb_path, THUMB_MAX_EDGE, 72)

    width = int(video_meta.get("width", poster_size[0]))
    height = int(video_meta.get("height", poster_size[1]))

    return {
        "id": item["photoGuid"],
        "type": media_type,
        "caption": caption,
        "contributor": contributor_name(item),
        "dateCreated": item["dateCreated"],
        "width": width,
        "height": height,
        "thumbWidth": thumb_size[0],
        "thumbHeight": thumb_size[1],
        "src": video_rel.as_posix(),
        "thumb": thumb_rel.as_posix(),
        "poster": poster_rel.as_posix(),
    }


def build_manifest(
    album_data: dict[str, Any],
    rendered_items: list[dict[str, Any]],
    skipped_items: list[dict[str, str]],
) -> dict[str, Any]:
    contributors: dict[str, int] = {}
    photo_count = 0
    video_count = 0

    for rendered in rendered_items:
        contributors[rendered["contributor"]] = contributors.get(rendered["contributor"], 0) + 1
        if rendered["type"] == "video":
            video_count += 1
        else:
            photo_count += 1

    date_values = [item.get("dateCreated") for item in rendered_items if item.get("dateCreated")]

    return {
        "albumTitle": album_data.get("streamName", "Shared Album"),
        "generatedAt": datetime.now().astimezone().isoformat(),
        "counts": {
            "all": len(rendered_items),
            "photos": photo_count,
            "videos": video_count,
            "contributors": len(contributors),
            "skipped": len(skipped_items),
        },
        "dateRange": {
            "oldest": min(date_values) if date_values else None,
            "newest": max(date_values) if date_values else None,
        },
        "contributors": [
            {"name": name, "count": count}
            for name, count in sorted(contributors.items(), key=lambda entry: entry[0].lower())
        ],
        "skippedItems": skipped_items,
        "items": rendered_items,
    }


def fetch_album(token: str) -> tuple[str, dict[str, Any]]:
    payload = {"streamCtag": None}
    initial = post_json(f"https://{DEFAULT_SHARED_HOST}/{token}/sharedstreams/webstream", payload)
    host = initial.get("X-Apple-MMe-Host")
    if host:
        safe_host = validate_bare_public_host(str(host))
        return safe_host, post_json(f"https://{safe_host}/{token}/sharedstreams/webstream", payload)
    return DEFAULT_SHARED_HOST, initial


def main() -> int:
    args = parse_args()
    token = parse_album_token(args.album_url)
    output_dir = Path(args.output_dir)
    cache_dir = Path(args.cache_dir)

    print(f"Fetching album metadata for album {redact_token(token)}...")
    host, album_data = fetch_album(token)
    items = album_data.get("photos", [])
    if not items:
        print("No items returned from the shared album.")
        return 1

    items = sorted(items, key=lambda entry: parse_timestamp(entry["dateCreated"]), reverse=True)
    if args.max_items:
        items = items[: args.max_items]

    print(f"Album: {album_data.get('streamName', 'Shared Album')}")
    print(f"Host: {host}")
    print(f"Items selected: {len(items)}")

    copy_static_site(output_dir)

    print("Resolving asset URLs...")
    asset_urls = build_asset_url_map(host, token, items)

    rendered_items: list[dict[str, Any]] = [None] * len(items)  # type: ignore[list-item]
    skipped_items: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {
            executor.submit(
                process_item,
                item,
                asset_urls,
                output_dir,
                cache_dir,
                args.max_download_bytes,
            ): index
            for index, item in enumerate(items, start=1)
        }

        completed = 0
        for future in as_completed(futures):
            index = futures[future]
            item = items[index - 1]
            try:
                rendered_items[index - 1] = future.result()
            except SkipItemError as error:
                skipped_items.append(
                    {
                        "id": item["photoGuid"],
                        "dateCreated": item.get("dateCreated", ""),
                        "reason": str(error),
                    }
                )
                print(f"Skipped {item['photoGuid']}: {error}")
            completed += 1
            if completed % 25 == 0 or completed == len(items):
                print(f"Processed {completed}/{len(items)} items...")

    final_items = [item for item in rendered_items if item is not None]
    manifest = build_manifest(album_data, final_items, skipped_items)
    manifest_path = output_dir / "album.json"
    ensure_parent(manifest_path)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(f"Site written to {output_dir.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
