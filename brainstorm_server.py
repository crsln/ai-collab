"""MCP server for multi-AI brainstorming sessions backed by SQLite.

This is the server that Copilot and Gemini run as their own MCP instances.
Agents call bs_get_onboarding(agent_name) to self-discover workflow, tools,
and identity. No data is auto-injected into tool responses.
"""

import json
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from brainstorm_db import BrainstormDB
from brainstorm_service import BrainstormService
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
    handle_set_agent_definition,
    handle_set_context,
    handle_set_role,
    handle_set_tool_guide,
    handle_set_workflow_template,
    handle_suggest_roles,
    handle_update_feedback_status,
)

mcp = FastMCP("brainstorm")

_DB_PATH = Path(os.environ.get(
    "BRAINSTORM_DB",
    str(Path(__file__).parent / ".data" / "brainstorm.db"),
))
_db = BrainstormDB(_DB_PATH)
_svc = BrainstormService(_db)


def _json(obj) -> str:
    """Serialize handler result to JSON string (pass-through if already a string)."""
    if isinstance(obj, str):
        return obj
    return json.dumps(obj, indent=2)


# -- Session tools --

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

    Returns:
        List of sessions.
    """
    return _json(handle_list_sessions(_db, status, limit))


@mcp.tool()
def bs_complete_session(session_id: str) -> str:
    """Mark a brainstorming session as completed.

    Args:
        session_id: The session to complete.

    Returns:
        Confirmation message.
    """
    return handle_complete_session(_db, session_id)


# -- Round tools --

@mcp.tool()
def bs_new_round(session_id: str, objective: str | None = None, question: str | None = None) -> str:
    """Start a new round in a brainstorming session. Round number auto-increments.

    Args:
        session_id: The session this round belongs to.
        objective: What this round should focus on.
        question: The question/task for agents this round. Stored in DB and
                  delivered to agents via bs_get_onboarding (pull model).

    Returns:
        Round info with ID and round number.
    """
    return _json(handle_new_round(_db, session_id, objective, question))


@mcp.tool()
def bs_list_rounds(session_id: str) -> str:
    """List all rounds in a brainstorming session.

    Args:
        session_id: The session to list rounds for.

    Returns:
        List of rounds with their numbers and objectives.
    """
    return _json(handle_list_rounds(_db, session_id))


# -- Response tools --

@mcp.tool()
def bs_save_response(round_id: str, agent_name: str, content: str) -> str:
    """Save an agent's response for a round. Replaces any existing response from the same agent.

    Args:
        round_id: The round this response belongs to.
        agent_name: Which agent is responding (e.g. 'claude', 'copilot', 'gemini').
        content: The full, untruncated response text.

    Returns:
        Confirmation with response ID.
    """
    # This is the agent's own MCP server — mark as MCP-sourced (trusted)
    return _json(handle_save_response(_db, round_id, agent_name, content, mark_mcp_source=True))


@mcp.tool()
def bs_get_response(round_id: str, agent_name: str) -> str:
    """Get a specific agent's response for a round.

    Args:
        round_id: The round to look in.
        agent_name: Which agent's response to get.

    Returns:
        The full response content, or error if not found.
    """
    return _json(handle_get_response(_db, round_id, agent_name))


@mcp.tool()
def bs_get_round_responses(round_id: str) -> str:
    """Get all agent responses for a specific round.

    Args:
        round_id: The round to get responses for.

    Returns:
        All responses for the round.
    """
    return _json(handle_get_round_responses(_db, round_id))


# -- Consensus tools --

@mcp.tool()
def bs_save_consensus(session_id: str, content: str, round_id: str | None = None) -> str:
    """Save a consensus document. Version auto-increments.

    Args:
        session_id: The session this consensus belongs to.
        content: The full consensus text.
        round_id: Optional specific round this consensus covers.

    Returns:
        Confirmation with consensus ID and version number.
    """
    return _json(handle_save_consensus(_db, session_id, content, round_id))


@mcp.tool()
def bs_get_consensus(session_id: str) -> str:
    """Get the latest consensus for a session.

    Args:
        session_id: The session to get consensus for.

    Returns:
        Latest consensus content and version.
    """
    return _json(handle_get_consensus(_db, session_id))


# -- Context tools --

@mcp.tool()
def bs_set_context(session_id: str, context: str) -> str:
    """Attach codebase/project context to a session.

    Agents retrieve this via bs_get_onboarding or bs_get_briefing.

    Args:
        session_id: The session to attach context to.
        context: Summary of relevant code, architecture, key files, constraints.

    Returns:
        Confirmation message.
    """
    return handle_set_context(_db, session_id, context)


# -- Feedback tools --

@mcp.tool()
def bs_create_feedback(
    session_id: str, source_round_id: str, source_agent: str,
    title: str, content: str,
) -> str:
    """Create a feedback item for agents to review and respond to.

    Args:
        session_id: The session this feedback belongs to.
        source_round_id: The round that produced this feedback.
        source_agent: Which agent raised this feedback item.
        title: Short title for the feedback item.
        content: Detailed description of the feedback.

    Returns:
        Feedback item info with ID.
    """
    return _json(handle_create_feedback(_db, session_id, source_round_id, source_agent, title, content))


@mcp.tool()
def bs_list_feedback(session_id: str, status: str | None = None) -> str:
    """List feedback items for a session.

    Args:
        session_id: The session to list feedback for.
        status: Filter by status (pending, accepted, rejected, modified).

    Returns:
        List of feedback items.
    """
    # Canonical form: raw list (no wrapper)
    return _json(handle_list_feedback(_db, session_id, status))


@mcp.tool()
def bs_get_feedback(item_id: str) -> str:
    """Get a feedback item with all agent responses/verdicts.

    Args:
        item_id: The feedback item ID.

    Returns:
        Feedback item with title, content, status, and all verdicts.
    """
    return _json(handle_get_feedback(_db, item_id))


@mcp.tool()
def bs_respond_to_feedback(
    item_id: str, round_id: str, agent_name: str,
    verdict: str, reasoning: str,
) -> str:
    """Submit your verdict on a feedback item.

    Args:
        item_id: The feedback item to respond to.
        round_id: The current deliberation round.
        agent_name: Your agent name (copilot, gemini, claude).
        verdict: Your verdict: accept, reject, or modify.
        reasoning: Why you chose this verdict — include evidence.

    Returns:
        Confirmation with response ID.
    """
    return _json(handle_respond_to_feedback(_db, item_id, round_id, agent_name, verdict, reasoning))


@mcp.tool()
def bs_batch_respond(
    round_id: str, agent_name: str, verdicts: str,
) -> str:
    """Submit verdicts on multiple feedback items in a single call.

    Args:
        round_id: The current deliberation round.
        agent_name: Your agent name (copilot, gemini, claude).
        verdicts: JSON array of objects, each with: item_id, verdict (accept/reject/modify), reasoning.
            Example: [{"item_id":"fb_xxx","verdict":"accept","reasoning":"..."},...]
    """
    return _json(handle_batch_respond(_db, round_id, agent_name, verdicts))


@mcp.tool()
def bs_update_feedback_status(item_id: str, status: str) -> str:
    """Update the status of a feedback item (e.g. after convergence).

    Args:
        item_id: The feedback item to update.
        status: New status (pending, accepted, rejected, modified, consolidated).

    Returns:
        Confirmation message.
    """
    return handle_update_feedback_status(_db, item_id, status)


# -- Role tools --

@mcp.tool()
def bs_set_role(session_id: str, agent_name: str, role: str) -> str:
    """Set the role/instructions for an agent in a session.

    Args:
        session_id: The session.
        agent_name: The agent (copilot, gemini, claude).
        role: Role description and instructions for this agent.

    Returns:
        Confirmation with role ID.
    """
    return _json(handle_set_role(_db, session_id, agent_name, role))


@mcp.tool()
def bs_get_role(session_id: str, agent_name: str) -> str:
    """Get your role definition for a session.

    Args:
        session_id: The session.
        agent_name: The agent (copilot, gemini, claude).

    Returns:
        Role description, or message if no role set.
    """
    return _json(handle_get_role(_db, session_id, agent_name))


# -- Guidelines tools --

@mcp.tool()
def bs_add_guideline(session_id: str, content: str) -> str:
    """Add a project guideline/must-do for a session. Agents see this via bs_get_onboarding or bs_get_briefing.

    Args:
        session_id: The session.
        content: The guideline text (e.g. 'Always verify claims by reading source code').

    Returns:
        Guideline info with ID.
    """
    return _json(handle_add_guideline(_db, session_id, content))


@mcp.tool()
def bs_list_guidelines(session_id: str) -> str:
    """List all guidelines for a session.

    Args:
        session_id: The session.

    Returns:
        List of guidelines.
    """
    return _json(handle_list_guidelines(_db, session_id))


# -- Briefing tool --

@mcp.tool()
def bs_get_briefing(session_id: str, agent_name: str) -> str:
    """Get session-specific briefing: context, your role, and guidelines.

    Falls back to default_role from agent_definitions if no session role is set.

    Args:
        session_id: The session.
        agent_name: Your agent name (copilot, gemini, claude).

    Returns:
        Session context, role (or default role), and guidelines.
    """
    return _json(handle_get_briefing(_svc, session_id, agent_name))


# -- History tool --

@mcp.tool()
def bs_session_history(session_id: str) -> str:
    """Get the complete history of a brainstorming session including all rounds, responses, feedback, and consensus.

    Args:
        session_id: The session to dump.

    Returns:
        Full session history with all data.
    """
    return _json(handle_session_history(_db, session_id))


# -- Onboarding & self-describing tools (agent-facing reads) --

@mcp.tool()
def bs_get_onboarding(
    agent_name: str, session_id: str | None = None, round_id: str | None = None,
) -> str:
    """Primary entry point for agents. Returns everything you need: your identity,
    workflow overview, phases, convergence rules, response format, tool guides,
    and session context if a session_id is provided.

    Phase-aware: when feedback items exist, includes current_phase='deliberation'
    with explicit voting instructions and feedback item IDs.

    Call this FIRST when starting any brainstorm work.

    Args:
        agent_name: Your agent name (copilot, gemini, claude).
        session_id: Optional session ID for session-specific context, role, and guidelines.
        round_id: Optional round ID for phase-specific instructions.

    Returns:
        Full onboarding briefing with identity, workflow, tools, and optional session data.
    """
    return _json(handle_get_onboarding(_db, _svc, agent_name, session_id, round_id))


@mcp.tool()
def bs_get_workflow(name: str = "brainstorm_3phase") -> str:
    """Read the workflow template: phases, convergence rules, response format.

    Args:
        name: Workflow name (default: brainstorm_3phase).

    Returns:
        Workflow template with phases, rules, and format spec.
    """
    return _json(handle_get_workflow(_db, name))


@mcp.tool()
def bs_get_tool_guide(tool_name: str) -> str:
    """Read the usage guide for a specific brainstorm tool.

    Args:
        tool_name: The tool name (e.g. 'bs_list_feedback').

    Returns:
        Tool guide with phase, purpose, and usage instructions.
    """
    return _json(handle_get_tool_guide(_db, tool_name))


@mcp.tool()
def bs_list_tool_guides(phase: str | None = None) -> str:
    """List all tool guides, optionally filtered by workflow phase.

    Args:
        phase: Filter by phase (setup, phase1, phase2, phase3, any). None for all.

    Returns:
        List of tool guides.
    """
    return _json(handle_list_tool_guides(_db, phase))


# -- Admin tools (create/update global definitions) --

@mcp.tool()
def bs_set_agent_definition(
    agent_name: str, display_name: str, capabilities: str,
    default_role: str, approach: str,
) -> str:
    """Create or update an agent definition (upsert).

    Args:
        agent_name: Agent identifier (copilot, gemini, claude).
        display_name: Human-readable name (e.g. 'GitHub Copilot').
        capabilities: What this agent is good at.
        default_role: Fallback role when no session role is set.
        approach: How this agent should approach brainstorm tasks.

    Returns:
        Confirmation with ID and created/updated flag.
    """
    return _json(handle_set_agent_definition(_db, agent_name, display_name, capabilities, default_role, approach))


@mcp.tool()
def bs_set_workflow_template(
    name: str, overview: str, phases_json: str,
    convergence_rules: str, response_format: str,
) -> str:
    """Create or update a workflow template (upsert). Version auto-increments on update.

    Args:
        name: Workflow name (e.g. 'brainstorm_3phase').
        overview: Narrative description of the workflow.
        phases_json: JSON string — array of phase objects with name, objective, agent_actions, expected_outputs.
        convergence_rules: How verdicts converge (unanimous, majority, max rounds).
        response_format: Expected verdict format and quality expectations.

    Returns:
        Confirmation with ID, version, and created/updated flag.
    """
    return _json(handle_set_workflow_template(_db, name, overview, phases_json, convergence_rules, response_format))


@mcp.tool()
def bs_set_tool_guide(
    tool_name: str, phase: str, purpose: str, usage: str,
) -> str:
    """Create or update a tool usage guide (upsert).

    Args:
        tool_name: The MCP tool name (e.g. 'bs_list_feedback').
        phase: Which phase this tool is used in (setup, phase1, phase2, phase3, any).
        purpose: When and why to use this tool.
        usage: How to call it, key parameters.

    Returns:
        Confirmation with ID and created/updated flag.
    """
    return _json(handle_set_tool_guide(_db, tool_name, phase, purpose, usage))


# -- Role Library tools (read-only for agents) --

@mcp.tool()
def bs_list_roles(agent_name: str | None = None, tag: str | None = None) -> str:
    """List available role templates from the role library.

    Args:
        agent_name: Filter to roles for this agent (also includes agent-agnostic roles).
        tag: Filter by tag (e.g. 'security', 'architecture').

    Returns:
        List of role templates with slug, display_name, description, and usage stats.
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
    Apply suggestions with bs_apply_role(session_id, agent_name, slug).
    """
    return _json(handle_suggest_roles(_db, topic, agents, top_n, diversify))


@mcp.tool()
def bs_get_role_template(slug: str) -> str:
    """Get full details of a role template by slug or ID.

    Args:
        slug: The role template slug or ID.

    Returns:
        Full role template including role_text, approach, tags, and usage stats.
    """
    return _json(handle_get_role_template(_db, slug))


if __name__ == "__main__":
    mcp.run()
