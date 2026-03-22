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
from brainstorm_service import BrainstormService, classify_response_quality
from brainstorm_tools import (
    handle_add_guideline,
    handle_batch_respond,
    handle_complete_session,
    handle_create_feedback,
    handle_get_briefing,
    handle_get_consensus,
    handle_get_feedback,
    handle_get_onboarding,
    handle_get_response,
    handle_get_role,
    handle_get_role_template,
    handle_get_round_responses,
    handle_get_tool_guide,
    handle_get_workflow,
    handle_list_feedback,
    handle_list_guidelines,
    handle_list_roles,
    handle_list_rounds,
    handle_list_sessions,
    handle_list_tool_guides,
    handle_new_round,
    handle_new_session,
    handle_respond_to_feedback,
    handle_save_consensus,
    handle_save_response,
    handle_session_history,
    handle_set_context,
    handle_set_role,
    handle_suggest_roles,
    handle_update_feedback_status,
)
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
_svc = BrainstormService(_db)

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]|\x1b\].*?\x07|\x1b[()][A-Z0-9]")
_SAVE_RESPONSES = os.environ.get("AI_COLLAB_SAVE_RESPONSES", "").lower() in ("1", "true", "yes")
_RESPONSES_DIR = Path(os.environ.get(
    "AI_COLLAB_RESPONSES_DIR",
    str(Path(__file__).parent / ".brainstorm"),
))


def _json(obj) -> str:
    """Serialize handler result to JSON string (pass-through if already a string)."""
    if isinstance(obj, str):
        return obj
    return json.dumps(obj, indent=2)


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
    return _json(handle_new_session(_db, topic, project))


@mcp.tool()
def bs_list_sessions(status: str | None = None, limit: int = 10) -> str:
    """List brainstorming sessions.

    Args:
        status: Filter by status ('active', 'completed'). None for all.
        limit: Max sessions to return.
    """
    return _json(handle_list_sessions(_db, status, limit))


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
    return handle_set_context(_db, session_id, context)


@mcp.tool()
def bs_complete_session(session_id: str) -> str:
    """Mark a brainstorming session as completed.

    Args:
        session_id: The session to complete.
    """
    return handle_complete_session(_db, session_id)


@mcp.tool()
def bs_new_round(session_id: str, objective: str | None = None, question: str | None = None) -> str:
    """Start a new round in a brainstorming session. Round number auto-increments.

    Args:
        session_id: The session this round belongs to.
        objective: What this round should focus on.
        question: The question/task for agents this round. Stored in DB and
                  delivered to agents via bs_get_onboarding (pull model).
    """
    return _json(handle_new_round(_db, session_id, objective, question))


@mcp.tool()
def bs_list_rounds(session_id: str) -> str:
    """List all rounds in a brainstorming session.

    Args:
        session_id: The session to list rounds for.
    """
    return _json(handle_list_rounds(_db, session_id))


@mcp.tool()
def bs_save_response(round_id: str, agent_name: str, content: str) -> str:
    """Save an agent's response for a round. Replaces any existing response from the same agent.

    Args:
        round_id: The round this response belongs to.
        agent_name: Which agent is responding.
        content: The full response text.
    """
    # Orchestrator server — no MCP source marking (stdout responses are classified separately)
    return _json(handle_save_response(_db, round_id, agent_name, content))


@mcp.tool()
def bs_get_response(round_id: str, agent_name: str) -> str:
    """Get a specific agent's response for a round.

    Args:
        round_id: The round to look in.
        agent_name: Which agent's response to get.
    """
    return _json(handle_get_response(_db, round_id, agent_name))


@mcp.tool()
def bs_get_round_responses(round_id: str) -> str:
    """Get all agent responses for a specific round.

    Args:
        round_id: The round to get responses for.
    """
    return _json(handle_get_round_responses(_db, round_id))


@mcp.tool()
def bs_save_consensus(session_id: str, content: str, round_id: str | None = None) -> str:
    """Save a consensus document. Version auto-increments.

    Args:
        session_id: The session this consensus belongs to.
        content: The full consensus text.
        round_id: Optional specific round this consensus covers.
    """
    return _json(handle_save_consensus(_db, session_id, content, round_id))


