"""Default seed data for brainstorm self-describing DB tables.

Run via: python brainstorm_cli.py seed-defaults
All upserts — safe to re-run.
"""

# -- Agent Definitions --

AGENT_DEFINITIONS = [
    {
        "agent_name": "copilot",
        "display_name": "GitHub Copilot",
        "capabilities": (
            "Strong at code analysis, shell/git operations, grep-based verification, "
            "GitHub CLI integration, and reading/navigating large codebases. "
            "Has access to MCP tools (brainstorm + atlas) and can execute shell commands. "
            "Best used for concrete code-level tasks: verifying claims against source, "
            "finding bugs, checking implementations, running tests."
        ),
        "default_role": (
            "Critical code reviewer. Verify all claims against source code — cite file paths "
            "and line numbers. Don't accept or reject without evidence. Read the actual code "
            "before forming opinions. If you can't verify a claim, say so explicitly."
        ),
        "approach": (
            "Read code first, form opinions second. Use grep/find to locate relevant files. "
            "Cite specific file paths and line numbers in every verdict. Never make claims "
            "without evidence from the codebase. If a tool fails, skip it and move on."
        ),
    },
    {
        "agent_name": "gemini",
        "display_name": "Google Gemini",
        "capabilities": (
            "Strong at code generation, architectural analysis, research, alternative "
            "approaches, documentation review, and broad technical knowledge. "
            "Has access to MCP tools (brainstorm + atlas) and can search the web. "
            "Best used for design-level analysis: evaluating architecture, suggesting "
            "alternatives, identifying patterns and anti-patterns."
        ),
        "default_role": (
            "Architecture analyst and design reviewer. Evaluate proposals for architectural "
            "soundness, maintainability, and alignment with best practices. Consider "
            "alternatives and trade-offs. Cite specific code when possible, but also bring "
            "broader design perspective."
        ),
        "approach": (
            "Think architecturally — consider maintainability, scalability, and design patterns. "
            "Read code to ground your analysis, but also consider the bigger picture. "
            "Suggest alternatives when you see a better approach. If a tool fails, skip it and move on."
        ),
    },
    {
        "agent_name": "claude",
        "display_name": "Claude (Orchestrator)",
        "capabilities": (
            "Orchestrator and synthesizer. Creates sessions, manages rounds, extracts feedback "
            "items from agent responses, drives convergence, and writes final consensus documents. "
            "Has direct DB access via CLI. Best at: complex reasoning, synthesis across multiple "
            "perspectives, making final judgment calls."
        ),
        "default_role": (
            "Orchestrator and final arbiter. Synthesize perspectives from all agents into "
            "coherent consensus. Make judgment calls on contested items after max rounds. "
            "Ensure the final document is actionable and complete."
        ),
        "approach": (
            "Coordinate the overall workflow. Extract clear feedback items from Phase 1 responses. "
            "Drive deliberation toward convergence. Synthesize final consensus that captures the "
            "best insights from all agents. Be decisive on contested items."
        ),
    },
]

# -- Workflow Template --

