# Reviewer roles

Choose the smallest useful roster. Select a role only when paths, file types, or changed behavior provide a concrete signal. Record selection and skip reasons. Use at most the configured limit, or four when none is configured.

| Role | Select for | Focus |
|---|---|---|
| `correctness` | Behavior-bearing production code | Broken logic, invalid states, boundary cases, and regressions introduced by the change |
| `testing` | Production behavior without obviously sufficient regression coverage | Missing tests for realistic failure paths and weak assertions that would let the defect escape |
| `security` | Authentication, authorization, secrets, permissions, parsing, or untrusted input | Exploitable access, injection, data exposure, unsafe defaults, and validation gaps |
| `data-integrity` | Schemas, migrations, persistence, models, or transactions | Lossy conversion, invalid stored state, unsafe migrations, constraint gaps, and transaction boundaries |
| `api-compatibility` | Routes, schemas, protocols, exports, or public signatures | Breaking request/response, serialization, versioning, and caller contracts |
| `frontend-accessibility` | UI components, templates, styles, or interaction behavior | Broken states, keyboard/screen-reader access, semantic regressions, and user-flow failures |
| `concurrency-reliability` | Async code, queues, retries, jobs, locks, or failure handling | Races, duplicate work, hangs, retry storms, lost errors, and non-idempotent recovery |
| `performance` | Queries, caching, batching, bulk work, hot paths, or large loops | Unbounded work, excess I/O, query multiplication, memory growth, and cache errors |
| `architecture` | Dependency manifests, framework configuration, infrastructure, or structural refactors | Layer violations, dangerous coupling, lifecycle mistakes, and incompatible dependency choices |

## Selection rules

- Select `correctness` and usually `testing` for production behavior changes.
- Prefer security, data integrity, API compatibility, or concurrency ahead of lower-risk roles when the cap applies.
- Allow an explicit local policy to include or exclude roles, but never let it add an external target.
- Use an empty specialist roster for documentation-only or similarly low-risk changes unless the content changes executable policy or configuration.
- Give every dispatched reviewer exactly one role. Put secondary concerns in its exclusions rather than asking it to perform a general review.
