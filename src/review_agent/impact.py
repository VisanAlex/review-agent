from __future__ import annotations

import re
import subprocess
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Any

from .git_changes import ChangeSet


MAX_IDENTIFIERS = 24
MAX_FILES = 500
MAX_FILE_CHARS = 200_000
MAX_TOTAL_CHARS = 5_000_000
MAX_LOCATIONS = 80
MAX_LOCATIONS_PER_FILE = 8

DOCUMENT_SUFFIXES = {".adoc", ".md", ".rst", ".txt"}
IGNORED_PARTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".tox",
    ".venv",
    "build",
    "coverage",
    "dist",
    "generated",
    "node_modules",
    "target",
    "vendor",
    "venv",
}
BINARY_SUFFIXES = {
    ".7z",
    ".avi",
    ".bmp",
    ".class",
    ".dll",
    ".dylib",
    ".eot",
    ".exe",
    ".gif",
    ".gz",
    ".ico",
    ".jar",
    ".jpeg",
    ".jpg",
    ".lockb",
    ".mov",
    ".mp3",
    ".mp4",
    ".o",
    ".pdf",
    ".png",
    ".pyc",
    ".so",
    ".tar",
    ".tgz",
    ".ttf",
    ".wav",
    ".webm",
    ".webp",
    ".woff",
    ".woff2",
    ".zip",
}
STOP_IDENTIFIERS = {
    "abstract",
    "break",
    "case",
    "catch",
    "class",
    "const",
    "continue",
    "default",
    "delete",
    "else",
    "enum",
    "except",
    "export",
    "extends",
    "false",
    "finally",
    "from",
    "function",
    "global",
    "implements",
    "import",
    "interface",
    "lambda",
    "module",
    "namespace",
    "new",
    "none",
    "null",
    "package",
    "private",
    "protected",
    "public",
    "raise",
    "return",
    "static",
    "struct",
    "super",
    "switch",
    "this",
    "throw",
    "trait",
    "true",
    "type",
    "typeof",
    "undefined",
    "using",
    "while",
}

IDENTIFIER_PATTERN = re.compile(r"[^\W\d]\w*", re.UNICODE)
DECLARATION_PATTERN = re.compile(
    r"\b(?:class|def|enum|fn|func|function|interface|module|procedure|record|struct|sub|trait|type)\s+([^\W\d]\w*)",
    re.UNICODE,
)
ASSIGNMENT_PATTERN = re.compile(
    r"^\s*(?:(?:export|public|private|protected|static|const|final|let|var|readonly)\s+)*"
    r"([^\W\d]\w*)\s*(?::[^=]+)?=",
    re.UNICODE,
)


def _limits_document(
    *,
    max_identifiers: int,
    max_files: int,
    max_file_chars: int,
    max_total_chars: int,
    max_locations: int,
) -> dict[str, int]:
    return {
        "max_identifiers": max_identifiers,
        "max_files": max_files,
        "max_file_chars": max_file_chars,
        "max_total_chars": max_total_chars,
        "max_locations": max_locations,
    }


def _empty_context(
    *,
    limits: dict[str, int],
    status: str = "empty",
    limitations: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "trusted": False,
        "status": status,
        "strategy": "bounded-text-reference-scan",
        "changed_identifiers": [],
        "affected_locations": [],
        "scanned_files": 0,
        "limits": limits,
        "limitations": limitations or [],
    }


def _documentation_only(paths: list[str]) -> bool:
    if not paths:
        return False
    for path in paths:
        pure = PurePosixPath(path.replace("\\", "/").casefold())
        if pure.suffix not in DOCUMENT_SUFFIXES:
            return False
        if pure.name.startswith("requirements") and pure.suffix == ".txt":
            return False
    return True


def _styled_identifier(identifier: str) -> bool:
    return (
        "_" in identifier
        or any(character.isupper() for character in identifier[1:])
        or (identifier.isupper() and len(identifier) > 3)
    )


