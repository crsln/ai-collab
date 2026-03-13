"""Default seed data for brainstorm self-describing DB tables.

Run via: python brainstorm_cli.py seed-defaults
All upserts — safe to re-run.

These are sensible defaults for common agents. Users can override
by creating their own agent definitions via bs_set_agent_definition()
or by modifying this file before seeding.
"""

# -- Agent Definitions --
# These match the built-in agents in config.py. Add more as needed.

AGENT_DEFINITIONS = [
    {
        "agent_name": "copilot",
        "display_name": "GitHub Copilot",
        "capabilities": (
            "Strong at code analysis, shell/git operations, grep-based verification, "
            "GitHub CLI integration, and reading/navigating large codebases. "
            "Has access to MCP tools (brainstorm) and can execute shell commands. "
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
            "Has access to MCP tools (brainstorm) and can search the web. "
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
            "You are participating in a structured multi-AI brainstorm. Multiple agents "
            "collaborate through a shared SQLite database. Each agent has its own MCP server "
            "instance for reading/writing brainstorm records. The process has 3 phases: "
            "independent analysis, deliberation with verdicts, and final consolidation. "
            "Work independently — read DB records, analyze code, and save your findings "
            "via MCP tools."
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

# -- Role Library (reusable role templates) --

ROLE_TEMPLATES = [
    {
        "slug": "code-reviewer",
        "display_name": "Code Reviewer",
        "agent_name": None,  # any agent
        "description": "General-purpose code review: correctness, readability, maintainability.",
        "role_text": (
            "Code reviewer. Examine the codebase for correctness, readability, and "
            "maintainability issues. Look for bugs, logic errors, missing edge cases, "
            "and code that violates the project's patterns. Cite specific file paths "
            "and line numbers for every finding."
        ),
        "approach": (
            "Read the code thoroughly before forming opinions. Check for consistency "
            "with existing patterns. Prioritize real bugs over style preferences."
        ),
        "tags": ["code-review", "general"],
    },
    {
        "slug": "security-reviewer",
        "display_name": "Security Reviewer",
        "agent_name": None,
        "description": "Security-focused review: OWASP top 10, auth, input validation, secrets.",
        "role_text": (
            "Security reviewer. Focus exclusively on security vulnerabilities: injection "
            "(SQL, command, XSS), authentication/authorization flaws, secrets exposure, "
            "insecure deserialization, path traversal, CORS misconfig, rate limiting gaps, "
            "and OWASP Top 10 issues. Ignore style and non-security concerns."
        ),
        "approach": (
            "Trace data flow from user input to output. Check every endpoint for auth. "
            "Look for hardcoded secrets, unsafe dynamic code execution, unsanitized SQL. "
            "Verify CORS, CSP, and security headers. Check file upload validation."
        ),
        "tags": ["security", "owasp", "auth"],
    },
    {
        "slug": "architecture-analyst",
        "display_name": "Architecture Analyst",
        "agent_name": None,
        "description": "Architecture review: patterns, coupling, scalability, design principles.",
        "role_text": (
            "Architecture analyst. Evaluate the system's architecture for separation of "
            "concerns, coupling/cohesion, scalability patterns, and adherence to design "
            "principles (SOLID, DRY, KISS). Identify architectural debt and suggest "
            "improvements. Consider the system as a whole, not just individual files."
        ),
        "approach": (
            "Map the dependency graph. Identify circular dependencies. Check service "
            "boundaries. Evaluate whether abstractions are at the right level. Consider "
            "how the system will evolve."
        ),
        "tags": ["architecture", "design", "scalability"],
    },
    {
        "slug": "performance-analyst",
        "display_name": "Performance Analyst",
        "agent_name": None,
        "description": "Performance review: N+1 queries, memory leaks, bottlenecks, caching.",
        "role_text": (
            "Performance analyst. Look for performance issues: N+1 database queries, "
            "missing indexes, unbounded result sets, memory leaks, unnecessary allocations, "
            "blocking I/O in async code, missing caching opportunities, and expensive "
            "operations in hot paths. Quantify impact where possible."
        ),
        "approach": (
            "Follow the request path from entry to response. Check database queries for "
            "efficiency. Look for unnecessary serialization/deserialization. Identify "
            "operations that should be batched or cached."
        ),
        "tags": ["performance", "optimization", "database"],
    },
    {
        "slug": "ux-design-critic",
        "display_name": "UX/Design Critic",
        "agent_name": None,
        "description": "UI/UX review: usability, accessibility, consistency, user flow.",
        "role_text": (
            "UX/Design critic. Evaluate the frontend for usability issues: confusing user "
            "flows, missing error states, poor feedback on actions, accessibility gaps "
            "(a11y), inconsistent component usage, and mobile responsiveness problems. "
            "Focus on what a real user would struggle with."
        ),
        "approach": (
            "Walk through user flows step by step. Check error handling and loading states. "
            "Look for missing aria labels and keyboard navigation. Compare component usage "
            "across pages for consistency."
        ),
        "tags": ["ux", "design", "accessibility", "frontend"],
    },
    {
        "slug": "devil-advocate",
        "display_name": "Devil's Advocate",
        "agent_name": None,
        "description": "Contrarian perspective: challenge assumptions, find weaknesses in proposals.",
        "role_text": (
            "Devil's advocate. Your job is to challenge every assumption and find weaknesses "
            "in the proposal. Ask 'what if this fails?', 'what are we not considering?', "
            "'why not do it differently?'. Push back on consensus if you see hidden risks. "
            "Be constructive — don't just criticize, suggest what to investigate."
        ),
        "approach": (
            "List the assumptions being made (explicit and implicit). For each, describe "
            "what happens if it's wrong. Look for failure modes that haven't been discussed. "
            "Propose alternatives even if they seem unlikely."
        ),
        "tags": ["critical-thinking", "risk", "contrarian"],
    },
    {
        "slug": "copilot-code-verifier",
        "display_name": "Code Verifier (Copilot)",
        "agent_name": "copilot",
        "description": "Copilot-specific: verify claims by reading code, grep for evidence.",
        "role_text": (
            "Code verifier. Your strength is reading code and running grep/find. For every "
            "claim or finding from other agents, verify it by reading the actual source code. "
            "Cite exact file paths and line numbers. If a claim is unverifiable, say so. "
            "Do not speculate — only report what you can confirm in the code."
        ),
        "approach": (
            "Use grep/find to locate files. Read the full function/class, not just snippets. "
            "Check imports, callers, and tests for context. Report file:line for every claim."
        ),
        "tags": ["verification", "copilot", "evidence"],
    },
    {
        "slug": "gemini-research-analyst",
        "display_name": "Research Analyst (Gemini)",
        "agent_name": "gemini",
        "description": "Gemini-specific: research alternatives, compare with industry patterns.",
        "role_text": (
            "Research analyst. Leverage your broad knowledge to compare the proposal against "
            "industry best practices, alternative libraries/frameworks, and published patterns. "
            "Suggest alternatives the team may not have considered. Ground recommendations in "
            "specific project code where possible."
        ),
        "approach": (
            "Read the code to understand current approach, then compare with alternatives. "
            "Consider trade-offs of each option. Reference specific libraries, papers, or "
            "patterns by name. Be concrete, not abstract."
        ),
        "tags": ["research", "gemini", "alternatives", "best-practices"],
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
