#!/usr/bin/env python3
"""Bundle every source/config file of this project into a single .txt file.

By default only git-tracked files are included, which automatically skips
virtualenvs, site-packages, caches, build artifacts and other unused files.
Use --scan-all to walk the filesystem instead (exclusion rules still apply).

Usage:
    python dump_code.py
    python dump_code.py -o bundle.txt
    python dump_code.py --scan-all --ext .py,.json,.toml
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Extensions considered source/config code. Add or remove as needed.
DEFAULT_EXTENSIONS = {
    ".py",
    ".pyi",
    ".json",
    ".toml",
    ".cfg",
    ".ini",
    ".yaml",
    ".yml",
    ".txt",
}

# Directories that never contain project source.
EXCLUDED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "site-packages",
    "dist",
    "build",
    ".eggs",
    ".idea",
    ".vscode",
}

# Exact filenames that should not be bundled (secrets, tooling, outputs).
EXCLUDED_FILES = {
    ".env",
    "dump_code.py",  # this helper itself
}

MAX_FILE_SIZE = 2 * 1024 * 1024  # 2 MiB safety cap per file

SEPARATOR = "=" * 80


def git_tracked_files(root: Path) -> list[Path] | None:
    """Return git-tracked files, or None when root is not a git repository."""
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=root,
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None

    paths = [p for p in result.stdout.decode("utf-8", "replace").split("\0") if p]
    return [root / p for p in paths]


def walk_files(root: Path) -> list[Path]:
    """Walk the filesystem, pruning excluded directories."""
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in EXCLUDED_DIRS for part in path.relative_to(root).parts[:-1]):
            continue
        files.append(path)
    return files


def collect_files(
    root: Path,
    extensions: set[str],
    scan_all: bool,
    output: Path,
) -> tuple[list[Path], list[Path]]:
    """Return (included, skipped) files sorted by relative path."""
    candidates = walk_files(root) if scan_all else git_tracked_files(root)
    if candidates is None:  # not a git repo -> fall back to walking
        candidates = walk_files(root)

    output_resolved = output.resolve()
    included: list[Path] = []
    skipped: list[Path] = []

    for path in candidates:
        rel = path.relative_to(root)
        if not path.is_file():
            continue
        if any(part in EXCLUDED_DIRS for part in rel.parts[:-1]):
            continue
        if path.name in EXCLUDED_FILES or path.resolve() == output_resolved:
            continue
        if path.suffix.lower() not in extensions:
            skipped.append(rel)
            continue
        if path.stat().st_size > MAX_FILE_SIZE:
            print(f"  skip (too large): {rel}", file=sys.stderr)
            skipped.append(rel)
            continue
        included.append(path)

    included.sort(key=lambda p: p.relative_to(root).as_posix())
    return included, skipped


def build_bundle(root: Path, files: list[Path]) -> tuple[str, int]:
    """Return the bundle text and the number of files actually written."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    rel_paths = [p.relative_to(root).as_posix() for p in files]

    parts: list[str] = []
    parts.append(SEPARATOR)
    parts.append("PROJECT CODE BUNDLE")
    parts.append(SEPARATOR)
    parts.append(f"Project : {root.name}")
    parts.append(f"Created : {now}")
    parts.append(f"Files   : {len(files)}")
    parts.append("")
    parts.append("FILES INCLUDED")
    parts.append("-" * 80)
    parts.extend(rel_paths)
    parts.append("")

    written = 0
    for path, rel in zip(files, rel_paths):
        try:
            content = path.read_bytes().decode("utf-8")
        except (UnicodeDecodeError, OSError) as exc:
            print(f"  skip (unreadable): {rel} ({exc})", file=sys.stderr)
            continue

        parts.append(SEPARATOR)
        parts.append(f"FILE: {rel}")
        parts.append(SEPARATOR)
        parts.append("")
        parts.append(content)
        if content and not content.endswith("\n"):
            parts.append("")
        parts.append("")
        written += 1

    return "\n".join(parts), written


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Copy all project source files into one .txt file."
    )
    parser.add_argument(
        "-r",
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="project root (default: directory of this script)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="output file (default: <root>/codebase_dump.txt)",
    )
    parser.add_argument(
        "--ext",
        default=",".join(sorted(DEFAULT_EXTENSIONS)),
        help="comma-separated extensions to include (default: %(default)s)",
    )
    parser.add_argument(
        "--scan-all",
        action="store_true",
        help="scan the filesystem instead of using git ls-files",
    )
    args = parser.parse_args()

    root = args.root.resolve()
    if not root.is_dir():
        parser.error(f"root is not a directory: {root}")

    output = (args.output or root / "codebase_dump.txt").resolve()
    extensions = {
        ext if ext.startswith(".") else f".{ext}"
        for ext in (e.strip().lower() for e in args.ext.split(","))
        if ext
    }

    files, skipped = collect_files(root, extensions, args.scan_all, output)
    if not files:
        print("No matching files found.", file=sys.stderr)
        return 1

    bundle, written = build_bundle(root, files)
    output.write_text(bundle, encoding="utf-8", newline="")

    total_bytes = sum(p.stat().st_size for p in files)
    print(f"Wrote {output}")
    print(f"  files : {written}")
    print(f"  bytes : {total_bytes:,} (source)")
    print(f"  size  : {output.stat().st_size:,} bytes (bundle)")
    if skipped:
        print(f"  skipped non-matching: {len(skipped)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
