"""Real-time DB-backed TUI monitor for ai-collab brainstorm sessions.

Each session card shows a horizontal neural-network style flow:

    ● question  ──┬── ● agent1  summary…  ──┐
                  ├── ⠋ agent2  thinking…  ──┤── ◆ consensus
                  └── ● agent3  summary…  ──┘

Animation (0.5s tick) shows braille spinners on waiting agents.

Usage:
    ai-collab tui
    ai-collab tui --hours 48
    ai-collab tui --limit 8
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import ScrollableContainer, Vertical
from textual.widgets import Footer, Header, Static

from config import get_config, get_enabled_agents

# ── Backward-compat IPC ───────────────────────────────────────────────────

_LIVE_DIR = Path(os.environ.get(
    "AI_COLLAB_LIVE_DIR",
    str(Path(__file__).resolve().parent / ".data" / "live"),
))
QUEUE_FILE = _LIVE_DIR / "queue.jsonl"
RESULTS_DIR = _LIVE_DIR / "results"


def dispatch_to_tui(question: str, request_id: str | None = None,
                    cwd: str | None = None, label: str | None = None) -> str:
    import uuid
    req_id = request_id or uuid.uuid4().hex[:12]
    _LIVE_DIR.mkdir(parents=True, exist_ok=True)
    with open(QUEUE_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "id": req_id, "question": question, "cwd": cwd,
            "label": label, "timestamp": datetime.now().isoformat(),
        }) + "\n")
    return req_id


def get_tui_result(request_id: str, timeout: float = 900) -> dict | None:
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


# ── Constants ─────────────────────────────────────────────────────────────

_AGENT_COLORS = ["#ff9f00", "#a78bfa", "#34d399", "#f472b6", "#60a5fa"]
_BRAILLE = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


# ── Data structures ───────────────────────────────────────────────────────

@dataclass
class AgentResponse:
    agent_name: str
    content: str | None = None
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
class FullSessionData:
    session_id: str
    topic: str
    project: str | None
    status: str
    created_at: str
    rounds: list[RoundInfo]
    consensus_content: str | None
    is_running: bool


# ── DB helpers ────────────────────────────────────────────────────────────

def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _time_ago(s: str | None) -> str:
    dt = _parse_dt(s)
    if not dt:
        return ""
    secs = int((datetime.now(timezone.utc) - dt).total_seconds())
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    return f"{secs // 3600}h {(secs % 3600) // 60}m ago"


def _open_db(db_path: Path) -> sqlite3.Connection | None:
    if not db_path.exists():
        return None
    for uri in (f"file:{db_path}?mode=ro", str(db_path)):
        try:
            conn = sqlite3.connect(uri if "?" in uri else uri,
                                   uri="?" in uri, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            return conn
        except Exception:
            pass
    return None


def _poll_sessions(
    db_path: Path,
    hours: int = 24,
    limit: int = 6,
    agent_names: set[str] | None = None,
) -> list[FullSessionData]:
    """Load recent sessions with all rounds/responses in one DB round-trip."""
    conn = _open_db(db_path)
    if conn is None:
        return []
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        sessions = conn.execute(
            "SELECT * FROM sessions WHERE created_at >= ? ORDER BY created_at DESC LIMIT ?",
            (cutoff, limit),
        ).fetchall()

        results: list[FullSessionData] = []
        for s in sessions:
            rounds_rows = conn.execute(
                "SELECT * FROM rounds WHERE session_id = ? ORDER BY round_number",
                (s["id"],),
            ).fetchall()
            total_rounds = len(rounds_rows)

            rounds: list[RoundInfo] = []
            for r in rounds_rows:
                resp_rows = conn.execute(
                    "SELECT * FROM responses WHERE round_id = ? ORDER BY created_at",
                    (r["id"],),
                ).fetchall()
                rounds.append(RoundInfo(
                    round_id=r["id"],
                    round_number=r["round_number"],
                    total_rounds=total_rounds,
                    objective=r["objective"],
                    question=r["question"],
                    created_at=r["created_at"],
                    responses=[
                        AgentResponse(row["agent_name"], row["content"], row["created_at"])
                        for row in resp_rows
                    ],
                ))

            is_running = False
            if rounds and s["status"] == "active":
                latest = rounds[-1]
                round_dt = _parse_dt(latest.created_at)
                if round_dt:
                    age = (datetime.now(timezone.utc) - round_dt).total_seconds()
                    if age < 1800:
                        responded = {r.agent_name for r in latest.responses if r.content}
                        if agent_names:
                            is_running = not agent_names.issubset(responded)
                        else:
                            # Infer expected agents from all responses seen in this session
                            all_session_agents = {
                                resp.agent_name
                                for r in rounds
                                for resp in r.responses
                                if resp.content
                            }
                            is_running = bool(all_session_agents - responded)

            consensus_row = conn.execute(
                "SELECT content FROM consensus WHERE session_id = ? LIMIT 1",
                (s["id"],),
            ).fetchone()

            results.append(FullSessionData(
                session_id=s["id"],
                topic=s["topic"],
                project=s["project"],
                status=s["status"],
                created_at=s["created_at"],
                rounds=rounds,
                consensus_content=consensus_row["content"] if consensus_row else None,
                is_running=is_running,
            ))
        return results
    except Exception:
        return []
    finally:
        conn.close()


def _complete_session_db(db_path: Path, session_id: str) -> None:
    """Mark a session as completed directly in the DB."""
    try:
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.execute("UPDATE sessions SET status = 'completed' WHERE id = ?", (session_id,))
        conn.commit()
        conn.close()
    except Exception:
        pass


# ── Flow art renderer ─────────────────────────────────────────────────────

def _extract_summary(content: str) -> str:
    """Return first meaningful line from response content."""
    import re
    _skip = re.compile(
        r"^[✓✗⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏⚠✘]"
        r"|brainstorm-|atlas-|skill\("
        r"|^\s*[\[{]"
        r"|^\s*[|┌┐└┘├┤┬┴┼─│]",
        re.UNICODE,
    )
    for raw in content.split("\n"):
        ln = raw.strip()
        if not ln or _skip.search(ln):
            continue
        for pfx in ("## ", "### ", "# ", "**", "- ", "> ", "* "):
            if ln.startswith(pfx):
                ln = ln[len(pfx):].strip()
        if ln:
            return ln[:70] + "…" if len(ln) > 70 else ln
    return "(no summary)"


def _render_flow_art(
    session: FullSessionData,
    agent_names: set[str],
    color_map: dict[str, str],
    frame: int,
) -> list[str]:
    """Branching dot-graph for one session.

    Each agent gets its own horizontal lane; rounds are columns.
    ◉ fans out to all agents, results converge to ◆.

      ╭── ● ── ● ──╮
      │             │
    ◉─┼── ● ── ⠋ ──┼── ◆
      │             │
      ╰── ● ── ● ──╯

    Colored dot = agent responded  ○ = no response  ⠋ = pending (running only)
    """
    sp = _BRAILLE[frame % len(_BRAILLE)]
    rounds = session.rounds

    all_agents = sorted(
        set(agent_names) | {
            resp.agent_name for r in rounds for resp in r.responses
        }
    )
    N = len(all_agents)

    if N == 0 or not rounds:
        return [f"  [#00d7ff]◉[/]  [dim]{sp} waiting…[/dim]"]

    M = len(rounds)
    H = 2 * N - 1
    center = N - 1

    responded_sets = [
        {resp.agent_name for resp in r.responses if resp.content}
        for r in rounds
    ]

    # Plain rendered width of the middle section: M dots + (M-1) × " ── " (4 chars)
    mid_width = M + (M - 1) * 4
    has_consensus = bool(session.consensus_content)

    rows: list[str] = []

    for row in range(H):
        is_agent_row = (row % 2 == 0)
        ai = row // 2
        parts: list[str] = []

        # ── LEFT (6 rendered chars) ──────────────────────────────────────
        if N == 1:
            parts.append("[#00d7ff]◉[/][dim]── [/dim]")
        elif row == 0:
            parts.append("  [dim]╭── [/dim]")
        elif row == H - 1:
            parts.append("  [dim]╰── [/dim]")
        elif is_agent_row and row == center:
            parts.append("[#00d7ff]◉[/][dim]─┼── [/dim]")
        elif not is_agent_row and row == center:
            parts.append("[#00d7ff]◉[/][dim]─┤   [/dim]")
        elif is_agent_row:
            parts.append("  [dim]├── [/dim]")
        else:
            parts.append("  [dim]│   [/dim]")

        # ── MIDDLE (rounds as columns) ───────────────────────────────────
        if is_agent_row:
            agent = all_agents[ai]
            color = color_map.get(agent, "#ffffff")
            for ri, responded in enumerate(responded_sets):
                if agent in responded:
                    parts.append(f"[{color}]●[/]")
                elif session.is_running and ri == M - 1:
                    parts.append(f"[{color}]{sp}[/]")
                else:
                    parts.append(f"[{color}]○[/]")
                if ri < M - 1:
                    parts.append("[dim] ── [/dim]")
        else:
            parts.append(" " * mid_width)

        # ── RIGHT (fan-in + consensus) ───────────────────────────────────
        if N == 1:
            if has_consensus:
                short = _extract_summary(session.consensus_content)[:40]
                parts.append(f"[dim] ── [/dim][#00ff9f]◆[/] [dim]{short}[/dim]")
            elif session.is_running:
                parts.append(f"[dim] ── {sp}[/dim]")
        else:
            if row == 0:
                parts.append("[dim] ──╮[/dim]")
            elif row == H - 1:
                parts.append("[dim] ──╯[/dim]")
            elif is_agent_row and row == center:
                if has_consensus:
                    short = _extract_summary(session.consensus_content)[:40]
                    parts.append(f"[dim] ──┼── [/dim][#00ff9f]◆[/] [dim]{short}[/dim]")
                elif session.is_running:
                    parts.append(f"[dim] ──┼── {sp}[/dim]")
                else:
                    parts.append("[dim] ──┤[/dim]")
            elif not is_agent_row and row == center:
                if has_consensus:
                    short = _extract_summary(session.consensus_content)[:40]
                    parts.append(f"[dim] ──┤ [/dim][#00ff9f]◆[/] [dim]{short}[/dim]")
                elif session.is_running:
                    parts.append(f"[dim] ──┤ {sp}[/dim]")
                else:
                    parts.append("[dim] ──┤[/dim]")
            elif is_agent_row:
                parts.append("[dim] ──┤[/dim]")
            else:
                parts.append("[dim]   │[/dim]")

        rows.append("  " + "".join(parts))

    return rows


# ── Session Card ──────────────────────────────────────────────────────────

class SessionCard(Static):
    """Full-width session card with embedded neural-net flow visualization."""

    DEFAULT_CSS = """
    SessionCard {
        border: solid $primary-darken-2;
        padding: 0 1;
        margin: 0 0 1 0;
        height: auto;
    }
    SessionCard.running  { border: solid $accent; }
    SessionCard.done     { border: solid $success; }
    SessionCard.focused  { border: solid $warning; }
    """

    def __init__(
        self,
        session: FullSessionData,
        color_map: dict[str, str],
        agent_names: set[str],
        frame: int = 0,
        **kwargs,
    ):
        self._session = session
        self._color_map = color_map
        self._agent_names = agent_names
        self._frame = frame
        super().__init__(self._build(), **kwargs)

    def update_session(self, session: FullSessionData) -> None:
        self._session = session
        self._repaint()

    def tick(self, frame: int) -> None:
        """Advance animation frame — only repaints if session is running."""
        if self._session.is_running:
            self._frame = frame
            self._repaint()

    def _repaint(self) -> None:
        s = self._session
        cls = "running" if s.is_running else ("done" if s.status == "completed" else "")
        self.remove_class("running", "done")
        if cls:
            self.add_class(cls)
        self.update(self._build())

    def _build(self) -> str:
        s = self._session
        lines: list[str] = []

        # ── Header ──────────────────────────────────────────────────────
        if s.is_running:
            badge = "[bold yellow]◌ RUNNING[/bold yellow]"
        elif s.status == "completed":
            badge = "[bold green]✓ DONE[/bold green]"
        else:
            badge = "[dim]● IDLE[/dim]"

        topic = s.topic if len(s.topic) <= 52 else s.topic[:51] + "…"
        sid = s.session_id[-8:]
        lines.append(f"{badge}  [bold]{topic}[/bold]  [dim]{sid}[/dim]")

        # ── Metadata ─────────────────────────────────────────────────────
        meta: list[str] = []
        if s.project:
            meta.append(f"[dim]project:[/dim] {s.project}")
        meta.append(_time_ago(s.created_at))
        if s.rounds:
            r = s.rounds[-1]
            obj = (r.objective or r.question or "").strip()
            label = f"round {r.round_number}/{r.total_rounds}"
            if obj:
                label += f" · {obj[:40]}{'…' if len(obj) > 40 else ''}"
            meta.append(f"[dim]{label}[/dim]")
        lines.append("  " + "  [dim]·[/dim]  ".join(meta))

        # ── Flow visualization ────────────────────────────────────────────
        lines.append("")
        lines.extend(_render_flow_art(s, self._agent_names, self._color_map, self._frame))
        lines.append("")

        return "\n".join(lines)


# ── Monitor App ───────────────────────────────────────────────────────────

class BrainstormMonitor(App):
    """Live session monitor with inline neural-net flow animations."""

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
        ("r", "refresh", "Refresh"),
        ("escape", "quit", "Quit"),
        ("j", "focus_next", "↓"),
        ("down", "focus_next", "↓"),
        ("k", "focus_prev", "↑"),
        ("up", "focus_prev", "↑"),
        ("x", "stop_session", "Stop"),
    ]

    def __init__(
        self,
        db_path: Path,
        hours: int = 24,
        limit: int = 6,
        agent_names: set[str] | None = None,
    ):
        super().__init__()
        self.db_path = db_path
        self.hours = hours
        self.limit = limit
        self._agent_names: set[str] = agent_names or set()
        self._sessions: list[FullSessionData] = []
        self._cards: dict[str, SessionCard] = {}
        self._color_map: dict[str, str] = {}
        self._anim_frame = 0
        self._last_refresh = "—"
        self._focused_sid: str | None = None
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
        self.set_interval(0.5, self._tick_anim)

    def action_refresh(self) -> None:
        self._do_refresh()

    def _sorted_ids(self) -> list[str]:
        return [
            s.session_id for s in sorted(
                self._sessions,
                key=lambda s: (1 if s.is_running else 0, s.created_at),
                reverse=True,
            )
        ]

    def _update_focus(self) -> None:
        ids = self._sorted_ids()
        if not ids:
            self._focused_sid = None
            return
        if self._focused_sid not in ids:
            self._focused_sid = ids[0]
        for sid, card in self._cards.items():
            if sid == self._focused_sid:
                card.add_class("focused")
            else:
                card.remove_class("focused")

    def action_focus_next(self) -> None:
        ids = self._sorted_ids()
        if not ids:
            return
        try:
            self._focused_sid = ids[(ids.index(self._focused_sid) + 1) % len(ids)]
        except ValueError:
            self._focused_sid = ids[0]
        self._update_focus()

    def action_focus_prev(self) -> None:
        ids = self._sorted_ids()
        if not ids:
            return
        try:
            self._focused_sid = ids[(ids.index(self._focused_sid) - 1) % len(ids)]
        except ValueError:
            self._focused_sid = ids[-1]
        self._update_focus()

    def action_stop_session(self) -> None:
        if not self._focused_sid:
            return
        session = next((s for s in self._sessions if s.session_id == self._focused_sid), None)
        if session and session.is_running:
            _complete_session_db(self.db_path, self._focused_sid)
            self._do_refresh()

    def _tick_anim(self) -> None:
        self._anim_frame += 1
        for card in self._cards.values():
            card.tick(self._anim_frame)

    def _do_refresh(self) -> None:
        sessions = _poll_sessions(self.db_path, self.hours, self.limit, self._agent_names)
        self._sessions = sessions
        self._last_refresh = datetime.now().strftime("%H:%M:%S")
        self._update_color_map(sessions)
        self._sync_cards(sessions)
        self._update_focus()
        self._update_status()

    def _update_color_map(self, sessions: list[FullSessionData]) -> None:
        """Assign stable colors to all known agent names (alphabetical sort)."""
        all_names: set[str] = set(self._agent_names)
        for s in sessions:
            for r in s.rounds:
                for resp in r.responses:
                    all_names.add(resp.agent_name)
        for name in sorted(all_names):
            if name not in self._color_map:
                self._color_map[name] = _AGENT_COLORS[len(self._color_map) % len(_AGENT_COLORS)]

    def _sync_cards(self, sessions: list[FullSessionData]) -> None:
        grid = self.query_one("#session-grid", Vertical)

        empty = grid.query("#empty")
        if sessions and empty:
            empty.remove()

        new_ids = {s.session_id for s in sessions}
        old_ids = set(self._cards.keys())

        for sid in old_ids - new_ids:
            card = self._cards.pop(sid, None)
            if card:
                card.remove()

        # Sort: running first, then newest first
        sorted_sessions = sorted(
            sessions,
            key=lambda s: (1 if s.is_running else 0, s.created_at),
            reverse=True,
        )

        pre_existing = set(self._cards.keys())

        # Update existing cards
        for s in sorted_sessions:
            if s.session_id in pre_existing:
                self._cards[s.session_id].update_session(s)

        # Mount new cards at the correct sorted position
        for i, s in enumerate(sorted_sessions):
            sid = s.session_id
            if sid in pre_existing:
                continue
            cls = "running" if s.is_running else ("done" if s.status == "completed" else "")
            card = SessionCard(s, self._color_map, self._agent_names, self._anim_frame, classes=cls)
            self._cards[sid] = card

            # Find first already-mounted card that should appear after this one
            insert_before = None
            for later in sorted_sessions[i + 1:]:
                if later.session_id in pre_existing:
                    insert_before = self._cards[later.session_id]
                    break

            if insert_before is not None:
                grid.mount(card, before=insert_before)
            else:
                grid.mount(card)

        if not sessions and not grid.query("#empty"):
            grid.mount(Static(
                f"[dim]No sessions in the last {self.hours}h. "
                "Run /multi-ai-brainstorm in any Claude Code window to start one.[/dim]",
                id="empty",
            ))

    def _update_status(self) -> None:
        bar = self.query_one("#statusbar", Static)
        running = sum(1 for s in self._sessions if s.is_running)
        total = len(self._sessions)
        parts: list[str] = []
        if running:
            parts.append(f"[bold yellow]◌ {running} running[/bold yellow]")
        if total:
            parts.append(f"{total} session(s)")
        parts.append(f"refreshed {self._last_refresh}")
        parts.append("[bold]j/k[/bold] nav  [bold]x[/bold] stop  [bold]r[/bold] refresh  [bold]q[/bold] quit")
        bar.update(" " + "  |  ".join(parts))


# ── Public API ─────────────────────────────────────────────────────────────

def run_tui(
    question: str | None = None,
    agent_names: list[str] | None = None,
    cwd: str | None = None,
    hours: int = 24,
    limit: int = 6,
) -> None:
    cfg = get_config()
    db_path = Path(os.environ.get("BRAINSTORM_DB", str(cfg.db_path)))
    names = set(get_enabled_agents().keys()) or None
    BrainstormMonitor(db_path=db_path, hours=hours, limit=limit, agent_names=names).run()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Live AI Collab brainstorm monitor")
    parser.add_argument("question", nargs="?", default=None,
                        help="Ignored — backward compat")
    parser.add_argument("--agents", "-a", help="Ignored — backward compat")
    parser.add_argument("--cwd", "-d",   help="Ignored — backward compat")
    parser.add_argument("--hours", type=int, default=24,
                        help="Hours of history to show (default: 24)")
    parser.add_argument("--limit", type=int, default=6,
                        help="Max sessions to display (default: 6)")
    args = parser.parse_args()
    run_tui(hours=args.hours, limit=args.limit)


if __name__ == "__main__":
    main()
