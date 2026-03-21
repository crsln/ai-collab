# ai-collab

Multi-AI collaboration MCP server for Claude Code. Orchestrate brainstorming sessions across any combination of AI CLI agents — GitHub Copilot, Google Gemini, OpenAI Codex, or any custom CLI tool.

## What it does

ai-collab lets Claude Code delegate questions to other AI agents and run structured multi-agent brainstorming sessions with a 3-phase workflow:

1. **Independent Analysis** — Each agent analyzes the topic independently
2. **Deliberation** — Agents review findings and vote (accept/reject/modify) with evidence
3. **Consolidation** — Claude synthesizes a final consensus

All communication happens through a shared SQLite database. Each agent has its own MCP server instance for reading/writing records.

## Quick Start

### Prerequisites

- Python 3.10+
- Claude Code CLI
- At least one AI CLI tool installed:
  - [GitHub Copilot CLI](https://github.com/github/copilot-cli) (`copilot`)
  - [Google Gemini CLI](https://github.com/google/gemini-cli) (`gemini`)
  - [OpenAI Codex CLI](https://github.com/openai/codex) (`codex`)
  - Or any other CLI-based AI tool

### Install

```bash
pip install ai-collab
```

Or for development:

```bash
git clone https://github.com/crsln/ai-collab.git
cd ai-collab
pip install -e .
```

### Setup

Run the interactive setup wizard:

```bash
ai-collab init
```

This will:
1. Scan your PATH for installed AI CLIs
2. Let you choose which agents to enable
3. Generate `ai-collab.toml` with your agent configuration
4. Output MCP config snippets for Claude Code and each agent
5. Seed the database with workflow templates and tool guides

### Configure Claude Code

Add to your Claude Code MCP settings (`.claude/settings.json` or global settings):

```json
{
  "mcpServers": {
    "ai-collab": {
      "command": "python",
      "args": ["/path/to/ai-collab/mcp_server.py"]
    }
  }
}
```

> **Windows:** Use the full path to your Python executable instead of `"python"`:
> ```json
> "command": "C:/Users/YOU/AppData/Local/Programs/Python/Python312/python.exe"
> ```
> Use forward slashes in all paths. The setup wizard (`ai-collab init`) generates the correct paths automatically.

### Configure Agent MCP Access

Each AI agent needs its own MCP server instance for brainstorm tools. The setup wizard generates config snippets for each agent. Examples:

**GitHub Copilot** (`~/.copilot/mcp-config.json`):
```json
{
  "mcpServers": {
    "brainstorm": {
      "command": "python",
      "args": ["/path/to/ai-collab/brainstorm_server.py"]
    }
  }
}
```

**Google Gemini** (`.gemini/settings.json` in your project):
```json
{
  "mcpServers": {
    "brainstorm": {
      "command": "python",
      "args": ["/path/to/ai-collab/brainstorm_server.py"]
    }
  }
}
```

**OpenAI Codex** (`~/.codex/config.toml`):
```toml
[mcp_servers.brainstorm]
command = "python"
args = ["/path/to/ai-collab/brainstorm_server.py"]
```

## Usage

### Quick Delegation

Ask a single agent a question:
```
Use ask_agent("copilot", "How do I rebase interactively?")
```

Ask a single agent in a specific project directory:
```
Use ask_agent("gemini", "Analyze the architecture", cwd="/path/to/project")
```

Ask all configured agents in parallel:
```
Use ask_agents("What's the best approach for caching in this codebase?", cwd="/path/to/project")
```

List available agents:
```
Use list_agents()
```

### Structured Brainstorming

Use the `/multi-ai-brainstorm` skill for full 3-phase brainstorming sessions. See the skill documentation for the complete workflow.

## Architecture

```
Claude Code (orchestrator)
  ├── mcp_server.py — MCP tools: ask_agent, list_agents, ask_agents, bs_* (brainstorm)
  ├── brainstorm_server.py — Agent-facing MCP server (brainstorm tools)
  ├── brainstorm_tools.py — Shared tool handlers (used by both servers)
  ├── brainstorm_service.py — Orchestration logic: onboarding, phase detection, gates
  ├── brainstorm_db.py — SQLite persistence (WAL mode, CRUD/DDL only)
  ├── brainstorm_seeds.py — Default agent definitions, workflow templates, tool guides
  ├── config.py — TOML config loading + agent registry (BUILTIN_AGENTS)
  ├── dashboard.py + dashboard.html — Web dashboard for session visualization
  └── providers/
       ├── generic.py — GenericCLIProvider (subprocess dispatch with error handling)
       ├── copilot.py — Copilot-specific output parsing
       ├── codex.py — Codex-specific provider (exec mode)
       └── gemini.py — Gemini-specific output parsing
```

## Configuration

### ai-collab.toml

Define your agents in `ai-collab.toml`:

```toml
[settings]
db_path = ".data/brainstorm.db"
default_timeout = 900

[agents.copilot]
enabled = true
command = "copilot"
args = ["-p", "{prompt}", "--allow-all"]
display_name = "GitHub Copilot"
description = "Code analysis, git operations, verification"

[agents.gemini]
enabled = true
command = "gemini"
args = ["-p", "{prompt}", "--yolo"]
display_name = "Google Gemini"
description = "Architecture, research, alternatives"
```

See `ai-collab.toml.example` for more examples including Codex, Aider, and custom agents.

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `BRAINSTORM_DB` | `.data/brainstorm.db` | Path to SQLite database |
| `AI_COLLAB_SAVE_RESPONSES` | `false` | Save agent responses to files |
| `AI_COLLAB_RESPONSES_DIR` | `.brainstorm/` | Where to save responses |
| `AI_COLLAB_CONFIG` | `ai-collab.toml` | Path to config file |

## Self-Describing Database

The brainstorm database is self-describing. Agent definitions, workflow templates, tool guides, and role templates are stored in global tables. Agents call `bs_get_onboarding(agent_name)` to discover everything they need — including their task for the current round.

### Pure ID-Based Prompts

Agent dispatch prompts contain only IDs (~150 chars): agent name, session/round IDs, and a bootstrap instruction. The question is stored in the `rounds` table and retrieved by agents via `bs_get_onboarding()` (in the `task` field). This eliminates prompt bloat and Windows stdin piping issues.

Seed defaults:
```bash
python brainstorm_cli.py seed-defaults
```

### Role Library

Reusable role templates let you assign specialized behaviors to agents without rewriting instructions each session:

```
# List available roles
Use bs_list_roles()
Use bs_list_roles(agent_name="copilot")  # includes copilot-specific + generic roles
Use bs_list_roles(tag="security")         # filter by tag

# Apply to a session
Use bs_apply_role(session_id, "copilot", "security-reviewer")
Use bs_apply_role(session_id, "gemini", "architecture-analyst")

# Refine over time
Use bs_update_role("security-reviewer", notes="Works best when combined with code-verifier on copilot")

# Create custom roles
Use bs_create_role(slug="api-reviewer", display_name="API Reviewer", ...)
```

8 seed templates included: `code-reviewer`, `security-reviewer`, `architecture-analyst`, `performance-analyst`, `ux-design-critic`, `devil-advocate`, `copilot-code-verifier`, `gemini-research-analyst`.

Templates track usage counts and can be agent-specific or generic (any agent).

## License

MIT
