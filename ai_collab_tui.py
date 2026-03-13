"""Real-time DB-backed TUI monitor for ai-collab brainstorm sessions.

Polls the SQLite brainstorm.db directly — works with any number of
simultaneous Claude Code instances using the multi-ai-brainstorm skill.
Shows one compact session card per active/recent session, auto-refreshing
every 2 seconds.

Previous IPC layer (queue.jsonl dispatch) is preserved for backward
compatibility — scripts calling dispatch_to_tui() / get_tui_result()
continue to work, but the TUI itself no longer depends on the queue.

Usage:
    ai-collab tui                   Watch for brainstorm sessions (live)
    ai-collab tui --hours 48        Show sessions from last 48 hours
    ai-collab tui --limit 8         Show up to 8 sessions (default 6)
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.containers import ScrollableContainer, Vertical
from textual.reactive import var
from textual.widgets import Footer, Header, Static

from config import get_config, get_enabled_agents

# ── Backward-compat IPC (queue-based dispatch, kept for scripts) ──────────

_LIVE_DIR = Path(os.environ.get(
    "AI_COLLAB_LIVE_DIR",
    str(Path(__file__).resolve().parent / ".data" / "live"),
))
QUEUE_FILE = _LIVE_DIR / "queue.jsonl"
RESULTS_DIR = _LIVE_DIR / "results"


def dispatch_to_tui(
    question: str,
    request_id: str | None = None,
    cwd: str | None = None,
    label: str | None = None,
) -> str:
    """Write a request to the TUI queue (backward-compat IPC).

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
    """Poll for a TUI result (backward-compat IPC)."""
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


# ── Data structures ───────────────────────────────────────────────────────


@dataclass
class AgentResponse:
    agent_name: str
    content: str | None = None  # None means response not yet saved
    created_at: str | None = None


@dataclass
class RoundInfo:
    round_id: str
    round_number: int
    total_rounds: int
    objective: str | None
    question: str | None
    created_at: str
    responses: list[AgentResponse] = field(default_factory=list)


@dataclass
class SessionSnapshot:
    session_id: str
    topic: str
    project: str | None
    status: str          # 'active' | 'completed'
    created_at: str
    latest_round: RoundInfo | None
    consensus_available: bool
    is_running: bool     # round in-progress (round exists, responses incomplete)


# ── DB polling ─────────────────────────────────────────────────────────────


