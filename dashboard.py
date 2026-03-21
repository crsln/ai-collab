"""Brainstorm Dashboard — lightweight web server for session visualization.

Usage:
    python dashboard.py [--port 8111] [--db .data/brainstorm.db]

Serves a single-page Canvas2D dashboard that polls SQLite for live session data.
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse


DB_PATH: Path = Path(".data/brainstorm.db")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=2)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def time_ago(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        secs = max(0, int((datetime.now(timezone.utc) - dt).total_seconds()))
        if secs < 60:
            return f"{secs}s ago"
        elif secs < 3600:
            return f"{secs // 60}m ago"
        else:
            return f"{secs // 3600}h {(secs % 3600) // 60}m ago"
    except Exception:
        return ""


def is_running(session: dict) -> bool:
    if session["status"] != "active" or not session["rounds"]:
        return False
    latest = session["rounds"][-1]
    try:
        dt = datetime.fromisoformat(latest["created_at"].replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - dt).total_seconds()
        if age >= 1800:
            return False
    except Exception:
        return False
    responded = {r["agent_name"] for r in latest["responses"] if r.get("content")}
    all_agents: set[str] = set()
    for rnd in session["rounds"]:
        for resp in rnd["responses"]:
            if resp.get("content"):
                all_agents.add(resp["agent_name"])
    return bool(all_agents - responded)


def load_sessions(hours: int = 48, limit: int = 20) -> list[dict]:
    conn = get_conn()
    cutoff = datetime.now(timezone.utc).isoformat()  # just use a wide window
    rows = conn.execute(
        "SELECT id, topic, project, status, created_at "
        "FROM sessions ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()

    sessions = []
    for row in rows:
        sid = row["id"]
        rounds = load_rounds(conn, sid)
        feedback = load_feedback(conn, sid)
        consensus = conn.execute(
            "SELECT content FROM consensus WHERE session_id = ? LIMIT 1", (sid,)
        ).fetchone()

        session = {
            "session_id": sid,
            "topic": row["topic"],
            "project": row["project"],
            "status": row["status"],
            "created_at": row["created_at"],
            "time_ago": time_ago(row["created_at"]),
            "rounds": rounds,
            "feedback_items": feedback,
            "consensus_content": consensus["content"] if consensus else None,
        }
        session["is_running"] = is_running(session)
        sessions.append(session)

    conn.close()
    return sessions


def load_rounds(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT id, round_number, objective, question, created_at "
        "FROM rounds WHERE session_id = ? ORDER BY round_number",
        (session_id,),
    ).fetchall()

    total = len(rows)
    rounds = []
    for r in rows:
        responses = conn.execute(
            "SELECT agent_name, content, created_at "
            "FROM responses WHERE round_id = ? ORDER BY created_at",
            (r["id"],),
        ).fetchall()
        rounds.append({
            "round_id": r["id"],
            "round_number": r["round_number"],
            "total_rounds": total,
            "objective": r["objective"],
            "question": r["question"],
            "created_at": r["created_at"],
            "responses": [
                {"agent_name": rr["agent_name"], "content": rr["content"], "created_at": rr["created_at"]}
                for rr in responses
            ],
        })
    return rounds


def load_feedback(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT id, source_round_id, source_agent, title, content, status, created_at "
        "FROM feedback_items WHERE session_id = ? ORDER BY created_at",
        (session_id,),
    ).fetchall()

    items = []
    for fi in rows:
        verdicts = conn.execute(
            "SELECT agent_name, verdict, reasoning, created_at "
            "FROM feedback_responses WHERE item_id = ? ORDER BY created_at",
            (fi["id"],),
        ).fetchall()
        items.append({
            "item_id": fi["id"],
            "source_round_id": fi["source_round_id"],
            "source_agent": fi["source_agent"],
            "title": fi["title"],
            "content": fi["content"],
            "status": fi["status"],
            "created_at": fi["created_at"],
            "verdicts": [
                {
                    "agent_name": v["agent_name"],
                    "verdict": v["verdict"],
                    "reasoning": v["reasoning"],
                    "created_at": v["created_at"],
                }
                for v in verdicts
            ],
        })
    return items


class DashboardHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # silent

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/api/sessions":
            data = load_sessions()
            self._json_response(data)

        elif parsed.path == "/" or parsed.path == "/index.html":
            html_path = Path(__file__).parent / "dashboard.html"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html_path.read_bytes())

        else:
            self.send_error(404)

    def _json_response(self, data):
        body = json.dumps(data, default=str).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)


def main(port: int | None = None, db_path: str | None = None):
    parser = argparse.ArgumentParser(description="Brainstorm Dashboard")
    parser.add_argument("--port", type=int, default=8111)
    parser.add_argument("--db", type=str, default=None)
    args = parser.parse_args()

    # CLI kwargs override argparse (when called from ai_collab_cli)
    _port = port or args.port
    _db = db_path or args.db

    global DB_PATH
    if _db:
        DB_PATH = Path(_db)
    elif os.environ.get("BRAINSTORM_DB"):
        DB_PATH = Path(os.environ["BRAINSTORM_DB"])
    else:
        # Try relative to script location
        script_dir = Path(__file__).parent
        candidate = script_dir / ".data" / "brainstorm.db"
        if candidate.exists():
            DB_PATH = candidate

    if not DB_PATH.exists():
        print(f"Database not found: {DB_PATH}", file=sys.stderr)
        sys.exit(1)

    print(f"Dashboard: http://localhost:{_port}")
    print(f"Database:  {DB_PATH}")

    server = HTTPServer(("0.0.0.0", _port), DashboardHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