@mcp.tool()
def bs_get_consensus(session_id: str) -> str:
    """Get the latest consensus for a session.

    Args:
        session_id: The session to get consensus for.
    """
    return _json(handle_get_consensus(_db, session_id))


@mcp.tool()
def bs_session_history(session_id: str) -> str:
    """Get the complete history of a brainstorming session including all rounds, responses, and consensus.

    Args:
        session_id: The session to dump.
    """
    return _json(handle_session_history(_db, session_id))


@mcp.tool()
def bs_get_briefing(session_id: str, agent_name: str) -> str:
    """Get session-specific briefing: context, your role, and guidelines.

    Falls back to default_role from agent_definitions if no session role is set.

    Args:
        session_id: The session.
        agent_name: Your agent name (copilot, gemini, claude).
    """
    return _json(handle_get_briefing(_svc, session_id, agent_name))


@mcp.tool()
def bs_get_onboarding(
    agent_name: str, session_id: str | None = None, round_id: str | None = None,
) -> str:
    """Primary entry point for agents. Returns everything needed: identity, workflow,
    phases, convergence rules, response format, tool guides, and session context.

    Phase-aware: when feedback items exist, includes deliberation instructions.
    Call this FIRST when starting any brainstorm work.

    Args:
        agent_name: Your agent name (copilot, gemini, claude).
        session_id: Optional — for session-specific context, role, and guidelines.
        round_id: Optional — for phase-specific instructions.
    """
    return _json(handle_get_onboarding(_db, _svc, agent_name, session_id, round_id))


@mcp.tool()
def bs_get_workflow(name: str = "brainstorm_3phase") -> str:
    """Read the workflow template: phases, convergence rules, response format.

    Args:
        name: Workflow name (default: brainstorm_3phase).
    """
    return _json(handle_get_workflow(_db, name))


@mcp.tool()
def bs_get_tool_guide(tool_name: str) -> str:
    """Read the usage guide for a specific brainstorm tool.

    Args:
        tool_name: The tool name (e.g. 'bs_list_feedback').
    """
    return _json(handle_get_tool_guide(_db, tool_name))


@mcp.tool()
def bs_list_tool_guides(phase: str | None = None) -> str:
    """List all tool guides, optionally filtered by workflow phase.

    Args:
        phase: Filter by phase (setup, phase1, phase2, phase3, any). None for all.
    """
    return _json(handle_list_tool_guides(_db, phase))


@mcp.tool()
def bs_add_guideline(session_id: str, content: str) -> str:
    """Add a must-do guideline to a session (e.g. 'Always cite file:line for claims').

    Args:
        session_id: The session.
        content: The guideline text.
    """
    # Canonical param name: content (was 'guideline' — standardized across servers)
    return _json(handle_add_guideline(_db, session_id, content))


@mcp.tool()
def bs_list_guidelines(session_id: str) -> str:
    """List all guidelines attached to a session.

    Args:
        session_id: The session.
    """
    return _json(handle_list_guidelines(_db, session_id))