def _changed_identifiers(diff: str, maximum: int) -> list[str]:
    scores: dict[str, int] = defaultdict(int)
    added: set[str] = set()
    removed: set[str] = set()

    for raw_line in diff.splitlines():
        if raw_line.startswith("@@"):
            trailer = raw_line.split("@@", 2)[-1]
            for identifier in IDENTIFIER_PATTERN.findall(trailer):
                if len(identifier) > 3 and identifier.casefold() not in STOP_IDENTIFIERS:
                    scores[identifier] += 7
            continue
        if raw_line.startswith(("diff --git ", "index ", "+++ ", "--- ")):
            continue
        if not raw_line or raw_line[0] not in {"+", "-", " "}:
            continue
        changed = raw_line[0] in {"+", "-"}
        content = raw_line[1:]

        for identifier in DECLARATION_PATTERN.findall(content):
            if len(identifier) > 3 and identifier.casefold() not in STOP_IDENTIFIERS:
                scores[identifier] += 8

        if not changed:
            continue

        assignment = ASSIGNMENT_PATTERN.match(content)
        if assignment:
            identifier = assignment.group(1)
            if len(identifier) > 3 and identifier.casefold() not in STOP_IDENTIFIERS:
                scores[identifier] += 6

        line_identifiers = {
            identifier
            for identifier in IDENTIFIER_PATTERN.findall(content)
            if len(identifier) > 3 and identifier.casefold() not in STOP_IDENTIFIERS
        }
        if raw_line[0] == "+":
            added.update(line_identifiers)
        else:
            removed.update(line_identifiers)
        for identifier in line_identifiers:
            scores[identifier] += 2 if _styled_identifier(identifier) else 1

    for identifier in added & removed:
        scores[identifier] += 3

    candidates = [identifier for identifier, score in scores.items() if score >= 3]
    return sorted(candidates, key=lambda item: (-scores[item], item.casefold(), item))[:maximum]


