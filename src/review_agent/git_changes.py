from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


class ChangeCollectionError(RuntimeError):
    pass


@dataclass(frozen=True)
class ChangeSet:
    repo: Path
    mode: str
    source: str
    diff: str
    files: list[str]
    languages: list[str]
    truncated: bool


LANGUAGES = {
    ".c": "C",
    ".cc": "C++",
    ".cpp": "C++",
    ".cs": "C#",
    ".css": "CSS",
    ".dart": "Dart",
    ".ex": "Elixir",
    ".exs": "Elixir",
    ".go": "Go",
    ".h": "C/C++",
    ".hpp": "C++",
    ".html": "HTML",
    ".java": "Java",
    ".js": "JavaScript",
    ".jsx": "JavaScript/JSX",
    ".kt": "Kotlin",
    ".kts": "Kotlin",
    ".lua": "Lua",
    ".md": "Markdown",
    ".php": "PHP",
    ".py": "Python",
    ".rb": "Ruby",
    ".rs": "Rust",
    ".scala": "Scala",
    ".sh": "Shell",
    ".sql": "SQL",
    ".swift": "Swift",
    ".ts": "TypeScript",
    ".tsx": "TypeScript/TSX",
    ".vue": "Vue",
    ".xml": "XML",
    ".yaml": "YAML",
    ".yml": "YAML",
}

def _git(repo: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (FileNotFoundError, PermissionError, OSError) as exc:
        raise ChangeCollectionError(f"Git could not be started: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown Git error"
        raise ChangeCollectionError(detail)
    return result.stdout


def _nul_list(repo: Path, *args: str) -> list[str]:
    output = _git(repo, *args)
    return sorted(item for item in output.split("\0") if item)


def _untracked_files(repo: Path) -> list[str]:
    return _nul_list(repo, "ls-files", "--others", "--exclude-standard", "-z")


def repository_root(repo: Path) -> Path:
    repo = repo.resolve()
    try:
        return Path(_git(repo, "rev-parse", "--show-toplevel").strip()).resolve()
    except ChangeCollectionError as exc:
        raise ChangeCollectionError(f"{repo} is not a Git repository: {exc}") from exc


def _language(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix in LANGUAGES:
        return LANGUAGES[suffix]
    if suffix:
        return suffix[1:].upper()
    return "Other"


def _untracked_patch(repo: Path, relative_path: str, remaining_chars: int) -> str:
    path = repo / relative_path
    header = f"diff --git a/{relative_path} b/{relative_path}\nnew file (untracked)\n+++ b/{relative_path}\n"
    if remaining_chars <= len(header):
        return header[:remaining_chars]
    if path.is_symlink():
        return header + f"+symlink -> {os.readlink(path)}\n"
    read_limit = max(8192, remaining_chars * 4) + 1
    try:
        with path.open("rb") as handle:
            data = handle.read(read_limit)
    except OSError as exc:
        return header + f"+[review-agent could not read untracked file: {exc}]\n"
    if b"\0" in data[:8192]:
        return header + "+[binary untracked file omitted]\n"
    text = data.decode("utf-8", errors="replace")
    body = "".join(f"+{line}" for line in text.splitlines(keepends=True))
    if text and not text.endswith(("\n", "\r")):
        body += "\n"
    return (header + body)[:remaining_chars]


def collect_changes(
    repo: Path,
    *,
    base: str | None = None,
    head: str = "HEAD",
    staged: bool = False,
    working_tree: bool = False,
    max_diff_chars: int = 200_000,
) -> ChangeSet:
    if max_diff_chars < 100:
        raise ChangeCollectionError("max_diff_chars must be at least 100")
    repo = repository_root(repo)

    selected_modes = int(base is not None) + int(staged) + int(working_tree)
    if selected_modes > 1:
        raise ChangeCollectionError("Choose only one of base/head, staged, or working-tree mode")

    untracked: list[str] = []
    diff: str | None = None
    files: list[str] | None = None
    if base is not None:
        merge_base = _git(repo, "merge-base", base, head).strip()
        diff_args = ("diff", "--no-ext-diff", "--find-renames", "--unified=40", merge_base, head)
        name_args = ("diff", "--name-only", "-z", merge_base, head)
        mode = "committed"
        source = f"{merge_base}...{head} (base {base})"
    elif staged:
        diff_args = ("diff", "--cached", "--no-ext-diff", "--find-renames", "--unified=40")
        name_args = ("diff", "--cached", "--name-only", "-z")
        mode = "staged"
        source = "Git index"
    else:
        mode = "working-tree"
        source = "HEAD plus staged, unstaged, and untracked changes"
        untracked = _untracked_files(repo)
        try:
            _git(repo, "rev-parse", "--verify", "HEAD")
        except ChangeCollectionError:
            source = "uncommitted repository before its first commit"
            cached_diff_args = (
                "diff",
                "--cached",
                "--no-ext-diff",
                "--find-renames",
                "--unified=40",
            )
            working_diff_args = ("diff", "--no-ext-diff", "--find-renames", "--unified=40")
            diff = _git(repo, *cached_diff_args) + _git(repo, *working_diff_args)
            files = sorted(
                set(
                    _nul_list(repo, "diff", "--cached", "--name-only", "-z")
                    + _nul_list(repo, "diff", "--name-only", "-z")
                    + untracked
                )
            )
        else:
            diff_args = ("diff", "HEAD", "--no-ext-diff", "--find-renames", "--unified=40")
            name_args = ("diff", "HEAD", "--name-only", "-z")

    if diff is None or files is None:
        diff = _git(repo, *diff_args)
        files = sorted(set(_nul_list(repo, *name_args) + untracked))

    for relative_path in untracked:
        if len(diff) >= max_diff_chars:
            break
        if diff and not diff.endswith("\n"):
            diff += "\n"
        diff += _untracked_patch(repo, relative_path, max_diff_chars - len(diff))

    truncated = len(diff) > max_diff_chars
    if truncated:
        diff = diff[:max_diff_chars]
    if truncated or (untracked and len(diff) >= max_diff_chars):
        truncated = True
        diff += f"\n\n[review-agent: diff truncated at {max_diff_chars} characters]\n"

    languages = sorted({_language(path) for path in files})
    return ChangeSet(
        repo=repo,
        mode=mode,
        source=source,
        diff=diff,
        files=files,
        languages=languages,
        truncated=truncated,
    )
