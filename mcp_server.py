"""MCP server exposing AI collaboration and brainstorming tools for Claude Code.

Provides:
- ask_agent / list_agents / ask_agents: Generic delegation to any configured AI CLI
- bs_*: Brainstorm session management tools (3-phase workflow)

Agents are configured in ai-collab.toml. See ai-collab.toml.example for format.
"""

import asyncio
import json
import logging
import os
import re
import shutil
import sys
import uuid
from datetime import datetime
from pathlib import Path

from mcp.server.fastmcp import Context, FastMCP

from brainstorm_db import BrainstormDB
from config import get_config, get_enabled_agents, AgentConfig
from providers import get_provider
from providers.errors import (
    ProviderExecution,
    ProviderTimeout,
    ProviderUnavailable,
)

log = logging.getLogger("ai-collab")
mcp = FastMCP("ai-collab")

_cfg = get_config()
_DB_PATH = Path(os.environ.get(
    "BRAINSTORM_DB",
    str(_cfg.db_path),
))
_db = BrainstormDB(_DB_PATH)

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]|\x1b\].*?\x07|\x1b[()][A-Z0-9]")
_SAVE_RESPONSES = os.environ.get("AI_COLLAB_SAVE_RESPONSES", "").lower() in ("1", "true", "yes")
_RESPONSES_DIR = Path(os.environ.get(
    "AI_COLLAB_RESPONSES_DIR",
    str(Path(__file__).parent / ".brainstorm"),
))


def _save_response(agent: str, question: str, response: str) -> Path:
    """Save agent response to a timestamped file for reference."""
    _RESPONSES_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    uid = uuid.uuid4().hex[:6]
    filename = f"{agent}_{ts}_{uid}.md"
    filepath = _RESPONSES_DIR / filename
    filepath.write_text(
        f"# {agent.title()} Response\n\n"
        f"**Question:** {question}\n\n"
        f"**Time:** {datetime.now().isoformat()}\n\n"
        f"---\n\n{response}\n",
        encoding="utf-8",
    )
    log.info("Saved response to %s", filepath)
    return filepath


def _format_error(provider: str, error_type: str, message: str, retryable: bool = False) -> str:
    """Format a structured error string for LLM consumption."""
    return f"[ERROR][provider={provider}][type={error_type}][retryable={str(retryable).lower()}] {message}"


async def _run_agent(
    agent_name: str,
    question: str,
    *,
    cwd: str | None = None,
    timeout: float | None = None,
) -> str:
    """Run a configured agent CLI and return cleaned output."""
    agents = get_enabled_agents()
    if agent_name not in agents:
        available = ", ".join(agents.keys()) or "none"
        return _format_error(
            agent_name, "not_configured",
            f"Agent '{agent_name}' is not configured or not enabled. Available: {available}",
        )

    agent_config = agents[agent_name]
    provider = get_provider(agent_config)

    try:
        provider._find_cmd()
    except ProviderUnavailable:
        return _format_error(
            agent_name, "unavailable",
            f"executable '{agent_config.command}' not found in PATH",
        )

    await provider.send(question)
    try:
        response = await provider.read_response(timeout=timeout or agent_config.timeout, cwd=cwd)
    except ProviderTimeout:
        t = timeout or agent_config.timeout
        return _format_error(agent_name, "timeout", f"did not respond within {t}s", retryable=True)
    except ProviderUnavailable as e:
        return _format_error(agent_name, "unavailable", str(e))

    if _SAVE_RESPONSES:
        _save_response(agent_name, question, response)

    return response


# ── Generic delegation tools ────────────────────────────────────────────


@mcp.tool()
async def ask_agent(agent_name: str, question: str, cwd: str | None = None) -> str:
    """Ask a specific AI agent a question.

    Dispatches the question to the named agent's CLI tool and returns the response.
    Use list_agents() to see available agents and their capabilities.

    Args:
        agent_name: The agent to ask (e.g. 'copilot', 'gemini', 'codex').
        question: The question or prompt to send.
        cwd: Working directory for the CLI (so it can read project files).

    Returns:
        The agent's response text.
    """
    return await _run_agent(agent_name, question, cwd=cwd)


