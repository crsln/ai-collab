# Multi-AI Collaboration

MCP server for delegating questions to GitHub Copilot and Google Gemini,
plus structured brainstorming sessions backed by SQLite.

## Architecture

```
Claude Code (orchestrator)
  ├── mcp_server.py — MCP tools for quick delegation (ask_copilot, ask_gemini, ask_both)
  ├── brainstorm_server.py — MCP server for Copilot/Gemini (they run their own instances)
  ├── brainstorm_db.py — SQLite DB logic (.data/brainstorm.db)
  ├── brainstorm_cli.py — CLI for Claude to read/write DB directly
  └── atlas (cc-memory-ollama) — shared knowledge graph (all 3 agents have access)
```

## Delegation Tools

- `ask_copilot(question)` — runs GitHub Copilot CLI (quick questions only)
- `ask_gemini(question)` — runs Google Gemini CLI (quick questions only)
- `ask_both(question)` — runs both in parallel (quick questions only)

**IMPORTANT**: For brainstorming, do NOT use these MCP tools — they drop connections on long calls.
Use the Agent tool to dispatch CLI commands instead.

## Brainstorming (use `/multi-ai-brainstorm` skill for full workflow)

### Agent Dispatch

Agents are dispatched via **Claude Code Agent tool subagents**, NOT via mcp__ai-collab MCP tools:

```
Agent(name="copilot-task", prompt="Run: copilot --allow-all-tools -p '...'", mode="bypassPermissions")
Agent(name="gemini-task", prompt="Run: gemini --yolo -p '...'", mode="bypassPermissions")
```

Launch both in the same message for parallel execution.

### 3-Phase Process

1. **Phase 1 — Independent Analysis**: Each agent analyzes independently (parallel via Agent tool)
2. **Phase 2 — Deliberation**: Agents read feedback records, save verdicts via MCP tools (accept/reject/modify). Multiple rounds until convergence. Max 5 rounds, then 2-1 majority wins.
3. **Phase 3 — Consolidation**: Claude synthesizes final consensus

### Key tools

| Tool | Purpose | Notes |
|------|---------|-------|
| `bs_new_session(topic, project?)` | Start session | |
| `bs_set_context(session_id, context)` | Attach codebase context | |
| `bs_new_round(session_id, objective)` | Create round | |
| `bs_create_feedback(...)` | Create feedback items | After Phase 1 |
| `bs_list_feedback(session_id)` | List items | Agents use via MCP |
| `bs_get_feedback(item_id)` | Read item + verdicts | Agents use via MCP |
| `bs_respond_to_feedback(...)` | Save verdict | Agents save independently |
| `bs_update_feedback_status(item_id, status)` | Mark converged | Claude only |
| `bs_save_consensus(session_id, content)` | Final document | |
| `bs_complete_session(session_id)` | Close session | |
| `bs_get_onboarding(agent_name, session_id?)` | Full agent onboarding | Primary entry point for agents |
| `bs_get_briefing(session_id, agent_name)` | Session briefing | Falls back to default_role |
| `bs_get_workflow(name?)` | Read workflow template | |
| `bs_list_tool_guides(phase?)` | List tool guides | |
| `bs_set_agent_definition(...)` | Create/update agent def | Admin |
| `bs_set_workflow_template(...)` | Create/update workflow | Admin |
| `bs_set_tool_guide(...)` | Create/update tool guide | Admin |

### Self-Describing DB

The brainstorm DB is self-describing. Agent definitions, workflow templates, and tool guides are stored in global tables. Seed with:

```bash
python brainstorm_cli.py seed-defaults
```

Agents call `bs_get_onboarding(agent_name)` to discover everything they need. Dispatch prompts can be minimal — just session/round IDs and "call bs_get_onboarding('copilot') first."

### Agent MCP Configuration

Both Copilot and Gemini have these MCP servers configured:
- **brainstorm** → `python E:/GitHub/ai-collab/brainstorm_server.py` (feedback tools)
- **atlas** → `atlas.exe` (persistent knowledge graph)

Copilot config: `~/.copilot/mcp-config.json`
Gemini config: `.gemini/settings.json` (project-level)

### CLI (for Claude's direct DB access)

```bash
python E:/GitHub/ai-collab/brainstorm_cli.py <command> [args]
```

Commands: `list-sessions`, `session-history`, `get-feedback`, `respond-feedback`, `save-consensus`

## When to Delegate

**ask_copilot**: shell commands, git, GitHub CLI, quick snippets
**ask_gemini**: code generation, research, alternative approaches, docs
**Handle yourself**: architecture decisions, complex reasoning, synthesis
**Brainstorm**: use `/multi-ai-brainstorm` for structured multi-agent deliberation
