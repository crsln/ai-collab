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

- [Rust toolchain](https://rustup.rs/) (for building)
- Claude Code CLI
- At least one AI CLI tool installed:
  - [GitHub Copilot CLI](https://github.com/github/copilot-cli) (`copilot`)
  - [Google Gemini CLI](https://github.com/google/gemini-cli) (`gemini`)
  - [OpenAI Codex CLI](https://github.com/openai/codex) (`codex`)
  - Or any other CLI-based AI tool

### Install

```bash
cargo install --git https://github.com/crsln/ai-collab.git ai-collab-server
```

Or for development:

```bash
git clone https://github.com/crsln/ai-collab.git
cd ai-collab
cargo install --path crates/ai-collab-server
```

This puts `ai-collab-server` in your cargo bin directory (already in PATH).

### Setup

1. Copy the example config and enable your agents:

```bash
cp ai-collab.toml.example ai-collab.toml
# Edit ai-collab.toml — enable/disable agents as needed
```

2. Seed the database with default workflows, tool guides, and role templates:

```bash
ai-collab-server seed-defaults --db .data/brainstorm.db
```

### Configure Claude Code

Add to your project's `.mcp.json` (or global `~/.claude/settings.json`):

```json
{
  "mcpServers": {
    "ai-collab": {
      "command": "ai-collab-server",
      "args": ["serve", "--db", ".data/brainstorm.db"]
    }
  }
}
```

### Configure Agent MCP Access

Each AI agent needs the agent-facing MCP server for brainstorm tools.

**Claude subagents** (`.claude/agent-mcp.json`):
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

**GitHub Copilot** (`~/.copilot/mcp-config.json`):
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

**Google Gemini** (`.gemini/settings.json` in your project):
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

**OpenAI Codex** (`~/.codex/config.toml`):
```toml
[mcp_servers.brainstorm]
command = "ai-collab-server"
args = ["agent-serve", "--db", ".data/brainstorm.db"]
```

## Usage

### Quick Delegation

```
Use ask_agent("copilot", "How do I rebase interactively?")
Use ask_agent("gemini", "Analyze the architecture", cwd="/path/to/project")
Use ask_agents("What's the best approach for caching?", cwd="/path/to/project")
Use list_agents()
```

### Structured Brainstorming

Use the `/multi-ai-brainstorm` skill for full 3-phase brainstorming sessions.

## Architecture

A single Rust binary (`ai-collab-server`) provides two MCP server modes:

1. **`ai-collab-server serve`** — Orchestrator MCP server (Claude Code connects here)
2. **`ai-collab-server agent-serve`** — Agent-facing MCP server (each agent connects here)

### Data Flow

```
User asks Claude Code to brainstorm
  │
  ├── Claude Code ←──MCP──→ ai-collab-server serve (orchestrator tools)
  │     ├── ask_agent / ask_agents → subprocess dispatch via providers
  │     ├── bs_run_round → parallel agent dispatch with fail-fast gates
  │     └── bs_* tools → brainstorm.db read/write
  │
  ├── brainstorm.db (shared SQLite — single source of truth)
  │
  └── Each agent (Copilot/Gemini/Codex) ←──MCP──→ ai-collab-server agent-serve
        ├── bs_get_onboarding → discover task, role, workflow, prior work
        ├── bs_save_response → save analysis (auto-validated)
        └── bs_batch_respond → vote on all feedback items at once
```

### Crate Structure

```
ai-collab/
  ├── crates/
  │   ├── ai-collab-server    — Two MCP servers (orchestrator + agent-facing) + validator
  │   ├── ai-collab-core      — Enums, models, validation heuristics, traits
  │   ├── ai-collab-db        — SQLite persistence (WAL mode, CRUD/DDL)
  │   ├── ai-collab-config    — TOML config loading + agent registry
  │   └── ai-collab-provider  — Subprocess dispatch for agents
  ├── Cargo.toml              — Workspace root
  ├── ai-collab.toml.example  — Agent configuration template
  └── dashboard.html          — Web dashboard for session visualization
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

[agents.gemini]
enabled = true
command = "gemini"
args = ["-p", "{prompt}", "--yolo"]
display_name = "Google Gemini"
```

See `ai-collab.toml.example` for more examples including Codex, Aider, and custom agents.

### Self-Describing Database

The brainstorm database is self-describing. Agent definitions, workflow templates, tool guides, and role templates are stored in global tables. Agents call `bs_get_onboarding(agent_name)` to discover everything they need.

Agent dispatch prompts are pure ID-based (~150 chars): agent name, session/round IDs, and a bootstrap instruction. The question is stored in the `rounds` table and retrieved via `bs_get_onboarding()`.

### Role Library

Reusable role templates for assigning specialized behaviors to agents:

```
Use bs_list_roles()
Use bs_apply_role(session_id, "copilot", "security-reviewer")
Use bs_create_role(slug="api-reviewer", display_name="API Reviewer", ...)
```

8 seed templates included: `code-reviewer`, `security-reviewer`, `architecture-analyst`, `performance-analyst`, `ux-design-critic`, `devil-advocate`, `copilot-code-verifier`, `gemini-research-analyst`.

## License

MIT