def _poll_db(db_path: Path, hours: int = 24, limit: int = 6, agent_count: int = 2) -> list[SessionSnapshot]:
    """Read recent sessions from the brainstorm SQLite DB (read-only, WAL-safe)."""
    if not db_path.exists():
        return []

    try:
        # WAL mode allows concurrent reads without blocking the MCP writer
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row
    except Exception:
        try:
            conn = sqlite3.connect(str(db_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
        except Exception:
            return []

    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        sessions = conn.execute(
            "SELECT * FROM sessions WHERE created_at >= ? ORDER BY created_at DESC LIMIT ?",
            (cutoff, limit),
        ).fetchall()

        snapshots = []
        for s in sessions:
            rounds = conn.execute(
                "SELECT * FROM rounds WHERE session_id = ? ORDER BY round_number",
                (s["id"],),
            ).fetchall()

            total_rounds = len(rounds)
            latest_round = None
            is_running = False

            if rounds:
                r = rounds[-1]  # latest round
                responses_rows = conn.execute(
                    "SELECT * FROM responses WHERE round_id = ? ORDER BY created_at",
                    (r["id"],),
                ).fetchall()

                responses = [
                    AgentResponse(
                        agent_name=row["agent_name"],
                        content=row["content"],
                        created_at=row["created_at"],
                    )
                    for row in responses_rows
                ]

                latest_round = RoundInfo(
                    round_id=r["id"],
                    round_number=r["round_number"],
                    total_rounds=total_rounds,
                    objective=r["objective"],
                    question=r["question"],
                    created_at=r["created_at"],
                    responses=responses,
                )

                # "In progress": round created < 30 min ago, not all agents responded yet
                if s["status"] == "active":
                    round_dt = _parse_dt(r["created_at"])
                    if round_dt:
                        age = (datetime.now(timezone.utc) - round_dt).total_seconds()
                        if age < 1800 and len(responses) < agent_count:
                            is_running = True

            consensus = conn.execute(
                "SELECT id FROM consensus WHERE session_id = ? LIMIT 1",
                (s["id"],),
            ).fetchone()

            snapshots.append(SessionSnapshot(
                session_id=s["id"],
                topic=s["topic"],
                project=s["project"],
                status=s["status"],
                created_at=s["created_at"],
                latest_round=latest_round,
                consensus_available=bool(consensus),
                is_running=is_running,
            ))

        return snapshots

    except Exception:
        return []
    finally:
        conn.close()


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _time_ago(s: str | None) -> str:
    dt = _parse_dt(s)
    if not dt:
        return ""
    secs = int((datetime.now(timezone.utc) - dt).total_seconds())
    if secs < 60:
        return f"{secs}s ago"
    elif secs < 3600:
        return f"{secs // 60}m ago"
    else:
        return f"{secs // 3600}h {(secs % 3600) // 60}m ago"


# ── Session Card Widget ────────────────────────────────────────────────────


class SessionCard(Static):
    """Compact rich-text card for one brainstorm session.

    Extends Static — call update_snap() to refresh the rendered content.
    """

    DEFAULT_CSS = """
    SessionCard {
        border: solid $primary-darken-2;
        padding: 0 1;
        margin: 0 0 1 0;
        height: auto;
    }

    SessionCard.running {
        border: solid $accent;
    }

    SessionCard.done {
        border: solid $success;
    }
    """

    def __init__(self, snap: SessionSnapshot, **kwargs):
        super().__init__(self._build(snap), **kwargs)
        self._snap = snap

    def update_snap(self, snap: SessionSnapshot) -> None:
        self._snap = snap
        new_cls = "running" if snap.is_running else ("done" if snap.status == "completed" else "")
        # Update CSS classes
        self.remove_class("running", "done")
        if new_cls:
            self.add_class(new_cls)
        self.update(self._build(snap))

    @staticmethod
    def _extract_summary(content: str) -> str:
        """Return a short summary line from agent response content.

        Skips tool-call lines, JSON artifacts, spinner chars, and blank lines.
        """
        import re as _re
        # Patterns that indicate tool-call/status lines, not real content
        _tool_re = _re.compile(
            r"^[✓✗⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏⚠✘]"   # leading status glyph
            r"|brainstorm-|atlas-|skill\("   # tool name fragments
            r"|^\s*[\[{]"                    # JSON arrays/objects
            r"|^\s*[|┌┐└┘├┤┬┴┼─│]",         # box drawing / table chars
            _re.UNICODE,
        )
        for raw in content.split("\n"):
            ln = raw.strip()
            if not ln:
                continue
            if _tool_re.search(ln):
                continue
            # Strip leading markdown markers
            for prefix in ("## ", "### ", "# ", "**", "- ", "> ", "* "):
                if ln.startswith(prefix):
                    ln = ln[len(prefix):].strip()
            if not ln:
                continue
            return ln[:72] + "…" if len(ln) > 72 else ln
        return "(no summary)"

    @staticmethod
    def _build(snap: SessionSnapshot) -> str:
        lines: list[str] = []

        # ── Header row: status badge + topic + session ID ──
        if snap.is_running:
            badge = "[bold yellow]◌ RUNNING[/bold yellow]"
        elif snap.status == "completed":
            badge = "[bold green]✓ DONE[/bold green]"
        else:
            badge = "[dim]● IDLE[/dim]"

        topic = snap.topic if len(snap.topic) <= 48 else snap.topic[:47] + "…"
        sid_short = snap.session_id[-8:]
        lines.append(f"{badge}  [bold]{topic}[/bold]  [dim]{sid_short}[/dim]")

        # ── Metadata row ──
        meta_parts = []
        if snap.project:
            meta_parts.append(f"[dim]project:[/dim] {snap.project}")
        meta_parts.append(_time_ago(snap.created_at))
        lines.append("  " + "  [dim]·[/dim]  ".join(meta_parts))

        # ── Latest round ──
        r = snap.latest_round
        if r:
            obj = (r.objective or r.question or "").strip()
            obj_short = obj if len(obj) <= 60 else obj[:59] + "…"
            lines.append(
                f"  [dim]Round {r.round_number}/{r.total_rounds}[/dim]"
                + (f"  [dim]{obj_short}[/dim]" if obj_short else "")
            )

            if r.responses:
                lines.append("")
                for resp in r.responses:
                    name = resp.agent_name
                    if resp.content:
                        first = SessionCard._extract_summary(resp.content)
                        lines.append(f"  [green]✓[/green] [bold]{name}[/bold]  [dim]{first}[/dim]")
                    else:
                        lines.append(f"  [yellow]⟳[/yellow] [bold]{name}[/bold]  [dim]waiting for response…[/dim]")
            elif snap.is_running:
                lines.append("")
                lines.append("  [yellow]⟳[/yellow] [dim]agents dispatched, waiting for responses…[/dim]")
            else:
                lines.append("")
                lines.append("  [dim]No responses yet[/dim]")
        else:
            lines.append("  [dim]No rounds yet[/dim]")

        # ── Consensus ──
        if snap.consensus_available:
            lines.append("")
            lines.append("  [bold green]◆ Consensus available[/bold green]")

        return "\n".join(lines)


# ── Main Monitor App ───────────────────────────────────────────────────────


class BrainstormMonitor(App):
    """Live DB monitor — shows all recent brainstorm sessions from any Claude Code instance."""

    CSS = """
    Screen {
        layout: vertical;
    }

    #content {
        height: 1fr;
        overflow-y: auto;
        padding: 0 1;
    }

    #session-grid {
        layout: vertical;
    }

    #empty {
        color: $text-muted;
        text-align: center;
        padding: 4 0;
    }

    #statusbar {
        height: 1;
        dock: bottom;
        background: $surface;
        color: $text-muted;
        padding: 0 1;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "refresh", "Refresh now"),
        ("escape", "quit", "Quit"),
    ]

    def __init__(self, db_path: Path, hours: int = 24, limit: int = 6, agent_count: int = 2):
        super().__init__()
        self.db_path = db_path
        self.hours = hours
        self.limit = limit
        self.agent_count = agent_count
        self._snapshots: list[SessionSnapshot] = []
        self._cards: dict[str, SessionCard] = {}  # session_id → card
        self._last_refresh = "—"
        self.title = "AI Collab Monitor"
        self.sub_title = str(db_path)

    def compose(self) -> ComposeResult:
        yield Header()
        with ScrollableContainer(id="content"):
            yield Vertical(id="session-grid")
        yield Static("", id="statusbar")
        yield Footer()

    def on_mount(self) -> None:
        self._do_refresh()
        self.set_interval(2.0, self._do_refresh)

    def action_refresh(self) -> None:
        self._do_refresh()

    def _do_refresh(self) -> None:
        snaps = _poll_db(self.db_path, hours=self.hours, limit=self.limit, agent_count=self.agent_count)
        self._snapshots = snaps
        self._last_refresh = datetime.now().strftime("%H:%M:%S")
        self._sync_cards(snaps)
        self._update_status()

    def _sync_cards(self, snaps: list[SessionSnapshot]) -> None:
        """Add new session cards, update existing ones, remove stale ones."""
        grid = self.query_one("#session-grid", Vertical)

        # Remove the empty-notice if any sessions arrived
        empty = grid.query("#empty")
        if snaps and empty:
            empty.remove()

        new_ids = {s.session_id for s in snaps}
        old_ids = set(self._cards.keys())

        # Remove cards no longer in the result set
        for sid in old_ids - new_ids:
            card = self._cards.pop(sid, None)
            if card:
                card.remove()

        # Update existing cards and mount new ones
        for snap in reversed(snaps):  # reversed so newest is at bottom
            sid = snap.session_id
            if sid in self._cards:
                self._cards[sid].update_snap(snap)
            else:
                cls = "running" if snap.is_running else ("done" if snap.status == "completed" else "")
                card = SessionCard(snap, classes=cls)
                self._cards[sid] = card
                grid.mount(card)

        # Show empty notice if no sessions
        if not snaps and not grid.query("#empty"):
            grid.mount(Static(
                f"[dim]No sessions in the last {self.hours}h. "
                "Run /multi-ai-brainstorm in any Claude Code window to start one.[/dim]",
                id="empty",
            ))

    def _update_status(self) -> None:
        bar = self.query_one("#statusbar", Static)
        running = sum(1 for s in self._snapshots if s.is_running)
        total = len(self._snapshots)

        parts = []
        if running:
            parts.append(f"[bold yellow]◌ {running} running[/bold yellow]")
        if total:
            parts.append(f"{total} session(s)")
        parts.append(f"refreshed {self._last_refresh}")
        parts.append("[bold]r[/bold] refresh  [bold]q[/bold] quit")

        bar.update(" " + "  |  ".join(parts))


# ── Public API ─────────────────────────────────────────────────────────────


def run_tui(
    question: str | None = None,
    agent_names: list[str] | None = None,
    cwd: str | None = None,
    hours: int = 24,
    limit: int = 6,
) -> None:
    """Launch the brainstorm monitor TUI.

    Args:
        question: Ignored (kept for CLI backward compatibility).
        agent_names: Ignored (kept for CLI backward compatibility).
        cwd: Ignored (kept for CLI backward compatibility).
        hours: How many hours back to show sessions (default 24).
        limit: Max sessions to display (default 6).
    """
    cfg = get_config()
    db_path = Path(os.environ.get("BRAINSTORM_DB", str(cfg.db_path)))
    agent_count = max(1, len(get_enabled_agents()))

    app = BrainstormMonitor(
        db_path=db_path,
        hours=hours,
        limit=limit,
        agent_count=agent_count,
    )
    app.run()


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Live monitor for multi-AI brainstorm sessions (SQLite-backed, multi-instance)"
    )
    parser.add_argument(
        "question",
        nargs="?",
        default=None,
        help="Ignored — kept for backward compatibility",
    )
    parser.add_argument("--agents", "-a", help="Ignored — kept for backward compatibility")
    parser.add_argument("--cwd", "-d", help="Ignored — kept for backward compatibility")
    parser.add_argument(
        "--hours",
        type=int,
        default=24,
        help="Hours of history to show (default: 24)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=6,
        help="Max sessions to display (default: 6)",
    )

    args = parser.parse_args()
    run_tui(hours=args.hours, limit=args.limit)


if __name__ == "__main__":
    main()
