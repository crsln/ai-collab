# ai-collab

## IMPORTANT: What This Is

ai-collab is an **MCP server** that Claude Code connects to. It is **NOT** a standalone CLI tool.

Two MCP servers exist — both MUST be configured for the system to work:

1. **`mcp_server.py`** → Claude Code's MCP server (configured in `.mcp.json` or Claude settings)
   - Provides: `ask_agent`, `ask_agents`, `list_agents`, and all `bs_*` brainstorm tools
2. **`brainstorm_server.py`** → Agent-facing MCP server (configured in each agent's MCP settings)
   - Provides: `bs_get_onboarding`, `bs_save_response`, `bs_respond_to_feedback`, etc.
   - Each agent (Copilot, Gemini, Codex) needs this configured separately

The `providers/` directory contains **subprocess adapters** that `mcp_server.py` uses internally
to dispatch prompts to agent CLIs — they are NOT standalone CLI tools.

Without MCP configuration, this project does nothing. Run `ai-collab init` to generate all configs.

## First-Time Setup

```bash
# 1. Install
pip install -e .

# 2. Run the setup wizard — detects installed CLIs, generates config, seeds DB
ai-collab init

# 3. The wizard outputs MCP config snippets. Copy them to:
#    - Claude Code: .mcp.json (project-level) or ~/.claude/settings.json (global)
#    - Copilot: ~/.copilot/mcp-config.json
#    - Gemini: .gemini/settings.json (project-level)
#    - Codex: ~/.codex/config.toml

# 4. Restart Claude Code to pick up the new MCP server

# 5. Test — this should show your configured agents:
#    Use list_agents()
```

**Windows note:** MCP configs must use the full path to `python.exe`, not just `"python"`.
The setup wizard generates correct absolute paths automatically.

## Architecture

```
User asks Claude Code to brainstorm
  │
  ├── Claude Code connects to mcp_server.py (via MCP)
  │     ├── ask_agent / ask_agents → dispatches via providers/ (subprocess)
  │     ├── bs_run_round → dispatches all agents in parallel
  │     └── bs_* tools → reads/writes brainstorm.db
  │
  ├── brainstorm.db (shared SQLite database — single source of truth)
  │
  └── Each agent (Copilot/Gemini/Codex) connects to brainstorm_server.py (via MCP)
        ├── bs_get_onboarding → discovers task, role, workflow
        ├── bs_save_response → saves analysis
        └── bs_respond_to_feedback / bs_batch_respond → votes on findings
```

Key files:
- `brainstorm_tools.py` — Shared tool handlers (both servers import from here)
- `brainstorm_service.py` — Orchestration logic (onboarding builder, phase detection, gates)
- `brainstorm_db.py` — SQLite CRUD/DDL
- `config.py` — TOML config loading + agent registry (`BUILTIN_AGENTS`)
- `brainstorm_seeds.py` — Default data (agent definitions, workflows, tool guides, roles)

## Configuration

Agents are defined in `ai-collab.toml`:

```toml
[agents.copilot]
command = "copilot"
args = ["-p", "{prompt}", "--allow-all"]
display_name = "GitHub Copilot"

[agents.gemini]
command = "gemini"
args = ["-p", "{prompt}", "--yolo"]
display_name = "Google Gemini"
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
| `bs_new_session(topic, project?)` | Start session |
| `bs_set_context(session_id, context)` | Attach codebase context |
| `bs_run_round(session_id, objective, question, cwd?)` | Run full round with all agents (FAIL-FAST) |
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

### Sync Barrier (FAIL-FAST)

`bs_run_round` now registers participants and enforces completion gates:
- All agents must respond with valid content before the round succeeds
- If ANY agent fails, the round returns `[ROUND FAILED]` and flow STOPS
- Use `bs_retry_agent` to retry failed agents, then `bs_check_round_status` to verify
- Phase 2 requires ALL agents to vote on ALL feedback items before consolidation
- No graceful degradation — fix the problem, don't skip agents

### Self-Describing DB (Pure ID-Based Prompts)

Agent definitions, workflow templates, tool guides, and role templates are stored in the DB.
Agents call `bs_get_onboarding(agent_name)` to discover everything they need.

Agent dispatch prompts are **pure ID-based** (~150 chars): just the agent name, session/round IDs,
and a bootstrap instruction to call `bs_get_onboarding()`. The question itself is stored in the
`rounds` table and delivered to agents as part of the onboarding response (`task.question`).

Seed defaults: `python brainstorm_cli.py seed-defaults`

### Role Library & Role Rotation

Reusable role templates live in the `role_library` table. All roles are globally assignable
to ANY agent — roles are NOT fixed to specific models.

**Role rotation pattern:** Each round, ALL agents get the SAME role. Rotate the role each
round so every model covers every perspective. Model diversity on the same role produces
different takes; role rotation ensures comprehensive coverage.

Use `bs_suggest_roles(topic)` to get a ranked list, then apply one role per round via
`bs_apply_role(session_id, agent_name, slug)`. Templates track `usage_count` and
`last_used_at` for optimization.

### Agent MCP Configuration

Each agent needs the brainstorm MCP server configured. Example for Gemini:

```json
{
  "mcpServers": {
    "brainstorm": {
      "command": "python",
      "args": ["<path-to>/brainstorm_server.py"]
    }
  }
}
```

Run `ai-collab init` to auto-generate these config snippets.

### Testing Policy

**NEVER use workarounds** when testing the brainstorm system. Always use the actual system
tools and flows (`bs_run_round`, MCP tools, `/multi-ai-brainstorm` skill). If something fails
during testing, **fix the system** — don't work around it. Manual MCP tool calls for inspection
are OK, but the orchestration flow must run through the real system without manual intervention.
