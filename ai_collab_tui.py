"""Real-time TUI monitor for ai-collab brainstorm sessions.

Stays open in a separate terminal. When /multi-ai-brainstorm dispatches
agents, the TUI picks up the request via a queue file and streams each
agent's output live in its own panel.

IPC: The brainstorm writes requests to .data/live/queue.jsonl
     The TUI watches that file, spawns agents, streams output,
     and writes results to .data/live/results/{request_id}.json

Security: Uses asyncio.create_subprocess_exec (not shell) — all arguments
are passed as a list, preventing command injection.

Usage:
    ai-collab tui                     Start monitor (waits for brainstorm)
    ai-collab tui "question"          Start monitor + run one question immediately
    ai-collab tui --agents copilot    Only show specific agents
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.reactive import var
from textual.widgets import Footer, Header, RichLog, Static

from config import AgentConfig, get_enabled_agents

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]|\x1b\].*?\x07|\x1b[()][A-Z0-9]")

# Shared IPC paths — brainstorm writes here, TUI reads
_LIVE_DIR = Path(os.environ.get(
    "AI_COLLAB_LIVE_DIR",
    str(Path(__file__).resolve().parent / ".data" / "live"),
))
QUEUE_FILE = _LIVE_DIR / "queue.jsonl"
RESULTS_DIR = _LIVE_DIR / "results"


# ── Queue helpers (used by brainstorm to dispatch work to TUI) ────────────


def dispatch_to_tui(
    question: str,
    request_id: str | None = None,
    cwd: str | None = None,
    label: str | None = None,
) -> str:
    """Write a request to the TUI queue. Called from brainstorm/MCP side.

    Args:
        question: The prompt to send to agents.
        request_id: Optional ID for tracking results.
        cwd: Working directory for agent subprocesses.
        label: Display label for the TUI (e.g. "Phase 1: Independent Analysis").

    Returns the request_id so the caller can poll for results.
    """
    import uuid
    req_id = request_id or uuid.uuid4().hex[:12]
    _LIVE_DIR.mkdir(parents=True, exist_ok=True)

    entry = {
        "id": req_id,
        "question": question,
        "cwd": cwd,
        "label": label,
        "timestamp": datetime.now().isoformat(),
    }
    with open(QUEUE_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")

    return req_id


def get_tui_result(request_id: str, timeout: float = 900) -> dict | None:
    """Poll for a TUI result. Called from brainstorm/MCP side."""
    import time
    result_path = RESULTS_DIR / f"{request_id}.json"
    deadline = time.time() + timeout
    while time.time() < deadline:
        if result_path.exists():
            data = json.loads(result_path.read_text(encoding="utf-8"))
            result_path.unlink(missing_ok=True)
            return data
        time.sleep(1.0)
    return None


# ── Data ──────────────────────────────────────────────────────────────────


@dataclass
class AgentResult:
    """Collected result from an agent run."""
    agent_name: str
    output: str = ""
    exit_code: int | None = None
    error: str = ""
    finished: bool = False


# ── Widgets ───────────────────────────────────────────────────────────────


class AgentPanel(Vertical):
    """A single agent's output panel with title bar and streaming log."""

    def __init__(self, agent: AgentConfig, **kwargs):
        super().__init__(**kwargs)
        self.agent = agent
        self.border_title = f" {agent.display_name} "

    def compose(self) -> ComposeResult:
        yield Static(
            f"[bold]{self.agent.display_name}[/bold] ({self.agent.command})",
            id=f"title-{self.agent.name}",
            classes="panel-title",
        )
        yield RichLog(
            id=f"log-{self.agent.name}",
            highlight=True,
            markup=True,
            wrap=True,
            auto_scroll=True,
        )

    @property
    def log_widget(self) -> RichLog:
        return self.query_one(f"#log-{self.agent.name}", RichLog)

    @property
    def title_widget(self) -> Static:
        return self.query_one(f"#title-{self.agent.name}", Static)

    def set_status(self, status: str, style: str = ""):
        base = f"[bold]{self.agent.display_name}[/bold] ({self.agent.command})"
        if style:
            self.title_widget.update(f"{base}  [{style}]{status}[/{style}]")
        else:
            self.title_widget.update(f"{base}  {status}")


