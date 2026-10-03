"""Preview or remove regenerable workspace caches; application data is excluded.

Usage: .venv/bin/python scripts/clean_workspace.py [--apply]
Only fixed paths are considered. Release bundles, environments, reports,
credentials, service logs/PIDs and databases are never cleanup targets.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIRS = (
    "build", ".pytest_cache", ".ruff_cache", "frontend/test-results",
    "frontend/playwright-report", "src-tauri/target/debug",
)
SOURCE_DIRS = ("tradingagents", "cli", "scripts", "tests", "frontend/src")


def cleanup_targets(root: Path, tracked: set[str]) -> list[Path]:
    """Fail closed on tracked files, symlinks or paths outside the checkout."""
    candidates = [root / name for name in CACHE_DIRS]
    candidates += [root / ".DS_Store"]
    for name in SOURCE_DIRS:
        source = root / name
        if source.is_symlink() or not source.is_dir():
            continue
        candidates.extend(source.rglob("__pycache__"))
        candidates.extend(source.rglob(".DS_Store"))
    targets = []
    for path in sorted(set(candidates)):
        relative = path.relative_to(root)
        ancestors = [root / Path(*relative.parts[:index]) for index in range(1, len(relative.parts) + 1)]
        if any(parent.is_symlink() for parent in ancestors) or not path.exists():
            continue
        if any(item == relative.as_posix() or item.startswith(relative.as_posix() + "/") for item in tracked):
            continue
        if not any(parent in targets for parent in path.parents):
            targets.append(path)
    return targets


def size_bytes(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(child.stat().st_size for child in path.rglob("*") if child.is_file() and not child.is_symlink())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Remove the listed regenerable caches")
    args = parser.parse_args()
    tracked = set(subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0"))
    targets = cleanup_targets(ROOT, tracked)
    total = 0
    for path in targets:
        size = size_bytes(path)
        total += size
        print(f"{'REMOVE' if args.apply else 'PREVIEW'} {path.relative_to(ROOT)} ({size / 1024**2:.1f} MiB)")
        if args.apply:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
    print(f"Total: {total / 1024**2:.1f} MiB. {'Removed.' if args.apply else 'Use --apply to remove.'}")


if __name__ == "__main__":
    main()
