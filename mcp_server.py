"""MCP server exposing AI collaboration and brainstorming tools for Claude Code."""

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
from providers.errors import (
    ProviderExecution,
    ProviderTimeout,
    ProviderUnavailable,
)

log = logging.getLogger("ai-collab")
mcp = FastMCP("ai-collab")

_DB_PATH = Path(os.environ.get(
    "BRAINSTORM_DB",
    str(Path(__file__).parent / ".data" / "brainstorm.db"),
))
_db = BrainstormDB(_DB_PATH)

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]|\x1b\].*?\x07|\x1b[()][A-Z0-9]")
_SAVE_RESPONSES = os.environ.get("AI_COLLAB_SAVE_RESPONSES", "").lower() in ("1", "true", "yes")
_RESPONSES_DIR = Path(os.environ.get(
    "AI_COLLAB_RESPONSES_DIR",
    str(Path(__file__).parent / ".brainstorm"),
))
_DEFAULT_TIMEOUT = 900.0  # 15 min — agents with tool access need time


def _find_cmd(name: str) -> str:
    """Find a CLI executable, preferring .cmd shims on Windows."""
    if sys.platform == "win32":
        cmd = shutil.which(f"{name}.cmd") or shutil.which(name)
        if cmd:
            return cmd
    found = shutil.which(name)
    if found:
        return found
    raise ProviderUnavailable(name, f"executable '{name}' not found in PATH")


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


def _clean_output(output: str) -> str:
    """Strip ANSI codes and CLI footers from output."""
    output = _ANSI_RE.sub("", output)
    lines = output.strip().split("\n")
    content_lines = []
    for line in lines:
        if line.strip().startswith("Total usage est:"):
            break
        content_lines.append(line)
    return "\n".join(content_lines).strip()


def _format_error(provider: str, error_type: str, message: str, retryable: bool = False) -> str:
    """Format a structured error string for LLM consumption."""
    return f"[ERROR][provider={provider}][type={error_type}][retryable={str(retryable).lower()}] {message}"


async def _run_cli(
    cmd_name: str,
    question: str,
    *,
    model: str | None = None,
    cwd: str | None = None,
    extra_args: list[str] | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
) -> str:
    """Run a CLI tool and return cleaned output. Uses -p for short prompts, stdin for long ones."""
    try:
        cmd = _find_cmd(cmd_name)
    except ProviderUnavailable:
        return _format_error(cmd_name, "unavailable", f"executable '{cmd_name}' not found in PATH")

    args = [cmd]
    if model:
        args.extend(["--model", model])
    if extra_args:
        args.extend(extra_args)
    # Pipe long prompts via stdin to avoid Windows command line limit (8191 chars)
    stdin_input = None
    if sys.platform == "win32" and len(question) > 7000:
        stdin_input = question.encode("utf-8")
    else:
        args.extend(["-p", question])

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE if stdin_input is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
    except FileNotFoundError:
        return _format_error(cmd_name, "unavailable", f"executable '{cmd}' not found")
    except OSError as e:
        return _format_error(cmd_name, "execution", f"failed to start: {e}")

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=stdin_input), timeout=timeout
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        log.warning("%s timed out after %ss", cmd_name, timeout)
        return _format_error(cmd_name, "timeout", f"did not respond within {timeout}s", retryable=True)

    if proc.returncode != 0:
        err = stderr.decode("utf-8", errors="replace").strip()
        log.warning("%s exited with code %d: %s", cmd_name, proc.returncode, err)
        return _format_error(cmd_name, "execution", f"exited with code {proc.returncode}: {err}")

    output = stdout.decode("utf-8", errors="replace")
    response = _clean_output(output)

    if _SAVE_RESPONSES:
        _save_response(cmd_name, question, response)

    return response


@mcp.tool()
async def ask_copilot(question: str, cwd: str | None = None) -> str:
    """Ask GitHub Copilot CLI a question.

    Best for: shell commands, git operations, GitHub CLI usage, quick code snippets.

    Args:
        question: The question or prompt to send to Copilot.
        cwd: Working directory for the CLI (so it can read project files).

    Returns:
        Copilot's response text.
    """
    return await _run_cli(
        "copilot", question, model="gpt-5.3-codex", cwd=cwd,
        extra_args=["--allow-all-tools"],
    )


