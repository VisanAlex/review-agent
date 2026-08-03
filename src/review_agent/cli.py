from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .external import (
    assignment_from_mapping,
    make_adapter,
    parse_external_targets,
    run_external_reviews,
)
from .git_changes import ChangeCollectionError, ChangeSet, collect_changes, repository_root
from .models import (
    ReviewerAssignment,
    ReviewerRole,
    ReviewPlan,
    ReviewPolicy,
    RoleRecommendation,
)
from .planning import recommend_roles
from .review import (
    build_assignment_prompt,
    consolidate,
    render_json,
    render_markdown,
    reviewer_run_from_mapping,
)
from .skills import SkillInstallError, install_personal_skills


DEFAULT_CONFIG: dict[str, Any] = {
    "version": 2,
    "review": {
        "max_diff_chars": 200_000,
        "max_reviewers": 4,
    },
    "roles": {
        "include": [],
        "exclude": [],
    },
    "external": {
        "timeout_seconds": 300,
    },
}


class ConfigError(RuntimeError):
    pass


def _repo(value: str) -> Path:
    return Path(value).resolve()


def _object(value: Any, *, path: Path, key: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{path}: {key} must be an object")
    return value


def _role_list(value: Any, *, path: Path, key: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"{path}: {key} must be an array of role names")
    normalized = [item.strip().lower() for item in value]
    if len(set(normalized)) != len(normalized):
        raise ConfigError(f"{path}: {key} must not contain duplicate roles")
    try:
        return [ReviewerRole(item).value for item in normalized]
    except ValueError as exc:
        valid = ", ".join(role.value for role in ReviewerRole)
        raise ConfigError(f"{path}: {key} contains an unknown role; choose from {valid}") from exc


def load_config(repo: Path) -> dict[str, Any]:
    path = repo / ".review-agent.json"
    if not path.exists():
        return copy.deepcopy(DEFAULT_CONFIG)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Could not read {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must contain an object")
    if raw.get("version") == 1:
        raise ConfigError(
            f"{path} uses version 1. Migrate to version 2: remove providers, add review.max_reviewers "
            "and optional roles.include/roles.exclude. Version 2 never launches external reviewers by default."
        )
    if raw.get("version") != 2:
        raise ConfigError(f"{path} must contain review-agent config version 2")

    review = _object(raw.get("review", {}), path=path, key="review")
    roles = _object(raw.get("roles", {}), path=path, key="roles")
    external = _object(raw.get("external", {}), path=path, key="external")

    max_diff_chars = review.get("max_diff_chars", DEFAULT_CONFIG["review"]["max_diff_chars"])
    max_reviewers = review.get("max_reviewers", DEFAULT_CONFIG["review"]["max_reviewers"])
    timeout_seconds = external.get(
        "timeout_seconds", DEFAULT_CONFIG["external"]["timeout_seconds"]
    )
    if type(max_diff_chars) is not int or max_diff_chars < 100:
        raise ConfigError(f"{path}: review.max_diff_chars must be an integer of at least 100")
    if type(max_reviewers) is not int or not 1 <= max_reviewers <= len(ReviewerRole):
        raise ConfigError(
            f"{path}: review.max_reviewers must be an integer from 1 to {len(ReviewerRole)}"
        )
    if type(timeout_seconds) is not int or timeout_seconds < 1:
        raise ConfigError(f"{path}: external.timeout_seconds must be a positive integer")

    include = _role_list(roles.get("include", []), path=path, key="roles.include")
    exclude = _role_list(roles.get("exclude", []), path=path, key="roles.exclude")
    overlap = set(include) & set(exclude)
    if overlap:
        raise ConfigError(
            f"{path}: roles cannot be both included and excluded: {', '.join(sorted(overlap))}"
        )
    return {
        "version": 2,
        "review": {
            "max_diff_chars": max_diff_chars,
            "max_reviewers": max_reviewers,
        },
        "roles": {"include": include, "exclude": exclude},
        "external": {"timeout_seconds": timeout_seconds},
    }


def _policy(config: dict[str, Any]) -> ReviewPolicy:
    return ReviewPolicy(
        max_reviewers=config["review"]["max_reviewers"],
        include_roles=[ReviewerRole(value) for value in config["roles"]["include"]],
        exclude_roles=[ReviewerRole(value) for value in config["roles"]["exclude"]],
    )


def _changes(args: argparse.Namespace, config: dict[str, Any]) -> ChangeSet:
    configured_limit = config["review"]["max_diff_chars"]
    max_diff_chars = args.max_diff_chars if args.max_diff_chars is not None else configured_limit
    return collect_changes(
        args.repo,
        base=args.base,
        head=args.head,
        staged=args.staged,
        working_tree=args.working_tree,
        max_diff_chars=max_diff_chars,
    )


def _context_document(changes: ChangeSet, plan: ReviewPlan) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "change_context": {
            "repository": changes.repo.name,
            "repository_root": str(changes.repo),
            "source": changes.source,
            "mode": changes.mode,
            "files": list(changes.files),
            "languages": list(changes.languages),
            "diff": changes.diff,
            "diff_trusted": False,
            "truncated": changes.truncated,
        },
        "review_plan": plan.to_dict(),
    }


def _add_repo_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--repo", type=_repo, default=Path.cwd(), help="Git repository (default: current directory)"
    )


