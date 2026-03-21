"""Shared brainstorm tool handlers — plain functions used by both MCP servers.

Each handler takes `db` (BrainstormDB) and/or `svc` (BrainstormService) as
first parameter(s), accepts the same user-facing params as the MCP tool, and
returns a Python object (dict, list, or str). Callers are responsible for
json.dumps() serialization.

Divergences resolved here (canonical forms):
- bs_create_feedback: uses `source_round_id` (not `round_id`)
- bs_list_feedback: returns raw list (not {"feedback_items": ...} wrapper)
- bs_add_guideline: param is `content` (not `guideline`)
- bs_set_context: returns f-string with char count
- bs_save_response: always marks quality+source when called from agent MCP
- bs_get_onboarding: always includes participation tracking when round_id given
"""

from __future__ import annotations

import json

from brainstorm_db import BrainstormDB
from brainstorm_service import BrainstormService


# -- Session tools --

def handle_new_session(db: BrainstormDB, topic: str, project: str | None = None) -> dict:
    return db.create_session(topic, project)


def handle_list_sessions(db: BrainstormDB, status: str | None = None, limit: int = 10) -> list:
    return db.list_sessions(status, limit)


def handle_complete_session(db: BrainstormDB, session_id: str) -> str:
    db.complete_session(session_id)
    return f"Session {session_id} marked as completed."


# -- Round tools --

def handle_new_round(
    db: BrainstormDB, session_id: str,
    objective: str | None = None, question: str | None = None,
) -> dict:
    return db.create_round(session_id, objective, question=question)


def handle_list_rounds(db: BrainstormDB, session_id: str) -> list:
    return db.list_rounds(session_id)


# -- Response tools --

def handle_save_response(
    db: BrainstormDB, round_id: str, agent_name: str, content: str,
    *, mark_mcp_source: bool = False,
) -> dict:
    """Save an agent's response. When mark_mcp_source=True (agent's own MCP server),
    also marks quality/source and updates participant status."""
    result = db.save_response(round_id, agent_name, content)
    if mark_mcp_source:
        db.mark_response_quality_and_source(round_id, agent_name, "self_saved", "mcp")
        db.update_participant_status(round_id, agent_name, "responded", quality="self_saved")
    return result


def handle_get_response(db: BrainstormDB, round_id: str, agent_name: str) -> dict | str:
    result = db.get_response(round_id, agent_name)
    if not result:
        return f"No response from {agent_name} in round {round_id}"
    return result


def handle_get_round_responses(db: BrainstormDB, round_id: str) -> list:
    return db.get_round_responses(round_id)


# -- Consensus tools --

def handle_save_consensus(
    db: BrainstormDB, session_id: str, content: str, round_id: str | None = None,
) -> dict:
    return db.save_consensus(session_id, content, round_id)


def handle_get_consensus(db: BrainstormDB, session_id: str) -> dict | str:
    result = db.get_latest_consensus(session_id)
    if not result:
        return f"No consensus yet for session {session_id}"
    return result


# -- Context tools --

def handle_set_context(db: BrainstormDB, session_id: str, context: str) -> str:
    """Canonical form includes character count in confirmation."""
    db.set_context(session_id, context)
    return f"Context attached to session {session_id} ({len(context)} chars)."


# -- Feedback tools --

def handle_create_feedback(
    db: BrainstormDB, session_id: str, source_round_id: str,
    source_agent: str, title: str, content: str,
) -> dict:
    """Canonical param name: source_round_id (not round_id)."""
    return db.create_feedback_item(session_id, source_round_id, source_agent, title, content)


def handle_list_feedback(
    db: BrainstormDB, session_id: str, status: str | None = None,
) -> list:
    """Canonical form: returns raw list (not {"feedback_items": ...} wrapper)."""
    return db.list_feedback_items(session_id, status)


def handle_get_feedback(db: BrainstormDB, item_id: str) -> dict | str:
    result = db.get_feedback_item(item_id)
    if not result:
        return f"Feedback item {item_id} not found."
    return result


def handle_respond_to_feedback(
    db: BrainstormDB, item_id: str, round_id: str,
    agent_name: str, verdict: str, reasoning: str,
) -> dict | str:
    valid_verdicts = ("accept", "reject", "modify")
    if verdict.lower() not in valid_verdicts:
        return f"Invalid verdict '{verdict}'. Must be one of: {', '.join(valid_verdicts)}"
    return db.save_feedback_response(item_id, round_id, agent_name, verdict.lower(), reasoning)


def handle_batch_respond(
    db: BrainstormDB, round_id: str, agent_name: str, verdicts: str,
) -> dict | str:
    """Parse JSON verdicts string, validate, and batch-save."""
    try:
        items = json.loads(verdicts)
    except (ValueError, TypeError):
        return "Invalid JSON in verdicts parameter."
    valid_verdicts = ("accept", "reject", "modify")
    for v in items:
        if not all(k in v for k in ("item_id", "verdict", "reasoning")):
            return f"Each verdict must have item_id, verdict, reasoning. Got: {list(v.keys())}"
        if v["verdict"].lower() not in valid_verdicts:
            return f"Invalid verdict '{v['verdict']}'. Must be one of: {', '.join(valid_verdicts)}"
        v["verdict"] = v["verdict"].lower()
    results = db.batch_save_feedback_responses(round_id, agent_name, items)
    return {"saved": len(results), "verdicts": results}