# ── Main App ──────────────────────────────────────────────────────────────


class AgentTUI(App):
    """Persistent multi-agent live feed monitor."""

    CSS = """
    Screen {
        layout: vertical;
    }

    #panels {
        height: 1fr;
    }

    #panels.cols-1 { layout: vertical; }
    #panels.cols-2 { layout: horizontal; }
    #panels.cols-3, #panels.cols-4 {
        layout: grid;
        grid-size: 2;
        grid-gutter: 1;
    }

    AgentPanel {
        border: solid $accent;
        height: 1fr;
        width: 1fr;
    }

    .panel-title {
        height: 1;
        background: $surface;
        color: $text;
        padding: 0 1;
    }

    RichLog {
        height: 1fr;
        scrollbar-size: 1 1;
        padding: 0 1;
    }

    #status-bar {
        height: 1;
        dock: bottom;
        background: $surface;
        color: $text-muted;
        padding: 0 1;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("escape", "quit", "Quit"),
        ("c", "cancel_all", "Cancel agents"),
    ]

    running_count: var[int] = var(0)
    round_number: var[int] = var(0)

    def __init__(
        self,
        agents: dict[str, AgentConfig] | None = None,
        initial_question: str | None = None,
        cwd: str | None = None,
    ):
        super().__init__()
        self.agents = agents or get_enabled_agents()
        self.initial_question = initial_question
        # Always use ai-collab project dir as agent cwd so MCP configs
        # (.gemini/settings.json, etc.) are discovered correctly.
        self.cwd = cwd or str(Path(__file__).resolve().parent)
        self._active_tasks: dict[str, asyncio.Task] = {}
        self._queue_lines_read = 0
        self.title = "ai-collab TUI"
        self.sub_title = "Waiting for brainstorm..."

    def compose(self) -> ComposeResult:
        yield Header()

        count = len(self.agents)
        cols_class = f"cols-{min(count, 4)}"

        with Horizontal(id="panels", classes=cols_class):
            for name, agent in self.agents.items():
                yield AgentPanel(agent, id=f"panel-{name}")

        yield Static("", id="status-bar")
        yield Footer()

    def on_mount(self) -> None:
        # Ensure queue dir exists, truncate old queue
        _LIVE_DIR.mkdir(parents=True, exist_ok=True)
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        if QUEUE_FILE.exists():
            QUEUE_FILE.write_text("", encoding="utf-8")
        self._queue_lines_read = 0

        if self.initial_question:
            self._dispatch_round(self.initial_question, request_id="cli")
            self._update_status()
        else:
            self._update_status("Watching for brainstorm requests...")

        # Start polling the queue file
        self._watch_queue()

    @work(thread=False)
    async def _watch_queue(self) -> None:
        """Poll queue.jsonl for new dispatch requests from the brainstorm."""
        while True:
            await asyncio.sleep(0.5)
            if not QUEUE_FILE.exists():
                continue
            try:
                lines = QUEUE_FILE.read_text(encoding="utf-8").strip().splitlines()
            except OSError:
                continue

            # Process new lines only
            new_lines = lines[self._queue_lines_read:]
            self._queue_lines_read = len(lines)

            for line in new_lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    request = json.loads(line)
                except json.JSONDecodeError:
                    continue
                question = request.get("question", "")
                req_id = request.get("id", "")
                cwd = request.get("cwd") or self.cwd
                label = request.get("label")
                if question:
                    self._dispatch_round(question, request_id=req_id, cwd=cwd, label=label)

    def _dispatch_round(
        self,
        question: str,
        request_id: str = "",
        cwd: str | None = None,
        label: str | None = None,
    ) -> None:
        """Send a question to all agents with streaming output."""
        # Cancel any still-running agents from previous round
        self._cancel_running()

        self.round_number += 1
        num = self.round_number
        timestamp = datetime.now().strftime("%H:%M:%S")

        # Use label if provided, otherwise fall back to generic round title
        display_label = label or f"Round {num}"
        self.sub_title = display_label

        # Write separator + phase header to each panel
        for name in self.agents:
            panel = self.query_one(f"#panel-{name}", AgentPanel)
            log = panel.log_widget
            if num > 1:
                log.write("")
                log.write("")
            log.write(f"[bold cyan]{'━' * 56}[/bold cyan]")
            log.write(f"[bold cyan]  {display_label}[/bold cyan]  [dim]{timestamp}[/dim]")
            log.write(f"[bold cyan]{'━' * 56}[/bold cyan]")
            log.write("")

        # Launch streaming agent subprocesses
        for name, agent in self.agents.items():
            self._active_tasks[name] = self._stream_agent(
                name, agent, question, request_id, cwd,
            )
        self._update_status()

    @work(thread=False)
    async def _stream_agent(
        self,
        name: str,
        agent: AgentConfig,
        question: str,
        request_id: str,
        cwd: str | None,
    ) -> None:
        """Spawn agent subprocess and stream stdout line-by-line into panel.

        Uses asyncio.create_subprocess_exec with argument list (no shell).
        """
        panel = self.query_one(f"#panel-{name}", AgentPanel)
        log = panel.log_widget

        self.running_count += 1
        panel.set_status("running...", "green")
        self._update_status()

        try:
            cmd = self._find_cmd(agent)
        except FileNotFoundError:
            panel.set_status("NOT FOUND", "red bold")
            log.write(f"[red]'{agent.command}' not found in PATH[/red]")
            self.running_count -= 1
            self._update_status()
            self._maybe_write_result(request_id, name, "", -1)
            return

        args = [cmd] + agent.build_args(question)

        stdin_input = None
        if sys.platform == "win32" and len(question) > 7000:
            args = [cmd]
            for arg in agent.args:
                if "{prompt}" in arg:
                    continue
                args.append(arg)
            if agent.model:
                args.extend(["--model", agent.model])
            stdin_input = question.encode("utf-8")

        proc = None
        collected: list[str] = []
        exit_code = -1

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.PIPE if stdin_input else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )

            if stdin_input and proc.stdin:
                proc.stdin.write(stdin_input)
                await proc.stdin.drain()
                proc.stdin.close()

            if proc.stdout:
                prev_blank = False
                async for raw_line in proc.stdout:
                    line = raw_line.decode("utf-8", errors="replace")
                    clean = _ANSI_RE.sub("", line).rstrip()

                    # Skip noisy tool-call / progress lines
                    if not clean or clean.startswith(("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")):
                        continue

                    # Add spacing around markdown headers for readability
                    if clean.startswith(("## ", "### ", "---", "***", "___")):
                        if not prev_blank:
                            log.write("")
                        if clean.startswith("## "):
                            log.write(f"[bold yellow]{clean}[/bold yellow]")
                        elif clean.startswith("### "):
                            log.write(f"[bold]{clean}[/bold]")
                        else:
                            log.write(f"[dim]{clean}[/dim]")
                        log.write("")
                        prev_blank = True
                    elif clean == "":
                        if not prev_blank:
                            log.write("")
                            prev_blank = True
                    else:
                        log.write(clean)
                        prev_blank = False

                    collected.append(clean)

            await proc.wait()
            exit_code = proc.returncode or 0

            if exit_code == 0:
                panel.set_status("DONE", "green bold")
            else:
                panel.set_status(f"EXIT {exit_code}", "red bold")

        except asyncio.CancelledError:
            panel.set_status("CANCELLED", "yellow bold")
            log.write("[yellow]-- cancelled --[/yellow]")
            if proc and proc.returncode is None:
                proc.kill()
                await proc.wait()

        except Exception as e:
            panel.set_status("ERROR", "red bold")
            log.write(f"[red]Error: {e}[/red]")

        finally:
            self.running_count -= 1
            self._update_status()
            self._maybe_write_result(
                request_id, name, "\n".join(collected), exit_code,
            )

    def _maybe_write_result(
        self,
        request_id: str,
        agent_name: str,
        output: str,
        exit_code: int,
    ) -> None:
        """Write agent result to results dir so the brainstorm can pick it up."""
        if not request_id:
            return
        try:
            RESULTS_DIR.mkdir(parents=True, exist_ok=True)
            # Append to a per-request file (one entry per agent)
            path = RESULTS_DIR / f"{request_id}.jsonl"
            entry = {
                "agent": agent_name,
                "exit_code": exit_code,
                "output": output,
                "timestamp": datetime.now().isoformat(),
            }
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError:
            pass

    def _find_cmd(self, agent: AgentConfig) -> str:
        name = agent.command
        if sys.platform == "win32":
            cmd = shutil.which(f"{name}.cmd") or shutil.which(name)
            if cmd:
                return cmd
        found = shutil.which(name)
        if found:
            return found
        raise FileNotFoundError(f"'{name}' not found in PATH")

    def _update_status(self, msg: str | None = None) -> None:
        bar = self.query_one("#status-bar", Static)
        total = len(self.agents)
        if msg:
            bar.update(f" {msg}")
        elif self.running_count > 0:
            bar.update(
                f" Agents working: {self.running_count}/{total}  |  "
                f"{self.sub_title}  |  "
                f"[bold]c[/bold] cancel  [bold]q[/bold] quit"
            )
        elif self.round_number > 0:
            bar.update(
                f" {self.sub_title} — complete  |  "
                f"Watching for next round...  |  "
                f"[bold]q[/bold] quit"
            )
        else:
            bar.update(
                f" Watching for brainstorm requests...  |  "
                f"[bold]q[/bold] quit"
            )

    def _cancel_running(self) -> None:
        for name, worker in self._active_tasks.items():
            if not worker.is_finished:
                worker.cancel()

    def action_cancel_all(self) -> None:
        self._cancel_running()
        self._update_status("Cancelling...")


# ── Public API ────────────────────────────────────────────────────────────


def run_tui(
    question: str | None = None,
    agent_names: list[str] | None = None,
    cwd: str | None = None,
) -> None:
    """Launch the persistent TUI monitor.

    Args:
        question: Optional question to run immediately on start.
        agent_names: Optional list of agent names (defaults to all enabled).
        cwd: Working directory for agent subprocesses.
    """
    all_agents = get_enabled_agents()

    if agent_names:
        agents = {}
        for name in agent_names:
            if name in all_agents:
                agents[name] = all_agents[name]
            else:
                print(f"Warning: agent '{name}' not found in config, skipping")
        if not agents:
            print("Error: no valid agents specified")
            sys.exit(1)
    else:
        agents = all_agents

    if not agents:
        print("Error: no agents configured. Run: ai-collab init")
        sys.exit(1)

    app = AgentTUI(agents=agents, initial_question=question, cwd=cwd)
    app.run()


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Live feed monitor for multi-AI brainstorm sessions"
    )
    parser.add_argument(
        "question",
        nargs="?",
        default=None,
        help="Optional: run this question immediately on start",
    )
    parser.add_argument(
        "--agents", "-a",
        help="Comma-separated list of agent names (default: all enabled)",
    )
    parser.add_argument(
        "--cwd", "-d",
        help="Working directory for agent subprocesses",
    )

    args = parser.parse_args()
    agent_names = args.agents.split(",") if args.agents else None
    run_tui(args.question, agent_names=agent_names, cwd=args.cwd)


if __name__ == "__main__":
    main()