@mcp.tool()
def list_agents() -> str:
    """List all configured and enabled AI agents with their capabilities.

    Returns:
        JSON list of agents with name, display_name, description, and command.
    """
    agents = get_enabled_agents()
    result = []
    for name, cfg in agents.items():
        result.append({
            "name": name,
            "display_name": cfg.display_name,
            "description": cfg.description,
            "command": cfg.command,
            "enabled": cfg.enabled,
        })
    return json.dumps(result, indent=2)


@mcp.tool()
async def ask_agents(
    question: str,
    agents: str | None = None,
    cwd: str | None = None,
) -> str:
    """Ask multiple AI agents the same question in parallel.

    Runs all specified (or all enabled) agents concurrently and returns
    all responses.

    Args:
        question: The question or prompt to send to all agents.
        agents: Comma-separated agent names. If omitted, asks all enabled agents.
        cwd: Working directory for the CLIs.

    Returns:
        Combined responses from all agents, separated by headers.
    """
    enabled = get_enabled_agents()
    if agents:
        agent_list = [a.strip() for a in agents.split(",") if a.strip()]
    else:
        agent_list = list(enabled.keys())

    tasks = {
        name: _run_agent(name, question, cwd=cwd)
        for name in agent_list
    }

    results = await asyncio.gather(
        *tasks.values(), return_exceptions=True
    )

    parts = []
    for name, result in zip(tasks.keys(), results):
        if isinstance(result, Exception):
            parts.append(f"## {name}\n\n{_format_error(name, 'exception', str(result))}")
        else:
            parts.append(f"## {name}\n\n{result}")

    return "\n\n---\n\n".join(parts)


# ── Brainstorm session tools ──────────────────────────────────────────


@mcp.tool()
def bs_new_session(topic: str, project: str | None = None) -> str:
    """Start a new brainstorming session.

    Args:
        topic: What the brainstorming session is about.
        project: Optional project name for context.

    Returns:
        Session info with ID to use in subsequent calls.
    """
    return json.dumps(_db.create_session(topic, project), indent=2)


@mcp.tool()
def bs_list_sessions(status: str | None = None, limit: int = 10) -> str:
    """List brainstorming sessions.

    Args:
        status: Filter by status ('active', 'completed'). None for all.
        limit: Max sessions to return.
    """
    return json.dumps(_db.list_sessions(status, limit), indent=2)


@mcp.tool()
def bs_set_context(session_id: str, context: str) -> str:
    """Attach codebase/project context to a session.

    This context is automatically included in all bs_run_round prompts so agents
    don't need to re-analyze the codebase each round. Call this once after creating
    a session with a summary of relevant code, architecture, and constraints.

    Args:
        session_id: The session to attach context to.
        context: Codebase summary, relevant file contents, architecture notes, etc.
    """
    _db.set_context(session_id, context)
    return f"Context attached to session {session_id} ({len(context)} chars)."


@mcp.tool()
def bs_complete_session(session_id: str) -> str:
    """Mark a brainstorming session as completed.

    Args:
        session_id: The session to complete.
    """
    _db.complete_session(session_id)
    return f"Session {session_id} marked as completed."


@mcp.tool()
def bs_new_round(session_id: str, objective: str | None = None) -> str:
    """Start a new round in a brainstorming session. Round number auto-increments.

    Args:
        session_id: The session this round belongs to.
        objective: What this round should focus on.
    """
    return json.dumps(_db.create_round(session_id, objective), indent=2)


@mcp.tool()
def bs_list_rounds(session_id: str) -> str:
    """List all rounds in a brainstorming session.

    Args:
        session_id: The session to list rounds for.
    """
    return json.dumps(_db.list_rounds(session_id), indent=2)


@mcp.tool()
def bs_save_response(round_id: str, agent_name: str, content: str) -> str:
    """Save an agent's response for a round. Replaces any existing response from the same agent.

    Args:
        round_id: The round this response belongs to.
        agent_name: Which agent is responding.
        content: The full response text.
    """
    return json.dumps(_db.save_response(round_id, agent_name, content), indent=2)