def handle_update_feedback_status(db: BrainstormDB, item_id: str, status: str) -> str:
    db.update_feedback_status(item_id, status)
    return f"Feedback item {item_id} status updated to '{status}'."


# -- Role tools --

def handle_set_role(
    db: BrainstormDB, session_id: str, agent_name: str, role: str,
) -> dict:
    return db.set_role(session_id, agent_name, role)


def handle_get_role(db: BrainstormDB, session_id: str, agent_name: str) -> dict | str:
    result = db.get_role(session_id, agent_name)
    if not result:
        return f"No role set for {agent_name} in session {session_id}."
    return result


# -- Guideline tools --

def handle_add_guideline(db: BrainstormDB, session_id: str, content: str) -> dict:
    return db.add_guideline(session_id, content)


def handle_list_guidelines(db: BrainstormDB, session_id: str) -> list:
    return db.list_guidelines(session_id)


# -- Briefing tool --

def handle_get_briefing(
    svc: BrainstormService, session_id: str, agent_name: str,
) -> dict:
    return svc.get_agent_briefing(session_id, agent_name)


# -- History tool --

def handle_session_history(db: BrainstormDB, session_id: str) -> dict:
    return db.get_session_history(session_id)


# -- Onboarding tool --

def handle_get_onboarding(
    db: BrainstormDB, svc: BrainstormService,
    agent_name: str, session_id: str | None = None,
    round_id: str | None = None,
) -> dict:
    """Full onboarding with participation tracking when round_id is provided."""
    result = svc.get_onboarding_briefing(agent_name, session_id, round_id)
    if round_id:
        participant = db.get_participant(round_id, agent_name)
        if participant:
            result["your_participation"] = {
                "status": participant["status"],
                "feedback_items_expected": participant["feedback_items_expected"],
                "feedback_items_completed": participant["feedback_items_completed"],
            }
            if participant["feedback_items_expected"] > 0:
                result["your_participation"]["instruction"] = (
                    f"You MUST vote on ALL {participant['feedback_items_expected']} "
                    "feedback items before saving your response."
                )
    return result


# -- Workflow / tool-guide tools --

def handle_get_workflow(db: BrainstormDB, name: str = "brainstorm_3phase") -> dict | str:
    wf = db.get_workflow_template(name)
    if not wf:
        return f"Workflow '{name}' not found. Run 'seed-defaults' to populate."
    wf["phases"] = json.loads(wf["phases"])
    return wf


def handle_get_tool_guide(db: BrainstormDB, tool_name: str) -> dict | str:
    guide = db.get_tool_guide(tool_name)
    if not guide:
        return f"No guide found for tool '{tool_name}'."
    return guide


def handle_list_tool_guides(db: BrainstormDB, phase: str | None = None) -> list:
    return db.list_tool_guides(phase)


# -- Role library tools --

def handle_list_roles(
    db: BrainstormDB, agent_name: str | None = None, tag: str | None = None,
) -> list:
    roles = db.list_role_templates(agent_name, tag)
    return [
        {
            "slug": r["slug"],
            "display_name": r["display_name"],
            "agent_name": r["agent_name"],
            "description": r["description"],
            "tags": r["tags"],
            "usage_count": r["usage_count"],
            "has_behavior_definition": bool(r.get("vision") or r.get("behavior")),
        }
        for r in roles
    ]


def handle_suggest_roles(
    db: BrainstormDB, topic: str,
    agents: str | None = None, top_n: int = 6, diversify: bool = False,
) -> dict:
    agent_list = [a.strip() for a in agents.split(",") if a.strip()] if agents else []
    return db.suggest_roles(topic, agent_list, top_n=top_n, diversify=diversify)


def handle_get_role_template(db: BrainstormDB, slug: str) -> dict | str:
    result = db.get_role_template(slug)
    if not result:
        return f"Role template '{slug}' not found."
    return result


# -- Admin tools (shared between servers that expose them) --

def handle_set_agent_definition(
    db: BrainstormDB, agent_name: str, display_name: str,
    capabilities: str, default_role: str, approach: str,
) -> dict:
    return db.upsert_agent_definition(agent_name, display_name, capabilities, default_role, approach)


def handle_set_workflow_template(
    db: BrainstormDB, name: str, overview: str,
    phases_json: str, convergence_rules: str, response_format: str,
) -> dict:
    return db.upsert_workflow_template(name, overview, phases_json, convergence_rules, response_format)


def handle_set_tool_guide(
    db: BrainstormDB, tool_name: str, phase: str, purpose: str, usage: str,
) -> dict:
    return db.upsert_tool_guide(tool_name, phase, purpose, usage)
