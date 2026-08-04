from __future__ import annotations

import re
from pathlib import Path
from typing import Any


MAX_DOCUMENT_CHARS = 20_000
MAX_TOTAL_CHARS = 50_000
MAX_INCIDENTS = 3
MAX_INCIDENT_FILES = 200
MAX_DIFF_QUERY_TOKENS = 2_000
TOKEN_PATTERN = re.compile(r"[^\W_]{2,}", re.UNICODE)
GENERIC_TOKENS = {
    "change",
    "changed",
    "code",
    "file",
    "incident",
    "main",
    "previous",
    "review",
    "src",
    "test",
    "tests",
}


def _tokens(value: str, *, limit: int | None = None) -> set[str]:
    tokens: set[str] = set()
    for match in TOKEN_PATTERN.finditer(value):
        token = match.group().casefold()
        if token in GENERIC_TOKENS:
            continue
        tokens.add(token)
        if limit is not None and len(tokens) >= limit:
            break
    return tokens


def _matching_token_count(
    query: set[str],
    document: set[str],
    *,
    fuzzy_unicode: bool = False,
) -> int:
    matches = 0
    for token in query:
        if token in document:
            matches += 1
        elif fuzzy_unicode and not token.isascii() and any(
            token in candidate or candidate in token for candidate in document if not candidate.isascii()
        ):
            matches += 1
    return matches


def _has_symlink_ancestor(repo: Path, path: Path) -> bool:
    relative = path.relative_to(repo)
    current = repo
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _read_document(repo: Path, path: Path, *, remaining: int) -> dict[str, Any] | None:
    if remaining < 1 or not path.is_file() or _has_symlink_ancestor(repo, path):
        return None
    limit = min(MAX_DOCUMENT_CHARS, remaining)
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            content = handle.read(limit + 1)
    except OSError:
        return None
    truncated = len(content) > limit
    if truncated:
        content = content[:limit]
    return {
        "path": path.relative_to(repo).as_posix(),
        "content": content,
        "truncated": truncated,
    }


def collect_repository_context(
    repo: Path,
    *,
    changed_files: list[str],
    risk_signals: list[str],
    diff: str = "",
) -> dict[str, Any]:
    """Collect bounded, opt-in repository invariants and relevant incident notes."""

    repo = repo.resolve()
    root = repo / ".review-agent"
    remaining = MAX_TOTAL_CHARS
    invariants: list[dict[str, Any]] = []
    invariant = _read_document(repo, root / "invariants.md", remaining=remaining)
    if invariant is not None:
        invariants.append(invariant)
        remaining -= len(invariant["content"])

    primary_query = _tokens(" ".join([*changed_files, *risk_signals]))
    diff_query = _tokens(diff, limit=MAX_DIFF_QUERY_TOKENS)
    candidates: list[tuple[int, str, Path]] = []
    incident_root = root / "incidents"
    if (primary_query or diff_query) and incident_root.is_dir() and not _has_symlink_ancestor(
        repo, incident_root
    ):
        for path in sorted(incident_root.glob("*.md"))[:MAX_INCIDENT_FILES]:
            if _has_symlink_ancestor(repo, path):
                continue
            try:
                with path.open("r", encoding="utf-8", errors="replace") as handle:
                    searchable = path.name + " " + handle.read(MAX_DOCUMENT_CHARS)
            except OSError:
                continue
            document_tokens = _tokens(searchable)
            score = 3 * _matching_token_count(
                primary_query,
                document_tokens,
                fuzzy_unicode=True,
            ) + _matching_token_count(diff_query, document_tokens)
            if score:
                candidates.append((score, path.as_posix().casefold(), path))

    incidents: list[dict[str, Any]] = []
    for score, _, path in sorted(candidates, key=lambda item: (-item[0], item[1]))[:MAX_INCIDENTS]:
        document = _read_document(repo, path, remaining=remaining)
        if document is None:
            continue
        document["relevance_score"] = score
        incidents.append(document)
        remaining -= len(document["content"])
        if remaining < 1:
            break

    return {
        "trusted": False,
        "invariants": invariants,
        "incidents": incidents,
        "limits": {
            "max_document_chars": MAX_DOCUMENT_CHARS,
            "max_total_chars": MAX_TOTAL_CHARS,
            "max_incidents": MAX_INCIDENTS,
            "max_incident_files_scanned": MAX_INCIDENT_FILES,
            "max_diff_query_tokens": MAX_DIFF_QUERY_TOKENS,
        },
    }
