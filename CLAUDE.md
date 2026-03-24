# ai-collab

## IMPORTANT: What This Is

ai-collab is an **MCP server** that Claude Code connects to. It is **NOT** a standalone CLI tool.

A single Rust binary (`ai-collab-server`) provides two MCP server modes:

1. **`ai-collab-server serve`** → Orchestrator MCP server (configured in `.mcp.json` or Claude settings)
   - Provides: `ask_agent`, `ask_agents`, `list_agents`, and all `bs_*` brainstorm tools
2. **`ai-collab-server agent-serve`** → Agent-facing MCP server (configured in each agent's MCP settings)
   - Provides: `bs_get_onboarding`, `bs_save_response`, `bs_respond_to_feedback`, etc.
   - Each agent (Copilot, Gemini, Codex) needs this configured separately

Without MCP configuration, this project does nothing.

## First-Time Setup

```bash
# 1. Build and install the binary (adds to PATH via cargo bin)
cargo install --path crates/ai-collab-server

# 2. Copy and edit the agent config
cp ai-collab.toml.example ai-collab.toml

# 3. Seed the database with defaults
ai-collab-server seed-defaults --db .data/brainstorm.db

# 4. Add MCP config snippets to:
#    - Claude Code: .mcp.json (project-level) or ~/.claude/settings.json (global)
#    - Claude subagents: .claude/agent-mcp.json
#    - Copilot: ~/.copilot/mcp-config.json
#    - Gemini: .gemini/settings.json (project-level)
#    - Codex: ~/.codex/config.toml

# 5. Restart Claude Code to pick up the new MCP server

# 6. Test — this should show your configured agents:
#    Use list_agents()
```

## Architecture

```
User asks Claude Code to brainstorm
  │
  ├── Claude Code connects to ai-collab-server serve (via MCP)
  │     ├── ask_agent / ask_agents → dispatches via subprocess providers
  │     ├── bs_run_round → dispatches all agents in parallel
  │     ├── bs_* tools → reads/writes brainstorm.db
  │     └── auto-validates responses (heuristic + background Haiku)
  │
  ├── brainstorm.db (shared SQLite database — single source of truth)
  │
  └── Each agent (Copilot/Gemini/Codex) connects to ai-collab-server agent-serve (via MCP)
        ├── bs_get_onboarding → discovers task, role, workflow
        ├── bs_save_response → saves analysis (auto-validated)
        └── bs_respond_to_feedback / bs_batch_respond → votes on findings
```

Key crates (under `crates/`):
- `ai-collab-server` — Two MCP servers (orchestrator + agent-facing) + validator dispatch
- `ai-collab-core` — Enums, models, validation heuristics, traits
- `ai-collab-db` — SQLite persistence (WAL mode, CRUD/DDL)
- `ai-collab-config` — TOML config loading + agent registry
- `ai-collab-provider` — Subprocess dispatch for agents

## Configuration

Agents are defined in `ai-collab.toml`:

```toml
[agents.copilot]
command = "copilot"
args = ["-p", "{prompt}", "--allow-all"]
display_name = "GitHub Copilot"
max_auto_retries = 2  # Copilot is more timeout-prone

[agents.gemini]
command = "gemini"
args = ["-p", "{prompt}", "--yolo"]
display_name = "Google Gemini"
# max_auto_retries defaults to 1
```

Add any CLI-based AI tool as an agent. See `ai-collab.toml.example` for more examples.

## Delegation Tools

- `ask_agent(agent_name, question, cwd?)` — ask a specific agent (runs in given cwd)
- `ask_agents(question, cwd?)` — ask all enabled agents in parallel
- `list_agents()` — show configured agents and capabilities

**IMPORTANT**: For brainstorming, do NOT use these MCP tools — they drop connections on long calls.
Use the Agent tool to dispatch CLI commands instead, or use `bs_run_round`.

### Agent Dispatch & cwd

The `cwd` parameter is passed through to the subprocess so agents run in the correct project
directory. This is critical for brainstorming — without it, agents analyze whichever directory
the MCP server process happens to be in, not the target project.

## Brainstorming (use `/multi-ai-brainstorm` skill for full workflow)

### 3-Phase Process

1. **Phase 1 — Independent Analysis**: ALL agents (including Claude as 3rd peer) analyze independently in parallel
2. **Phase 2 — Deliberation**: ALL agents (including Claude as 3rd peer) review feedback records, save verdicts via MCP tools in parallel
3. **Phase 3 — Consolidation**: Orchestrator synthesizes final consensus

**IMPORTANT**: Claude is always dispatched as a parallel Agent subagent alongside external agents.
The orchestrator NEVER submits responses or verdicts inline — its only job is setup, dispatch,
feedback extraction, and convergence checking.

### Key tools

| Tool | Purpose |
|------|---------|
| `bs_new_session(topic, project?, mode?)` | Start session (mode: quick/standard/deep) |
| `bs_set_context(session_id, context)` | Attach codebase context |
| `bs_run_round(session_id, objective, question, cwd?, gate_mode?)` | Run round (gate_mode: strict/quorum/best_effort) |
| `bs_get_contested_items(session_id)` | Get contested feedback with all dissenting verdicts |
| `bs_check_round_status(round_id)` | Check round completion gate |
| `bs_check_feedback_status(round_id, session_id)` | Check feedback vote completeness |
| `bs_retry_agent(round_id, agent_name, cwd?)` | Retry a failed agent |
| `bs_create_feedback(...)` | Create feedback items (after Phase 1) |
| `bs_respond_to_feedback(...)` | Save verdict |
| `bs_save_consensus(session_id, content)` | Final document |
| `bs_get_onboarding(agent_name, session_id?)` | Agent onboarding entry point |
| `bs_create_role(slug, display_name, ...)` | Create reusable role template |
| `bs_list_roles(agent_name?, tag?)` | List role templates from library |
| `bs_apply_role(session_id, agent_name, slug)` | Apply role template to session |
| `bs_update_role(slug, ...)` | Refine a role template |

### Session Modes

`bs_new_session` accepts a `mode` parameter:
- **`quick`**: Single round, no deliberation — for simple questions
- **`standard`** (default): 2-phase (analysis + consolidation, or with deliberation if needed)
- **`deep`**: Full 3-phase with extended deliberation — for complex architectural decisions

### Sync Barrier & Gate Modes

`bs_run_round` supports configurable gate modes via `gate_mode` parameter:
- **`strict`** (default): All agents must succeed — if ANY fails, returns `[ROUND FAILED]`
- **`quorum`**: Majority must succeed (2/3) — returns `partial_success` if some fail
- **`best_effort`**: Any single success = proceed — maximum resilience

Default remains `strict` for backward compatibility. Use `quorum` for production resilience.
- Use `bs_retry_agent` to retry failed agents, then `bs_check_round_status` to verify
- Phase 2 requires ALL agents to vote on ALL feedback items before consolidation
- Agents auto-retry once on transient failures (configurable via `max_auto_retries` in ai-collab.toml)

### Verdict Quality Gates

- Verdict reasoning must be at least 50 characters (prevents rubber-stamp "I agree" voting)
- Verdict must be one of: `accept`, `reject`, `modify`, `abstain`
- Abstain votes are excluded from the effective total in auto-resolve (abstain = truly "no opinion")

### Self-Describing DB (Pure ID-Based Prompts)

Agent definitions, workflow templates, tool guides, and role templates are stored in the DB.
Agents call `bs_get_onboarding(agent_name)` to discover everything they need.

Agent dispatch prompts include the session topic + urgency hint + onboarding instruction.
The question itself is stored in the `rounds` table and delivered via `bs_get_onboarding()`.

Seed defaults: `ai-collab-server seed-defaults --db .data/brainstorm.db`

### Role Library & Role Rotation

Reusable role templates live in the `role_library` table. All roles are globally assignable
to ANY agent — roles are NOT fixed to specific models.

**Hybrid role rotation pattern (recommended):** Round 1: each agent gets a DIFFERENT role
(maximizes perspective diversity — 3 agents = 3 perspectives per round). Round 2: roles rotate
for cross-validation. This yields up to 9 perspectives across 3 rounds.

**Same-role pattern (alternative):** Each round, ALL agents get the SAME role. Rotate the role
each round. Model diversity on the same role produces different takes.

Use `bs_suggest_roles(topic)` to get a ranked list (TF-IDF scoring with logarithmic usage
penalty), then apply roles via `bs_apply_role(session_id, agent_name, slug)`.
Templates track `usage_count` and `last_used_at` for optimization.

### Agent MCP Configuration

Each agent needs the agent-facing MCP server configured. Example:

```json
{
  "mcpServers": {
    "brainstorm": {
      "command": "ai-collab-server",
      "args": ["agent-serve", "--db", ".data/brainstorm.db"]
    }
  }
}
```

### Testing Policy

**NEVER use workarounds** when testing the brainstorm system. Always use the actual system
tools and flows (`bs_run_round`, MCP tools, `/multi-ai-brainstorm` skill). If something fails
during testing, **fix the system** — don't work around it. Manual MCP tool calls for inspection
are OK, but the orchestration flow must run through the real system without manual intervention.
