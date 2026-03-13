#!/usr/bin/env python
"""CLI for brainstorm DB operations — usable by any agent that can execute commands.

Examples:
    # List active sessions
    python brainstorm_cli.py list-sessions

    # Get full session history
    python brainstorm_cli.py session-history --session-id bs_abc123

    # Get your role for a session
    python brainstorm_cli.py get-role --session-id bs_abc123 --agent copilot

    # List feedback items to review
    python brainstorm_cli.py list-feedback --session-id bs_abc123

    # Get a specific feedback item with all responses
    python brainstorm_cli.py get-feedback --item-id fb_abc123

    # Submit your verdict on a feedback item
    python brainstorm_cli.py respond-feedback --item-id fb_abc123 --round-id r_abc123 --agent copilot --verdict accept --reasoning "Confirmed by reading code"

    # Save your response for a round
    python brainstorm_cli.py save-response --round-id r_abc123 --agent copilot --content "My analysis..."

    # Save consensus
    python brainstorm_cli.py save-consensus --session-id bs_abc123 --content "Agreed approach..."
"""

import argparse
import json
import sys
from pathlib import Path

from brainstorm_db import BrainstormDB

_DB_PATH = Path(__file__).parent / ".data" / "brainstorm.db"


def _read_content(args) -> str:
    """Read content from --content arg or stdin."""
    if hasattr(args, "content") and args.content:
        return args.content
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    print("Error: provide --content or pipe content via stdin", file=sys.stderr)
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Brainstorm session CLI for multi-agent collaboration",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--db", default=str(_DB_PATH), help="Path to brainstorm DB (default: .data/brainstorm.db)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # -- Read operations --

    sub.add_parser("list-sessions", help="List brainstorming sessions")

    p = sub.add_parser("session-history", help="Full session dump with all rounds/responses/feedback/consensus")
    p.add_argument("--session-id", required=True)

    p = sub.add_parser("get-responses", help="Get all agent responses for a round")
    p.add_argument("--round-id", required=True)

    p = sub.add_parser("get-consensus", help="Get latest consensus for a session")
    p.add_argument("--session-id", required=True)

    p = sub.add_parser("get-role", help="Get your role definition for a session")
    p.add_argument("--session-id", required=True)
    p.add_argument("--agent", required=True, help="Agent name (copilot, gemini, claude)")

    p = sub.add_parser("list-feedback", help="List feedback items for a session")
    p.add_argument("--session-id", required=True)
    p.add_argument("--status", default=None, help="Filter by status (pending, accepted, rejected, modified)")

    p = sub.add_parser("get-feedback", help="Get a feedback item with all agent responses")
    p.add_argument("--item-id", required=True)

    # -- Write operations --

    p = sub.add_parser("save-response", help="Save your response for a brainstorm round")
    p.add_argument("--round-id", required=True, help="Round ID (e.g. r_abc123)")
    p.add_argument("--agent", required=True, help="Your agent name (e.g. copilot, gemini, claude)")
    p.add_argument("--content", default=None, help="Response content (or pipe via stdin)")

    p = sub.add_parser("respond-feedback", help="Submit your verdict on a feedback item")
    p.add_argument("--item-id", required=True, help="Feedback item ID (e.g. fb_abc123)")
    p.add_argument("--round-id", required=True, help="Current deliberation round ID")
    p.add_argument("--agent", required=True, help="Your agent name")
    p.add_argument("--verdict", required=True, choices=["accept", "reject", "modify"],
                   help="Your verdict: accept, reject, or modify")
    p.add_argument("--reasoning", required=True, help="Why you chose this verdict")

    p = sub.add_parser("save-consensus", help="Save a consensus document for a session")
    p.add_argument("--session-id", required=True)
    p.add_argument("--round-id", default=None, help="Optional: specific round this consensus covers")
    p.add_argument("--content", default=None, help="Consensus text (or pipe via stdin)")

    # -- Self-describing DB commands --

    sub.add_parser("seed-defaults", help="Populate agent definitions, workflow templates, and tool guides with defaults")

    p = sub.add_parser("get-onboarding", help="Get full onboarding briefing for an agent")
    p.add_argument("--agent", required=True, help="Agent name (copilot, gemini, claude)")
    p.add_argument("--session-id", default=None, help="Optional: session ID for session-specific data")

    args = parser.parse_args()
    db = BrainstormDB(args.db)

    try:
        if args.command == "list-sessions":
            result = db.list_sessions()

        elif args.command == "session-history":
            result = db.get_session_history(args.session_id)

        elif args.command == "get-responses":
            result = db.get_round_responses(args.round_id)

        elif args.command == "get-consensus":
            result = db.get_latest_consensus(args.session_id)
            if not result:
                result = {"message": f"No consensus yet for session {args.session_id}"}

        elif args.command == "get-role":
            result = db.get_role(args.session_id, args.agent)
            if not result:
                result = {"message": f"No role set for {args.agent} in session {args.session_id}"}

        elif args.command == "list-feedback":
            result = db.list_feedback_items(args.session_id, args.status)

        elif args.command == "get-feedback":
            result = db.get_feedback_item(args.item_id)
            if not result:
                result = {"message": f"Feedback item {args.item_id} not found"}

        elif args.command == "save-response":
            content = _read_content(args)
            result = db.save_response(args.round_id, args.agent, content)

        elif args.command == "respond-feedback":
            result = db.save_feedback_response(
                args.item_id, args.round_id, args.agent, args.verdict, args.reasoning,
            )

        elif args.command == "save-consensus":
            content = _read_content(args)
            result = db.save_consensus(args.session_id, content, args.round_id)

        elif args.command == "seed-defaults":
            from brainstorm_seeds import AGENT_DEFINITIONS, WORKFLOW_TEMPLATES, TOOL_GUIDES, ROLE_TEMPLATES
            counts = {"agents": 0, "workflows": 0, "tools": 0, "roles": 0, "roles_updated": 0, "roles_renamed": 0}
            for defn in AGENT_DEFINITIONS:
                db.upsert_agent_definition(**defn)
                counts["agents"] += 1
            for wf in WORKFLOW_TEMPLATES:
                phases_json = json.dumps(wf["phases"])
                db.upsert_workflow_template(
                    name=wf["name"], overview=wf["overview"],
                    phases_json=phases_json,
                    convergence_rules=wf["convergence_rules"],
                    response_format=wf["response_format"],
                )
                counts["workflows"] += 1
            for guide in TOOL_GUIDES:
                db.upsert_tool_guide(**guide)
                counts["tools"] += 1
            # Slug renames: rename old slugs to new slugs when old exists but new doesn't
            SLUG_RENAMES = {
                "copilot-code-verifier": "code-verifier",
                "gemini-research-analyst": "research-analyst",
            }
            for old_slug, new_slug in SLUG_RENAMES.items():
                if db.get_role_template(old_slug) and not db.get_role_template(new_slug):
                    db.update_role_template(old_slug, new_slug=new_slug)
                    counts["roles_renamed"] += 1
            for role in ROLE_TEMPLATES:
                existing = db.get_role_template(role["slug"])
                if not existing:
                    db.create_role_template(**role)
                    counts["roles"] += 1
                else:
                    update_kwargs = {
                        k: role[k] for k in (
                            "vision", "angle", "behavior", "mandates",
                            "display_name", "description", "agent_name", "tags",
                        ) if k in role
                    }
                    if update_kwargs:
                        db.update_role_template(role["slug"], **update_kwargs)
                        counts["roles_updated"] += 1
            result = {"seeded": counts, "status": "ok"}

        elif args.command == "get-onboarding":
            result = db.get_onboarding_briefing(args.agent, args.session_id)

        print(json.dumps(result, indent=2))
    finally:
        db.close()


if __name__ == "__main__":
    main()