def _build_round_prompt(
    session_id: str,
    round_id: str,
    agent_name: str,
) -> str:
    """Build a pure ID-based prompt for an agent in a brainstorm round.

    The prompt contains only IDs and a bootstrap instruction. The agent
    retrieves everything (task, role, workflow, context) from the DB
    via bs_get_onboarding().
    """
    # Use space instead of \n — newlines in subprocess args break Windows .cmd parsing
    return (
        f"You are '{agent_name}'. Session: {session_id}, Round: {round_id}. "
        f"Call bs_get_onboarding(agent_name='{agent_name}', session_id='{session_id}', "
        f"round_id='{round_id}') to get your task, role, and instructions."
    )


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

    # 1. Create the round (question stored in DB for agent retrieval via onboarding)
    round_info = _db.create_round(session_id, objective, question=question)
    round_id = round_info["id"]
    round_num = round_info["round_number"]
    if ctx:
        await ctx.report_progress(1, total_steps)
        await ctx.info(f"Round {round_num} created. Dispatching to {len(agent_list)} agents...")

    # Detect phase and register participants
    feedback_items = _db.list_feedback_items(session_id)
    phase = "deliberation" if feedback_items else "analysis"
    _db.set_round_phase(round_id, phase)
    _db.register_participants(round_id, agent_list, phase=phase)
    # Set feedback expectations for deliberation
    if phase == "deliberation":
        pending_count = len([f for f in feedback_items if f.get("status") == "pending"])
        for an in agent_list:
            _db.set_participant_feedback_expected(round_id, an, pending_count)

    # 2. Build per-agent prompts (pure IDs) and dispatch in parallel
    for agent_name in agent_list:
        _db.update_participant_status(round_id, agent_name, "dispatched")

    tasks = {}
    for agent_name in agent_list:
        prompt = _build_round_prompt(session_id, round_id, agent_name)
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

    # 3. Save responses with quality classification
    output_parts = [f"# Round {round_num}: {objective}\n"]
    for agent_name in agent_list:
        result = results[agent_name]
        existing = _db.get_response(round_id, agent_name)

        if existing and existing.get("source") == "mcp":
            # Agent self-saved via MCP — trusted
            _db.update_participant_status(round_id, agent_name, "validated", quality="self_saved")
            output_parts.append(f"## {agent_name} (self-saved)\n\n{existing['content']}")
        elif isinstance(result, Exception):
            content = _format_error(agent_name, "exception", str(result))
            _db.save_response(round_id, agent_name, content)
            _db.mark_response_quality_and_source(round_id, agent_name, "error", "stdout")
            _db.update_participant_status(round_id, agent_name, "failed", quality="error", error_detail=str(result))
            output_parts.append(f"## {agent_name} [FAILED]\n\n{content}")
        else:
            quality = classify_response_quality(result)
            if not existing:
                _db.save_response(round_id, agent_name, result)
            _db.mark_response_quality_and_source(round_id, agent_name, quality, "stdout")
            status = "responded" if quality == "valid" else "failed"
            _db.update_participant_status(round_id, agent_name, status, quality=quality)
            output_parts.append(f"## {agent_name}\n\n{result}")

    # 4. FAIL-FAST gate check
    gate = _svc.check_round_complete(round_id)
    if gate["complete"]:
        _db.complete_round(round_id)
        output_parts.append(f"\n---\n*Round gate: {gate['responded']}/{gate['total']} valid. Round ID: {round_id}*")
    else:
        failed_agents = [a for a, s in gate["agents"].items() if s["status"] in ("failed", "timed_out")]
        error_details = []
        for a in failed_agents:
            err = gate["agents"][a].get("error") or gate["agents"][a].get("quality", "unknown")
            error_details.append(f"{a} ({err})")
        output_parts.append(
            f"\n---\n**[ROUND FAILED]** {gate['responded']}/{gate['total']} valid responses. "
            f"Failed: {', '.join(error_details)}.\n"
            f"Round cannot proceed. Fix the issue and retry with `bs_retry_agent(round_id='{round_id}', agent_name='...', cwd='...')`.\n"
            f"*Round ID: {round_id}*"
        )

    return "\n\n".join(output_parts)


@mcp.tool()
def bs_check_round_status(round_id: str) -> str:
    """Check completion status of a round — which agents responded, quality, and gate status.

    Args:
        round_id: The round to check.

    Returns:
        Completion gate status with per-agent details.
    """
    return json.dumps(_svc.check_round_complete(round_id), indent=2)


@mcp.tool()
def bs_check_feedback_status(round_id: str, session_id: str) -> str:
    """Check if all agents voted on all feedback items for a deliberation round.

    Args:
        round_id: The deliberation round.
        session_id: The session (to find feedback items).

    Returns:
        Vote completeness matrix with per-agent and per-item details.
    """
    return json.dumps(_svc.check_feedback_votes_complete(round_id, session_id), indent=2)


