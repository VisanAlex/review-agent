# External reviewers

External review is optional. The current host remains the only default.

## Authorization grammar

Authorize targets only when the user's review invocation explicitly includes `with` followed by one or more supported targets:

- `with claude`
- `with codex`
- `with openrouter:<model-id>`
- a comma-separated list after `with`, such as `with claude, openrouter:openai/gpt-5.4`

Normalize case for target names, preserve the OpenRouter model ID, reject duplicate or unsupported targets, and remove the invoking host from the external set. A mere mention that Codex, Claude, OpenRouter, Kiro, or Cursor is installed or available is not consent. Do not infer consent from configuration, environment variables, credentials, prior runs, or tool discovery.

## Target types

- `codex` is an external host and its results use origin `external-host`.
- `claude` is an external host and its results use origin `external-host`.
- `openrouter:<model-id>` is an external provider and its results use origin `external-provider`.

Kiro and Cursor are not external adapters in the initial registry. They can still be the invoking host.

## Dispatch

Do not send repository context before parsing valid authorization. If the optional helper exposes the `external` command, give each named target one bounded reviewer assignment and use only its registered adapter. Never substitute a different host or model when a target is unavailable.

Require `OPENROUTER_API_KEY` for OpenRouter and an explicit model ID. Do not place credentials in repository configuration or output. Keep temporary handoff data private and ephemeral.

Validate every external response with the shared reviewer contract. Record unavailable, unauthenticated, timed-out, invalid, and interrupted targets as coverage errors with secrets redacted. Preserve all successful native, fallback, and external findings.