@mcp.tool()
def bs_get_response(round_id: str, agent_name: str) -> str:
    """Get a specific agent's response for a round.

    Args:
        round_id: The round to look in.
        agent_name: Which agent's response to get.
    """
    result = _db.get_response(round_id, agent_name)
    if not result:
        return f"No response from {agent_name} in round {round_id}"
    return json.dumps(result, indent=2)


@mcp.tool()
def bs_get_round_responses(round_id: str) -> str:
    """Get all agent responses for a specific round.

    Args:
        round_id: The round to get responses for.
    """
    return json.dumps(_db.get_round_responses(round_id), indent=2)


@mcp.tool()
def bs_save_consensus(session_id: str, content: str, round_id: str | None = None) -> str:
    """Save a consensus document. Version auto-increments.

    Args:
        session_id: The session this consensus belongs to.
        content: The full consensus text.
        round_id: Optional specific round this consensus covers.
    """
    return json.dumps(_db.save_consensus(session_id, content, round_id), indent=2)


@mcp.tool()
def bs_get_consensus(session_id: str) -> str:
    """Get the latest consensus for a session.

    Args:
        session_id: The session to get consensus for.
    """
    result = _db.get_latest_consensus(session_id)
    if not result:
        return f"No consensus yet for session {session_id}"
    return json.dumps(result, indent=2)


@mcp.tool()
def bs_session_history(session_id: str) -> str:
    """Get the complete history of a brainstorming session including all rounds, responses, and consensus.

    Args:
        session_id: The session to dump.
    """
    return json.dumps(_db.get_session_history(session_id), indent=2)


def _build_round_prompt(
    session_id: str,
    round_id: str,
    agent_name: str,
    question: str,
) -> str:
    """Build a context-rich prompt for an agent in a brainstorm round.

    Agents MUST call bs_get_onboarding() to self-discover their role,
    workflow, and tool guides.
    """
    session = _db.get_session(session_id)
    if not session:
        return question

    parts = [
        f"You are '{agent_name}' in brainstorm session {session_id}, round {round_id}.",
        "",
        "MANDATORY FIRST STEP: Call bs_get_onboarding(agent_name='"
        f"{agent_name}', session_id='{session_id}', round_id='{round_id}') to get your"
        " identity, role, workflow, current phase, and tools. Do this BEFORE anything else.",
        "",
        question,
    ]

    return "\n".join(parts)


@mcp.tool()
async def bs_run_round(
    session_id: str,
    objective: str,
    question: str,
    cwd: str | None = None,
    agents: str | None = None,
    ctx: Context | None = None,
) -> str:
    """Run a full brainstorm round: create round, delegate to agents, auto-save all responses.

    Args:
        session_id: The brainstorm session ID.
        objective: What this round should focus on.
        question: The question to ask all agents.
        cwd: Working directory for agent CLIs.
        agents: Comma-separated agent names (default: all enabled agents).

    Returns:
        All agent responses with round metadata.
    """
    enabled = get_enabled_agents()
    if agents:
        agent_list = [a.strip() for a in agents.split(",") if a.strip()]
    else:
        agent_list = list(enabled.keys())

    total_steps = len(agent_list) + 1

    # 1. Create the round
    round_info = _db.create_round(session_id, objective)
    round_id = round_info["id"]
    round_num = round_info["round_number"]
    if ctx:
        await ctx.report_progress(1, total_steps)
        await ctx.info(f"Round {round_num} created. Dispatching to {len(agent_list)} agents...")

    # 2. Build per-agent prompts and dispatch in parallel
    tasks = {}
    for agent_name in agent_list:
        prompt = _build_round_prompt(session_id, round_id, agent_name, question)
        tasks[agent_name] = _run_agent(agent_name, prompt, cwd=cwd)

    # Run agents in parallel, report progress as each completes
    pending = {name: asyncio.ensure_future(coro) for name, coro in tasks.items()}
    results = {}
    completed = 0
    while pending:
        done, _ = await asyncio.wait(
            pending.values(), return_when=asyncio.FIRST_COMPLETED,
        )
        for task in done:
            agent_name = next(n for n, t in pending.items() if t is task)
            del pending[agent_name]
            completed += 1
            try:
                results[agent_name] = task.result()
            except Exception as e:
                results[agent_name] = e
            if ctx:
                await ctx.report_progress(1 + completed, total_steps)
                await ctx.info(f"{agent_name} finished ({completed}/{len(agent_list)})")

    # 3. Save responses — skip if agent already self-saved to DB
    output_parts = [f"# Round {round_num}: {objective}\n"]
    for agent_name in agent_list:
        result = results[agent_name]
        if isinstance(result, Exception):
            content = _format_error(agent_name, "exception", str(result))
        else:
            content = result

        existing = _db.get_response(round_id, agent_name)
        if existing:
            output_parts.append(f"## {agent_name} (self-saved)\n\n{existing['content']}")
        else:
            _db.save_response(round_id, agent_name, content)
            output_parts.append(f"## {agent_name}\n\n{content}")

    output_parts.append(f"\n---\n*Round ID: {round_id} | Responses saved to DB.*")
    return "\n\n".join(output_parts)