WORKFLOW_TEMPLATES = [
    {
        "name": "brainstorm_3phase",
        "overview": (
            "You are participating in a structured multi-AI brainstorm. Three agents "
            "(Claude, Copilot, Gemini) collaborate through a shared SQLite database. "
            "Each agent has its own MCP server instance for reading/writing brainstorm records. "
            "The process has 3 phases: independent analysis, deliberation with verdicts, "
            "and final consolidation. Work independently — read DB records, analyze code, "
            "and save your findings via MCP tools."
        ),
        "phases": [
            {
                "name": "Phase 1: Independent Analysis",
                "objective": "Each agent analyzes the topic independently without seeing others' work.",
                "agent_actions": (
                    "Analyze the topic thoroughly. Read relevant code. Form your own conclusions. "
                    "Save your analysis via bs_save_response(round_id, agent_name, content). "
                    "Store important findings in Atlas via atlas_store."
                ),
                "expected_outputs": (
                    "A comprehensive analysis response saved to the DB. Should include: "
                    "key findings, concerns, recommendations, and evidence from code."
                ),
            },
            {
                "name": "Phase 2: Deliberation",
                "objective": "Review feedback items created from Phase 1 and submit verdicts.",
                "agent_actions": (
                    "1. Call bs_list_feedback(session_id) to see all items to review.\n"
                    "2. For each item, call bs_get_feedback(item_id) to read details and existing verdicts.\n"
                    "3. Read source code to verify claims made in the feedback.\n"
                    "4. Call bs_respond_to_feedback(item_id, round_id, agent_name, verdict, reasoning) "
                    "for each item.\n"
                    "5. Do ALL items. If a tool fails, skip and move on."
                ),
                "expected_outputs": (
                    "A verdict (accept/reject/modify) with evidence-based reasoning for every "
                    "feedback item. Cite file paths and line numbers where possible."
                ),
            },
            {
                "name": "Phase 3: Consolidation",
                "objective": "Synthesize all verdicts into a final consensus document.",
                "agent_actions": (
                    "Claude (orchestrator) reviews all verdicts, resolves contested items, "
                    "and writes a final consensus document via bs_save_consensus. "
                    "Other agents do not participate in this phase."
                ),
                "expected_outputs": (
                    "A comprehensive consensus document covering all feedback items with "
                    "final decisions, rationale, and action items."
                ),
            },
        ],
        "convergence_rules": (
            "After each deliberation round, Claude checks convergence per feedback item:\n"
            "- Unanimous ACCEPT (all 3 agents) → item status = 'accepted'\n"
            "- Unanimous REJECT (all 3 agents) → item status = 'rejected'\n"
            "- Mixed verdicts → item stays 'pending', another round for contested items only\n"
            "- After 5 rounds with no convergence → 2-1 majority wins\n"
            "- If still tied after 5 rounds → Claude makes the final call"
        ),
        "response_format": (
            "When responding to feedback items, you MUST provide:\n"
            "- verdict: exactly one of 'accept', 'reject', or 'modify'\n"
            "- reasoning: evidence-based explanation (required, not optional)\n\n"
            "Quality expectations:\n"
            "- Cite specific file paths and line numbers when referencing code\n"
            "- Don't just agree/disagree — explain WHY with evidence\n"
            "- If you can't verify a claim, say 'unable to verify' rather than guessing\n"
            "- 'modify' means you agree with the spirit but want changes — explain what changes"
        ),
    },
]

# -- Tool Guides --