def _add_change_arguments(parser: argparse.ArgumentParser) -> None:
    _add_repo_argument(parser)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--base", help="Base revision for a committed change")
    modes.add_argument("--staged", action="store_true", help="Review only staged changes")
    modes.add_argument(
        "--working-tree",
        action="store_true",
        help="Review staged, unstaged, and untracked changes (default)",
    )
    parser.add_argument("--head", default="HEAD", help="Head revision used with --base (default: HEAD)")
    parser.add_argument("--max-diff-chars", type=int, help="Maximum diff characters in the context")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="review-agent",
        description="Prepare and consolidate portable, host-orchestrated code reviews",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Create a small .review-agent.json config")
    _add_repo_argument(init_parser)
    init_parser.add_argument("--force", action="store_true", help="Replace an existing config")

    doctor_parser = subparsers.add_parser("doctor", help="Check the local Git prerequisite")
    _add_repo_argument(doctor_parser)
    doctor_parser.add_argument(
        "--external",
        action="append",
        default=[],
        help="Also check an explicitly named external target (repeatable)",
    )

    skills_parser = subparsers.add_parser(
        "install-skills", help="Install personal review-agent skill entry points"
    )
    skills_parser.add_argument(
        "--target",
        choices=("all", "codex", "claude", "kiro", "cursor"),
        default="all",
        help="Skill host to install (default: all)",
    )
    skills_parser.add_argument("--force", action="store_true", help="Replace managed skill files")

    plan_parser = subparsers.add_parser(
        "plan", help="Inspect the change and recommend specialists without calling a model"
    )
    _add_change_arguments(plan_parser)
    plan_parser.add_argument("--format", choices=("text", "json"), default="text")

    context_parser = subparsers.add_parser(
        "context", help="Emit bounded review context without calling a model"
    )
    _add_change_arguments(context_parser)
    context_parser.add_argument("--format", choices=("json", "prompt"), default="json")
    context_parser.add_argument("--role", choices=tuple(role.value for role in ReviewerRole))

    consolidate_parser = subparsers.add_parser(
        "consolidate", help="Validate reviewer result files and create one report"
    )
    consolidate_parser.add_argument("--plan", type=Path, required=True, help="Plan/context JSON file")
    consolidate_parser.add_argument(
        "--result", type=Path, action="append", required=True, help="Reviewer result JSON (repeatable)"
    )
    consolidate_parser.add_argument("--output", type=Path, help="Write Markdown report")
    consolidate_parser.add_argument("--json-output", type=Path, help="Write structured JSON report")

    external_parser = subparsers.add_parser(
        "external", help="Run only external targets explicitly authorized by a with directive"
    )
    _add_change_arguments(external_parser)
    external_parser.add_argument("--request", required=True, help="Original review invocation text")
    external_parser.add_argument("--current-host", required=True, help="Invoking host name")
    external_parser.add_argument(
        "--assignment", type=Path, help="Existing bounded reviewer assignment JSON"
    )
    external_parser.add_argument(
        "--role",
        choices=tuple(role.value for role in ReviewerRole),
        help="Build an assignment for this role directly from the requested Git scope",
    )
    external_parser.add_argument(
        "--focus", help="Optional role-specific focus when building the assignment"
    )
    external_parser.add_argument("--timeout", type=int, help="Per-target timeout in seconds")
    external_parser.add_argument("--output", type=Path, help="Write external result envelope")
    return parser