@mcp.tool()
async def bs_retry_agent(
    round_id: str, agent_name: str, cwd: str | None = None,
) -> str:
    """Retry a failed/timed-out agent in an existing round.

    Args:
        round_id: The round to retry in.
        agent_name: The agent to retry.
        cwd: Working directory for agent CLI.

    Returns:
        The retry result or error message.
    """
    participant = _db.get_participant(round_id, agent_name)
    if not participant:
        return f"Agent '{agent_name}' is not a participant in round {round_id}"
    if participant["status"] not in ("failed", "timed_out"):
        return f"Agent '{agent_name}' status is '{participant['status']}', not failed/timed_out"
    if participant["retry_count"] >= participant["max_retries"]:
        return f"Agent '{agent_name}' has exhausted retries ({participant['retry_count']}/{participant['max_retries']})"

    _db.increment_retry(round_id, agent_name)
    round_info = _db.get_round(round_id)
    if not round_info:
        return f"Round {round_id} not found"

    session_id = round_info["session_id"]
    prompt = _build_round_prompt(session_id, round_id, agent_name)
    _db.update_participant_status(round_id, agent_name, "dispatched")

    try:
        result = await _run_agent(agent_name, prompt, cwd=cwd)
    except Exception as e:
        _db.update_participant_status(round_id, agent_name, "failed", quality="error", error_detail=str(e))
        return f"[RETRY FAILED] {agent_name}: {e}"

    # Check if agent self-saved
    existing = _db.get_response(round_id, agent_name)
    if existing and existing.get("source") == "mcp":
        _db.update_participant_status(round_id, agent_name, "validated", quality="self_saved")
        return f"[RETRY OK] {agent_name} self-saved via MCP.\n\n{existing['content']}"

    quality = classify_response_quality(result)
    _db.save_response(round_id, agent_name, result)
    _db.mark_response_quality_and_source(round_id, agent_name, quality, "stdout")
    status = "responded" if quality == "valid" else "failed"
    _db.update_participant_status(round_id, agent_name, status, quality=quality)

    if quality == "valid":
        gate = _svc.check_round_complete(round_id)
        gate_msg = f"Round gate: {gate['responded']}/{gate['total']} valid."
        if gate["complete"]:
            _db.complete_round(round_id)
            gate_msg += " Round COMPLETE."
        return f"[RETRY OK] {agent_name} responded ({quality}).\n{gate_msg}\n\n{result}"
    else:
        return f"[RETRY FAILED] {agent_name} response quality: {quality}.\n\n{result}"


# ── Feedback tools ────────────────────────────────────────────────────


@mcp.tool()
def bs_create_feedback(
    session_id: str, source_round_id: str, source_agent: str,
    title: str, content: str,
) -> str:
    """Create a feedback item from a round's findings. Claude creates these after Phase 1.

    Args:
        session_id: The brainstorm session.
        source_round_id: The round this finding came from.
        source_agent: Which agent produced this finding.
        title: Short title for the finding.
        content: Full description of the finding.
    """
    # Canonical param name: source_round_id (was 'round_id' — standardized across servers)
    return _json(handle_create_feedback(_db, session_id, source_round_id, source_agent, title, content))


@mcp.tool()
def bs_batch_create_feedback(session_id: str, items: str) -> str:
    """Create multiple feedback items in a single call.

    Args:
        session_id: The brainstorm session.
        items: JSON array of objects, each with: source_round_id, source_agent, title, content.
            Example: [{"source_round_id":"r_xxx","source_agent":"copilot","title":"...","content":"..."},...]
    """
    try:
        parsed = json.loads(items)
    except (ValueError, TypeError):
        return "Invalid JSON in items parameter."
    for item in parsed:
        if not all(k in item for k in ("source_round_id", "source_agent", "title", "content")):
            return f"Each item must have source_round_id, source_agent, title, content. Got: {list(item.keys())}"
    results = _db.batch_create_feedback_items(session_id, parsed)
    return json.dumps({"created": len(results), "items": results}, indent=2)


@mcp.tool()
def bs_list_feedback(session_id: str, status: str | None = None) -> str:
    """List feedback items for a session.

    Args:
        session_id: The session to list feedback for.
        status: Filter by status (pending, accepted, rejected, modified, consolidated).
    """
    # Canonical form: raw list (no {"feedback_items": ...} wrapper)
    return _json(handle_list_feedback(_db, session_id, status))


