from __future__ import annotations

import hashlib
import shutil
from pathlib import Path


STATIC_FILE_NAMES = ("index.html", "styles.css", "app.js")
VERSION_PLACEHOLDER = "__BUILD_VERSION__"


def get_source_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "site-src"


def compute_site_version(source_dir: Path | None = None) -> str:
    source_root = source_dir or get_source_dir()
    digest = hashlib.sha256()

    for file_name in STATIC_FILE_NAMES:
        file_path = source_root / file_name
        digest.update(file_name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_path.read_bytes())
        digest.update(b"\0")

    return digest.hexdigest()[:12]


def copy_static_site(output_dir: Path, site_version: str | None = None) -> str:
    source_dir = get_source_dir()
    resolved_version = site_version or compute_site_version(source_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    rendered_index = (source_dir / "index.html").read_text(encoding="utf-8").replace(
        VERSION_PLACEHOLDER,
        resolved_version,
    )
    (output_dir / "index.html").write_text(rendered_index, encoding="utf-8")

    for file_name in ("styles.css", "app.js"):
        shutil.copyfile(source_dir / file_name, output_dir / file_name)

    return resolved_version
