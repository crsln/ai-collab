"""Brainstorm DB — lightweight SQLite store for multi-AI brainstorming sessions."""

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uid() -> str:
    return uuid.uuid4().hex[:12]


class BrainstormDB:
    """SQLite-backed storage for brainstorming sessions, rounds, and responses."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()
        self._migrate()

    def _init_schema(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                topic TEXT NOT NULL,
                project TEXT,
                context TEXT,
                status TEXT DEFAULT 'active',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS rounds (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                round_number INTEGER NOT NULL,
                objective TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
                UNIQUE (session_id, round_number)
            );

            CREATE TABLE IF NOT EXISTS responses (
                id TEXT PRIMARY KEY,
                round_id TEXT NOT NULL,
                agent_name TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (round_id) REFERENCES rounds(id) ON DELETE CASCADE,
                UNIQUE (round_id, agent_name)
            );

            CREATE TABLE IF NOT EXISTS consensus (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                round_id TEXT,
                version INTEGER DEFAULT 1,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
                FOREIGN KEY (round_id) REFERENCES rounds(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS feedback_items (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                source_round_id TEXT NOT NULL,
                source_agent TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
                FOREIGN KEY (source_round_id) REFERENCES rounds(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS feedback_responses (
                id TEXT PRIMARY KEY,
                item_id TEXT NOT NULL,
                round_id TEXT NOT NULL,
                agent_name TEXT NOT NULL,
                verdict TEXT NOT NULL,
                reasoning TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (item_id) REFERENCES feedback_items(id) ON DELETE CASCADE,
                FOREIGN KEY (round_id) REFERENCES rounds(id) ON DELETE CASCADE,
                UNIQUE (item_id, round_id, agent_name)
            );

            CREATE TABLE IF NOT EXISTS agent_roles (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                agent_name TEXT NOT NULL,
                role TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
                UNIQUE (session_id, agent_name)
            );

            CREATE TABLE IF NOT EXISTS guidelines (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            );

            -- Global tables (not session-scoped) --

            CREATE TABLE IF NOT EXISTS agent_definitions (
                id TEXT PRIMARY KEY,
                agent_name TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                capabilities TEXT NOT NULL,
                default_role TEXT NOT NULL,
                approach TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS workflow_templates (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                version INTEGER DEFAULT 1,
                overview TEXT NOT NULL,
                phases TEXT NOT NULL,
                convergence_rules TEXT NOT NULL,
                response_format TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tool_guides (
                id TEXT PRIMARY KEY,
                tool_name TEXT NOT NULL UNIQUE,
                phase TEXT NOT NULL,
                purpose TEXT NOT NULL,
                usage TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_rounds_session ON rounds(session_id);
            CREATE INDEX IF NOT EXISTS idx_responses_lookup ON responses(round_id, agent_name);
            CREATE INDEX IF NOT EXISTS idx_consensus_session ON consensus(session_id);
            CREATE INDEX IF NOT EXISTS idx_feedback_session ON feedback_items(session_id);
            CREATE INDEX IF NOT EXISTS idx_feedback_responses_item ON feedback_responses(item_id);
            CREATE INDEX IF NOT EXISTS idx_guidelines_session ON guidelines(session_id);
        """)

    def _migrate(self):
        """Add columns to existing tables if missing."""
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(sessions)").fetchall()}
        if "context" not in cols:
            self._conn.execute("ALTER TABLE sessions ADD COLUMN context TEXT")
            self._conn.commit()

    # -- Sessions --

    def create_session(self, topic: str, project: str | None = None) -> dict:
        sid = f"bs_{_uid()}"
        now = _now()
        self._conn.execute(
            "INSERT INTO sessions (id, topic, project, created_at) VALUES (?, ?, ?, ?)",
            (sid, topic, project, now),
        )
        self._conn.commit()
        return {"id": sid, "topic": topic, "project": project, "created_at": now}

    def get_session(self, session_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_sessions(self, status: str | None = None, limit: int = 10) -> list[dict]:
        if status:
            rows = self._conn.execute(
                "SELECT * FROM sessions WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM sessions ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def set_context(self, session_id: str, context: str) -> None:
        """Attach codebase/project context to a session (included in all round prompts)."""
        self._conn.execute(
            "UPDATE sessions SET context = ? WHERE id = ?", (context, session_id)
        )
        self._conn.commit()

    def get_context(self, session_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT context FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return row["context"] if row else None

    def complete_session(self, session_id: str) -> None:
        self._conn.execute(
            "UPDATE sessions SET status = 'completed' WHERE id = ?", (session_id,)
        )
        self._conn.commit()

    # -- Rounds --

    def create_round(self, session_id: str, objective: str | None = None) -> dict:
        rid = f"r_{_uid()}"
        now = _now()
        # Atomic INSERT with subquery to avoid TOCTOU race on round_number
        self._conn.execute(
            "INSERT INTO rounds (id, session_id, round_number, objective, created_at)"
            " VALUES (?, ?, (SELECT COALESCE(MAX(round_number), 0) + 1 FROM rounds WHERE session_id = ?), ?, ?)",
            (rid, session_id, session_id, objective, now),
        )
        self._conn.commit()
        row = self._conn.execute("SELECT round_number FROM rounds WHERE id = ?", (rid,)).fetchone()
        round_num = row["round_number"]
        return {"id": rid, "session_id": session_id, "round_number": round_num, "objective": objective, "created_at": now}

    def get_round(self, round_id: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM rounds WHERE id = ?", (round_id,)).fetchone()
        return dict(row) if row else None

    def list_rounds(self, session_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM rounds WHERE session_id = ? ORDER BY round_number",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # -- Responses --

    def save_response(self, round_id: str, agent_name: str, content: str) -> dict:
        rid = f"resp_{_uid()}"
        now = _now()
        self._conn.execute(
            "INSERT INTO responses (id, round_id, agent_name, content, created_at)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(round_id, agent_name) DO UPDATE SET content=excluded.content, created_at=excluded.created_at",
            (rid, round_id, agent_name, content, now),
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT id FROM responses WHERE round_id = ? AND agent_name = ?", (round_id, agent_name)
        ).fetchone()
        return {"id": row["id"], "round_id": round_id, "agent_name": agent_name, "created_at": now}

    def get_response(self, round_id: str, agent_name: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM responses WHERE round_id = ? AND agent_name = ?",
            (round_id, agent_name),
        ).fetchone()
        return dict(row) if row else None

    def get_round_responses(self, round_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM responses WHERE round_id = ? ORDER BY created_at",
            (round_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # -- Consensus --

    def save_consensus(self, session_id: str, content: str, round_id: str | None = None) -> dict:
        cid = f"con_{_uid()}"
        now = _now()
        # Atomic INSERT with subquery to avoid TOCTOU race on version
        self._conn.execute(
            "INSERT INTO consensus (id, session_id, round_id, version, content, created_at)"
            " VALUES (?, ?, ?, (SELECT COALESCE(MAX(version), 0) + 1 FROM consensus WHERE session_id = ?), ?, ?)",
            (cid, session_id, round_id, session_id, content, now),
        )
        self._conn.commit()
        row = self._conn.execute("SELECT version FROM consensus WHERE id = ?", (cid,)).fetchone()
        return {"id": cid, "session_id": session_id, "version": row["version"], "created_at": now}

    def get_latest_consensus(self, session_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM consensus WHERE session_id = ? ORDER BY version DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        return dict(row) if row else None

    # -- Feedback Items --

    def create_feedback_item(
        self, session_id: str, source_round_id: str, source_agent: str,
        title: str, content: str,
    ) -> dict:
        fid = f"fb_{_uid()}"
        now = _now()
        self._conn.execute(
            "INSERT INTO feedback_items (id, session_id, source_round_id, source_agent, title, content, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (fid, session_id, source_round_id, source_agent, title, content, now),
        )
        self._conn.commit()
        return {"id": fid, "session_id": session_id, "source_agent": source_agent,
                "title": title, "status": "pending", "created_at": now}

    def list_feedback_items(self, session_id: str, status: str | None = None) -> list[dict]:
        if status:
            rows = self._conn.execute(
                "SELECT * FROM feedback_items WHERE session_id = ? AND status = ? ORDER BY created_at",
                (session_id, status),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM feedback_items WHERE session_id = ? ORDER BY created_at",
                (session_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_feedback_item(self, item_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM feedback_items WHERE id = ?", (item_id,)
        ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["responses"] = self.get_feedback_responses(item_id)
        return item

    def update_feedback_status(self, item_id: str, status: str) -> None:
        self._conn.execute(
            "UPDATE feedback_items SET status = ? WHERE id = ?", (status, item_id)
        )
        self._conn.commit()

    # -- Feedback Responses --

    def save_feedback_response(
        self, item_id: str, round_id: str, agent_name: str,
        verdict: str, reasoning: str,
    ) -> dict:
        rid = f"fbr_{_uid()}"
        now = _now()
        self._conn.execute(
            "INSERT INTO feedback_responses"
            " (id, item_id, round_id, agent_name, verdict, reasoning, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(item_id, round_id, agent_name) DO UPDATE SET"
            " verdict=excluded.verdict, reasoning=excluded.reasoning, created_at=excluded.created_at",
            (rid, item_id, round_id, agent_name, verdict, reasoning, now),
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT id FROM feedback_responses WHERE item_id = ? AND round_id = ? AND agent_name = ?",
            (item_id, round_id, agent_name),
        ).fetchone()
        return {"id": row["id"], "item_id": item_id, "agent_name": agent_name,
                "verdict": verdict, "created_at": now}

    def get_feedback_responses(self, item_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM feedback_responses WHERE item_id = ? ORDER BY created_at",
            (item_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # -- Agent Roles --

    def set_role(self, session_id: str, agent_name: str, role: str) -> dict:
        rid = f"role_{_uid()}"
        now = _now()
        self._conn.execute(
            "INSERT INTO agent_roles (id, session_id, agent_name, role, created_at)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(session_id, agent_name) DO UPDATE SET role=excluded.role, created_at=excluded.created_at",
            (rid, session_id, agent_name, role, now),
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT id FROM agent_roles WHERE session_id = ? AND agent_name = ?",
            (session_id, agent_name),
        ).fetchone()
        return {"id": row["id"], "session_id": session_id, "agent_name": agent_name, "created_at": now}

    def get_role(self, session_id: str, agent_name: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM agent_roles WHERE session_id = ? AND agent_name = ?",
            (session_id, agent_name),
        ).fetchone()
        return dict(row) if row else None

    def list_roles(self, session_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM agent_roles WHERE session_id = ? ORDER BY agent_name",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # -- Guidelines --

    def add_guideline(self, session_id: str, content: str) -> dict:
        gid = f"gl_{_uid()}"
        now = _now()
        self._conn.execute(
            "INSERT INTO guidelines (id, session_id, content, created_at) VALUES (?, ?, ?, ?)",
            (gid, session_id, content, now),
        )
        self._conn.commit()
        return {"id": gid, "session_id": session_id, "content": content, "created_at": now}

    def list_guidelines(self, session_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM guidelines WHERE session_id = ? ORDER BY created_at",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def remove_guideline(self, guideline_id: str) -> None:
        self._conn.execute("DELETE FROM guidelines WHERE id = ?", (guideline_id,))
        self._conn.commit()

    # -- Agent Definitions (global) --

    def upsert_agent_definition(
        self, agent_name: str, display_name: str,
        capabilities: str, default_role: str, approach: str,
    ) -> dict:
        now = _now()
        existing = self.get_agent_definition(agent_name)
        if existing:
            self._conn.execute(
                "UPDATE agent_definitions SET display_name=?, capabilities=?, "
                "default_role=?, approach=?, updated_at=? WHERE agent_name=?",
                (display_name, capabilities, default_role, approach, now, agent_name),
            )
            self._conn.commit()
            return {"id": existing["id"], "agent_name": agent_name, "updated": True}
        aid = f"ad_{_uid()}"
        self._conn.execute(
            "INSERT INTO agent_definitions (id, agent_name, display_name, capabilities, "
            "default_role, approach, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (aid, agent_name, display_name, capabilities, default_role, approach, now, now),
        )
        self._conn.commit()
        return {"id": aid, "agent_name": agent_name, "created": True}

    def get_agent_definition(self, agent_name: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM agent_definitions WHERE agent_name = ?", (agent_name,)
        ).fetchone()
        return dict(row) if row else None

    def list_agent_definitions(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM agent_definitions ORDER BY agent_name"
        ).fetchall()
        return [dict(r) for r in rows]

    # -- Workflow Templates (global) --

    def upsert_workflow_template(
        self, name: str, overview: str, phases_json: str,
        convergence_rules: str, response_format: str,
    ) -> dict:
        now = _now()
        existing = self.get_workflow_template(name)
        if existing:
            new_version = existing["version"] + 1
            self._conn.execute(
                "UPDATE workflow_templates SET version=?, overview=?, phases=?, "
                "convergence_rules=?, response_format=?, updated_at=? WHERE name=?",
                (new_version, overview, phases_json, convergence_rules, response_format, now, name),
            )
            self._conn.commit()
            return {"id": existing["id"], "name": name, "version": new_version, "updated": True}
        wid = f"wf_{_uid()}"
        self._conn.execute(
            "INSERT INTO workflow_templates (id, name, version, overview, phases, "
            "convergence_rules, response_format, created_at, updated_at) "
            "VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?)",
            (wid, name, overview, phases_json, convergence_rules, response_format, now, now),
        )
        self._conn.commit()
        return {"id": wid, "name": name, "version": 1, "created": True}

    def get_workflow_template(self, name: str = "brainstorm_3phase") -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM workflow_templates WHERE name = ?", (name,)
        ).fetchone()
        return dict(row) if row else None

    # -- Tool Guides (global) --

    def upsert_tool_guide(
        self, tool_name: str, phase: str, purpose: str, usage: str,
    ) -> dict:
        now = _now()
        existing = self.get_tool_guide(tool_name)
        if existing:
            self._conn.execute(
                "UPDATE tool_guides SET phase=?, purpose=?, usage=?, created_at=? "
                "WHERE tool_name=?",
                (phase, purpose, usage, now, tool_name),
            )
            self._conn.commit()
            return {"id": existing["id"], "tool_name": tool_name, "updated": True}
        tid = f"tg_{_uid()}"
        self._conn.execute(
            "INSERT INTO tool_guides (id, tool_name, phase, purpose, usage, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (tid, tool_name, phase, purpose, usage, now),
        )
        self._conn.commit()
        return {"id": tid, "tool_name": tool_name, "created": True}

    def get_tool_guide(self, tool_name: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM tool_guides WHERE tool_name = ?", (tool_name,)
        ).fetchone()
        return dict(row) if row else None

    def list_tool_guides(self, phase: str | None = None) -> list[dict]:
        if phase:
            rows = self._conn.execute(
                "SELECT * FROM tool_guides WHERE phase = ? ORDER BY tool_name",
                (phase,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM tool_guides ORDER BY phase, tool_name"
            ).fetchall()
        return [dict(r) for r in rows]

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
        defn = self.get_agent_definition(agent_name)
        identity = None
        if defn:
            identity = {
                "agent_name": defn["agent_name"],
                "display_name": defn["display_name"],
                "capabilities": defn["capabilities"],
                "default_role": defn["default_role"],
                "approach": defn["approach"],
            }

        # Workflow
        wf = self.get_workflow_template("brainstorm_3phase")
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
        tools = self.list_tool_guides()
        tool_list = [
            {"tool_name": t["tool_name"], "phase": t["phase"],
             "purpose": t["purpose"], "usage": t["usage"]}
            for t in tools
        ]

        # Session-scoped data (if session_id provided)
        session_role = None
        session_context = None
        guidelines_list = []
        current_phase = None
        phase_instructions = None
        feedback_item_ids = []

        if session_id:
            context = self.get_context(session_id)
            session_context = context

            role = self.get_role(session_id, agent_name)
            if role:
                session_role = role["role"]
            elif defn:
                session_role = defn["default_role"]

            guidelines = self.list_guidelines(session_id)
            guidelines_list = [g["content"] for g in guidelines]

            # Phase detection: feedback items exist → deliberation mode
            feedback_items = self.list_feedback_items(session_id)
            if feedback_items:
                current_phase = "deliberation"
                feedback_item_ids = [item["id"] for item in feedback_items]
                round_ref = f", round_id='{round_id}'" if round_id else ""
                phase_instructions = (
                    "YOU ARE IN PHASE 2: DELIBERATION. "
                    "You must review and vote on feedback items — do NOT do general analysis. "
                    "Follow these steps EXACTLY:\n"
                    f"1. Call bs_list_feedback(session_id='{session_id}') to see all items\n"
                    "2. For EACH item, call bs_get_feedback(item_id=<id>) to read the full "
                    "content and all agents' prior verdicts\n"
                    "3. For EACH item, call bs_respond_to_feedback("
                    f"item_id=<id>{round_ref}, agent_name='{agent_name}', "
                    "verdict='accept' or 'reject' or 'modify', reasoning='your reasoning')\n"
                    f"4. Call bs_save_response({round_ref}, "
                    f"agent_name='{agent_name}', content='summary of your verdicts')\n"
                    f"\nFeedback item IDs: {', '.join(feedback_item_ids)}"
                )
            else:
                current_phase = "analysis"

        return {
            "your_identity": identity,
            "workflow": workflow,
            "tools": tool_list,
            "session_role": session_role,
            "session_context": session_context,
            "guidelines": guidelines_list,
            "current_phase": current_phase,
            "phase_instructions": phase_instructions,
            "feedback_item_ids": feedback_item_ids,
        }

    # -- Session briefing (context + role + guidelines) --

    def get_agent_briefing(self, session_id: str, agent_name: str) -> dict:
        """Get everything an agent needs before starting work."""
        context = self.get_context(session_id)
        role = self.get_role(session_id, agent_name)
        if not role:
            defn = self.get_agent_definition(agent_name)
            role_text = defn["default_role"] if defn else None
        else:
            role_text = role["role"]
        guidelines = self.list_guidelines(session_id)
        return {
            "session_context": context,
            "your_role": role_text,
            "guidelines": [g["content"] for g in guidelines],
        }

    # -- Full session dump --

    def get_session_history(self, session_id: str) -> dict:
        """Get complete session with all rounds, responses, and consensus."""
        session = self.get_session(session_id)
        if not session:
            return {"error": f"Session {session_id} not found"}

        rounds = self.list_rounds(session_id)
        for rnd in rounds:
            rnd["responses"] = self.get_round_responses(rnd["id"])

        feedback = self.list_feedback_items(session_id)
        for item in feedback:
            item["responses"] = self.get_feedback_responses(item["id"])

        roles = self.list_roles(session_id)
        consensus = self.get_latest_consensus(session_id)
        return {
            "session": session,
            "rounds": rounds,
            "feedback_items": feedback,
            "roles": roles,
            "consensus": consensus,
        }

    def close(self):
        self._conn.close()