@mcp.tool()
async def ask_gemini(question: str, cwd: str | None = None) -> str:
    """Ask Google Gemini CLI a question.

    Best for: code generation, research, alternative approaches, documentation lookups.

    Args:
        question: The question or prompt to send to Gemini.
        cwd: Working directory for the CLI (so it can read project files).

    Returns:
        Gemini's response text.
    """
    return await _run_cli(
        "gemini", question, model="gemini-3.1-pro-preview", cwd=cwd,
        extra_args=["--yolo"],
    )


@mcp.tool()
async def ask_both(question: str, cwd: str | None = None) -> str:
    """Ask both Copilot and Gemini the same question in parallel.

    Runs both CLI tools concurrently and returns both responses.

    Args:
        question: The question or prompt to send to both agents.
        cwd: Working directory for the CLIs (so they can read project files).

    Returns:
        Combined responses from both agents.
    """
    copilot_task = _run_cli(
        "copilot", question, model="gpt-5.3-codex", cwd=cwd,
        extra_args=["--allow-all-tools"],
    )
    gemini_task = _run_cli(
        "gemini", question, model="gemini-3.1-pro-preview", cwd=cwd,
        extra_args=["--yolo"],
    )
    copilot_resp, gemini_resp = await asyncio.gather(
        copilot_task, gemini_task, return_exceptions=True
    )
    parts = []
    for name, resp in [("Copilot", copilot_resp), ("Gemini", gemini_resp)]:
        if isinstance(resp, Exception):
            parts.append(f"## {name}\n\n{_format_error(name.lower(), 'exception', str(resp))}")
        else:
            parts.append(f"## {name}\n\n{resp}")
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
        agent_name: Which agent is responding (e.g. 'claude', 'copilot', 'gemini').
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

    Structure: identity + onboarding first, then task, then context.
    Agents MUST call bs_get_onboarding() to self-discover their role,
    workflow, and tool guides. Without this, agents (especially Gemini)
    do ad-hoc analysis instead of following the brainstorm protocol.

    Codebase context is inlined (small, set once per session).
    Prior round responses are NOT inlined — agents read them from the DB via CLI
    to avoid command-line length limits and unbounded prompt growth.
    """
    session = _db.get_session(session_id)
    if not session:
        return question

    # Detect Phase 2: feedback items exist → deliberation mode
    feedback_items = _db.list_feedback_items(session_id)
    is_deliberation = len(feedback_items) > 0

    # Lead with identity and mandatory onboarding — this ensures agents
    # know WHO they are and HOW to participate before seeing the task.
    parts = [
        f"You are '{agent_name}' in brainstorm session {session_id}, round {round_id}.",
        "",
        "MANDATORY FIRST STEP: Call bs_get_onboarding(agent_name='"
        f"{agent_name}', session_id='{session_id}') to get your identity,"
        " role, workflow instructions, and available tools.",
        "Do this BEFORE any other action. The onboarding response tells you"
        " exactly what to do in this round.",
    ]

    if is_deliberation:
        # Phase 2: explicit deliberation instructions
        item_ids = [item["id"] for item in feedback_items]
        parts.append("")
        parts.append("=" * 60)
        parts.append("PHASE 2: DELIBERATION — You must review and vote on feedback items.")
        parts.append("=" * 60)
        parts.append("")
        parts.append("After calling bs_get_onboarding, follow these steps EXACTLY:")
        parts.append(
            f"1. Call bs_list_feedback(session_id='{session_id}') to see all items"
        )
        parts.append(
            "2. For EACH item, call bs_get_feedback(item_id=<id>) to read the full"
            " description including all agents' prior positions and verdicts"
        )
        parts.append(
            "3. For EACH item, call bs_respond_to_feedback("
            f"item_id=<id>, round_id='{round_id}', agent_name='{agent_name}',"
            " verdict='accept' or 'reject' or 'modify', reasoning='your reasoning')"
        )
        parts.append(
            f"4. Call bs_save_response(round_id='{round_id}',"
            f" agent_name='{agent_name}', content='your summary')"
        )
        parts.append("")
        parts.append(f"Feedback item IDs to review: {', '.join(item_ids)}")
        parts.append("")

    # The actual task question
    parts.append("")
    parts.append("YOUR TASK:")
    parts.append(question)

    # Instructions: use tools, be specific
    parts.append(
        "\nIMPORTANT: Use your brainstorm MCP tools (bs_get_onboarding,"
        " bs_list_feedback, bs_get_feedback, bs_respond_to_feedback,"
        " bs_save_response) to interact with the brainstorm session."
        " Also use file-reading tools to verify claims against source code."
        " Reference exact files and line numbers."
    )

    # Background context
    parts.append(f"\n---\nProject: {session.get('project', 'N/A')} | Topic: {session['topic']}")

    # Inline codebase context (set once per session, manageable size)
    ctx = _db.get_context(session_id)
    if ctx:
        parts.append(f"\nBackground:\n{ctx}")

    # Prior rounds — metadata only; agents fetch full responses from DB via CLI
    prior_rounds = _db.list_rounds(session_id)
    prior_meta = []
    for rnd in prior_rounds:
        if rnd["id"] == round_id:
            break
        responses = _db.get_round_responses(rnd["id"])
        agent_names_list = [r["agent_name"] for r in responses]
        if agent_names_list:
            prior_meta.append(
                f"  - Round {rnd['round_number']} ({rnd['id']}): "
                f"{rnd.get('objective', 'N/A')} — responses from: {', '.join(agent_names_list)}"
            )

    if prior_meta:
        cli_path = str(Path(__file__).parent / "brainstorm_cli.py")
        parts.append(
            "\nPrior rounds have been completed. BEFORE responding, read the full"
            " responses by running this command:"
            f'\n  python "{cli_path}" session-history --session-id {session_id}'
            "\n\nRound summary:\n" + "\n".join(prior_meta)
        )

    return "\n".join(parts)


@mcp.tool()
async def bs_run_round(
    session_id: str,
    objective: str,
    question: str,
    cwd: str | None = None,
    agents: str = "copilot,gemini",
    ctx: Context | None = None,
) -> str:
    """Run a full brainstorm round: create round, delegate to agents, auto-save all responses.

    Reports progress via MCP notifications so the caller can see which agent is running.

    Args:
        session_id: The brainstorm session ID.
        objective: What this round should focus on.
        question: The question to ask all agents.
        cwd: Working directory for agent CLIs (for codebase access in round 1).
        agents: Comma-separated agent names to call (default: "copilot,gemini").

    Returns:
        All agent responses with round metadata.
    """
    agent_list = [a.strip() for a in agents.split(",") if a.strip()]
    total_steps = len(agent_list) + 1  # +1 for round creation

    # 1. Create the round
    round_info = _db.create_round(session_id, objective)
    round_id = round_info["id"]
    round_num = round_info["round_number"]
    if ctx:
        await ctx.report_progress(1, total_steps)
        await ctx.info(f"Round {round_num} created. Dispatching to {len(agent_list)} agents...")

    # 2. Build per-agent prompts and dispatch in parallel
    agent_config = {
        "copilot": {
            "cmd": "copilot",
            "model": "gpt-5.3-codex",
            "extra_args": ["--allow-all-tools"],
        },
        "gemini": {
            "cmd": "gemini",
            "model": "gemini-3.1-pro-preview",
            "extra_args": ["--yolo"],
        },
    }

    tasks = {}
    for agent_name in agent_list:
        prompt = _build_round_prompt(session_id, round_id, agent_name, question)
        cfg = agent_config.get(agent_name, {"cmd": agent_name, "model": None, "extra_args": []})
        tasks[agent_name] = _run_cli(
            cfg["cmd"], prompt, model=cfg["model"], cwd=cwd, extra_args=cfg["extra_args"],
        )

    # Run agents in parallel, but report progress as each completes
    pending = {name: asyncio.ensure_future(coro) for name, coro in tasks.items()}
    results = {}
    completed = 0
    while pending:
        done, _ = await asyncio.wait(
            pending.values(), return_when=asyncio.FIRST_COMPLETED,
        )
        for task in done:
            # Find which agent this task belongs to
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
            # Agent saved its own response via brainstorm tools — keep it
            output_parts.append(f"## {agent_name} (self-saved)\n\n{existing['content']}")
        else:
            # Agent didn't self-save — save the CLI output as their response
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

    The role tells the agent what their job is. Agents read this from the DB
    before participating in deliberation rounds.

    Args:
        session_id: The brainstorm session.
        agent_name: The agent (copilot, gemini, claude).
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