# ── Feedback tools ────────────────────────────────────────────────────


@mcp.tool()
def bs_create_feedback(
    session_id: str, round_id: str, source_agent: str,
    title: str, content: str,
) -> str:
    """Create a feedback item from a round's findings. Claude creates these after Phase 1.

    Args:
        session_id: The brainstorm session.
        round_id: The round this finding came from.
        source_agent: Which agent produced this finding.
        title: Short title for the finding.
        content: Full description of the finding.
    """
    return json.dumps(
        _db.create_feedback_item(session_id, round_id, source_agent, title, content),
        indent=2,
    )


@mcp.tool()
def bs_list_feedback(session_id: str, status: str | None = None) -> str:
    """List feedback items for a session.

    Args:
        session_id: The session to list feedback for.
        status: Filter by status (pending, accepted, rejected, modified, consolidated).
    """
    return json.dumps(_db.list_feedback_items(session_id, status), indent=2)


@mcp.tool()
def bs_get_feedback(item_id: str) -> str:
    """Get a feedback item with all agent responses.

    Args:
        item_id: The feedback item ID.
    """
    result = _db.get_feedback_item(item_id)
    if not result:
        return f"Feedback item {item_id} not found"
    return json.dumps(result, indent=2)


@mcp.tool()
def bs_respond_to_feedback(
    item_id: str, round_id: str, agent_name: str,
    verdict: str, reasoning: str,
) -> str:
    """Record an agent's verdict on a feedback item.

    Args:
        item_id: The feedback item to respond to.
        round_id: The current deliberation round.
        agent_name: Which agent is responding.
        verdict: One of: accept, reject, modify.
        reasoning: Why the agent chose this verdict.
    """
    if verdict not in ("accept", "reject", "modify"):
        return f"Invalid verdict '{verdict}'. Must be: accept, reject, modify"
    return json.dumps(
        _db.save_feedback_response(item_id, round_id, agent_name, verdict, reasoning),
        indent=2,
    )


@mcp.tool()
def bs_update_feedback_status(item_id: str, status: str) -> str:
    """Update the status of a feedback item.

    Args:
        item_id: The feedback item.
        status: New status (pending, accepted, rejected, modified, consolidated).
    """
    _db.update_feedback_status(item_id, status)
    return f"Feedback {item_id} status updated to '{status}'."


# ── Agent role tools ──────────────────────────────────────────────────


@mcp.tool()
def bs_set_role(session_id: str, agent_name: str, role: str) -> str:
    """Set an agent's role definition for a brainstorm session.

    Args:
        session_id: The brainstorm session.
        agent_name: The agent name.
        role: The role description / instructions for this agent.
    """
    return json.dumps(_db.set_role(session_id, agent_name, role), indent=2)


@mcp.tool()
def bs_get_role(session_id: str, agent_name: str) -> str:
    """Get an agent's role definition for a session.

    Args:
        session_id: The brainstorm session.
        agent_name: The agent to get the role for.
    """
    result = _db.get_role(session_id, agent_name)
    if not result:
        return f"No role set for {agent_name} in session {session_id}"
    return json.dumps(result, indent=2)


if __name__ == "__main__":
    mcp.run()
