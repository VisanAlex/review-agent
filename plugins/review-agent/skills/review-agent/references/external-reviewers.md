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

Do not send repository context before parsing valid authorization. After authorization, echo the normalized external targets and state that bounded review context will be sent.

When `review-agent` is callable, external dispatch is not discretionary. Choose the most relevant selected role, or `correctness` when no specialist was selected, and execute exactly one helper command for all authorized targets:

```text
review-agent external --repo <repository-root> <scope-flags> --request <exact-user-invocation> --current-host <invoking-host> --role <selected-role>
```

Map the review scope to no scope flags for the working tree, `--staged` for staged changes, or `--base <ref> --head <ref>` for a comparison. Pass dynamic values as separate process arguments when the host supports argument arrays; otherwise quote them for the active shell. The helper collects the bounded context, removes the invoking host from the target set, calls only registered adapters, and prints a schema-versioned result envelope.

Use the helper result as the sole authority for external coverage:

- Parse every item in `reviewer_runs` and preserve its exact `target`, `origin`, `status`, and redacted `error`.
- Never claim that an adapter is unregistered, unavailable, unauthenticated, timed out, or invalid unless the helper process or its structured result says so.
- If the helper cannot be started or exits before returning structured JSON, report the concrete redacted process error. Do not replace that error with an inference about IDE, extension, terminal, PATH, or host capabilities.
- Do not substitute a different host or model. Do not retry through an unrequested target.

If the helper is absent, record each authorized target as unavailable because `review-agent` could not be started. Native or fallback review still proceeds. External adapters are the one feature for which the optional helper is required.

Require `OPENROUTER_API_KEY` for OpenRouter and an explicit model ID. Do not place credentials in repository configuration or output. Keep temporary handoff data private and ephemeral.

Validate every external response with the shared reviewer contract. Record unavailable, unauthenticated, timed-out, invalid, and interrupted targets as coverage errors with secrets redacted. Preserve all successful native, fallback, and external findings.
