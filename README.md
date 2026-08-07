# Review Agent

Review Agent is a portable skill that turns your current coding agent into the parent of a focused code-review team. It selects specialists from the actual Git change, uses the current host's native subagents when available, and returns one evidence-based report.

Codex does not require Claude. Claude Code does not require Codex. A Claude Code session configured through Amazon Bedrock keeps its native review work on that active configuration. Extra hosts and providers participate only when you explicitly add them with a `with` directive.

No Docker, database, server, daemon, or project-command execution is required.

## Quick start

Add the [`VisanAlex/review-agent`](https://github.com/VisanAlex/review-agent) repository as a marketplace. You do not need Python, Docker, or the helper executable for the normal review workflow.

### Claude Code

In Claude Code, including its VS Code integration, run:

```text
/plugin marketplace add VisanAlex/review-agent
/plugin install review-agent@review-agent
```

Start a new session, then invoke `/review-agent:review-agent` or ask Claude to use the Review Agent skill. Claude remains the parent and uses its own subagents and current model configuration, including Amazon Bedrock when that is how Claude Code is configured.

### Codex

From a terminal, register the marketplace:

```text
codex plugin marketplace add VisanAlex/review-agent
```

Open `/plugins` in Codex, install **Review Agent**, start a new session, and invoke `$review-agent`. Repository marketplaces are supported by the Codex CLI and desktop app. The Codex IDE extension does not currently load plugins; in that surface, use the personal-skill installation below.

### Other hosts or personal skill installation

The plugin contains a regular portable Agent Skill at `plugins/review-agent/skills/review-agent`. A compatible host can copy that directory into its personal or project skill directory. The optional helper automates this for Codex, Claude Code, Kiro, and Cursor:

```powershell
python -m pip install .
review-agent install-skills
```

Use `--target codex`, `claude`, `kiro`, or `cursor` to install only one host. Restart a host if it does not discover the new skill immediately.

For a personal-skill installation, invoke it through that host's skill UI:

- Codex: invoke `$review-agent` or select Review Agent from the skills list.
- Claude Code: invoke `/review-agent`.
- Kiro or Cursor: select or invoke `review-agent` through the available Agent Skills surface.

The skill itself is the product. The Python command is a replaceable deterministic helper; a host can still follow the skill using its own Git and read tools when the helper is unavailable. The helper becomes necessary only for its convenience commands or explicitly requested external-model adapters.

## Default behavior

Without an external directive, the invoking host is the complete reviewer pool:

```text
Codex invocation       -> Codex parent -> selected Codex subagents
Claude Code invocation -> Claude parent -> selected Claude subagents
Kiro/Cursor invocation -> native subagents when exposed, otherwise labeled fallback
```

Installing another agent CLI never changes this behavior. Review Agent does not scan for Codex, Claude, OpenRouter, or credentials during a normal run.

The parent inspects the change and selects only justified roles from correctness, testing, security, data integrity, API compatibility, frontend/accessibility, concurrency/reliability, performance, architecture, dependency/supply-chain, deployment/operations, and internationalization. The roles are language-independent. Documentation-only changes can intentionally use no specialist roster.

Before selecting those roles, a fixed `impact-mapper` traces changed symbols and contracts into unchanged callers, consumers, tests, templates, adapters, and integrations. It is a pipeline component, not a 13th agent. The helper supplies a bounded cross-language text-reference fallback; hosts with code intelligence can refine it with semantic references. Every candidate must be verified before use.

If native subagents are unavailable or denied, the parent runs the selected lenses sequentially and labels the result `single-agent-fallback`. Those passes share one context and are never presented as independent agreement.

## Reviewer depth

Four specialists is the default, not a hard limit. Override it for one review in natural language:

```text
Use $review-agent on this branch using max 7 specialists
Use /review-agent on my staged changes with up to 6 review agents
Use $review-agent on this branch using all relevant specialists
```

`all relevant specialists` raises the cap to the full 12-role roster; it does not spawn irrelevant reviewers. Limit precedence is the current invocation, then `.review-agent.json`, then the default of four. Direct helper users can pass `--max-reviewers N` or `--all-relevant`; an explicit CLI flag takes precedence over text passed with `--request`.

## Optional external review

Add external review only in the invocation that authorizes it:

```text
Use $review-agent on my staged changes with claude
Use /review-agent on this branch with codex
Use review-agent with codex, claude
Use $review-agent with openrouter:anthropic/claude-sonnet-4
```

Supported initial targets are:

- `codex`: external agent host via Codex CLI.
- `claude`: external agent host via Claude Code CLI.
- `openrouter:<model-id>`: external model provider; the model ID is required.

A phrase such as “Claude is installed” is context, not consent. The parent removes itself from the requested external set, echoes the normalized targets, and states that bounded review context will be sent. It never silently substitutes another target.

External adapters require the optional helper. Codex and Claude use their existing CLI authentication. OpenRouter requires `OPENROUTER_API_KEY` in the process environment; credentials do not belong in `.review-agent.json`.

If an external target is unavailable, times out, or returns invalid output, native results remain valid and the report names the missing coverage.

The host must use the helper as the external boundary. For example, a Claude parent reviewing against `staging` dispatches Codex through the equivalent of:

```bash
review-agent external --repo . --base staging \
  --request "Review the current branch against staging with codex" \
  --current-host claude --role correctness
```

The skill selects the role and runs this command automatically. The command prints a structured external result; it does not run the invoking host's native specialists or produce the final consolidated report by itself.

## Host support

| Host | Skill install | Native execution |
|---|---|---|
| Codex CLI/desktop | Repository marketplace plugin or `.agents/skills/review-agent` | First-class native subagents using the current session configuration |
| Codex IDE extension | `.agents/skills/review-agent` | Native subagents exposed by the current session; plugin installation is not currently supported |
| Claude Code IDE/CLI | Repository marketplace plugin or `.claude/skills/review-agent` | First-class native subagents, including active Amazon Bedrock configuration |
| Kiro | `.kiro/skills/review-agent` | Native subagents when the current surface exposes them; fallback otherwise |
| Cursor | `.cursor/skills/review-agent` | Native subagents when the current surface exposes them; fallback otherwise |
| Other Agent Skills-compatible host | Copy the canonical skill bundle | Native delegation when clearly exposed; fallback otherwise |

Installation compatibility and runtime subagent capability are separate. The report describes what actually ran.

## Optional helper commands

These commands run Git and bounded deterministic read-only analysis. They do not call a model:

```powershell
review-agent doctor
review-agent plan
review-agent plan --staged --format json
review-agent context --base main --head HEAD --format json --request "Review using max 7 specialists"
review-agent context --format prompt --role security
```

`review-agent consolidate --plan <plan.json> --result <result.json>` validates reviewer results, removes unsupported/off-diff findings, deduplicates likely matches, and renders the final report. The `external` command calls the explicitly authorized model target and is normally owned by the skill. It accepts either the direct `--role` plus Git-scope form shown above or the lower-level `--assignment <file>` form.

The JSON context includes `impact_context.changed_identifiers` and bounded `impact_context.affected_locations`. Those locations are unverified candidates, not findings. A published finding still uses a changed file as its primary root cause and may attach verified unchanged consumers through its own `affected_locations` array.

Check an optional adapter without sending repository context:

```powershell
review-agent doctor --external claude
review-agent doctor --external openrouter:anthropic/claude-sonnet-4
```

## Project policy

No configuration file is required. To create one:

```powershell
review-agent init
```

Version 2 has no provider list and no external default:

```json
{
  "version": 2,
  "review": {
    "max_diff_chars": 200000,
    "max_reviewers": 4
  },
  "roles": {
    "include": [],
    "exclude": []
  },
  "external": {
    "timeout_seconds": 300
  }
}
```

Version 1 was an unreleased provider-first prototype. The helper rejects it with migration guidance instead of carrying forward automatic Codex-plus-Claude behavior.

The configured maximum can be any value from 1 through 12. It is still a cap: deterministic change signals decide which relevant roles actually run. `roles.include` can force a role into the candidate roster and `roles.exclude` can forbid one.

## Repository review memory

Review Agent can give specialists bounded project-specific history without adding another service:

```text
.review-agent/invariants.md
.review-agent/incidents/2026-08-orders-retry.md
```

Invariants are always included when present. Incident notes are selected deterministically from changed paths, risk signals, and identifiers in the bounded diff, with Unicode-aware matching, at most three notes, and strict per-document/total character limits. Symlinked context is ignored. This material is always labeled untrusted: it may strengthen a failure scenario, but it cannot change the review workflow or replace changed-code evidence.

## Report semantics

Every report names its real execution mode:

- `parent-only-limited`: no specialist was justified.
- `native-multi-agent`: isolated native specialists ran.
- `single-agent-fallback`: the current parent applied roles sequentially.
- `hybrid-parent-external`: a limited parent review plus explicit external review.
- `hybrid-native-external`: native specialists plus explicit external review.
- `hybrid-fallback-external`: sequential fallback plus explicit external review.

Raw reviewer findings are never published directly. The parent must reopen the changed root cause and every claimed affected location, then verify the literal paths, locations, evidence, trigger, affected behavior, relationship, and change causality before deduplication and severity calibration. Agreement counts only when distinct context IDs support the same verified defect.

See [How it works](docs/how-it-works.md) for the execution and security contracts.

## Development

The core package uses Python 3.10+ and the standard library.

```powershell
$env:PYTHONPATH = "src"
python scripts/sync_plugin_skill.py --check
python -m unittest discover -s tests -v
python -m pip wheel . --no-deps --wheel-dir dist
```

`src/review_agent/skill_template/review-agent` is the canonical skill source. After changing it, run `python scripts/sync_plugin_skill.py` to refresh the marketplace copy; tests reject drift between them.

Routine tests use fake hosts, fake CLI processes, and a fake OpenRouter transport. They do not invoke paid models.

## Troubleshooting helper installation

If an older Ubuntu Python packaging stack previously installed `UNKNOWN-0.0.0`, remove that placeholder and reinstall without its cached wheel:

```bash
python3 -m pip uninstall -y UNKNOWN
python3 -m pip install --user --no-cache-dir --force-reinstall "git+https://github.com/VisanAlex/review-agent.git"
export PATH="$HOME/.local/bin:$PATH"
review-agent --version
```

The expected result is `review-agent 0.5.0` or newer. The package retains a legacy setuptools metadata fallback for older Ubuntu Python packaging stacks while using `pyproject.toml` on modern installers.
