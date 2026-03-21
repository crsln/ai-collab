"""Brainstorm Service — orchestration and policy logic on top of BrainstormDB.

Extracted from brainstorm_db.py to separate CRUD/DDL (BrainstormDB) from
orchestration concerns (onboarding, completion gates, quality classification).
"""

from __future__ import annotations

from brainstorm_db import BrainstormDB


def classify_response_quality(content: str | None) -> str:
    """Classify a response as valid/empty/error based on content heuristics."""
    if not content or not content.strip():
        return "empty"
    stripped = content.strip()
    if stripped.startswith("[ERROR]"):
        return "error"
    if len(stripped.split()) < 5:
        return "empty"
    return "valid"


class BrainstormService:
    """Orchestration and policy logic wrapping a BrainstormDB instance.

    Handles onboarding assembly, completion gates, phase detection,
    quality classification, and session briefings. All data access
    is delegated to self._db.
    """

    def __init__(self, db: BrainstormDB):
        self._db = db

    # -- Onboarding (composite) --

    def get_onboarding_briefing(
        self, agent_name: str, session_id: str | None = None,
        round_id: str | None = None,
    ) -> dict:
        """Build full onboarding response: identity + workflow + tools + optional session context.

        Phase-aware: when feedback items exist for the session, includes current_phase='deliberation'
        with explicit instructions and feedback item IDs so agents know they must vote, not analyze.
        """
        import json as _json

        # Agent identity
        defn = self._db.get_agent_definition(agent_name)
        identity = None
        if defn:
            identity = {
                "agent_name": defn["agent_name"],
                "display_name": defn["display_name"],
                "capabilities": defn["capabilities"],
                "default_role": defn["default_role"],
                "approach": defn["approach"],
                "vision": defn.get("vision"),
                "angle": defn.get("angle"),
                "behavior": defn.get("behavior"),
                "tags": defn.get("tags") or [],
            }

        # Workflow
        wf = self._db.get_workflow_template("brainstorm_3phase")
        workflow = None
        if wf:
            workflow = {
                "name": wf["name"],
                "overview": wf["overview"],
                "phases": _json.loads(wf["phases"]),
                "convergence_rules": wf["convergence_rules"],
                "response_format": wf["response_format"],
            }

        # Tool guides
        tools = self._db.list_tool_guides()
        tool_list = [
            {"tool_name": t["tool_name"], "phase": t["phase"],
             "purpose": t["purpose"], "usage": t["usage"]}
            for t in tools
        ]

        # Session-scoped data (if session_id provided)
        session_role = None
        session_context = None
        session_role_detail = None
        guidelines_list = []
        current_phase = None
        phase_instructions = None
        feedback_item_ids = []

        if session_id:
            context = self._db.get_context(session_id)
            session_context = context

            role_row = self._db.get_role(session_id, agent_name)
            if role_row:
                session_role = role_row["role"]
                if role_row.get("source_slug"):
                    tmpl = self._db.get_role_template(role_row["source_slug"])
                    if tmpl:
                        session_role_detail = {
                            "vision": tmpl.get("vision"),
                            "angle": tmpl.get("angle"),
                            "behavior": tmpl.get("behavior"),
                            "mandates": tmpl.get("mandates") or [],
                        }
            elif defn:
                session_role = defn["default_role"]

            guidelines = self._db.list_guidelines(session_id)
            guidelines_list = [g["content"] for g in guidelines]

            # Phase detection: feedback items exist -> deliberation mode
            feedback_items = self._db.list_feedback_items(session_id)
            if feedback_items:
                current_phase = "deliberation"
                feedback_item_ids = [item["id"] for item in feedback_items]
                round_ref = f", round_id='{round_id}'" if round_id else ""
                # DB-driven phase instructions (from workflow template)
                phases = _json.loads(wf["phases"]) if wf else []
                current_phase_def = next(
                    (p for p in phases if "deliberation" in p.get("name", "").lower()), None
                )
                if current_phase_def and current_phase_def.get("instructions"):
                    phase_instructions = current_phase_def["instructions"].format(
                        session_id=session_id,
                        round_id=round_id or "",
                        agent_name=agent_name,
                        feedback_item_ids=", ".join(feedback_item_ids),
                    )
                else:
                    fi_json = ", ".join(f'{{"item_id":"{fid}","verdict":"...","reasoning":"..."}}'
                                        for fid in feedback_item_ids)
                    phase_instructions = (
                        "YOU ARE IN PHASE 2: DELIBERATION. "
                        "You must review and vote on feedback items — do NOT do general analysis. "
                        "Follow these steps EXACTLY:\n"
                        f"1. Call bs_list_feedback(session_id='{session_id}') to see all items\n"
                        "2. For EACH item, call bs_get_feedback(item_id=<id>) to read the full "
                        "content and all agents' prior verdicts\n"
                        "3. Submit ALL verdicts in ONE call: bs_batch_respond("
                        f"round_id='{round_id or ''}', agent_name='{agent_name}', "
                        f"verdicts='[{fi_json}]')\n"
                        f"4. Call bs_save_response(round_id='{round_id or ''}', "
                        f"agent_name='{agent_name}', content='summary of your verdicts')\n"
                        f"\nFeedback item IDs: {', '.join(feedback_item_ids)}"
                    )
            else:
                current_phase = "analysis"

        # Prior work: agent's own responses from earlier rounds in this session
        prior_work = []
        if session_id and round_id:
            current_round = self._db.get_round(round_id)
            current_round_num = current_round["round_number"] if current_round else 999
            all_responses = self.get_agent_session_responses(session_id, agent_name)
            for resp in all_responses:
                if resp["round_number"] < current_round_num:
                    prior_work.append({
                        "round_id": resp["round_id"],
                        "round": resp["round_number"],
                        "phase": resp["phase"],
                        "objective": resp["objective"],
                    })

        # Task from round (question + objective)
        task = None
        if round_id:
            rnd = self._db.get_round(round_id)
            if rnd:
                task = {}
                if rnd.get("objective"):
                    task["objective"] = rnd["objective"]
                if rnd.get("question"):
                    task["question"] = rnd["question"]

        return {
            "your_identity": identity,
            "workflow": workflow,
            "tools": tool_list,
            "task": task,
            "session_role": session_role,
            "session_role_detail": session_role_detail,
            "session_context": session_context,
            "guidelines": guidelines_list,
            "current_phase": current_phase,
            "phase_instructions": phase_instructions,
            "feedback_item_ids": feedback_item_ids,
            "prior_work": prior_work,
        }

    # -- Session briefing (context + role + guidelines) --

    def get_agent_briefing(self, session_id: str, agent_name: str) -> dict:
        """Get everything an agent needs before starting work."""
        context = self._db.get_context(session_id)
        role = self._db.get_role(session_id, agent_name)
        if not role:
            defn = self._db.get_agent_definition(agent_name)
            role_text = defn["default_role"] if defn else None
        else:
            role_text = role["role"]
        guidelines = self._db.list_guidelines(session_id)
        return {
            "session_context": context,
            "your_role": role_text,
            "guidelines": [g["content"] for g in guidelines],
        }

    # -- Prior work query --

    def get_agent_session_responses(self, session_id: str, agent_name: str) -> list[dict]:
        """Fetch prior response references for an agent across all rounds in a session."""
        return self._db.get_agent_session_responses(session_id, agent_name)

    # -- Completion Gates --

    def check_round_complete(self, round_id: str) -> dict:
        """Check if all expected agents have valid responses."""
        participants = self._db.list_participants(round_id)
        if not participants:
            return {"complete": True, "total": 0, "responded": 0, "failed": 0, "pending": 0, "agents": {}}

        agents = {}
        responded = 0
        failed = 0
        pending = 0
        for p in participants:
            agents[p["agent_name"]] = {
                "status": p["status"],
                "quality": p["response_quality"],
                "error": p.get("error_detail"),
            }
            if p["status"] in ("responded", "validated"):
                responded += 1
            elif p["status"] in ("failed", "timed_out"):
                failed += 1
            else:
                pending += 1

        total = len(participants)
        return {
            "complete": responded == total and total > 0,
            "total": total,
            "responded": responded,
            "failed": failed,
            "pending": pending,
            "agents": agents,
        }

    def check_feedback_votes_complete(self, round_id: str, session_id: str) -> dict:
        """Check if all expected agents voted on all feedback items."""
        participants = self._db.list_participants(round_id)
        feedback_items = self._db.list_feedback_items(session_id, status="pending")
        if not participants or not feedback_items:
            return {"complete": True, "total_expected_votes": 0, "total_actual_votes": 0,
                    "agents": {}, "items": {}}

        item_ids = [f["id"] for f in feedback_items]
        agent_names = [p["agent_name"] for p in participants]
        total_expected = len(agent_names) * len(item_ids)

        # Count actual votes per agent and per item
        agents_info = {}
        items_info = {iid: {"votes": 0, "expected": len(agent_names), "complete": False} for iid in item_ids}
        total_actual = 0

        for p in participants:
            count = self._db.count_agent_feedback_votes(round_id, p["agent_name"], item_ids)
            agents_info[p["agent_name"]] = {
                "expected": len(item_ids),
                "completed": count,
                "complete": count >= len(item_ids),
            }
            total_actual += count

        for iid in item_ids:
            vote_count = self._db.count_item_votes(round_id, iid)
            items_info[iid]["votes"] = vote_count
            items_info[iid]["complete"] = vote_count >= len(agent_names)

        return {
            "complete": total_actual >= total_expected,
            "total_expected_votes": total_expected,
            "total_actual_votes": total_actual,
            "agents": agents_info,
            "items": items_info,
        }

    def check_phase_ready(self, session_id: str, target_phase: str) -> dict:
        """Check if preconditions are met to advance to target_phase."""
        rounds = self._db.list_rounds(session_id)
        if not rounds:
            return {"ready": False, "reason": "No rounds found", "blockers": ["no_rounds"]}

        if target_phase == "deliberation":
            last_round = rounds[-1]
            gate = self.check_round_complete(last_round["id"])
            if not gate["complete"]:
                failed = [a for a, s in gate["agents"].items() if s["status"] in ("failed", "timed_out")]
                empty = [a for a, s in gate["agents"].items() if s["quality"] in ("empty", "error")]
                blockers = [f"failed:{a}" for a in failed] + [f"bad_quality:{a}" for a in empty]
                return {
                    "ready": False,
                    "reason": f"Round incomplete: {gate['responded']}/{gate['total']} valid responses",
                    "blockers": blockers or ["incomplete_responses"],
                }
            return {"ready": True, "reason": "All agents responded with valid content", "blockers": []}

        if target_phase == "consolidation":
            last_round = rounds[-1]
            votes = self.check_feedback_votes_complete(last_round["id"], session_id)
            if not votes["complete"]:
                incomplete_agents = [a for a, s in votes["agents"].items() if not s["complete"]]
                return {
                    "ready": False,
                    "reason": f"Feedback votes incomplete: {votes['total_actual_votes']}/{votes['total_expected_votes']}",
                    "blockers": [f"missing_votes:{a}" for a in incomplete_agents],
                }
            return {"ready": True, "reason": "All feedback votes complete", "blockers": []}

        return {"ready": False, "reason": f"Unknown phase: {target_phase}", "blockers": ["unknown_phase"]}
