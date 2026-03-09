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

mcp = FastMCP("brainstorm")

_DB_PATH = Path(os.environ.get(
    "BRAINSTORM_DB",
    str(Path(__file__).parent / ".data" / "brainstorm.db"),
))
_db = BrainstormDB(_DB_PATH)


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
    result = _db.create_session(topic, project)
    return json.dumps(result, indent=2)


@mcp.tool()
def bs_list_sessions(status: str | None = None, limit: int = 10) -> str:
    """List brainstorming sessions.

    Args:
        status: Filter by status ('active', 'completed'). None for all.
        limit: Max sessions to return.

    Returns:
        List of sessions.
    """
    sessions = _db.list_sessions(status, limit)
    return json.dumps(sessions, indent=2)


@mcp.tool()
def bs_complete_session(session_id: str) -> str:
    """Mark a brainstorming session as completed.

    Args:
        session_id: The session to complete.

    Returns:
        Confirmation message.
    """
    _db.complete_session(session_id)
    return f"Session {session_id} marked as completed."


# -- Round tools --

@mcp.tool()
def bs_new_round(session_id: str, objective: str | None = None) -> str:
    """Start a new round in a brainstorming session. Round number auto-increments.

    Args:
        session_id: The session this round belongs to.
        objective: What this round should focus on.

    Returns:
        Round info with ID and round number.
    """
    result = _db.create_round(session_id, objective)
    return json.dumps(result, indent=2)


@mcp.tool()
def bs_list_rounds(session_id: str) -> str:
    """List all rounds in a brainstorming session.

    Args:
        session_id: The session to list rounds for.

    Returns:
        List of rounds with their numbers and objectives.
    """
    rounds = _db.list_rounds(session_id)
    return json.dumps(rounds, indent=2)


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
    result = _db.save_response(round_id, agent_name, content)
    return json.dumps(result, indent=2)


@mcp.tool()
def bs_get_response(round_id: str, agent_name: str) -> str:
    """Get a specific agent's response for a round.

    Args:
        round_id: The round to look in.
        agent_name: Which agent's response to get.

    Returns:
        The full response content, or error if not found.
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

    Returns:
        All responses for the round.
    """
    responses = _db.get_round_responses(round_id)
    return json.dumps(responses, indent=2)


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
    result = _db.save_consensus(session_id, content, round_id)
    return json.dumps(result, indent=2)


@mcp.tool()
def bs_get_consensus(session_id: str) -> str:
    """Get the latest consensus for a session.

    Args:
        session_id: The session to get consensus for.

    Returns:
        Latest consensus content and version.
    """
    result = _db.get_latest_consensus(session_id)
    if not result:
        return f"No consensus yet for session {session_id}"
    return json.dumps(result, indent=2)


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
    _db.set_context(session_id, context)
    return f"Context set for session {session_id}."


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
    result = _db.create_feedback_item(session_id, source_round_id, source_agent, title, content)
    return json.dumps(result, indent=2)


@mcp.tool()
def bs_list_feedback(session_id: str, status: str | None = None) -> str:
    """List feedback items for a session.

    Args:
        session_id: The session to list feedback for.
        status: Filter by status (pending, accepted, rejected, modified).

    Returns:
        List of feedback items.
    """
    items = _db.list_feedback_items(session_id, status)
    return json.dumps({"feedback_items": items}, indent=2)


@mcp.tool()
def bs_get_feedback(item_id: str) -> str:
    """Get a feedback item with all agent responses/verdicts.

    Args:
        item_id: The feedback item ID.

    Returns:
        Feedback item with title, content, status, and all verdicts.
    """
    result = _db.get_feedback_item(item_id)
    if not result:
        return f"Feedback item {item_id} not found."
    return json.dumps(result, indent=2)


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
    result = _db.save_feedback_response(item_id, round_id, agent_name, verdict, reasoning)
    return json.dumps(result, indent=2)