@mcp.tool()
def bs_get_feedback(item_id: str) -> str:
    """Get a feedback item with all agent responses.

    Args:
        item_id: The feedback item ID.
    """
    return _json(handle_get_feedback(_db, item_id))


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
    return _json(handle_respond_to_feedback(_db, item_id, round_id, agent_name, verdict, reasoning))


@mcp.tool()
def bs_batch_respond(
    round_id: str, agent_name: str, verdicts: str,
) -> str:
    """Submit verdicts on multiple feedback items in a single call.

    Args:
        round_id: The current deliberation round.
        agent_name: Which agent is responding.
        verdicts: JSON array of objects, each with: item_id, verdict (accept/reject/modify), reasoning.
            Example: [{"item_id":"fb_xxx","verdict":"accept","reasoning":"..."},...]
    """
    return _json(handle_batch_respond(_db, round_id, agent_name, verdicts))


@mcp.tool()
def bs_update_feedback_status(item_id: str, status: str) -> str:
    """Update the status of a feedback item.

    Args:
        item_id: The feedback item.
        status: New status (pending, accepted, rejected, modified, consolidated).
    """
    return handle_update_feedback_status(_db, item_id, status)


# ── Agent role tools ──────────────────────────────────────────────────


@mcp.tool()
def bs_set_role(session_id: str, agent_name: str, role: str) -> str:
    """Set an agent's role definition for a brainstorm session.

    Args:
        session_id: The brainstorm session.
        agent_name: The agent name.
        role: The role description / instructions for this agent.
    """
    return _json(handle_set_role(_db, session_id, agent_name, role))


@mcp.tool()
def bs_get_role(session_id: str, agent_name: str) -> str:
    """Get an agent's role definition for a session.

    Args:
        session_id: The brainstorm session.
        agent_name: The agent to get the role for.
    """
    return _json(handle_get_role(_db, session_id, agent_name))


# ── Role Library tools ─────────────────────────────────────────────────


@mcp.tool()
def bs_create_role(
    slug: str, display_name: str, description: str, role_text: str,
    agent_name: str | None = None, approach: str | None = None,
    tags: str | None = None, notes: str | None = None,
    vision: str | None = None, angle: str | None = None,
    behavior: str | None = None, mandates: str | None = None,
) -> str:
    """Create a reusable role template in the role library.

    Role templates define how an agent should behave in brainstorm sessions.
    They can be agent-specific (only for copilot) or generic (any agent).
    Apply them to sessions with bs_apply_role.

    Args:
        slug: Unique identifier (e.g. 'security-reviewer', 'perf-analyst').
        display_name: Human-readable name.
        description: One-line description of what this role focuses on.
        role_text: The full role instructions given to the agent.
        agent_name: If set, this role is only for this agent. None = any agent.
        approach: Optional approach guidance (appended to role_text when applied).
        tags: Comma-separated tags for filtering (e.g. 'security,code-review').
        notes: Optional notes about when/how to use this role.
        vision: The desired outcome this role is optimizing for.
        angle: The unique perspective or lens this role brings.
        behavior: How the agent should behave when in this role.
        mandates: JSON array string of non-negotiable rules, or a single rule string.
    """
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    mandates_list = (
        json.loads(mandates) if mandates and mandates.strip().startswith("[")
        else ([mandates] if mandates else None)
    )
    return json.dumps(
        _db.create_role_template(
            slug, display_name, description, role_text,
            agent_name=agent_name, approach=approach, tags=tag_list, notes=notes,
            vision=vision, angle=angle, behavior=behavior, mandates=mandates_list,
        ),
        indent=2,
    )


@mcp.tool()
def bs_list_roles(agent_name: str | None = None, tag: str | None = None) -> str:
    """List available role templates from the role library.

    Args:
        agent_name: Filter to roles for this agent (also includes agent-agnostic roles).
        tag: Filter by tag (e.g. 'security', 'architecture').
    """
    return _json(handle_list_roles(_db, agent_name, tag))