TOOL_GUIDES = [
    {
        "tool_name": "bs_new_session",
        "phase": "setup",
        "purpose": "Start a new brainstorming session. Creates the session record that all other operations reference.",
        "usage": "bs_new_session(topic='What to brainstorm', project='project-name'). Returns session ID (bs_xxx).",
    },
    {
        "tool_name": "bs_set_context",
        "phase": "setup",
        "purpose": "Attach codebase/project context to a session. This context is available to all agents via bs_get_briefing or bs_get_onboarding.",
        "usage": "bs_set_context(session_id='bs_xxx', context='Summary of relevant code, architecture, key files, constraints.')",
    },
    {
        "tool_name": "bs_set_role",
        "phase": "setup",
        "purpose": "Set a session-specific role for an agent. Overrides the agent's default_role for this session.",
        "usage": "bs_set_role(session_id='bs_xxx', agent_name='copilot', role='Focus on security vulnerabilities in auth code.')",
    },
    {
        "tool_name": "bs_add_guideline",
        "phase": "setup",
        "purpose": "Add a session guideline (must-do rule). All agents see guidelines via briefing.",
        "usage": "bs_add_guideline(session_id='bs_xxx', content='Always verify claims by reading source code.')",
    },
    {
        "tool_name": "bs_get_onboarding",
        "phase": "any",
        "purpose": "Primary entry point for agents. Returns your identity, workflow overview, phases, convergence rules, response format, tool guides, and session context if available. Call this FIRST when starting work.",
        "usage": "bs_get_onboarding(agent_name='copilot'). Optional: session_id for session-specific context.",
    },
    {
        "tool_name": "bs_get_briefing",
        "phase": "any",
        "purpose": "Get session-specific briefing: context, your role, and guidelines. Use when you already know the workflow but need session details.",
        "usage": "bs_get_briefing(session_id='bs_xxx', agent_name='copilot'). Returns context, role (or default_role), guidelines.",
    },
    {
        "tool_name": "bs_new_round",
        "phase": "phase1",
        "purpose": "Create a new round in a session. Round number auto-increments. Used by Claude to start each phase/deliberation round.",
        "usage": "bs_new_round(session_id='bs_xxx', objective='Phase 1: Independent analysis'). Returns round ID (r_xxx).",
    },
    {
        "tool_name": "bs_save_response",
        "phase": "phase1",
        "purpose": "Save your analysis response for a round. Replaces any existing response from the same agent.",
        "usage": "bs_save_response(round_id='r_xxx', agent_name='copilot', content='My full analysis...')",
    },
    {
        "tool_name": "bs_create_feedback",
        "phase": "phase1",
        "purpose": "Create a feedback item from Phase 1 findings. Claude creates these after reviewing agent responses.",
        "usage": "bs_create_feedback(session_id='bs_xxx', source_round_id='r_xxx', source_agent='copilot', title='Short title', content='Detailed description')",
    },
    {
        "tool_name": "bs_list_feedback",
        "phase": "phase2",
        "purpose": "List all feedback items for a session. Use this to see what items need your verdict.",
        "usage": "bs_list_feedback(session_id='bs_xxx'). Optional: status='pending' to filter. Returns list of items with IDs.",
    },
    {
        "tool_name": "bs_get_feedback",
        "phase": "phase2",
        "purpose": "Get a specific feedback item with all existing verdicts from other agents. Read this before submitting your own verdict.",
        "usage": "bs_get_feedback(item_id='fb_xxx'). Returns title, content, status, and all agent responses/verdicts.",
    },
    {
        "tool_name": "bs_respond_to_feedback",
        "phase": "phase2",
        "purpose": "Submit your verdict on a feedback item. Must include evidence-based reasoning.",
        "usage": "bs_respond_to_feedback(item_id='fb_xxx', round_id='r_xxx', agent_name='copilot', verdict='accept|reject|modify', reasoning='Evidence-based explanation with file paths')",
    },
    {
        "tool_name": "bs_update_feedback_status",
        "phase": "phase2",
        "purpose": "Update a feedback item's status after convergence. Claude uses this to mark items as accepted/rejected.",
        "usage": "bs_update_feedback_status(item_id='fb_xxx', status='accepted|rejected|modified|consolidated')",
    },
    {
        "tool_name": "bs_save_consensus",
        "phase": "phase3",
        "purpose": "Save the final consensus document. Version auto-increments if saved multiple times.",
        "usage": "bs_save_consensus(session_id='bs_xxx', content='Final consensus text...', round_id='r_xxx')",
    },
    {
        "tool_name": "bs_complete_session",
        "phase": "phase3",
        "purpose": "Mark a session as completed. Do this after saving the final consensus.",
        "usage": "bs_complete_session(session_id='bs_xxx')",
    },
    {
        "tool_name": "bs_get_workflow",
        "phase": "any",
        "purpose": "Read the workflow template (phases, convergence rules, response format). Included in onboarding but available separately.",
        "usage": "bs_get_workflow(). Optional: name='brainstorm_3phase' (default).",
    },
    {
        "tool_name": "bs_list_tool_guides",
        "phase": "any",
        "purpose": "List all tool guides, optionally filtered by phase. Useful to see what tools are available for a specific phase.",
        "usage": "bs_list_tool_guides(). Optional: phase='phase2' to filter.",
    },
]
