# ai-collab

MCP server for multi-AI collaboration. Delegate questions to any configured AI CLI agent
and run structured brainstorming sessions backed by SQLite.

## Setup

```bash
# Install
pip install -e .

# Interactive setup — detects installed CLIs, generates config
ai-collab init

# Or manually: copy and edit the config template
cp ai-collab.toml.example ai-collab.toml
```

## Architecture

```
Claude Code (orchestrator)
  ├── mcp_server.py — MCP tools: ask_agent, list_agents, ask_agents, bs_* (brainstorm)
  ├── brainstorm_server.py — MCP server for agent instances (feedback/session tools)
  ├── brainstorm_db.py — SQLite DB logic (.data/brainstorm.db)
  ├── brainstorm_cli.py — CLI for direct DB access
  ├── config.py — TOML config loading + agent registry
  └── providers/ — CLI adapters (GenericCLIProvider + specialized subclasses)
```

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

1. **Phase 1 — Independent Analysis**: Each agent analyzes independently (parallel)
2. **Phase 2 — Deliberation**: Agents review feedback records, save verdicts via MCP tools
3. **Phase 3 — Consolidation**: Claude synthesizes final consensus

### Key tools

| Tool | Purpose |
|------|---------|
| `bs_new_session(topic, project?)` | Start session |
| `bs_set_context(session_id, context)` | Attach codebase context |
| `bs_run_round(session_id, objective, question, cwd?)` | Run full round with all agents |
| `bs_create_feedback(...)` | Create feedback items (after Phase 1) |
| `bs_respond_to_feedback(...)` | Save verdict |
| `bs_save_consensus(session_id, content)` | Final document |
| `bs_get_onboarding(agent_name, session_id?)` | Agent onboarding entry point |

### Self-Describing DB

Agent definitions, workflow templates, and tool guides are stored in the DB.
Agents call `bs_get_onboarding(agent_name)` to discover everything they need.

Seed defaults: `python brainstorm_cli.py seed-defaults`

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
