"""Brainstorm DB — lightweight SQLite store for multi-AI brainstorming sessions."""

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

_UNSET = object()  # Sentinel for "parameter not passed" (distinct from None)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uid() -> str:
    return uuid.uuid4().hex[:12]


class BrainstormDB:
    """SQLite-backed storage for brainstorming sessions, rounds, and responses."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False is safe here: each MCP server process gets its
        # own BrainstormDB instance, and asyncio is single-threaded within a process.
        # Cross-process concurrency is handled by SQLite WAL mode.
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
                question TEXT,
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
                source_slug TEXT,
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
                vision TEXT,
                angle TEXT,
                behavior TEXT,
                tags TEXT DEFAULT '[]',
                backend_hint TEXT,
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

            CREATE TABLE IF NOT EXISTS role_library (
                id TEXT PRIMARY KEY,
                slug TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                agent_name TEXT,
                description TEXT NOT NULL,
                role_text TEXT NOT NULL,
                approach TEXT,
                vision TEXT,
                angle TEXT,
                behavior TEXT,
                mandates TEXT DEFAULT '[]',
                tags TEXT DEFAULT '[]',
                usage_count INTEGER DEFAULT 0,
                last_used_at TEXT,
                notes TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS round_participants (
                id TEXT PRIMARY KEY,
                round_id TEXT NOT NULL,
                agent_name TEXT NOT NULL,
                phase TEXT NOT NULL DEFAULT 'analysis',
                status TEXT NOT NULL DEFAULT 'pending',
                dispatched_at TEXT,
                responded_at TEXT,
                response_quality TEXT,
                error_detail TEXT,
                retry_count INTEGER DEFAULT 0,
                max_retries INTEGER DEFAULT 1,
                feedback_items_expected INTEGER DEFAULT 0,
                feedback_items_completed INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY (round_id) REFERENCES rounds(id) ON DELETE CASCADE,
                UNIQUE (round_id, agent_name)
            );

            CREATE INDEX IF NOT EXISTS idx_role_library_agent ON role_library(agent_name);
            CREATE INDEX IF NOT EXISTS idx_role_library_slug ON role_library(slug);
            CREATE INDEX IF NOT EXISTS idx_rounds_session ON rounds(session_id);
            CREATE INDEX IF NOT EXISTS idx_responses_lookup ON responses(round_id, agent_name);
            CREATE INDEX IF NOT EXISTS idx_consensus_session ON consensus(session_id);
            CREATE INDEX IF NOT EXISTS idx_feedback_session ON feedback_items(session_id);
            CREATE INDEX IF NOT EXISTS idx_feedback_responses_item ON feedback_responses(item_id);
            CREATE INDEX IF NOT EXISTS idx_guidelines_session ON guidelines(session_id);
            CREATE INDEX IF NOT EXISTS idx_rp_round ON round_participants(round_id);
            CREATE INDEX IF NOT EXISTS idx_rp_status ON round_participants(round_id, status);
        """)

    def _migrate(self):
        """Add columns to existing tables if missing."""
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(sessions)").fetchall()}
        if "context" not in cols:
            self._conn.execute("ALTER TABLE sessions ADD COLUMN context TEXT")
            self._conn.commit()

        round_cols = {r[1] for r in self._conn.execute("PRAGMA table_info(rounds)").fetchall()}
        if "question" not in round_cols:
            self._conn.execute("ALTER TABLE rounds ADD COLUMN question TEXT")
            self._conn.commit()

        rl_cols = {r[1] for r in self._conn.execute("PRAGMA table_info(role_library)").fetchall()}
        if "vision" not in rl_cols:
            for sql in [
                "ALTER TABLE role_library ADD COLUMN vision TEXT",
                "ALTER TABLE role_library ADD COLUMN angle TEXT",
                "ALTER TABLE role_library ADD COLUMN behavior TEXT",
                "ALTER TABLE role_library ADD COLUMN mandates TEXT DEFAULT '[]'",
            ]:
                self._conn.execute(sql)
            self._conn.commit()

        ad_cols = {r[1] for r in self._conn.execute("PRAGMA table_info(agent_definitions)").fetchall()}
        if "vision" not in ad_cols:
            for sql in [
                "ALTER TABLE agent_definitions ADD COLUMN vision TEXT",
                "ALTER TABLE agent_definitions ADD COLUMN angle TEXT",
                "ALTER TABLE agent_definitions ADD COLUMN behavior TEXT",
                "ALTER TABLE agent_definitions ADD COLUMN tags TEXT DEFAULT '[]'",
                "ALTER TABLE agent_definitions ADD COLUMN backend_hint TEXT",
            ]:
                self._conn.execute(sql)
            self._conn.commit()

        ar_cols = {r[1] for r in self._conn.execute("PRAGMA table_info(agent_roles)").fetchall()}
        if "source_slug" not in ar_cols:
            self._conn.execute("ALTER TABLE agent_roles ADD COLUMN source_slug TEXT")
            self._conn.commit()

        # Sync barrier: new columns on rounds
        round_cols2 = {r[1] for r in self._conn.execute("PRAGMA table_info(rounds)").fetchall()}
        if "phase" not in round_cols2:
            self._conn.execute("ALTER TABLE rounds ADD COLUMN phase TEXT DEFAULT 'analysis'")
            self._conn.execute("ALTER TABLE rounds ADD COLUMN completed_at TEXT")
            self._conn.commit()

        # Sync barrier: new columns on responses
        resp_cols = {r[1] for r in self._conn.execute("PRAGMA table_info(responses)").fetchall()}
        if "quality" not in resp_cols:
            self._conn.execute("ALTER TABLE responses ADD COLUMN quality TEXT")
            self._conn.execute("ALTER TABLE responses ADD COLUMN source TEXT DEFAULT 'stdout'")
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

    def create_round(self, session_id: str, objective: str | None = None, question: str | None = None) -> dict:
        rid = f"r_{_uid()}"
        now = _now()
        # Atomic INSERT with subquery to avoid TOCTOU race on round_number
        self._conn.execute(
            "INSERT INTO rounds (id, session_id, round_number, objective, question, created_at)"
            " VALUES (?, ?, (SELECT COALESCE(MAX(round_number), 0) + 1 FROM rounds WHERE session_id = ?), ?, ?, ?)",
            (rid, session_id, session_id, objective, question, now),
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

    def get_agent_session_responses(self, session_id: str, agent_name: str) -> list[dict]:
        """Fetch prior response references for an agent across all rounds in a session.

        NOTE: Orchestration wrapper moved to brainstorm_service.BrainstormService.
        This CRUD method is kept as the data-access layer.
        """
        rows = self._conn.execute(
            """SELECT rd.id AS round_id, rd.round_number, rd.phase, rd.objective
               FROM responses r
               JOIN rounds rd ON r.round_id = rd.id
               WHERE rd.session_id = ? AND r.agent_name = ?
               ORDER BY rd.round_number""",
            (session_id, agent_name),
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
        # Update feedback_items_completed counter on round_participants
        self._conn.execute(
            "UPDATE round_participants SET feedback_items_completed = ("
            "  SELECT COUNT(DISTINCT fr.item_id) FROM feedback_responses fr"
            "  WHERE fr.round_id = ? AND fr.agent_name = ?"
            ") WHERE round_id = ? AND agent_name = ?",
            (round_id, agent_name, round_id, agent_name),
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT id FROM feedback_responses WHERE item_id = ? AND round_id = ? AND agent_name = ?",
            (item_id, round_id, agent_name),
        ).fetchone()
        return {"id": row["id"], "item_id": item_id, "agent_name": agent_name,
                "verdict": verdict, "created_at": now}

    def batch_save_feedback_responses(
        self, round_id: str, agent_name: str,
        verdicts: list[dict],
    ) -> list[dict]:
        """Save multiple feedback verdicts in a single transaction.

        Each entry in verdicts: {"item_id": str, "verdict": str, "reasoning": str}
        """
        results = []
        now = _now()
        for v in verdicts:
            rid = f"fbr_{_uid()}"
            self._conn.execute(
                "INSERT INTO feedback_responses"
                " (id, item_id, round_id, agent_name, verdict, reasoning, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(item_id, round_id, agent_name) DO UPDATE SET"
                " verdict=excluded.verdict, reasoning=excluded.reasoning, created_at=excluded.created_at",
                (rid, v["item_id"], round_id, agent_name, v["verdict"], v["reasoning"], now),
            )
            results.append({"id": rid, "item_id": v["item_id"], "verdict": v["verdict"]})
        # Update feedback_items_completed counter once
        self._conn.execute(
            "UPDATE round_participants SET feedback_items_completed = ("
            "  SELECT COUNT(DISTINCT fr.item_id) FROM feedback_responses fr"
            "  WHERE fr.round_id = ? AND fr.agent_name = ?"
            ") WHERE round_id = ? AND agent_name = ?",
            (round_id, agent_name, round_id, agent_name),
        )
        self._conn.commit()
        return results

    def batch_create_feedback_items(
        self, session_id: str, items: list[dict],
    ) -> list[dict]:
        """Create multiple feedback items in a single transaction.

        Each entry: {"source_round_id": str, "source_agent": str, "title": str, "content": str}
        """
        results = []
        now = _now()
        for item in items:
            fid = f"fb_{_uid()}"
            self._conn.execute(
                "INSERT INTO feedback_items (id, session_id, source_round_id, source_agent, title, content, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (fid, session_id, item["source_round_id"], item["source_agent"],
                 item["title"], item["content"], now),
            )
            results.append({"id": fid, "title": item["title"], "source_agent": item["source_agent"]})
        self._conn.commit()
        return results

    def batch_apply_role_templates(
        self, session_id: str, assignments: list[dict],
    ) -> list[dict]:
        """Apply role templates to multiple agents in a single transaction.

        Each entry: {"agent_name": str, "slug": str}
        """
        results = []
        for a in assignments:
            result = self.apply_role_template(session_id, a["agent_name"], a["slug"])
            if result:
                results.append(result)
        return results

    def get_feedback_responses(self, item_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM feedback_responses WHERE item_id = ? ORDER BY created_at",
            (item_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # -- Agent Roles --

    def set_role(self, session_id: str, agent_name: str, role: str, *, source_slug: str | None = None) -> dict:
        rid = f"role_{_uid()}"
        now = _now()
        self._conn.execute(
            "INSERT INTO agent_roles (id, session_id, agent_name, role, source_slug, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(session_id, agent_name) DO UPDATE SET role=excluded.role, "
            "source_slug=excluded.source_slug, created_at=excluded.created_at",
            (rid, session_id, agent_name, role, source_slug, now),
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
        *, vision: str | None = None, angle: str | None = None,
        behavior: str | None = None, tags: list[str] | None = None,
        backend_hint: str | None = None,
    ) -> dict:
        import json as _json
        now = _now()
        tags_json = _json.dumps(tags or [])
        existing = self.get_agent_definition(agent_name)
        if existing:
            self._conn.execute(
                "UPDATE agent_definitions SET display_name=?, capabilities=?, "
                "default_role=?, approach=?, vision=?, angle=?, behavior=?, "
                "tags=?, backend_hint=?, updated_at=? WHERE agent_name=?",
                (display_name, capabilities, default_role, approach, vision, angle,
                 behavior, tags_json, backend_hint, now, agent_name),
            )
            self._conn.commit()
            return {"id": existing["id"], "agent_name": agent_name, "updated": True}
        aid = f"ad_{_uid()}"
        self._conn.execute(
            "INSERT INTO agent_definitions (id, agent_name, display_name, capabilities, "
            "default_role, approach, vision, angle, behavior, tags, backend_hint, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (aid, agent_name, display_name, capabilities, default_role, approach,
             vision, angle, behavior, tags_json, backend_hint, now, now),
        )
        self._conn.commit()
        return {"id": aid, "agent_name": agent_name, "created": True}

    def get_agent_definition(self, agent_name: str) -> dict | None:
        import json as _json
        row = self._conn.execute(
            "SELECT * FROM agent_definitions WHERE agent_name = ?", (agent_name,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["tags"] = _json.loads(d.get("tags") or "[]")
        return d

    def list_agent_definitions(self) -> list[dict]:
        import json as _json
        rows = self._conn.execute(
            "SELECT * FROM agent_definitions ORDER BY agent_name"
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["tags"] = _json.loads(d.get("tags") or "[]")
            result.append(d)
        return result

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

    # -- Role Library --

    def create_role_template(
        self, slug: str, display_name: str, description: str,
        role_text: str, agent_name: str | None = None,
        approach: str | None = None, tags: list[str] | None = None,
        notes: str | None = None, *, vision: str | None = None,
        angle: str | None = None, behavior: str | None = None,
        mandates: list[str] | None = None,
    ) -> dict:
        import json as _json
        rid = f"rl_{_uid()}"
        now = _now()
        tags_json = _json.dumps(tags or [])
        mandates_json = _json.dumps(mandates or [])
        self._conn.execute(
            "INSERT INTO role_library (id, slug, display_name, agent_name, description, "
            "role_text, approach, vision, angle, behavior, mandates, tags, notes, "
            "created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (rid, slug, display_name, agent_name, description, role_text, approach,
             vision, angle, behavior, mandates_json, tags_json, notes, now, now),
        )
        self._conn.commit()
        return {"id": rid, "slug": slug, "display_name": display_name, "created": True}

    def get_role_template(self, slug_or_id: str) -> dict | None:
        import json as _json
        row = self._conn.execute(
            "SELECT * FROM role_library WHERE slug = ? OR id = ?",
            (slug_or_id, slug_or_id),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["tags"] = _json.loads(d.get("tags") or "[]")
        d["mandates"] = _json.loads(d.get("mandates") or "[]")
        return d

    def list_role_templates(
        self, agent_name: str | None = None, tag: str | None = None,
    ) -> list[dict]:
        import json as _json
        if agent_name:
            rows = self._conn.execute(
                "SELECT * FROM role_library WHERE agent_name = ? OR agent_name IS NULL "
                "ORDER BY usage_count DESC, display_name",
                (agent_name,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM role_library ORDER BY usage_count DESC, display_name"
            ).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            d["tags"] = _json.loads(d.get("tags") or "[]")
            d["mandates"] = _json.loads(d.get("mandates") or "[]")
            if tag and tag not in d["tags"]:
                continue
            results.append(d)
        return results

    def suggest_roles(self, topic: str, agent_names: list[str], top_n: int = 6,
                      diversify: bool = False) -> dict:
        """Score role templates against a topic and suggest per-agent assignments.

        Roles are NOT fixed to models. The recommended pattern is role rotation:
        each round, ALL agents get the SAME role. Rotate the role each round so
        every model covers every perspective. Use top_roles ranking to pick which
        roles to rotate through.

        Args:
            topic: The brainstorm topic/question.
            agent_names: List of agent names to generate per-agent suggestions for.
            top_n: Number of top roles to return.
            diversify: If True, each slug can only be assigned once; forces unique
                roles per agent. Default False — all agents get the same top role.

        Returns:
            Dict with 'topic', 'top_roles' (ranked list), and 'assignments' (per-agent suggestions).
        """
        import json as _json
        import re

        _STOP_WORDS = {
            "the", "and", "for", "with", "are", "this", "that", "from", "have",
            "its", "our", "you", "all", "can", "not", "but", "any", "how",
            "what", "when", "who", "will", "would", "should", "could", "use",
            "used", "into", "each", "then", "also", "via", "per",
        }

        def _tokenize(text: str) -> set[str]:
            tokens = re.split(r'[\s\W]+', text.lower())
            return {t for t in tokens if len(t) >= 3 and t not in _STOP_WORDS}

        topic_tokens = _tokenize(topic)

        # Score all roles against topic
        roles = self.list_role_templates()
        scored_roles = []
        for role in roles:
            tag_tokens: set[str] = set()
            for t in role.get("tags") or []:
                tag_tokens.update(_tokenize(t))

            first_sentence = (role.get("role_text") or "").split(".")[0]
            corpus_tokens = _tokenize(" ".join([
                role.get("description") or "",
                role.get("angle") or "",
                " ".join(role.get("tags") or []),
                first_sentence,
            ]))

            if not topic_tokens:
                score = 0.0
                match_tokens: set[str] = set()
            else:
                matched = topic_tokens & corpus_tokens
                tag_matches = topic_tokens & tag_tokens
                score = (len(matched) + len(tag_matches) * 0.5) / len(topic_tokens)
                match_tokens = matched | tag_matches

            scored_roles.append({
                "slug": role["slug"],
                "display_name": role["display_name"],
                "score": round(score, 3),
                "description": role["description"],
                "tags": role.get("tags") or [],
                "usage_count": role.get("usage_count") or 0,
                "match_reason": ", ".join(sorted(match_tokens)) if match_tokens else "general (no topic match)",
                "_role_obj": role,  # internal, stripped before return
            })

        # Sort: score desc, then usage_count desc
        scored_roles.sort(key=lambda r: (-r["score"], -r["usage_count"]))

        # Zero-match fallback: sort by usage_count
        if all(r["score"] == 0.0 for r in scored_roles):
            scored_roles.sort(key=lambda r: -r["usage_count"])
            for r in scored_roles:
                r["match_reason"] = "general (no topic match)"

        top_roles = [
            {k: v for k, v in r.items() if k != "_role_obj"}
            for r in scored_roles[:top_n]
        ]

        # Per-agent assignment
        assignments: dict = {}
        assigned_slugs: dict[str, str] = {}  # slug -> agent already assigned it

        for agent_name in agent_names:
            defn = self.get_agent_definition(agent_name)
            if defn:
                agent_corpus_tokens = _tokenize(" ".join([
                    defn.get("capabilities") or "",
                    defn.get("angle") or "",
                    " ".join(defn.get("tags") or []),
                ]))
            else:
                agent_corpus_tokens = set()

            best_slug: str | None = None
            best_display: str | None = None
            best_combined = -1.0
            best_t_score = 0.0
            best_c_score = 0.0

            for entry in scored_roles:
                slug = entry["slug"]
                t_score = entry["score"]
                role_obj = entry["_role_obj"]

                # Capability overlap with agent
                if agent_corpus_tokens:
                    role_corpus = _tokenize(" ".join([
                        role_obj.get("description") or "",
                        role_obj.get("angle") or "",
                        " ".join(role_obj.get("tags") or []),
                    ]))
                    c_score = len(agent_corpus_tokens & role_corpus) / max(len(agent_corpus_tokens), 1)
                else:
                    c_score = 0.0

                if diversify and slug in assigned_slugs:
                    continue  # hard skip — slug already taken
                combined = t_score + c_score

                if combined > best_combined:
                    best_combined = combined
                    best_slug = slug
                    best_display = entry["display_name"]
                    best_t_score = t_score
                    best_c_score = c_score

            if best_slug:
                assigned_slugs[best_slug] = agent_name
                reason_parts = []
                if best_t_score > 0:
                    reason_parts.append(f"Topic match ({int(best_t_score * 100)}%)")
                if best_c_score > 0:
                    angle = (defn.get("angle") or "") if defn else ""
                    if angle:
                        reason_parts.append(f"capability alignment ({angle[:40].rstrip()})")
                    else:
                        reason_parts.append(f"capability overlap ({int(best_c_score * 100)}%)")
                if not reason_parts:
                    reason_parts.append("best available match")
                assignments[agent_name] = {
                    "suggested_slug": best_slug,
                    "suggested_display_name": best_display,
                    "reason": " + ".join(reason_parts),
                }
            else:
                assignments[agent_name] = {
                    "suggested_slug": None,
                    "suggested_display_name": None,
                    "reason": "No role templates available",
                }

        return {
            "topic": topic,
            "top_roles": top_roles,
            "assignments": assignments,
        }

    def update_role_template(
        self, slug_or_id: str, *,
        display_name: str | None = None, description: str | None = None,
        role_text: str | None = None, approach: str | None = None,
        tags: list[str] | None = None, notes: str | None = None,
        vision: str | None = None, angle: str | None = None,
        behavior: str | None = None, mandates: list[str] | None = None,
        agent_name=_UNSET, new_slug: str | None = None,
    ) -> dict | None:
        import json as _json
        existing = self.get_role_template(slug_or_id)
        if not existing:
            return None
        now = _now()
        updates = []
        params = []
        if display_name is not None:
            updates.append("display_name = ?"); params.append(display_name)
        if description is not None:
            updates.append("description = ?"); params.append(description)
        if role_text is not None:
            updates.append("role_text = ?"); params.append(role_text)
        if approach is not None:
            updates.append("approach = ?"); params.append(approach)
        if tags is not None:
            updates.append("tags = ?"); params.append(_json.dumps(tags))
        if notes is not None:
            updates.append("notes = ?"); params.append(notes)
        if vision is not None:
            updates.append("vision = ?"); params.append(vision)
        if angle is not None:
            updates.append("angle = ?"); params.append(angle)
        if behavior is not None:
            updates.append("behavior = ?"); params.append(behavior)
        if mandates is not None:
            updates.append("mandates = ?"); params.append(_json.dumps(mandates))
        if agent_name is not _UNSET:
            updates.append("agent_name = ?"); params.append(agent_name)
        if new_slug is not None:
            updates.append("slug = ?"); params.append(new_slug)
        if not updates:
            return existing
        updates.append("updated_at = ?"); params.append(now)
        params.append(existing["id"])
        self._conn.execute(
            f"UPDATE role_library SET {', '.join(updates)} WHERE id = ?", params,
        )
        self._conn.commit()
        return self.get_role_template(existing["id"])

    def delete_role_template(self, slug_or_id: str) -> bool:
        existing = self.get_role_template(slug_or_id)
        if not existing:
            return False
        self._conn.execute("DELETE FROM role_library WHERE id = ?", (existing["id"],))
        self._conn.commit()
        return True

    def apply_role_template(self, session_id: str, agent_name: str, slug_or_id: str) -> dict | None:
        """Apply a role template to a session. Copies role_text to agent_roles and bumps usage_count."""
        template = self.get_role_template(slug_or_id)
        if not template:
            return None
        now = _now()
        # Bump usage
        self._conn.execute(
            "UPDATE role_library SET usage_count = usage_count + 1, last_used_at = ? WHERE id = ?",
            (now, template["id"]),
        )
        # Compose role text from all behavioral fields
        role_text = template["role_text"]
        if template.get("approach"):
            role_text += f"\n\nApproach: {template['approach']}"
        if template.get("behavior"):
            role_text += f"\n\nBehavior: {template['behavior']}"
        if template.get("vision"):
            role_text += f"\n\nVision: {template['vision']}"
        if template.get("angle"):
            role_text += f"\n\nAngle: {template['angle']}"
        mandates = template.get("mandates") or []
        if mandates:
            role_text += "\n\nMandates (non-negotiable):\n" + "\n".join(f"- {m}" for m in mandates)
        role_result = self.set_role(session_id, agent_name, role_text, source_slug=template["slug"])
        self._conn.commit()
        return {
            "template_id": template["id"],
            "template_slug": template["slug"],
            "source_slug": template["slug"],
            "agent_name": agent_name,
            "session_id": session_id,
            "role_id": role_result["id"],
            "applied": True,
        }

    # -- Onboarding & briefing: Moved to brainstorm_service.BrainstormService --

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

    # -- Round Participants (sync barrier) --

    def register_participants(
        self, round_id: str, agent_names: list[str], phase: str = "analysis",
    ) -> list[dict]:
        """Register expected agents for a round. Call before dispatch."""
        now = _now()
        results = []
        for name in agent_names:
            pid = f"rp_{_uid()}"
            self._conn.execute(
                "INSERT INTO round_participants"
                " (id, round_id, agent_name, phase, status, created_at)"
                " VALUES (?, ?, ?, ?, 'pending', ?)"
                " ON CONFLICT(round_id, agent_name) DO UPDATE SET"
                " phase=excluded.phase, status='pending', created_at=excluded.created_at",
                (pid, round_id, name, phase, now),
            )
            results.append({"id": pid, "round_id": round_id, "agent_name": name})
        self._conn.commit()
        return results

    def update_participant_status(
        self, round_id: str, agent_name: str, status: str, *,
        quality: str | None = None, error_detail: str | None = None,
    ) -> dict | None:
        """Update a participant's status."""
        now = _now()
        time_col = "dispatched_at" if status == "dispatched" else "responded_at"
        self._conn.execute(
            f"UPDATE round_participants SET status=?, response_quality=?,"
            f" error_detail=?, {time_col}=? WHERE round_id=? AND agent_name=?",
            (status, quality, error_detail, now, round_id, agent_name),
        )
        self._conn.commit()
        return self.get_participant(round_id, agent_name)

    def get_participant(self, round_id: str, agent_name: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM round_participants WHERE round_id=? AND agent_name=?",
            (round_id, agent_name),
        ).fetchone()
        return dict(row) if row else None

    def list_participants(self, round_id: str, status: str | None = None) -> list[dict]:
        if status:
            rows = self._conn.execute(
                "SELECT * FROM round_participants WHERE round_id=? AND status=? ORDER BY agent_name",
                (round_id, status),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM round_participants WHERE round_id=? ORDER BY agent_name",
                (round_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def set_participant_feedback_expected(self, round_id: str, agent_name: str, count: int) -> None:
        """Set the expected feedback item count for a participant."""
        self._conn.execute(
            "UPDATE round_participants SET feedback_items_expected=? WHERE round_id=? AND agent_name=?",
            (count, round_id, agent_name),
        )
        self._conn.commit()

    def mark_response_quality_and_source(self, round_id: str, agent_name: str, quality: str, source: str) -> None:
        """Set the quality and source fields on a response."""
        self._conn.execute(
            "UPDATE responses SET quality=?, source=? WHERE round_id=? AND agent_name=?",
            (quality, source, round_id, agent_name),
        )
        self._conn.commit()

    def count_agent_feedback_votes(self, round_id: str, agent_name: str, item_ids: list[str]) -> int:
        """Count distinct feedback items this agent has voted on in a round."""
        if not item_ids:
            return 0
        placeholders = ",".join("?" * len(item_ids))
        row = self._conn.execute(
            f"SELECT COUNT(DISTINCT item_id) FROM feedback_responses"
            f" WHERE round_id=? AND agent_name=? AND item_id IN ({placeholders})",
            (round_id, agent_name, *item_ids),
        ).fetchone()
        return row[0]

    def count_item_votes(self, round_id: str, item_id: str) -> int:
        """Count distinct agents that voted on a feedback item in a round."""
        row = self._conn.execute(
            "SELECT COUNT(DISTINCT agent_name) FROM feedback_responses"
            " WHERE round_id=? AND item_id=?",
            (round_id, item_id),
        ).fetchone()
        return row[0]

    # -- Completion Gates, phase readiness, quality classification:
    #    Moved to brainstorm_service.BrainstormService
    #    (check_round_complete, check_feedback_votes_complete,
    #     check_phase_ready, classify_response_quality)

    # -- Retry support --

    def get_retryable_participants(self, round_id: str) -> list[dict]:
        """Get participants that failed but can still be retried."""
        rows = self._conn.execute(
            "SELECT * FROM round_participants"
            " WHERE round_id=? AND status IN ('failed','timed_out') AND retry_count < max_retries"
            " ORDER BY agent_name",
            (round_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def increment_retry(self, round_id: str, agent_name: str) -> dict | None:
        """Increment retry_count and reset status to pending."""
        self._conn.execute(
            "UPDATE round_participants SET retry_count = retry_count + 1, status = 'pending'"
            " WHERE round_id=? AND agent_name=?",
            (round_id, agent_name),
        )
        self._conn.commit()
        return self.get_participant(round_id, agent_name)

    # -- Round phase management --

    def set_round_phase(self, round_id: str, phase: str) -> None:
        self._conn.execute("UPDATE rounds SET phase=? WHERE id=?", (phase, round_id))
        self._conn.commit()

    def complete_round(self, round_id: str) -> None:
        now = _now()
        self._conn.execute(
            "UPDATE rounds SET completed_at=? WHERE id=?", (now, round_id)
        )
        self._conn.commit()

    def close(self):
        self._conn.close()