def _init(args: argparse.Namespace) -> int:
    repo = repository_root(args.repo)
    path = repo / ".review-agent.json"
    if path.exists() and not args.force:
        print(f"Config already exists: {path}", file=sys.stderr)
        return 2
    path.write_text(json.dumps(DEFAULT_CONFIG, indent=2) + "\n", encoding="utf-8")
    print(f"Created {path}")
    print("Next: invoke the review-agent skill, or inspect with review-agent plan")
    return 0


def _git_doctor(repo: Path) -> tuple[bool, str]:
    try:
        version = subprocess.run(
            ["git", "--version"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        repository = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--show-toplevel"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except (FileNotFoundError, PermissionError, OSError, subprocess.SubprocessError) as exc:
        return False, f"cannot launch Git: {exc}"
    if version.returncode != 0:
        return False, version.stderr.strip() or f"git --version exited with {version.returncode}"
    if repository.returncode != 0:
        return False, repository.stderr.strip() or f"{repo} is not a Git repository"
    return True, f"{version.stdout.strip()} - {repository.stdout.strip()}"


def _doctor(args: argparse.Namespace) -> int:
    try:
        repo = repository_root(args.repo)
    except ChangeCollectionError:
        repo = args.repo
    available, detail = _git_doctor(repo)
    print(f"Repository: {repo}")
    print(f"[{'ok' if available else 'FAILED'}] git: {detail}")
    failures = int(not available)
    for requested in args.external:
        try:
            targets = parse_external_targets(f"with {requested}")
            if len(targets) != 1:
                raise ValueError("exactly one target is required")
            check = make_adapter(targets[0]).doctor()
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc
        failures += int(not check.available)
        print(f"[{'ok' if check.available else 'FAILED'}] {check.target}: {check.detail}")
    return 0 if failures == 0 else 1


def _install_skills(args: argparse.Namespace) -> int:
    targets = ["codex", "claude", "kiro", "cursor"] if args.target == "all" else [args.target]
    installed = install_personal_skills(home=Path.home(), targets=targets, force=args.force)
    for result in installed:
        status = "Installed" if result.changed else "Already current"
        print(f"{status} {result.target} skill: {result.path}")
    print("Restart the host if the new skill does not appear immediately.")
    return 0


def _prepare(args: argparse.Namespace) -> tuple[ChangeSet, ReviewPlan]:
    args.repo = repository_root(args.repo)
    config = load_config(args.repo)
    changes = _changes(args, config)
    return changes, recommend_roles(changes, policy=_policy(config))


def _plan(args: argparse.Namespace) -> int:
    changes, plan = _prepare(args)
    document = _context_document(changes, plan)
    if args.format == "json":
        print(json.dumps(document, indent=2, ensure_ascii=False))
        return 0
    print(f"Repository: {changes.repo}")
    print(f"Source: {changes.source}")
    print(f"Files: {len(changes.files)}")
    for path in changes.files:
        print(f"  - {path}")
    print(f"Languages/file types: {', '.join(changes.languages) or 'none'}")
    print(f"Diff characters: {len(changes.diff)}{' (truncated)' if changes.truncated else ''}")
    print(f"Risk signals: {', '.join(plan.risk_signals) or 'none'}")
    print("Selected specialists:")
    if plan.selected_roles:
        for item in plan.selected_roles:
            print(f"  - {item.role.value}: {item.reason}")
    else:
        print("  - none (parent-only limited review)")
    print("Skipped specialists:")
    for item in plan.skipped_roles:
        print(f"  - {item.role.value}: {item.reason}")
    print("External targets: none")
    print("No project commands will be run.")
    return 0


def _context(args: argparse.Namespace) -> int:
    changes, plan = _prepare(args)
    if args.format == "json":
        print(json.dumps(_context_document(changes, plan), indent=2, ensure_ascii=False))
        return 0
    if args.role is None:
        raise ConfigError("context --format prompt requires --role")
    role = ReviewerRole(args.role)
    recommendation = next(
        (item for item in [*plan.selected_roles, *plan.skipped_roles] if item.role is role), None
    )
    focus = recommendation.reason if recommendation is not None else f"Review {role.value} risks."
    print(build_assignment_prompt(changes, role, focus))
    return 0


def _read_json(path: Path, *, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Could not read {label} {path}: {exc}") from exc


def _recommendation(value: Any) -> RoleRecommendation:
    if not isinstance(value, dict):
        raise ConfigError("review plan recommendations must be objects")
    try:
        role = ReviewerRole(str(value["role"]))
    except (KeyError, ValueError) as exc:
        raise ConfigError(f"invalid review plan role: {exc}") from exc
    reason = str(value.get("reason", "")).strip()
    signals = value.get("signals", [])
    if not reason or not isinstance(signals, list) or not all(isinstance(item, str) for item in signals):
        raise ConfigError("review plan recommendation requires a reason and string signals")
    return RoleRecommendation(role=role, reason=reason, signals=list(signals))


def _plan_from_document(document: Any) -> tuple[ReviewPlan, dict[str, Any]]:
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise ConfigError("plan must be a schema_version 1 object")
    context = document.get("change_context")
    raw_plan = document.get("review_plan")
    if not isinstance(context, dict) or not isinstance(raw_plan, dict):
        raise ConfigError("plan requires change_context and review_plan objects")
    files = context.get("files")
    if not isinstance(files, list) or not all(isinstance(item, str) for item in files):
        raise ConfigError("plan change_context.files must be an array of paths")
    selected = raw_plan.get("selected_roles", [])
    skipped = raw_plan.get("skipped_roles", [])
    external = raw_plan.get("requested_external_targets", [])
    risk_signals = raw_plan.get("risk_signals", [])
    if not isinstance(selected, list) or not isinstance(skipped, list):
        raise ConfigError("plan selected_roles and skipped_roles must be arrays")
    if not isinstance(external, list) or not all(isinstance(item, str) for item in external):
        raise ConfigError("plan requested_external_targets must be an array of strings")
    if not isinstance(risk_signals, list) or not all(isinstance(item, str) for item in risk_signals):
        raise ConfigError("plan risk_signals must be an array of strings")
    max_reviewers = raw_plan.get("max_reviewers", 4)
    if type(max_reviewers) is not int or max_reviewers < 1:
        raise ConfigError("plan max_reviewers must be a positive integer")
    source = str(raw_plan.get("source", context.get("source", ""))).strip()
    if not source:
        raise ConfigError("plan source must be non-empty")
    return (
        ReviewPlan(
            source=source,
            selected_roles=[_recommendation(item) for item in selected],
            skipped_roles=[_recommendation(item) for item in skipped],
            requested_external_targets=list(external),
            max_reviewers=max_reviewers,
            risk_signals=list(risk_signals),
        ),
        context,
    )


def _write_output(path: Path, content: str) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _consolidate(args: argparse.Namespace) -> int:
    plan, context = _plan_from_document(_read_json(args.plan, label="plan"))
    runs = []
    requested_external: list[str] = []
    for path in args.result:
        try:
            document = _read_json(path, label="reviewer result")
            if isinstance(document, dict) and "reviewer_runs" in document:
                if document.get("schema_version") != 1:
                    raise ValueError("external result envelope must use schema_version 1")
                raw_runs = document.get("reviewer_runs")
                raw_targets = document.get("normalized_targets", [])
                if not isinstance(raw_runs, list):
                    raise ValueError("external result reviewer_runs must be an array")
                if not isinstance(raw_targets, list) or not all(
                    isinstance(item, str) for item in raw_targets
                ):
                    raise ValueError("external result normalized_targets must be an array of strings")
                requested_external.extend(raw_targets)
                runs.extend(reviewer_run_from_mapping(item) for item in raw_runs)
            else:
                runs.append(reviewer_run_from_mapping(document))
        except ValueError as exc:
            raise ConfigError(f"Invalid reviewer result {path}: {exc}") from exc
    plan.requested_external_targets = list(
        dict.fromkeys([*plan.requested_external_targets, *requested_external])
    )
    review = consolidate(
        runs,
        source=str(context.get("source", plan.source)),
        plan=plan,
        changed_files=set(context["files"]),
    )
    markdown = render_markdown(review)
    print(markdown)
    if args.output:
        _write_output(args.output, markdown)
    if args.json_output:
        _write_output(args.json_output, render_json(review))
    return 0


def _external(args: argparse.Namespace) -> int:
    try:
        targets = parse_external_targets(args.request, current_host=args.current_host)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    if not targets:
        print("No explicit external targets were authorized; no context was sent.")
        return 0

    repo = repository_root(args.repo)
    config = load_config(repo)
    timeout = args.timeout if args.timeout is not None else config["external"]["timeout_seconds"]
    if type(timeout) is not int or timeout < 1:
        raise ConfigError("timeout must be a positive integer")
    if args.assignment is not None and args.role is not None:
        raise ConfigError("Choose either --assignment or --role, not both")
    if args.assignment is not None and args.focus is not None:
        raise ConfigError("--focus can only be used with --role")
    if args.assignment is not None:
        if (
            args.base is not None
            or args.staged
            or args.working_tree
            or args.head != "HEAD"
            or args.max_diff_chars is not None
        ):
            raise ConfigError("Git scope flags cannot be combined with --assignment")
        try:
            assignment = assignment_from_mapping(_read_json(args.assignment, label="assignment"))
        except ValueError as exc:
            raise ConfigError(f"Invalid assignment {args.assignment}: {exc}") from exc
        assignment_root = Path(str(assignment.change_context["repository_root"])).resolve()
        if assignment_root != repo:
            raise ConfigError(
                f"Assignment repository {assignment_root} does not match requested repository {repo}"
            )
    else:
        if args.role is None:
            raise ConfigError("external requires --role or --assignment after a target is authorized")
        args.repo = repo
        changes = _changes(args, config)
        plan = recommend_roles(changes, policy=_policy(config))
        role = ReviewerRole(args.role)
        recommendation = next(
            (item for item in plan.selected_roles if item.role is role),
            None,
        )
        focus = (args.focus or "").strip()
        if not focus:
            focus = (
                recommendation.reason
                if recommendation is not None
                else f"Review the changed code for concrete {role.value.replace('-', ' ')} risks."
            )
        assignment = ReviewerAssignment(
            reviewer_id=f"{role.value}-external",
            role=role,
            focus=focus,
            exclusions=[
                "Do not edit files or delegate.",
                "Do not run tests, builds, package managers, project scripts, or arbitrary commands.",
                "Do not report generic style advice or defects outside the supplied change.",
            ],
            change_context=_context_document(changes, plan)["change_context"],
        )

    print(f"External targets: {', '.join(targets)}", file=sys.stderr)
    print("Bounded review context will be sent to those targets.", file=sys.stderr)
    try:
        runs = run_external_reviews(targets, assignment, repo, timeout)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    document = {
        "schema_version": 1,
        "normalized_targets": targets,
        "reviewer_runs": [run.to_dict(include_findings=True) for run in runs],
    }
    output = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    print(output, end="")
    if args.output:
        _write_output(args.output, output)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            return _init(args)
        if args.command == "doctor":
            return _doctor(args)
        if args.command == "install-skills":
            return _install_skills(args)
        if args.command == "plan":
            return _plan(args)
        if args.command == "context":
            return _context(args)
        if args.command == "consolidate":
            return _consolidate(args)
        if args.command == "external":
            return _external(args)
    except (ConfigError, ChangeCollectionError, SkillInstallError, OSError) as exc:
        print(f"review-agent: {exc}", file=sys.stderr)
        return 2
    parser.error(f"Unknown command: {args.command}")
    return 2


def entrypoint() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
