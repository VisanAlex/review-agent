# Host capabilities

Use the invoking host's already-exposed native delegation primitive. Do not probe for other installed agent CLIs and do not change model or provider configuration.

| Invoking host | Native behavior |
|---|---|
| Codex | Dispatch selected Codex subagents. Keep the parent as sole dispatcher and let subagents inherit the current session configuration. |
| Claude Code, including Amazon Bedrock | Dispatch selected Claude Code subagents. They inherit the active Claude Code model/provider configuration; do not ask them to create subagents. |
| Kiro | Use its native subagent/task capability only when it is exposed in the current surface. |
| Cursor | Use its native subagent capability only when it is exposed in the current surface. |
| Another Agent Skills-compatible host | Use native isolated delegation only when the current interface clearly exposes it. |

## Capability decision

1. Check the tools/capabilities already available to the current parent.
2. Attempt one-level native dispatch for the selected roster when supported.
3. Treat successful isolated dispatch as `native-subagent` work.
4. If delegation is missing, denied, or fails before useful output, continue sequentially in the parent as `current-agent-fallback` work.

Installation compatibility does not prove native isolation. Label the actual execution, not the hoped-for capability. Parallel execution is optional; isolated contexts and parent-owned dispatch are required.
