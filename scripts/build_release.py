#!/usr/bin/env python3
"""Build a deterministic source archive suitable for GitHub and HAL."""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
EXCLUDED_PARTS = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".coverage",
    "build",
    "dist",
    "output",
}
REQUIRED = {"README.md", "AUTHORS.md", "LICENSE", "CITATION.cff", "codemeta.json"}
FIXED_ZIP_TIME = (2026, 7, 30, 0, 0, 0)


def project_version() -> str:
    metadata = json.loads((ROOT / "codemeta.json").read_text(encoding="utf-8"))
    return str(metadata["version"])


def source_files() -> list[Path]:
    files = []
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        if any(part in EXCLUDED_PARTS for part in relative.parts):
            continue
        if path.is_symlink() or not path.is_file():
            continue
        files.append(path)
    return sorted(files, key=lambda path: path.relative_to(ROOT).as_posix())


def build() -> tuple[Path, Path, str]:
    missing = sorted(name for name in REQUIRED if not (ROOT / name).is_file())
    if missing:
        raise SystemExit(f"release metadata missing: {', '.join(missing)}")

    version = project_version()
    release_root = f"musicxml-discovery-{version}"
    archive = DIST / f"{release_root}.zip"
    checksum = archive.with_suffix(".zip.sha256")
    DIST.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as output:
        for source in source_files():
            relative = source.relative_to(ROOT).as_posix()
            info = zipfile.ZipInfo(f"{release_root}/{relative}", FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            output.writestr(info, source.read_bytes(), compresslevel=9)

    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    checksum.write_text(f"{digest}  {archive.name}\n", encoding="ascii")
    return archive, checksum, digest


if __name__ == "__main__":
    archive_path, checksum_path, sha256 = build()
    print(f"Archive: {archive_path}")
    print(f"Checksum: {checksum_path}")
    print(f"SHA-256: {sha256}")
