from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path


class SkillInstallError(RuntimeError):
    pass


@dataclass(frozen=True)
class InstalledSkill:
    target: str
    path: Path
    changed: bool


SKILL_DESTINATIONS = {
    "codex": Path(".agents") / "skills" / "review-agent",
    "claude": Path(".claude") / "skills" / "review-agent",
    "kiro": Path(".kiro") / "skills" / "review-agent",
    "cursor": Path(".cursor") / "skills" / "review-agent",
}

MANAGED_FILES = (
    "SKILL.md",
    "agents/openai.yaml",
    "references/reviewer-contract.md",
    "references/reviewer-roles.md",
    "references/host-capabilities.md",
    "references/external-reviewers.md",
    "references/browser-verification.md",
)


def _template_text(relative_path: str) -> str:
    resource = files("review_agent").joinpath(
        "skill_template", "review-agent", *relative_path.split("/")
    )
    return resource.read_text(encoding="utf-8")


def _assert_safe_destination(home: Path, relative_destination: Path) -> None:
    destination = home
    for part in relative_destination.parts:
        destination /= part
        if destination.is_symlink():
            raise SkillInstallError(f"Refusing to install through a symlink: {destination}")
        if destination.exists() and not destination.is_dir():
            raise SkillInstallError(f"Skill destination is not a directory: {destination}")
    for relative_path in MANAGED_FILES:
        current = destination
        for part in Path(relative_path).parts:
            current = current / part
            if current.is_symlink():
                raise SkillInstallError(f"Refusing to install through a symlink: {current}")
            if current.exists() and current != destination / relative_path and not current.is_dir():
                raise SkillInstallError(f"Managed skill directory is not a directory: {current}")


def install_personal_skills(
    *,
    home: Path,
    targets: list[str],
    force: bool = False,
) -> list[InstalledSkill]:
    unknown = sorted(set(targets) - set(SKILL_DESTINATIONS))
    if unknown:
        raise SkillInstallError(f"Unknown skill target(s): {', '.join(unknown)}")
    if len(set(targets)) != len(targets):
        raise SkillInstallError("Skill targets must not be repeated")
    if not targets:
        raise SkillInstallError("At least one skill target is required")

    expected = {Path(path): _template_text(path) for path in MANAGED_FILES}
    planned: list[tuple[str, Path, bool]] = []
    resolved_home = home.resolve()

    for target in targets:
        destination = resolved_home / SKILL_DESTINATIONS[target]
        _assert_safe_destination(resolved_home, SKILL_DESTINATIONS[target])
        current = destination.exists() and all(
            (destination / relative).is_file()
            and (destination / relative).read_text(encoding="utf-8") == content
            for relative, content in expected.items()
        )
        if destination.exists() and not current and not force:
            raise SkillInstallError(
                f"Skill already exists with different content: {destination}. "
                "Use --force to replace its managed files."
            )
        planned.append((target, destination, not current))

    installed: list[InstalledSkill] = []
    for target, destination, changed in planned:
        if changed:
            for relative, content in expected.items():
                output = destination / relative
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(content, encoding="utf-8")
        installed.append(InstalledSkill(target=target, path=destination, changed=changed))
    return installed