@mcp.tool()
def bs_update_feedback_status(item_id: str, status: str) -> str:
    """Update the status of a feedback item (e.g. after convergence).

    Args:
        item_id: The feedback item to update.
        status: New status (pending, accepted, rejected, modified, consolidated).

    Returns:
        Confirmation message.
    """
    _db.update_feedback_status(item_id, status)
    return f"Feedback item {item_id} status updated to '{status}'."


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
    result = _db.set_role(session_id, agent_name, role)
    return json.dumps(result, indent=2)


@mcp.tool()
def bs_get_role(session_id: str, agent_name: str) -> str:
    """Get your role definition for a session.

    Args:
        session_id: The session.
        agent_name: The agent (copilot, gemini, claude).

    Returns:
        Role description, or message if no role set.
    """
    result = _db.get_role(session_id, agent_name)
    if not result:
        return f"No role set for {agent_name} in session {session_id}."
    return json.dumps(result, indent=2)


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
    result = _db.add_guideline(session_id, content)
    return json.dumps(result, indent=2)


@mcp.tool()
def bs_list_guidelines(session_id: str) -> str:
    """List all guidelines for a session.

    Args:
        session_id: The session.

    Returns:
        List of guidelines.
    """
    guidelines = _db.list_guidelines(session_id)
    return json.dumps(guidelines, indent=2)


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
    result = _db.get_agent_briefing(session_id, agent_name)
    return json.dumps(result, indent=2)


# -- History tool --

@mcp.tool()
def bs_session_history(session_id: str) -> str:
    """Get the complete history of a brainstorming session including all rounds, responses, feedback, and consensus.

    Args:
        session_id: The session to dump.

    Returns:
        Full session history with all data.
    """
    result = _db.get_session_history(session_id)
    return json.dumps(result, indent=2)


# -- Onboarding & self-describing tools (agent-facing reads) --

@mcp.tool()
def bs_get_onboarding(agent_name: str, session_id: str | None = None) -> str:
    """Primary entry point for agents. Returns everything you need: your identity,
    workflow overview, phases, convergence rules, response format, tool guides,
    and session context if a session_id is provided.

    Call this FIRST when starting any brainstorm work.

    Args:
        agent_name: Your agent name (copilot, gemini, claude).
        session_id: Optional session ID for session-specific context, role, and guidelines.

    Returns:
        Full onboarding briefing with identity, workflow, tools, and optional session data.
    """
    result = _db.get_onboarding_briefing(agent_name, session_id)
    return json.dumps(result, indent=2)


@mcp.tool()
def bs_get_workflow(name: str = "brainstorm_3phase") -> str:
    """Read the workflow template: phases, convergence rules, response format.

    Args:
        name: Workflow name (default: brainstorm_3phase).

    Returns:
        Workflow template with phases, rules, and format spec.
    """
    wf = _db.get_workflow_template(name)
    if not wf:
        return f"Workflow '{name}' not found. Run 'seed-defaults' to populate."
    wf["phases"] = json.loads(wf["phases"])
    return json.dumps(wf, indent=2)


@mcp.tool()
def bs_get_tool_guide(tool_name: str) -> str:
    """Read the usage guide for a specific brainstorm tool.

    Args:
        tool_name: The tool name (e.g. 'bs_list_feedback').

    Returns:
        Tool guide with phase, purpose, and usage instructions.
    """
    guide = _db.get_tool_guide(tool_name)
    if not guide:
        return f"No guide found for tool '{tool_name}'."
    return json.dumps(guide, indent=2)


@mcp.tool()
def bs_list_tool_guides(phase: str | None = None) -> str:
    """List all tool guides, optionally filtered by workflow phase.

    Args:
        phase: Filter by phase (setup, phase1, phase2, phase3, any). None for all.

    Returns:
        List of tool guides.
    """
    guides = _db.list_tool_guides(phase)
    return json.dumps(guides, indent=2)


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
    result = _db.upsert_agent_definition(agent_name, display_name, capabilities, default_role, approach)
    return json.dumps(result, indent=2)


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
    result = _db.upsert_workflow_template(name, overview, phases_json, convergence_rules, response_format)
    return json.dumps(result, indent=2)


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
    result = _db.upsert_tool_guide(tool_name, phase, purpose, usage)
    return json.dumps(result, indent=2)


if __name__ == "__main__":
    mcp.run()