@mcp.tool()
def bs_suggest_roles(
    topic: str,
    agents: str | None = None,
    top_n: int = 6,
    diversify: bool = False,
) -> str:
    """Suggest role templates appropriate for a brainstorm topic.

    Roles are NOT fixed to models. The recommended pattern is role rotation:
    each round, ALL agents get the SAME role. Rotate the role each round so
    every model covers every perspective. Use top_roles ranking to pick which
    roles to rotate through.

    Args:
        topic: The brainstorm topic/question (same string passed to bs_new_session).
        agents: Comma-separated agent names for per-agent suggestions (e.g. "copilot,gemini").
        top_n: Number of top roles to return (default: 6).
        diversify: If True, assigns unique roles per agent (each slug used once).
            Default False — all agents get the same top-matched role per round.

    Returns JSON with top_roles (ranked by topic match) and assignments (per-agent suggestions).
    Apply with bs_apply_role(session_id, agent_name, slug).
    """
    return _json(handle_suggest_roles(_db, topic, agents, top_n, diversify))


@mcp.tool()
def bs_get_role_template(slug: str) -> str:
    """Get full details of a role template by slug or ID.

    Args:
        slug: The role template slug or ID.
    """
    return _json(handle_get_role_template(_db, slug))


@mcp.tool()
def bs_update_role(
    slug: str, display_name: str | None = None, description: str | None = None,
    role_text: str | None = None, approach: str | None = None,
    tags: str | None = None, notes: str | None = None,
    vision: str | None = None, angle: str | None = None,
    behavior: str | None = None, mandates: str | None = None,
) -> str:
    """Update a role template. Only provided fields are changed.

    Args:
        slug: The role template slug or ID to update.
        display_name: New display name.
        description: New description.
        role_text: New role instructions.
        approach: New approach guidance.
        tags: New comma-separated tags (replaces existing).
        notes: New notes.
        vision: The desired outcome this role is optimizing for.
        angle: The unique perspective or lens this role brings.
        behavior: How the agent should behave when in this role.
        mandates: JSON array string of non-negotiable rules, or a single rule string.
    """
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
    mandates_list = (
        json.loads(mandates) if mandates and mandates.strip().startswith("[")
        else ([mandates] if mandates else None)
    )
    result = _db.update_role_template(
        slug, display_name=display_name, description=description,
        role_text=role_text, approach=approach, tags=tag_list, notes=notes,
        vision=vision, angle=angle, behavior=behavior, mandates=mandates_list,
    )
    if not result:
        return f"Role template '{slug}' not found."
    return json.dumps(result, indent=2)


@mcp.tool()
def bs_delete_role(slug: str) -> str:
    """Delete a role template from the library.

    Args:
        slug: The role template slug or ID to delete.
    """
    if _db.delete_role_template(slug):
        return f"Role template '{slug}' deleted."
    return f"Role template '{slug}' not found."


@mcp.tool()
def bs_apply_role(session_id: str, agent_name: str, slug: str) -> str:
    """Apply a role template to an agent in a session.

    Copies the role_text (+ approach) into the session's agent_roles,
    and increments the template's usage_count.

    Args:
        session_id: The brainstorm session.
        agent_name: Which agent to assign this role to.
        slug: The role template slug or ID.
    """
    result = _db.apply_role_template(session_id, agent_name, slug)
    if not result:
        return f"Role template '{slug}' not found."
    return json.dumps(result, indent=2)


@mcp.tool()
def bs_batch_apply_roles(session_id: str, assignments: str) -> str:
    """Apply role templates to multiple agents in a single call.

    Args:
        session_id: The brainstorm session.
        assignments: JSON array of objects, each with: agent_name, slug.
            Example: [{"agent_name":"copilot","slug":"code-reviewer"},{"agent_name":"gemini","slug":"code-reviewer"}]
    """
    try:
        parsed = json.loads(assignments)
    except (ValueError, TypeError):
        return "Invalid JSON in assignments parameter."
    for a in parsed:
        if not all(k in a for k in ("agent_name", "slug")):
            return f"Each assignment must have agent_name, slug. Got: {list(a.keys())}"
    results = _db.batch_apply_role_templates(session_id, parsed)
    not_found = [a["slug"] for a in parsed if not any(r.get("template_slug") == a["slug"] for r in results)]
    resp = {"applied": len(results), "roles": results}
    if not_found:
        resp["not_found"] = not_found
    return json.dumps(resp, indent=2)


if __name__ == "__main__":
    mcp.run()