def _tracked_files(changes: ChangeSet) -> tuple[list[str], str | None]:
    if changes.snapshot_ref is not None:
        args = ["ls-tree", "-r", "-z", changes.snapshot_ref]
        record_kind = "tree"
    elif changes.mode == "staged":
        args = ["ls-files", "-s", "-z"]
        record_kind = "index"
    else:
        args = ["ls-files", "-z"]
        record_kind = "paths"
    try:
        result = subprocess.run(
            ["git", "-C", str(changes.repo), *args],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (FileNotFoundError, PermissionError, OSError) as exc:
        return [], f"Git could not enumerate tracked files: {exc}"
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown Git error"
        return [], f"Git could not enumerate tracked files: {detail}"
    records = [record for record in result.stdout.split("\0") if record]
    if record_kind == "paths":
        return sorted(records), None

    paths: list[str] = []
    for record in records:
        try:
            metadata, path = record.split("\t", 1)
            mode = metadata.split(" ", 1)[0]
        except ValueError:
            continue
        if mode.startswith("100"):
            paths.append(path)
    return sorted(set(paths)), None


def _safe_candidate(repo: Path, relative_path: str) -> Path | None:
    normalized = relative_path.replace("\\", "/")
    pure = PurePosixPath(normalized)
    if pure.is_absolute() or ".." in pure.parts or any(
        part.casefold() in IGNORED_PARTS for part in pure.parts
    ):
        return None
    if pure.suffix.casefold() in BINARY_SUFFIXES:
        return None
    candidate = repo.joinpath(*pure.parts)
    if candidate.is_symlink() or not candidate.is_file():
        return None
    try:
        candidate.resolve().relative_to(repo.resolve())
    except (OSError, ValueError):
        return None
    return candidate


def _git_blob_prefix(repo: Path, spec: str, maximum_bytes: int) -> tuple[bytes | None, bool]:
    try:
        process = subprocess.Popen(
            ["git", "-C", str(repo), "show", spec],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except (FileNotFoundError, PermissionError, OSError):
        return None, False
    assert process.stdout is not None
    try:
        data = process.stdout.read(maximum_bytes)
    except OSError:
        data = b""
    truncated = len(data) == maximum_bytes
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    process.stdout.close()
    if not data and process.returncode not in {0, None}:
        return None, False
    return data, truncated


def collect_impact_context(
    changes: ChangeSet,
    *,
    max_identifiers: int = MAX_IDENTIFIERS,
    max_files: int = MAX_FILES,
    max_file_chars: int = MAX_FILE_CHARS,
    max_total_chars: int = MAX_TOTAL_CHARS,
    max_locations: int = MAX_LOCATIONS,
) -> dict[str, Any]:
    """Return bounded, unverified references from changed identifiers to unchanged files."""

    limits = [max_identifiers, max_files, max_file_chars, max_total_chars, max_locations]
    if any(type(value) is not int or value < 1 for value in limits):
        raise ValueError("impact mapping limits must be positive integers")
    limits_document = _limits_document(
        max_identifiers=max_identifiers,
        max_files=max_files,
        max_file_chars=max_file_chars,
        max_total_chars=max_total_chars,
        max_locations=max_locations,
    )
    if _documentation_only(changes.files):
        return _empty_context(
            limits=limits_document,
            limitations=["Documentation-only change; impact scan skipped."],
        )

    identifiers = _changed_identifiers(changes.diff, max_identifiers)
    if not identifiers:
        return _empty_context(
            limits=limits_document,
            limitations=["No stable changed identifier candidates were found in the bounded diff."],
        )

    tracked_files, git_error = _tracked_files(changes)
    if git_error:
        context = _empty_context(
            limits=limits_document,
            status="limited",
            limitations=[git_error],
        )
        context["changed_identifiers"] = identifiers
        return context

    changed = {path.replace("\\", "/").casefold() for path in changes.files}
    alternation = "|".join(re.escape(identifier) for identifier in sorted(identifiers, key=len, reverse=True))
    reference_pattern = re.compile(rf"(?<!\w)({alternation})(?!\w)", re.UNICODE)
    locations: list[dict[str, Any]] = []
    scanned_files = 0
    total_chars = 0
    limited = changes.truncated

    for relative_path in tracked_files:
        normalized = relative_path.replace("\\", "/")
        if normalized.casefold() in changed:
            continue
        if scanned_files >= max_files or total_chars >= max_total_chars:
            limited = True
            break
        maximum_bytes = max_file_chars * 4 + 1
        if changes.snapshot_ref is not None:
            data, byte_truncated = _git_blob_prefix(
                changes.repo,
                f"{changes.snapshot_ref}:{normalized}",
                maximum_bytes,
            )
        elif changes.mode == "staged":
            data, byte_truncated = _git_blob_prefix(
                changes.repo,
                f":{normalized}",
                maximum_bytes,
            )
        else:
            candidate = _safe_candidate(changes.repo, normalized)
            if candidate is None:
                continue
            try:
                with candidate.open("rb") as handle:
                    data = handle.read(maximum_bytes)
            except OSError:
                limited = True
                continue
            byte_truncated = len(data) == maximum_bytes
        if data is None:
            limited = True
            continue
        limited = limited or byte_truncated
        if b"\0" in data[:8192]:
            continue
        text = data.decode("utf-8", errors="replace")
        if len(text) > max_file_chars:
            text = text[:max_file_chars]
            limited = True
        if total_chars + len(text) > max_total_chars:
            text = text[: max_total_chars - total_chars]
            limited = True
        scanned_files += 1
        total_chars += len(text)
        per_file = 0
        seen_on_line: set[tuple[int, str]] = set()
        for line_number, line in enumerate(text.splitlines(), start=1):
            for match in reference_pattern.finditer(line):
                identifier = match.group(1)
                key = (line_number, identifier)
                if key in seen_on_line:
                    continue
                seen_on_line.add(key)
                locations.append(
                    {
                        "file": normalized,
                        "line": line_number,
                        "relationship": f"References changed identifier `{identifier}`; verify semantic impact.",
                    }
                )
                per_file += 1
                if per_file >= MAX_LOCATIONS_PER_FILE or len(locations) >= max_locations:
                    break
            if per_file >= MAX_LOCATIONS_PER_FILE or len(locations) >= max_locations:
                break
        if len(locations) >= max_locations:
            limited = True
            break

    limitations = [
        "Locations are text-reference candidates, not verified callers; the parent must confirm semantics."
    ]
    if limited:
        limitations.append("The bounded scan may omit additional identifiers or affected locations.")
    return {
        "trusted": False,
        "status": "limited" if limited else "complete",
        "strategy": "bounded-text-reference-scan",
        "changed_identifiers": identifiers,
        "affected_locations": locations,
        "scanned_files": scanned_files,
        "limits": limits_document,
        "limitations": limitations,
    }
