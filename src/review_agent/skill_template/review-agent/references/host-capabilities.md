# Host capabilities

Use the invoking host's already-exposed native delegation primitive. Do not probe for other installed agent CLIs and do not change model or provider configuration.

| Invoking host | Native behavior |
|---|---|
| Codex | Dispatch selected Codex subagents. Keep the parent as sole dispatcher and let subagents inherit the current session configuration. |
| Claude Code, including Amazon Bedrock | Dispatch selected Claude Code subagents using the **Agent tool** with `run_in_background: false` — this is the native delegation primitive. It is always available; do not treat it as optional or fall back to sequential without attempting it. Subagents inherit the active model/provider configuration; do not ask them to create subagents. |
| Kiro | Use its native subagent/task capability only when it is exposed in the current surface. |
| Cursor | Use its native subagent capability only when it is exposed in the current surface. |
| Another Agent Skills-compatible host | Use native isolated delegation only when the current interface clearly exposes it. |

## Capability decision

1. Check the tools/capabilities already available to the current parent.
2. In Claude Code, the Agent tool is always present — use it for every selected reviewer. Sequential fallback is only valid when the user denied agent dispatch or the tool is genuinely absent.
3. Treat successful isolated dispatch as `native-subagent` work.
4. If delegation is missing, denied, or fails before useful output, continue sequentially in the parent as `current-agent-fallback` work.

Installation compatibility does not prove native isolation. Label the actual execution, not the hoped-for capability. Parallel execution is optional; isolated contexts and parent-owned dispatch are required.

## Browser capability

Browser verification uses only a review-only browser or browser-test capability already exposed to the current host. This is separate from specialist delegation.

| Invoking host | Browser behavior |
|---|---|
| Codex | Use an exposed Codex browser tool or browser-testing skill. |
| Claude Code | Use an exposed browser tool, MCP integration, or optional skill such as `/ce-test-browser`. Compound Engineering and `/ce-test-browser` are examples, not required dependencies. |
| Kiro or Cursor | Use an exposed browser or browser-test capability only when it is available in the current session. |
| Another Agent Skills-compatible host | Follow the same outcome and safety contract with any already-exposed local browser capability. |

If the current host exposes no suitable browser capability, record browser coverage as `unavailable`. Never launch another installed agent host to obtain browser coverage, and never install a browser package or extension during a review.
