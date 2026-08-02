"""Synchronize the marketplace plugin's skill with the packaged canonical skill."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[1]
SOURCE = REPOSITORY / "src" / "review_agent" / "skill_template" / "review-agent"
DESTINATION = REPOSITORY / "plugins" / "review-agent" / "skills" / "review-agent"


def files_under(root: Path) -> dict[Path, Path]:
    return {
        path.relative_to(root): path
        for path in root.rglob("*")
        if path.is_file()
    }


def mismatches() -> list[str]:
    source_files = files_under(SOURCE)
    destination_files = files_under(DESTINATION) if DESTINATION.exists() else {}
    issues = [
        f"missing from plugin: {relative.as_posix()}"
        for relative in sorted(source_files.keys() - destination_files.keys())
    ]
    issues.extend(
        f"unexpected in plugin: {relative.as_posix()}"
        for relative in sorted(destination_files.keys() - source_files.keys())
    )
    issues.extend(
        f"content differs: {relative.as_posix()}"
        for relative in sorted(source_files.keys() & destination_files.keys())
        if source_files[relative].read_bytes() != destination_files[relative].read_bytes()
    )
    return issues


def synchronize() -> None:
    source_files = files_under(SOURCE)
    destination_files = files_under(DESTINATION) if DESTINATION.exists() else {}

    for relative in destination_files.keys() - source_files.keys():
        destination_files[relative].unlink()

    for relative, source in source_files.items():
        destination = DESTINATION / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    for directory in sorted(DESTINATION.rglob("*"), reverse=True):
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="report drift without changing files",
    )
    args = parser.parse_args()

    if args.check:
        issues = mismatches()
        if issues:
            print("Plugin skill is out of sync:")
            for issue in issues:
                print(f"- {issue}")
            return 1
        print("Plugin skill matches the canonical skill.")
        return 0

    synchronize()
    print(f"Synchronized {SOURCE} -> {DESTINATION}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
