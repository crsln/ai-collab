//! CRUD operations for the brainstorm SQLite database.
//!
//! All methods match the Python `brainstorm_db.py` interface 1:1.

use std::path::Path;

use chrono::{DateTime, Utc};
use rusqlite::{Connection, OptionalExtension, params};
use serde_json;

use ai_collab_core::{
    AgentDefinition, AgentDefinitionId, AgentRole, Consensus, ConsensusId, DbError, FeedbackId,
    FeedbackItem, FeedbackResponse, FeedbackResponseId, FeedbackStatus, Guideline, GuidelineId,
    ParticipantId, ParticipantStatus, Response, ResponseId, ResponseQuality, RoleId, RoleTemplate,
    RoleTemplateId, Round, RoundId, RoundParticipant, Session, SessionId, SessionStatus, ToolGuide,
    ToolGuideId, WorkflowId, WorkflowTemplate,
};

use crate::schema::SCHEMA;

// ---------------------------------------------------------------------------
// Error conversions (can't use From due to orphan rule — both types are foreign)
// We use a local extension trait so `?` works transparently.
// ---------------------------------------------------------------------------

/// Extension trait to convert foreign error types into `DbError` via `?`.
trait IntoDbError<T> {
    fn db(self) -> Result<T, DbError>;
}

impl<T> IntoDbError<T> for Result<T, rusqlite::Error> {
    fn db(self) -> Result<T, DbError> {
        self.map_err(|e| DbError::Sqlite(e.to_string()))
    }
}

impl<T> IntoDbError<T> for Result<T, serde_json::Error> {
    fn db(self) -> Result<T, DbError> {
        self.map_err(|e| DbError::Json(e.to_string()))
    }
}

// ---------------------------------------------------------------------------
// DateTime helpers
// ---------------------------------------------------------------------------

fn now_rfc3339() -> String {
    Utc::now().to_rfc3339()
}

fn parse_dt(s: &str) -> DateTime<Utc> {
    DateTime::parse_from_rfc3339(s)
        .map(|dt| dt.with_timezone(&Utc))
        .unwrap_or_else(|_| {
            // Fallback: try parsing ISO 8601 without timezone (assume UTC)
            chrono::NaiveDateTime::parse_from_str(s, "%Y-%m-%dT%H:%M:%S%.f")
                .map(|ndt| ndt.and_utc())
                .unwrap_or_else(|_| Utc::now())
        })
}

fn parse_dt_opt(s: Option<String>) -> Option<DateTime<Utc>> {
    s.map(|v| parse_dt(&v))
}

fn parse_json_vec(s: Option<String>) -> Vec<String> {
    s.and_then(|v| serde_json::from_str(&v).ok())
        .unwrap_or_default()
}

// ---------------------------------------------------------------------------
// BrainstormDb
// ---------------------------------------------------------------------------

pub struct BrainstormDb {
    conn: Connection,
}

impl BrainstormDb {
    /// Open (or create) a database at `path`, apply pragmas and schema.
    pub fn new(path: &Path) -> Result<Self, DbError> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).map_err(|e| DbError::Sqlite(e.to_string()))?;
        }
        let conn = Connection::open(path).db()?;
        Self::init(conn)
    }

    /// Create an in-memory database (for tests).
    pub fn new_in_memory() -> Result<Self, DbError> {
        let conn = Connection::open_in_memory().db()?;
        Self::init(conn)
    }

    fn init(conn: Connection) -> Result<Self, DbError> {
        conn.execute_batch("PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;")
            .db()?;
        conn.execute_batch(SCHEMA).db()?;
        Ok(Self { conn })
    }

    // -----------------------------------------------------------------------
    // Sessions
    // -----------------------------------------------------------------------

    pub fn create_session(&self, topic: &str, project: Option<&str>) -> Result<Session, DbError> {
        let id = SessionId::new();
        let now = now_rfc3339();
        self.conn
            .execute(
                "INSERT INTO sessions (id, topic, project, created_at) VALUES (?1, ?2, ?3, ?4)",
                params![id.as_str(), topic, project, now],
            )
            .db()?;
        Ok(Session {
            id,
            topic: topic.to_string(),
            project: project.map(String::from),
            context: None,
            status: SessionStatus::Active,
            created_at: parse_dt(&now),
        })
    }

    pub fn get_session(&self, session_id: &SessionId) -> Result<Option<Session>, DbError> {
        let mut stmt = self
            .conn
            .prepare("SELECT id, topic, project, context, status, created_at FROM sessions WHERE id = ?1").db()?;
        let row = stmt
            .query_row(params![session_id.as_str()], |row| {
                Ok(Session {
                    id: SessionId::from(row.get::<_, String>(0)?),
                    topic: row.get(1)?,
                    project: row.get(2)?,
                    context: row.get(3)?,
                    status: row
                        .get::<_, String>(4)?
                        .parse()
                        .unwrap_or(SessionStatus::Active),
                    created_at: parse_dt(&row.get::<_, String>(5)?),
                })
            })
            .optional()
            .db()?;
        Ok(row)
    }

    pub fn list_sessions(
        &self,
        status: Option<&SessionStatus>,
        limit: i64,
    ) -> Result<Vec<Session>, DbError> {
        let mut sessions = Vec::new();
        if let Some(st) = status {
            let mut stmt = self
                .conn
                .prepare(
                    "SELECT id, topic, project, context, status, created_at \
                 FROM sessions WHERE status = ?1 ORDER BY created_at DESC LIMIT ?2",
                )
                .db()?;
            let rows = stmt
                .query_map(params![st.to_string(), limit], |row| {
                    Ok(Session {
                        id: SessionId::from(row.get::<_, String>(0)?),
                        topic: row.get(1)?,
                        project: row.get(2)?,
                        context: row.get(3)?,
                        status: row
                            .get::<_, String>(4)?
                            .parse()
                            .unwrap_or(SessionStatus::Active),
                        created_at: parse_dt(&row.get::<_, String>(5)?),
                    })
                })
                .db()?;
            for r in rows {
                sessions.push(r.db()?);
            }
        } else {
            let mut stmt = self
                .conn
                .prepare(
                    "SELECT id, topic, project, context, status, created_at \
                 FROM sessions ORDER BY created_at DESC LIMIT ?1",
                )
                .db()?;
            let rows = stmt
                .query_map(params![limit], |row| {
                    Ok(Session {
                        id: SessionId::from(row.get::<_, String>(0)?),
                        topic: row.get(1)?,
                        project: row.get(2)?,
                        context: row.get(3)?,
                        status: row
                            .get::<_, String>(4)?
                            .parse()
                            .unwrap_or(SessionStatus::Active),
                        created_at: parse_dt(&row.get::<_, String>(5)?),
                    })
                })
                .db()?;
            for r in rows {
                sessions.push(r.db()?);
            }
        }
        Ok(sessions)
    }

    pub fn set_context(&self, session_id: &SessionId, context: &str) -> Result<(), DbError> {
        self.conn
            .execute(
                "UPDATE sessions SET context = ?1 WHERE id = ?2",
                params![context, session_id.as_str()],
            )
            .db()?;
        Ok(())
    }

    pub fn get_context(&self, session_id: &SessionId) -> Result<Option<String>, DbError> {
        let ctx: Option<String> = self
            .conn
            .query_row(
                "SELECT context FROM sessions WHERE id = ?1",
                params![session_id.as_str()],
                |row| row.get(0),
            )
            .optional()
            .db()?
            .flatten();
        Ok(ctx)
    }

    pub fn complete_session(&self, session_id: &SessionId) -> Result<(), DbError> {
        self.conn
            .execute(
                "UPDATE sessions SET status = 'completed' WHERE id = ?1",
                params![session_id.as_str()],
            )
            .db()?;
        Ok(())
    }

    // -----------------------------------------------------------------------
    // Rounds
    // -----------------------------------------------------------------------

    pub fn create_round(
        &self,
        session_id: &SessionId,
        objective: Option<&str>,
        question: Option<&str>,
    ) -> Result<Round, DbError> {
        let id = RoundId::new();
        let now = now_rfc3339();
        self.conn.execute(
            "INSERT INTO rounds (id, session_id, round_number, objective, question, created_at) \
             VALUES (?1, ?2, \
             (SELECT COALESCE(MAX(round_number), 0) + 1 FROM rounds WHERE session_id = ?2), \
             ?3, ?4, ?5)",
            params![id.as_str(), session_id.as_str(), objective, question, now],
        ).db()?;
        let round_number: i32 = self
            .conn
            .query_row(
                "SELECT round_number FROM rounds WHERE id = ?1",
                params![id.as_str()],
                |row| row.get(0),
            )
            .db()?;
        Ok(Round {
            id,
            session_id: session_id.clone(),
            round_number,
            objective: objective.map(String::from),
            question: question.map(String::from),
            created_at: parse_dt(&now),
        })
    }

    pub fn get_round(&self, round_id: &RoundId) -> Result<Option<Round>, DbError> {
        let mut stmt = self
            .conn
            .prepare(
                "SELECT id, session_id, round_number, objective, question, created_at \
             FROM rounds WHERE id = ?1",
            )
            .db()?;
        let row = stmt
            .query_row(params![round_id.as_str()], |row| {
                Ok(Round {
                    id: RoundId::from(row.get::<_, String>(0)?),
                    session_id: SessionId::from(row.get::<_, String>(1)?),
                    round_number: row.get(2)?,
                    objective: row.get(3)?,
                    question: row.get(4)?,
                    created_at: parse_dt(&row.get::<_, String>(5)?),
                })
            })
            .optional()
            .db()?;
        Ok(row)
    }

    pub fn list_rounds(&self, session_id: &SessionId) -> Result<Vec<Round>, DbError> {
        let mut stmt = self
            .conn
            .prepare(
                "SELECT id, session_id, round_number, objective, question, created_at \
             FROM rounds WHERE session_id = ?1 ORDER BY round_number",
            )
            .db()?;
        let rows = stmt
            .query_map(params![session_id.as_str()], |row| {
                Ok(Round {
                    id: RoundId::from(row.get::<_, String>(0)?),
                    session_id: SessionId::from(row.get::<_, String>(1)?),
                    round_number: row.get(2)?,
                    objective: row.get(3)?,
                    question: row.get(4)?,
                    created_at: parse_dt(&row.get::<_, String>(5)?),
                })
            })
            .db()?;
        rows.collect::<Result<Vec<_>, _>>()
            .map_err(|e| DbError::Sqlite(e.to_string()))
    }

    // -----------------------------------------------------------------------
    // Responses
    // -----------------------------------------------------------------------

    pub fn save_response(
        &self,
        round_id: &RoundId,
        agent_name: &str,
        content: &str,
    ) -> Result<Response, DbError> {
        let id = ResponseId::new();
        let now = now_rfc3339();
        self.conn
            .execute(
                "INSERT INTO responses (id, round_id, agent_name, content, created_at) \
             VALUES (?1, ?2, ?3, ?4, ?5) \
             ON CONFLICT(round_id, agent_name) DO UPDATE SET \
             content=excluded.content, created_at=excluded.created_at",
                params![id.as_str(), round_id.as_str(), agent_name, content, now],
            )
            .db()?;
        // Fetch the actual ID (may differ on upsert)
        let actual_id: String = self
            .conn
            .query_row(
                "SELECT id FROM responses WHERE round_id = ?1 AND agent_name = ?2",
                params![round_id.as_str(), agent_name],
                |row| row.get(0),
            )
            .db()?;
        Ok(Response {
            id: ResponseId::from(actual_id),
            round_id: round_id.clone(),
            agent_name: agent_name.to_string(),
            content: content.to_string(),
            quality: None,
            source: None,
            created_at: parse_dt(&now),
        })
    }

    pub fn update_response_quality(
        &self,
        response_id: &ResponseId,
        quality: &ResponseQuality,
    ) -> Result<(), DbError> {
        self.conn
            .execute(
                "UPDATE responses SET quality = ?1 WHERE id = ?2",
                params![quality.to_string(), response_id.as_str()],
            )
            .db()?;
        Ok(())
    }

    pub fn get_response(
        &self,
        round_id: &RoundId,
        agent_name: &str,
    ) -> Result<Option<Response>, DbError> {
        let mut stmt = self
            .conn
            .prepare(
                "SELECT id, round_id, agent_name, content, quality, source, created_at \
             FROM responses WHERE round_id = ?1 AND agent_name = ?2",
            )
            .db()?;
        let row = stmt
            .query_row(params![round_id.as_str(), agent_name], |row| {
                Ok(Response {
                    id: ResponseId::from(row.get::<_, String>(0)?),
                    round_id: RoundId::from(row.get::<_, String>(1)?),
                    agent_name: row.get(2)?,
                    content: row.get(3)?,
                    quality: row
                        .get::<_, Option<String>>(4)?
                        .and_then(|s| s.parse().ok()),
                    source: row.get(5)?,
                    created_at: parse_dt(&row.get::<_, String>(6)?),
                })
            })
            .optional()
            .db()?;
        Ok(row)
    }

    pub fn get_round_responses(&self, round_id: &RoundId) -> Result<Vec<Response>, DbError> {
        let mut stmt = self
            .conn
            .prepare(
                "SELECT id, round_id, agent_name, content, quality, source, created_at \
             FROM responses WHERE round_id = ?1 ORDER BY created_at",
            )
            .db()?;
        let rows = stmt
            .query_map(params![round_id.as_str()], |row| {
                Ok(Response {
                    id: ResponseId::from(row.get::<_, String>(0)?),
                    round_id: RoundId::from(row.get::<_, String>(1)?),
                    agent_name: row.get(2)?,
                    content: row.get(3)?,
                    quality: row
                        .get::<_, Option<String>>(4)?
                        .and_then(|s| s.parse().ok()),
                    source: row.get(5)?,
                    created_at: parse_dt(&row.get::<_, String>(6)?),
                })
            })
            .db()?;
        rows.collect::<Result<Vec<_>, _>>()
            .map_err(|e| DbError::Sqlite(e.to_string()))
    }

    // -----------------------------------------------------------------------
    // Consensus
    // -----------------------------------------------------------------------

    pub fn save_consensus(
        &self,
        session_id: &SessionId,
        content: &str,
        round_id: Option<&RoundId>,
    ) -> Result<Consensus, DbError> {
        let id = ConsensusId::new();
        let now = now_rfc3339();
        let round_id_str = round_id.map(|r| r.as_str().to_string());
        self.conn
            .execute(
                "INSERT INTO consensus (id, session_id, round_id, version, content, created_at) \
             VALUES (?1, ?2, ?3, \
             (SELECT COALESCE(MAX(version), 0) + 1 FROM consensus WHERE session_id = ?2), \
             ?4, ?5)",
                params![id.as_str(), session_id.as_str(), round_id_str, content, now],
            )
            .db()?;
        let version: i32 = self
            .conn
            .query_row(
                "SELECT version FROM consensus WHERE id = ?1",
                params![id.as_str()],
                |row| row.get(0),
            )
            .db()?;
        Ok(Consensus {
            id,
            session_id: session_id.clone(),
            round_id: round_id.cloned(),
            version,
            content: content.to_string(),
            created_at: parse_dt(&now),
        })
    }

    pub fn get_latest_consensus(
        &self,
        session_id: &SessionId,
    ) -> Result<Option<Consensus>, DbError> {
        let mut stmt = self
            .conn
            .prepare(
                "SELECT id, session_id, round_id, version, content, created_at \
             FROM consensus WHERE session_id = ?1 ORDER BY version DESC LIMIT 1",
            )
            .db()?;
        let row = stmt
            .query_row(params![session_id.as_str()], |row| {
                Ok(Consensus {
                    id: ConsensusId::from(row.get::<_, String>(0)?),
                    session_id: SessionId::from(row.get::<_, String>(1)?),
                    round_id: row.get::<_, Option<String>>(2)?.map(RoundId::from),
                    version: row.get(3)?,
                    content: row.get(4)?,
                    created_at: parse_dt(&row.get::<_, String>(5)?),
                })
            })
            .optional()
            .db()?;
        Ok(row)
    }

    // -----------------------------------------------------------------------
    // Feedback Items
    // -----------------------------------------------------------------------

    pub fn create_feedback_item(
        &self,
        session_id: &SessionId,
        source_round_id: &RoundId,
        source_agent: &str,
        title: &str,
        content: &str,
    ) -> Result<FeedbackItem, DbError> {
        let id = FeedbackId::new();
        let now = now_rfc3339();
        self.conn
            .execute(
                "INSERT INTO feedback_items \
             (id, session_id, source_round_id, source_agent, title, content, created_at) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
                params![
                    id.as_str(),
                    session_id.as_str(),
                    source_round_id.as_str(),
                    source_agent,
                    title,
                    content,
                    now
                ],
            )
            .db()?;
        Ok(FeedbackItem {
            id,
            session_id: session_id.clone(),
            source_round_id: source_round_id.clone(),
            source_agent: source_agent.to_string(),
            title: title.to_string(),
            content: content.to_string(),
            status: FeedbackStatus::Pending,
            created_at: parse_dt(&now),
        })
    }

    pub fn list_feedback_items(
        &self,
        session_id: &SessionId,
        status: Option<&FeedbackStatus>,
    ) -> Result<Vec<FeedbackItem>, DbError> {
        let mut items = Vec::new();
        if let Some(st) = status {
            let mut stmt = self.conn.prepare(
                "SELECT id, session_id, source_round_id, source_agent, title, content, status, created_at \
                 FROM feedback_items WHERE session_id = ?1 AND status = ?2 ORDER BY created_at",
            ).db()?;
            let rows = stmt
                .query_map(params![session_id.as_str(), st.to_string()], |row| {
                    Ok(Self::row_to_feedback_item(row))
                })
                .db()?;
            for r in rows {
                items.push(r.db()?);
            }
        } else {
            let mut stmt = self.conn.prepare(
                "SELECT id, session_id, source_round_id, source_agent, title, content, status, created_at \
                 FROM feedback_items WHERE session_id = ?1 ORDER BY created_at",
            ).db()?;
            let rows = stmt
                .query_map(params![session_id.as_str()], |row| {
                    Ok(Self::row_to_feedback_item(row))
                })
                .db()?;
            for r in rows {
                items.push(r.db()?);
            }
        }
        Ok(items)
    }

    fn row_to_feedback_item(row: &rusqlite::Row<'_>) -> FeedbackItem {
        FeedbackItem {
            id: FeedbackId::from(row.get::<_, String>(0).unwrap_or_default()),
            session_id: SessionId::from(row.get::<_, String>(1).unwrap_or_default()),
            source_round_id: RoundId::from(row.get::<_, String>(2).unwrap_or_default()),
            source_agent: row.get(3).unwrap_or_default(),
            title: row.get(4).unwrap_or_default(),
            content: row.get(5).unwrap_or_default(),
            status: row
                .get::<_, String>(6)
                .unwrap_or_default()
                .parse()
                .unwrap_or(FeedbackStatus::Pending),
            created_at: parse_dt(&row.get::<_, String>(7).unwrap_or_default()),
        }
    }

    pub fn get_feedback_item(
        &self,
        item_id: &FeedbackId,
    ) -> Result<Option<(FeedbackItem, Vec<FeedbackResponse>)>, DbError> {
        let mut stmt = self.conn.prepare(
            "SELECT id, session_id, source_round_id, source_agent, title, content, status, created_at \
             FROM feedback_items WHERE id = ?1",
        ).db()?;
        let item = stmt
            .query_row(params![item_id.as_str()], |row| {
                Ok(Self::row_to_feedback_item(row))
            })
            .optional()
            .db()?;
        match item {
            Some(fi) => {
                let responses = self.get_feedback_responses(&fi.id)?;
                Ok(Some((fi, responses)))
            }
            None => Ok(None),
        }
    }

    pub fn update_feedback_status(
        &self,
        item_id: &FeedbackId,
        status: &FeedbackStatus,
    ) -> Result<(), DbError> {
        self.conn
            .execute(
                "UPDATE feedback_items SET status = ?1 WHERE id = ?2",
                params![status.to_string(), item_id.as_str()],
            )
            .db()?;
        Ok(())
    }

    // -----------------------------------------------------------------------
    // Feedback Responses
    // -----------------------------------------------------------------------

    pub fn save_feedback_response(
        &self,
        item_id: &FeedbackId,
        round_id: &RoundId,
        agent_name: &str,
        verdict: &str,
        reasoning: &str,
    ) -> Result<FeedbackResponse, DbError> {
        let id = FeedbackResponseId::new();
        let now = now_rfc3339();
        self.conn.execute(
            "INSERT INTO feedback_responses \
             (id, item_id, round_id, agent_name, verdict, reasoning, created_at) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7) \
             ON CONFLICT(item_id, round_id, agent_name) DO UPDATE SET \
             verdict=excluded.verdict, reasoning=excluded.reasoning, created_at=excluded.created_at",
            params![
                id.as_str(),
                item_id.as_str(),
                round_id.as_str(),
                agent_name,
                verdict,
                reasoning,
                now
            ],
        ).db()?;
        // Update feedback_items_completed counter on round_participants
        self.conn
            .execute(
                "UPDATE round_participants SET feedback_items_completed = (\
             SELECT COUNT(DISTINCT fr.item_id) FROM feedback_responses fr \
             WHERE fr.round_id = ?1 AND fr.agent_name = ?2\
             ) WHERE round_id = ?1 AND agent_name = ?2",
                params![round_id.as_str(), agent_name],
            )
            .db()?;
        // Fetch actual ID (may differ on upsert)
        let actual_id: String = self.conn.query_row(
            "SELECT id FROM feedback_responses WHERE item_id = ?1 AND round_id = ?2 AND agent_name = ?3",
            params![item_id.as_str(), round_id.as_str(), agent_name],
            |row| row.get(0),
        ).db()?;
        Ok(FeedbackResponse {
            id: FeedbackResponseId::from(actual_id),
            item_id: item_id.clone(),
            round_id: round_id.clone(),
            agent_name: agent_name.to_string(),
            verdict: verdict.to_string(),
            reasoning: reasoning.to_string(),
            created_at: parse_dt(&now),
        })
    }

    pub fn get_feedback_responses(
        &self,
        item_id: &FeedbackId,
    ) -> Result<Vec<FeedbackResponse>, DbError> {
        let mut stmt = self
            .conn
            .prepare(
                "SELECT id, item_id, round_id, agent_name, verdict, reasoning, created_at \
             FROM feedback_responses WHERE item_id = ?1 ORDER BY created_at",
            )
            .db()?;
        let rows = stmt
            .query_map(params![item_id.as_str()], |row| {
                Ok(FeedbackResponse {
                    id: FeedbackResponseId::from(row.get::<_, String>(0)?),
                    item_id: FeedbackId::from(row.get::<_, String>(1)?),
                    round_id: RoundId::from(row.get::<_, String>(2)?),
                    agent_name: row.get(3)?,
                    verdict: row.get(4)?,
                    reasoning: row.get(5)?,
                    created_at: parse_dt(&row.get::<_, String>(6)?),
                })
            })
            .db()?;
        rows.collect::<Result<Vec<_>, _>>()
            .map_err(|e| DbError::Sqlite(e.to_string()))
    }

    // -----------------------------------------------------------------------
    // Agent Roles
    // -----------------------------------------------------------------------

    pub fn set_role(
        &self,
        session_id: &SessionId,
        agent_name: &str,
        role: &str,
        source_slug: Option<&str>,
    ) -> Result<AgentRole, DbError> {
        let id = RoleId::new();
        let now = now_rfc3339();
        self.conn.execute(
            "INSERT INTO agent_roles (id, session_id, agent_name, role, source_slug, created_at) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6) \
             ON CONFLICT(session_id, agent_name) DO UPDATE SET \
             role=excluded.role, source_slug=excluded.source_slug, created_at=excluded.created_at",
            params![
                id.as_str(),
                session_id.as_str(),
                agent_name,
                role,
                source_slug,
                now
            ],
        ).db()?;
        let actual_id: String = self
            .conn
            .query_row(
                "SELECT id FROM agent_roles WHERE session_id = ?1 AND agent_name = ?2",
                params![session_id.as_str(), agent_name],
                |row| row.get(0),
            )
            .db()?;
        Ok(AgentRole {
            id: RoleId::from(actual_id),
            session_id: session_id.clone(),
            agent_name: agent_name.to_string(),
            role: role.to_string(),
            source_slug: source_slug.map(String::from),
            created_at: parse_dt(&now),
        })
    }

    pub fn get_role(
        &self,
        session_id: &SessionId,
        agent_name: &str,
    ) -> Result<Option<AgentRole>, DbError> {
        let mut stmt = self
            .conn
            .prepare(
                "SELECT id, session_id, agent_name, role, source_slug, created_at \
             FROM agent_roles WHERE session_id = ?1 AND agent_name = ?2",
            )
            .db()?;
        let row = stmt
            .query_row(params![session_id.as_str(), agent_name], |row| {
                Ok(AgentRole {
                    id: RoleId::from(row.get::<_, String>(0)?),
                    session_id: SessionId::from(row.get::<_, String>(1)?),
                    agent_name: row.get(2)?,
                    role: row.get(3)?,
                    source_slug: row.get(4)?,
                    created_at: parse_dt(&row.get::<_, String>(5)?),
                })
            })
            .optional()
            .db()?;
        Ok(row)
    }

    pub fn list_roles(&self, session_id: &SessionId) -> Result<Vec<AgentRole>, DbError> {
        let mut stmt = self
            .conn
            .prepare(
                "SELECT id, session_id, agent_name, role, source_slug, created_at \
             FROM agent_roles WHERE session_id = ?1 ORDER BY agent_name",
            )
            .db()?;
        let rows = stmt
            .query_map(params![session_id.as_str()], |row| {
                Ok(AgentRole {
                    id: RoleId::from(row.get::<_, String>(0)?),
                    session_id: SessionId::from(row.get::<_, String>(1)?),
                    agent_name: row.get(2)?,
                    role: row.get(3)?,
                    source_slug: row.get(4)?,
                    created_at: parse_dt(&row.get::<_, String>(5)?),
                })
            })
            .db()?;
        rows.collect::<Result<Vec<_>, _>>()
            .map_err(|e| DbError::Sqlite(e.to_string()))
    }

    // -----------------------------------------------------------------------
    // Guidelines
    // -----------------------------------------------------------------------

    pub fn add_guideline(
        &self,
        session_id: &SessionId,
        content: &str,
    ) -> Result<Guideline, DbError> {
        let id = GuidelineId::new();
        let now = now_rfc3339();
        self.conn
            .execute(
                "INSERT INTO guidelines (id, session_id, content, created_at) \
             VALUES (?1, ?2, ?3, ?4)",
                params![id.as_str(), session_id.as_str(), content, now],
            )
            .db()?;
        Ok(Guideline {
            id,
            session_id: session_id.clone(),
            content: content.to_string(),
            created_at: parse_dt(&now),
        })
    }

    pub fn list_guidelines(&self, session_id: &SessionId) -> Result<Vec<Guideline>, DbError> {
        let mut stmt = self
            .conn
            .prepare(
                "SELECT id, session_id, content, created_at \
             FROM guidelines WHERE session_id = ?1 ORDER BY created_at",
            )
            .db()?;
        let rows = stmt
            .query_map(params![session_id.as_str()], |row| {
                Ok(Guideline {
                    id: GuidelineId::from(row.get::<_, String>(0)?),
                    session_id: SessionId::from(row.get::<_, String>(1)?),
                    content: row.get(2)?,
                    created_at: parse_dt(&row.get::<_, String>(3)?),
                })
            })
            .db()?;
        rows.collect::<Result<Vec<_>, _>>()
            .map_err(|e| DbError::Sqlite(e.to_string()))
    }

    // -----------------------------------------------------------------------
    // Agent Definitions (global)
    // -----------------------------------------------------------------------

    #[allow(clippy::too_many_arguments)]
    pub fn upsert_agent_definition(
        &self,
        agent_name: &str,
        display_name: &str,
        capabilities: &str,
        default_role: &str,
        approach: &str,
        vision: Option<&str>,
        angle: Option<&str>,
        behavior: Option<&str>,
        tags: Option<&[String]>,
        backend_hint: Option<&str>,
    ) -> Result<(), DbError> {
        let now = now_rfc3339();
        let tags_json = serde_json::to_string(&tags.unwrap_or(&[])).db()?;
        let existing = self.get_agent_definition(agent_name)?;
        if existing.is_some() {
            self.conn
                .execute(
                    "UPDATE agent_definitions SET display_name=?1, capabilities=?2, \
                 default_role=?3, approach=?4, vision=?5, angle=?6, behavior=?7, \
                 tags=?8, backend_hint=?9, updated_at=?10 WHERE agent_name=?11",
                    params![
                        display_name,
                        capabilities,
                        default_role,
                        approach,
                        vision,
                        angle,
                        behavior,
                        tags_json,
                        backend_hint,
                        now,
                        agent_name
                    ],
                )
                .db()?;
        } else {
            let id = AgentDefinitionId::new();
            self.conn
                .execute(
                    "INSERT INTO agent_definitions \
                 (id, agent_name, display_name, capabilities, default_role, approach, \
                  vision, angle, behavior, tags, backend_hint, created_at, updated_at) \
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13)",
                    params![
                        id.as_str(),
                        agent_name,
                        display_name,
                        capabilities,
                        default_role,
                        approach,
                        vision,
                        angle,
                        behavior,
                        tags_json,
                        backend_hint,
                        now,
                        now
                    ],
                )
                .db()?;
        }
        Ok(())
    }

    pub fn get_agent_definition(
        &self,
        agent_name: &str,
    ) -> Result<Option<AgentDefinition>, DbError> {
        let mut stmt = self
            .conn
            .prepare(
                "SELECT id, agent_name, display_name, capabilities, default_role, approach, \
             vision, angle, behavior, tags, backend_hint, created_at, updated_at \
             FROM agent_definitions WHERE agent_name = ?1",
            )
            .db()?;
        let row = stmt
            .query_row(params![agent_name], |row| {
                Ok(AgentDefinition {
                    id: AgentDefinitionId::from(row.get::<_, String>(0)?),
                    agent_name: row.get(1)?,
                    display_name: row.get(2)?,
                    capabilities: row.get(3)?,
                    default_role: row.get(4)?,
                    approach: row.get(5)?,
                    vision: row.get(6)?,
                    angle: row.get(7)?,
                    behavior: row.get(8)?,
                    tags: parse_json_vec(row.get(9)?),
                    backend_hint: row.get(10)?,
                    created_at: parse_dt(&row.get::<_, String>(11)?),
                    updated_at: parse_dt(&row.get::<_, String>(12)?),
                })
            })
            .optional()
            .db()?;
        Ok(row)
    }

    pub fn list_agent_definitions(&self) -> Result<Vec<AgentDefinition>, DbError> {
        let mut stmt = self
            .conn
            .prepare(
                "SELECT id, agent_name, display_name, capabilities, default_role, approach, \
             vision, angle, behavior, tags, backend_hint, created_at, updated_at \
             FROM agent_definitions ORDER BY agent_name",
            )
            .db()?;
        let rows = stmt
            .query_map([], |row| {
                Ok(AgentDefinition {
                    id: AgentDefinitionId::from(row.get::<_, String>(0)?),
                    agent_name: row.get(1)?,
                    display_name: row.get(2)?,
                    capabilities: row.get(3)?,
                    default_role: row.get(4)?,
                    approach: row.get(5)?,
                    vision: row.get(6)?,
                    angle: row.get(7)?,
                    behavior: row.get(8)?,
                    tags: parse_json_vec(row.get(9)?),
                    backend_hint: row.get(10)?,
                    created_at: parse_dt(&row.get::<_, String>(11)?),
                    updated_at: parse_dt(&row.get::<_, String>(12)?),
                })
            })
            .db()?;
        rows.collect::<Result<Vec<_>, _>>()
            .map_err(|e| DbError::Sqlite(e.to_string()))
    }

    // -----------------------------------------------------------------------
    // Workflow Templates (global)
    // -----------------------------------------------------------------------

    pub fn upsert_workflow_template(
        &self,
        name: &str,
        overview: &str,
        phases_json: &str,
        convergence_rules: &str,
        response_format: &str,
    ) -> Result<(), DbError> {
        let now = now_rfc3339();
        let existing = self.get_workflow_template(name)?;
        if let Some(ex) = existing {
            let new_version = ex.version + 1;
            self.conn
                .execute(
                    "UPDATE workflow_templates SET version=?1, overview=?2, phases=?3, \
                 convergence_rules=?4, response_format=?5, updated_at=?6 WHERE name=?7",
                    params![
                        new_version,
                        overview,
                        phases_json,
                        convergence_rules,
                        response_format,
                        now,
                        name
                    ],
                )
                .db()?;
        } else {
            let id = WorkflowId::new();
            self.conn
                .execute(
                    "INSERT INTO workflow_templates \
                 (id, name, version, overview, phases, convergence_rules, response_format, \
                  created_at, updated_at) \
                 VALUES (?1, ?2, 1, ?3, ?4, ?5, ?6, ?7, ?8)",
                    params![
                        id.as_str(),
                        name,
                        overview,
                        phases_json,
                        convergence_rules,
                        response_format,
                        now,
                        now
                    ],
                )
                .db()?;
        }
        Ok(())
    }

    pub fn get_workflow_template(&self, name: &str) -> Result<Option<WorkflowTemplate>, DbError> {
        let mut stmt = self
            .conn
            .prepare(
                "SELECT id, name, version, overview, phases, convergence_rules, response_format, \
             created_at, updated_at \
             FROM workflow_templates WHERE name = ?1",
            )
            .db()?;
        let row = stmt
            .query_row(params![name], |row| {
                Ok(WorkflowTemplate {
                    id: WorkflowId::from(row.get::<_, String>(0)?),
                    name: row.get(1)?,
                    version: row.get(2)?,
                    overview: row.get(3)?,
                    phases: row.get(4)?,
                    convergence_rules: row.get(5)?,
                    response_format: row.get(6)?,
                    created_at: parse_dt(&row.get::<_, String>(7)?),
                    updated_at: parse_dt(&row.get::<_, String>(8)?),
                })
            })
            .optional()
            .db()?;
        Ok(row)
    }

    // -----------------------------------------------------------------------
    // Tool Guides (global)
    // -----------------------------------------------------------------------

    pub fn upsert_tool_guide(
        &self,
        tool_name: &str,
        phase: &str,
        purpose: &str,
        usage: &str,
    ) -> Result<(), DbError> {
        let now = now_rfc3339();
        let existing = self.get_tool_guide(tool_name)?;
        if existing.is_some() {
            self.conn
                .execute(
                    "UPDATE tool_guides SET phase=?1, purpose=?2, usage=?3, created_at=?4 \
                 WHERE tool_name=?5",
                    params![phase, purpose, usage, now, tool_name],
                )
                .db()?;
        } else {
            let id = ToolGuideId::new();
            self.conn
                .execute(
                    "INSERT INTO tool_guides (id, tool_name, phase, purpose, usage, created_at) \
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
                    params![id.as_str(), tool_name, phase, purpose, usage, now],
                )
                .db()?;
        }
        Ok(())
    }

    pub fn get_tool_guide(&self, tool_name: &str) -> Result<Option<ToolGuide>, DbError> {
        let mut stmt = self
            .conn
            .prepare(
                "SELECT id, tool_name, phase, purpose, usage, created_at \
             FROM tool_guides WHERE tool_name = ?1",
            )
            .db()?;
        let row = stmt
            .query_row(params![tool_name], |row| {
                Ok(ToolGuide {
                    id: ToolGuideId::from(row.get::<_, String>(0)?),
                    tool_name: row.get(1)?,
                    phase: row.get(2)?,
                    purpose: row.get(3)?,
                    usage: row.get(4)?,
                    created_at: parse_dt(&row.get::<_, String>(5)?),
                })
            })
            .optional()
            .db()?;
        Ok(row)
    }

    pub fn list_tool_guides(&self, phase: Option<&str>) -> Result<Vec<ToolGuide>, DbError> {
        let mut guides = Vec::new();
        if let Some(ph) = phase {
            let mut stmt = self
                .conn
                .prepare(
                    "SELECT id, tool_name, phase, purpose, usage, created_at \
                 FROM tool_guides WHERE phase = ?1 ORDER BY tool_name",
                )
                .db()?;
            let rows = stmt
                .query_map(params![ph], |row| {
                    Ok(ToolGuide {
                        id: ToolGuideId::from(row.get::<_, String>(0)?),
                        tool_name: row.get(1)?,
                        phase: row.get(2)?,
                        purpose: row.get(3)?,
                        usage: row.get(4)?,
                        created_at: parse_dt(&row.get::<_, String>(5)?),
                    })
                })
                .db()?;
            for r in rows {
                guides.push(r.db()?);
            }
        } else {
            let mut stmt = self
                .conn
                .prepare(
                    "SELECT id, tool_name, phase, purpose, usage, created_at \
                 FROM tool_guides ORDER BY phase, tool_name",
                )
                .db()?;
            let rows = stmt
                .query_map([], |row| {
                    Ok(ToolGuide {
                        id: ToolGuideId::from(row.get::<_, String>(0)?),
                        tool_name: row.get(1)?,
                        phase: row.get(2)?,
                        purpose: row.get(3)?,
                        usage: row.get(4)?,
                        created_at: parse_dt(&row.get::<_, String>(5)?),
                    })
                })
                .db()?;
            for r in rows {
                guides.push(r.db()?);
            }
        }
        Ok(guides)
    }

    // -----------------------------------------------------------------------
    // Role Library
    // -----------------------------------------------------------------------

    #[allow(clippy::too_many_arguments)]
    pub fn create_role_template(
        &self,
        slug: &str,
        display_name: &str,
        description: &str,
        role_text: &str,
        agent_name: Option<&str>,
        approach: Option<&str>,
        tags: Option<&[String]>,
        notes: Option<&str>,
        vision: Option<&str>,
        angle: Option<&str>,
        behavior: Option<&str>,
        mandates: Option<&[String]>,
    ) -> Result<RoleTemplate, DbError> {
        let id = RoleTemplateId::new();
        let now = now_rfc3339();
        let tags_json = serde_json::to_string(&tags.unwrap_or(&[])).db()?;
        let mandates_json = serde_json::to_string(&mandates.unwrap_or(&[])).db()?;
        self.conn
            .execute(
                "INSERT INTO role_library \
             (id, slug, display_name, agent_name, description, role_text, approach, \
              vision, angle, behavior, mandates, tags, notes, created_at, updated_at) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15)",
                params![
                    id.as_str(),
                    slug,
                    display_name,
                    agent_name,
                    description,
                    role_text,
                    approach,
                    vision,
                    angle,
                    behavior,
                    mandates_json,
                    tags_json,
                    notes,
                    now,
                    now
                ],
            )
            .db()?;
        Ok(RoleTemplate {
            id,
            slug: slug.to_string(),
            display_name: display_name.to_string(),
            agent_name: agent_name.map(String::from),
            description: description.to_string(),
            role_text: role_text.to_string(),
            approach: approach.map(String::from),
            vision: vision.map(String::from),
            angle: angle.map(String::from),
            behavior: behavior.map(String::from),
            mandates: mandates.map(|m| m.to_vec()).unwrap_or_default(),
            tags: tags.map(|t| t.to_vec()).unwrap_or_default(),
            usage_count: 0,
            last_used_at: None,
            notes: notes.map(String::from),
            created_at: parse_dt(&now),
            updated_at: parse_dt(&now),
        })
    }

    pub fn get_role_template(&self, slug_or_id: &str) -> Result<Option<RoleTemplate>, DbError> {
        let mut stmt = self
            .conn
            .prepare(
                "SELECT id, slug, display_name, agent_name, description, role_text, approach, \
             vision, angle, behavior, mandates, tags, usage_count, last_used_at, notes, \
             created_at, updated_at \
             FROM role_library WHERE slug = ?1 OR id = ?1",
            )
            .db()?;
        let row = stmt
            .query_row(params![slug_or_id], |row| {
                Ok(RoleTemplate {
                    id: RoleTemplateId::from(row.get::<_, String>(0)?),
                    slug: row.get(1)?,
                    display_name: row.get(2)?,
                    agent_name: row.get(3)?,
                    description: row.get(4)?,
                    role_text: row.get(5)?,
                    approach: row.get(6)?,
                    vision: row.get(7)?,
                    angle: row.get(8)?,
                    behavior: row.get(9)?,
                    mandates: parse_json_vec(row.get(10)?),
                    tags: parse_json_vec(row.get(11)?),
                    usage_count: row.get(12)?,
                    last_used_at: parse_dt_opt(row.get(13)?),
                    notes: row.get(14)?,
                    created_at: parse_dt(&row.get::<_, String>(15)?),
                    updated_at: parse_dt(&row.get::<_, String>(16)?),
                })
            })
            .optional()
            .db()?;
        Ok(row)
    }

    pub fn list_role_templates(
        &self,
        agent_name: Option<&str>,
        tag: Option<&str>,
    ) -> Result<Vec<RoleTemplate>, DbError> {
        let mut templates = Vec::new();
        if let Some(an) = agent_name {
            let mut stmt = self
                .conn
                .prepare(
                    "SELECT id, slug, display_name, agent_name, description, role_text, approach, \
                 vision, angle, behavior, mandates, tags, usage_count, last_used_at, notes, \
                 created_at, updated_at \
                 FROM role_library WHERE agent_name = ?1 OR agent_name IS NULL \
                 ORDER BY usage_count DESC, display_name",
                )
                .db()?;
            let rows = stmt
                .query_map(params![an], |row| Ok(Self::row_to_role_template(row)))
                .db()?;
            for r in rows {
                templates.push(r.db()?);
            }
        } else {
            let mut stmt = self
                .conn
                .prepare(
                    "SELECT id, slug, display_name, agent_name, description, role_text, approach, \
                 vision, angle, behavior, mandates, tags, usage_count, last_used_at, notes, \
                 created_at, updated_at \
                 FROM role_library ORDER BY usage_count DESC, display_name",
                )
                .db()?;
            let rows = stmt
                .query_map([], |row| Ok(Self::row_to_role_template(row)))
                .db()?;
            for r in rows {
                templates.push(r.db()?);
            }
        }
        // Filter by tag in Rust (matches Python behavior)
        if let Some(t) = tag {
            templates.retain(|tmpl| tmpl.tags.iter().any(|s| s == t));
        }
        Ok(templates)
    }

    fn row_to_role_template(row: &rusqlite::Row<'_>) -> RoleTemplate {
        RoleTemplate {
            id: RoleTemplateId::from(row.get::<_, String>(0).unwrap_or_default()),
            slug: row.get(1).unwrap_or_default(),
            display_name: row.get(2).unwrap_or_default(),
            agent_name: row.get(3).unwrap_or_default(),
            description: row.get(4).unwrap_or_default(),
            role_text: row.get(5).unwrap_or_default(),
            approach: row.get(6).unwrap_or_default(),
            vision: row.get(7).unwrap_or_default(),
            angle: row.get(8).unwrap_or_default(),
            behavior: row.get(9).unwrap_or_default(),
            mandates: parse_json_vec(row.get(10).unwrap_or_default()),
            tags: parse_json_vec(row.get(11).unwrap_or_default()),
            usage_count: row.get(12).unwrap_or(0),
            last_used_at: parse_dt_opt(row.get(13).unwrap_or_default()),
            notes: row.get(14).unwrap_or_default(),
            created_at: parse_dt(&row.get::<_, String>(15).unwrap_or_default()),
            updated_at: parse_dt(&row.get::<_, String>(16).unwrap_or_default()),
        }
    }

    /// Update an existing role template. Only non-None fields are updated.
    #[allow(clippy::too_many_arguments)]
    pub fn update_role_template(
        &self,
        slug_or_id: &str,
        display_name: Option<&str>,
        description: Option<&str>,
        role_text: Option<&str>,
        approach: Option<&str>,
        tags: Option<&[String]>,
        notes: Option<&str>,
        vision: Option<&str>,
        angle: Option<&str>,
        behavior: Option<&str>,
        mandates: Option<&[String]>,
    ) -> Result<Option<RoleTemplate>, DbError> {
        let existing = self.get_role_template(slug_or_id)?;
        let existing = match existing {
            Some(e) => e,
            None => return Ok(None),
        };
        let now = now_rfc3339();
        let mut updates = Vec::new();
        let mut param_values: Vec<Box<dyn rusqlite::types::ToSql>> = Vec::new();

        if let Some(v) = display_name {
            updates.push("display_name = ?");
            param_values.push(Box::new(v.to_string()));
        }
        if let Some(v) = description {
            updates.push("description = ?");
            param_values.push(Box::new(v.to_string()));
        }
        if let Some(v) = role_text {
            updates.push("role_text = ?");
            param_values.push(Box::new(v.to_string()));
        }
        if let Some(v) = approach {
            updates.push("approach = ?");
            param_values.push(Box::new(v.to_string()));
        }
        if let Some(v) = tags {
            updates.push("tags = ?");
            param_values.push(Box::new(serde_json::to_string(v).db()?));
        }
        if let Some(v) = notes {
            updates.push("notes = ?");
            param_values.push(Box::new(v.to_string()));
        }
        if let Some(v) = vision {
            updates.push("vision = ?");
            param_values.push(Box::new(v.to_string()));
        }
        if let Some(v) = angle {
            updates.push("angle = ?");
            param_values.push(Box::new(v.to_string()));
        }
        if let Some(v) = behavior {
            updates.push("behavior = ?");
            param_values.push(Box::new(v.to_string()));
        }
        if let Some(v) = mandates {
            updates.push("mandates = ?");
            param_values.push(Box::new(serde_json::to_string(v).db()?));
        }

        if updates.is_empty() {
            return Ok(Some(existing));
        }

        updates.push("updated_at = ?");
        param_values.push(Box::new(now));

        let sql = format!(
            "UPDATE role_library SET {} WHERE id = ?",
            updates.join(", ")
        );
        param_values.push(Box::new(existing.id.as_str().to_string()));

        let params_refs: Vec<&dyn rusqlite::types::ToSql> =
            param_values.iter().map(|b| b.as_ref()).collect();
        self.conn.execute(&sql, params_refs.as_slice()).db()?;

        self.get_role_template(existing.id.as_str())
    }

    pub fn delete_role_template(&self, slug_or_id: &str) -> Result<bool, DbError> {
        let existing = self.get_role_template(slug_or_id)?;
        match existing {
            Some(e) => {
                self.conn
                    .execute(
                        "DELETE FROM role_library WHERE id = ?1",
                        params![e.id.as_str()],
                    )
                    .db()?;
                Ok(true)
            }
            None => Ok(false),
        }
    }

    /// Apply a role template to a session. Copies role_text to agent_roles
    /// and bumps usage_count.
    pub fn apply_role_template(
        &self,
        session_id: &SessionId,
        agent_name: &str,
        slug_or_id: &str,
    ) -> Result<Option<(RoleTemplate, AgentRole)>, DbError> {
        let template = match self.get_role_template(slug_or_id)? {
            Some(t) => t,
            None => return Ok(None),
        };
        let now = now_rfc3339();
        // Bump usage
        self.conn.execute(
            "UPDATE role_library SET usage_count = usage_count + 1, last_used_at = ?1 WHERE id = ?2",
            params![now, template.id.as_str()],
        ).db()?;
        // Compose role text from all behavioral fields
        let mut role_text = template.role_text.clone();
        if let Some(ref a) = template.approach {
            role_text.push_str(&format!("\n\nApproach: {a}"));
        }
        if let Some(ref b) = template.behavior {
            role_text.push_str(&format!("\n\nBehavior: {b}"));
        }
        if let Some(ref v) = template.vision {
            role_text.push_str(&format!("\n\nVision: {v}"));
        }
        if let Some(ref ang) = template.angle {
            role_text.push_str(&format!("\n\nAngle: {ang}"));
        }
        if !template.mandates.is_empty() {
            role_text.push_str("\n\nMandates (non-negotiable):\n");
            for m in &template.mandates {
                role_text.push_str(&format!("- {m}\n"));
            }
        }
        let role = self.set_role(session_id, agent_name, &role_text, Some(&template.slug))?;
        Ok(Some((template, role)))
    }

    // -----------------------------------------------------------------------
    // Round Participants (sync barrier)
    // -----------------------------------------------------------------------

    pub fn register_participant(
        &self,
        round_id: &RoundId,
        agent_name: &str,
        phase: &str,
    ) -> Result<RoundParticipant, DbError> {
        let id = ParticipantId::new();
        let now = now_rfc3339();
        self.conn
            .execute(
                "INSERT INTO round_participants \
             (id, round_id, agent_name, phase, status, created_at) \
             VALUES (?1, ?2, ?3, ?4, 'pending', ?5) \
             ON CONFLICT(round_id, agent_name) DO UPDATE SET \
             phase=excluded.phase, status='pending', created_at=excluded.created_at",
                params![id.as_str(), round_id.as_str(), agent_name, phase, now],
            )
            .db()?;
        // Fetch actual record (may differ on upsert)
        let p = self
            .conn
            .query_row(
                "SELECT id, round_id, agent_name, phase, status, dispatched_at, responded_at, \
                 response_quality, error_detail, retry_count, max_retries, \
                 feedback_items_expected, feedback_items_completed, created_at \
                 FROM round_participants WHERE round_id = ?1 AND agent_name = ?2",
                params![round_id.as_str(), agent_name],
                |row| Ok(Self::row_to_participant(row)),
            )
            .db()?;
        Ok(p)
    }

    pub fn update_participant_status(
        &self,
        round_id: &RoundId,
        agent_name: &str,
        status: &ParticipantStatus,
        quality: Option<&ResponseQuality>,
        error: Option<&str>,
    ) -> Result<(), DbError> {
        let now = now_rfc3339();
        let status_str = status.to_string();
        let quality_str = quality.map(|q| q.to_string());
        let time_col = if *status == ParticipantStatus::Dispatched {
            "dispatched_at"
        } else {
            "responded_at"
        };
        let sql = format!(
            "UPDATE round_participants SET status=?1, response_quality=?2, \
             error_detail=?3, {time_col}=?4 WHERE round_id=?5 AND agent_name=?6"
        );
        self.conn
            .execute(
                &sql,
                params![
                    status_str,
                    quality_str,
                    error,
                    now,
                    round_id.as_str(),
                    agent_name
                ],
            )
            .db()?;
        Ok(())
    }

    pub fn get_participant(
        &self,
        round_id: &RoundId,
        agent_name: &str,
    ) -> Result<Option<RoundParticipant>, DbError> {
        use rusqlite::OptionalExtension;
        let result = self
            .conn
            .query_row(
                "SELECT id, round_id, agent_name, phase, status, dispatched_at, responded_at, \
                 response_quality, error_detail, retry_count, max_retries, \
                 feedback_items_expected, feedback_items_completed, created_at \
                 FROM round_participants WHERE round_id = ?1 AND agent_name = ?2",
                params![round_id.as_str(), agent_name],
                |row| Ok(Self::row_to_participant(row)),
            )
            .optional()
            .db()?;
        Ok(result)
    }

    pub fn increment_retry_count(
        &self,
        round_id: &RoundId,
        agent_name: &str,
    ) -> Result<(), DbError> {
        self.conn
            .execute(
                "UPDATE round_participants SET retry_count = retry_count + 1, \
                 status = 'pending', error_detail = NULL \
                 WHERE round_id = ?1 AND agent_name = ?2",
                params![round_id.as_str(), agent_name],
            )
            .db()?;
        Ok(())
    }

    pub fn get_round_participants(
        &self,
        round_id: &RoundId,
    ) -> Result<Vec<RoundParticipant>, DbError> {
        let mut stmt = self
            .conn
            .prepare(
                "SELECT id, round_id, agent_name, phase, status, dispatched_at, responded_at, \
             response_quality, error_detail, retry_count, max_retries, \
             feedback_items_expected, feedback_items_completed, created_at \
             FROM round_participants WHERE round_id = ?1 ORDER BY agent_name",
            )
            .db()?;
        let rows = stmt
            .query_map(params![round_id.as_str()], |row| {
                Ok(Self::row_to_participant(row))
            })
            .db()?;
        rows.collect::<Result<Vec<_>, _>>()
            .map_err(|e| DbError::Sqlite(e.to_string()))
    }

    fn row_to_participant(row: &rusqlite::Row<'_>) -> RoundParticipant {
        RoundParticipant {
            id: ParticipantId::from(row.get::<_, String>(0).unwrap_or_default()),
            round_id: RoundId::from(row.get::<_, String>(1).unwrap_or_default()),
            agent_name: row.get(2).unwrap_or_default(),
            phase: row.get(3).unwrap_or_default(),
            status: row
                .get::<_, String>(4)
                .unwrap_or_default()
                .parse()
                .unwrap_or(ParticipantStatus::Pending),
            dispatched_at: parse_dt_opt(row.get(5).unwrap_or_default()),
            responded_at: parse_dt_opt(row.get(6).unwrap_or_default()),
            response_quality: row
                .get::<_, Option<String>>(7)
                .unwrap_or_default()
                .and_then(|s| s.parse().ok()),
            error_detail: row.get(8).unwrap_or_default(),
            retry_count: row.get(9).unwrap_or(0),
            max_retries: row.get(10).unwrap_or(1),
            feedback_items_expected: row.get(11).unwrap_or(0),
            feedback_items_completed: row.get(12).unwrap_or(0),
            created_at: parse_dt(&row.get::<_, String>(13).unwrap_or_default()),
        }
    }

    // -----------------------------------------------------------------------
    // Session History
    // -----------------------------------------------------------------------

    pub fn get_session_history(
        &self,
        session_id: &SessionId,
    ) -> Result<serde_json::Value, DbError> {
        let session = match self.get_session(session_id)? {
            Some(s) => s,
            None => {
                return Ok(serde_json::json!({
                    "error": format!("Session {} not found", session_id)
                }));
            }
        };

        let rounds = self.list_rounds(session_id)?;
        let mut rounds_json = Vec::new();
        for rnd in &rounds {
            let responses = self.get_round_responses(&rnd.id)?;
            rounds_json.push(serde_json::json!({
                "id": rnd.id.as_str(),
                "session_id": rnd.session_id.as_str(),
                "round_number": rnd.round_number,
                "objective": rnd.objective,
                "question": rnd.question,
                "created_at": rnd.created_at.to_rfc3339(),
                "responses": responses.iter().map(|r| serde_json::json!({
                    "id": r.id.as_str(),
                    "round_id": r.round_id.as_str(),
                    "agent_name": r.agent_name,
                    "content": r.content,
                    "quality": r.quality.as_ref().map(|q| q.to_string()),
                    "source": r.source,
                    "created_at": r.created_at.to_rfc3339(),
                })).collect::<Vec<_>>(),
            }));
        }

        let feedback = self.list_feedback_items(session_id, None)?;
        let mut feedback_json = Vec::new();
        for item in &feedback {
            let responses = self.get_feedback_responses(&item.id)?;
            feedback_json.push(serde_json::json!({
                "id": item.id.as_str(),
                "session_id": item.session_id.as_str(),
                "source_round_id": item.source_round_id.as_str(),
                "source_agent": item.source_agent,
                "title": item.title,
                "content": item.content,
                "status": item.status.to_string(),
                "created_at": item.created_at.to_rfc3339(),
                "responses": responses.iter().map(|r| serde_json::json!({
                    "id": r.id.as_str(),
                    "item_id": r.item_id.as_str(),
                    "round_id": r.round_id.as_str(),
                    "agent_name": r.agent_name,
                    "verdict": r.verdict,
                    "reasoning": r.reasoning,
                    "created_at": r.created_at.to_rfc3339(),
                })).collect::<Vec<_>>(),
            }));
        }

        let roles = self.list_roles(session_id)?;
        let consensus = self.get_latest_consensus(session_id)?;

        Ok(serde_json::json!({
            "session": {
                "id": session.id.as_str(),
                "topic": session.topic,
                "project": session.project,
                "context": session.context,
                "status": session.status.to_string(),
                "created_at": session.created_at.to_rfc3339(),
            },
            "rounds": rounds_json,
            "feedback_items": feedback_json,
            "roles": roles.iter().map(|r| serde_json::json!({
                "id": r.id.as_str(),
                "session_id": r.session_id.as_str(),
                "agent_name": r.agent_name,
                "role": r.role,
                "source_slug": r.source_slug,
                "created_at": r.created_at.to_rfc3339(),
            })).collect::<Vec<serde_json::Value>>(),
            "consensus": consensus.map(|c| serde_json::json!({
                "id": c.id.as_str(),
                "session_id": c.session_id.as_str(),
                "round_id": c.round_id.as_ref().map(|r| r.as_str().to_string()),
                "version": c.version,
                "content": c.content,
                "created_at": c.created_at.to_rfc3339(),
            })),
        }))
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    fn db() -> BrainstormDb {
        BrainstormDb::new_in_memory().expect("in-memory DB should open")
    }

    #[test]
    fn create_session_and_retrieve() {
        let db = db();
        let session = db
            .create_session("Test topic", Some("test-project"))
            .unwrap();
        assert!(session.id.as_str().starts_with("bs_"));
        assert_eq!(session.topic, "Test topic");
        assert_eq!(session.project.as_deref(), Some("test-project"));
        assert_eq!(session.status, SessionStatus::Active);

        let fetched = db.get_session(&session.id).unwrap().unwrap();
        assert_eq!(fetched.id, session.id);
        assert_eq!(fetched.topic, "Test topic");

        // Context
        db.set_context(&session.id, "Some context").unwrap();
        let ctx = db.get_context(&session.id).unwrap();
        assert_eq!(ctx.as_deref(), Some("Some context"));

        // Complete
        db.complete_session(&session.id).unwrap();
        let completed = db.get_session(&session.id).unwrap().unwrap();
        assert_eq!(completed.status, SessionStatus::Completed);

        // List
        let active = db.list_sessions(Some(&SessionStatus::Active), 10).unwrap();
        assert!(active.is_empty());
        let all = db.list_sessions(None, 10).unwrap();
        assert_eq!(all.len(), 1);
    }

    #[test]
    fn round_number_auto_increments() {
        let db = db();
        let session = db.create_session("Rounds test", None).unwrap();

        let r1 = db.create_round(&session.id, Some("Obj 1"), None).unwrap();
        assert_eq!(r1.round_number, 1);

        let r2 = db
            .create_round(&session.id, Some("Obj 2"), Some("Question?"))
            .unwrap();
        assert_eq!(r2.round_number, 2);

        let r3 = db.create_round(&session.id, None, None).unwrap();
        assert_eq!(r3.round_number, 3);

        let rounds = db.list_rounds(&session.id).unwrap();
        assert_eq!(rounds.len(), 3);
        assert_eq!(rounds[0].round_number, 1);
        assert_eq!(rounds[2].round_number, 3);

        // get_round
        let fetched = db.get_round(&r2.id).unwrap().unwrap();
        assert_eq!(fetched.round_number, 2);
        assert_eq!(fetched.question.as_deref(), Some("Question?"));
    }

    #[test]
    fn save_response_upserts() {
        let db = db();
        let session = db.create_session("Upsert test", None).unwrap();
        let round = db.create_round(&session.id, None, None).unwrap();

        // First insert
        let r1 = db
            .save_response(&round.id, "agent-a", "First content")
            .unwrap();
        assert_eq!(r1.content, "First content");

        // Upsert (same agent, same round)
        let r2 = db
            .save_response(&round.id, "agent-a", "Updated content")
            .unwrap();
        // ID should be the same (upsert keeps original ID)
        assert_eq!(r2.id, r1.id);

        // Verify only one response exists
        let responses = db.get_round_responses(&round.id).unwrap();
        assert_eq!(responses.len(), 1);
        assert_eq!(responses[0].content, "Updated content");

        // Different agent is a separate row
        db.save_response(&round.id, "agent-b", "B content").unwrap();
        let responses = db.get_round_responses(&round.id).unwrap();
        assert_eq!(responses.len(), 2);

        // get_response
        let fetched = db.get_response(&round.id, "agent-a").unwrap().unwrap();
        assert_eq!(fetched.content, "Updated content");
        assert!(db.get_response(&round.id, "no-agent").unwrap().is_none());
    }

    #[test]
    fn feedback_flow() {
        let db = db();
        let session = db.create_session("Feedback test", None).unwrap();
        let round = db.create_round(&session.id, None, None).unwrap();
        let round2 = db.create_round(&session.id, None, None).unwrap();

        // Create feedback item
        let item = db
            .create_feedback_item(
                &session.id,
                &round.id,
                "agent-a",
                "Important finding",
                "Details here",
            )
            .unwrap();
        assert!(item.id.as_str().starts_with("fb_"));
        assert_eq!(item.status, FeedbackStatus::Pending);

        // List feedback
        let items = db.list_feedback_items(&session.id, None).unwrap();
        assert_eq!(items.len(), 1);

        let pending = db
            .list_feedback_items(&session.id, Some(&FeedbackStatus::Pending))
            .unwrap();
        assert_eq!(pending.len(), 1);

        // Save feedback response
        let fr = db
            .save_feedback_response(&item.id, &round2.id, "agent-b", "agree", "Looks good")
            .unwrap();
        assert!(fr.id.as_str().starts_with("fbr_"));
        assert_eq!(fr.verdict, "agree");

        // Get feedback item with responses
        let (fi, responses) = db.get_feedback_item(&item.id).unwrap().unwrap();
        assert_eq!(fi.title, "Important finding");
        assert_eq!(responses.len(), 1);
        assert_eq!(responses[0].agent_name, "agent-b");

        // Update status
        db.update_feedback_status(&item.id, &FeedbackStatus::Accepted)
            .unwrap();
        let (fi2, _) = db.get_feedback_item(&item.id).unwrap().unwrap();
        assert_eq!(fi2.status, FeedbackStatus::Accepted);

        // Feedback response list
        let frs = db.get_feedback_responses(&item.id).unwrap();
        assert_eq!(frs.len(), 1);
    }

    #[test]
    fn role_template_crud() {
        let db = db();

        // Create
        let tmpl = db
            .create_role_template(
                "test-role",
                "Test Role",
                "A testing role",
                "You are a tester.",
                None,
                Some("Be thorough"),
                Some(&["testing".to_string(), "qa".to_string()]),
                Some("For testing purposes"),
                None,
                Some("Quality angle"),
                None,
                Some(&["Must be thorough".to_string()]),
            )
            .unwrap();
        assert!(tmpl.id.as_str().starts_with("rl_"));
        assert_eq!(tmpl.slug, "test-role");
        assert_eq!(tmpl.tags, vec!["testing", "qa"]);
        assert_eq!(tmpl.mandates, vec!["Must be thorough"]);

        // Get by slug
        let fetched = db.get_role_template("test-role").unwrap().unwrap();
        assert_eq!(fetched.slug, "test-role");
        assert_eq!(fetched.description, "A testing role");

        // Get by ID
        let fetched2 = db.get_role_template(tmpl.id.as_str()).unwrap().unwrap();
        assert_eq!(fetched2.slug, "test-role");

        // List
        let list = db.list_role_templates(None, None).unwrap();
        assert_eq!(list.len(), 1);

        // List with tag filter
        let tagged = db.list_role_templates(None, Some("testing")).unwrap();
        assert_eq!(tagged.len(), 1);
        let no_match = db.list_role_templates(None, Some("nonexistent")).unwrap();
        assert!(no_match.is_empty());

        // Update
        let updated = db
            .update_role_template(
                "test-role",
                Some("Updated Name"),
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
            )
            .unwrap()
            .unwrap();
        assert_eq!(updated.display_name, "Updated Name");
        assert_eq!(updated.description, "A testing role"); // unchanged

        // Apply to session
        let session = db.create_session("Apply test", None).unwrap();
        let result = db
            .apply_role_template(&session.id, "agent-x", "test-role")
            .unwrap();
        assert!(result.is_some());
        let (tmpl_applied, role) = result.unwrap();
        assert_eq!(tmpl_applied.slug, "test-role");
        assert!(role.role.contains("You are a tester."));
        assert!(role.role.contains("Approach: Be thorough"));
        assert_eq!(role.source_slug.as_deref(), Some("test-role"));

        // Usage count bumped
        let after = db.get_role_template("test-role").unwrap().unwrap();
        assert_eq!(after.usage_count, 1);
        assert!(after.last_used_at.is_some());

        // Delete
        assert!(db.delete_role_template("test-role").unwrap());
        assert!(db.get_role_template("test-role").unwrap().is_none());
        assert!(!db.delete_role_template("test-role").unwrap());
    }

    #[test]
    fn session_history_complete() {
        let db = db();
        let session = db.create_session("History test", Some("proj")).unwrap();

        let round = db
            .create_round(&session.id, Some("Objective"), Some("Question?"))
            .unwrap();
        db.save_response(&round.id, "agent-a", "A's answer")
            .unwrap();
        db.save_response(&round.id, "agent-b", "B's answer")
            .unwrap();

        let fb = db
            .create_feedback_item(&session.id, &round.id, "agent-a", "Title", "Content")
            .unwrap();
        let round2 = db.create_round(&session.id, None, None).unwrap();
        db.save_feedback_response(&fb.id, &round2.id, "agent-b", "agree", "Reason")
            .unwrap();

        db.set_role(&session.id, "agent-a", "Architect", None)
            .unwrap();
        db.save_consensus(&session.id, "Final consensus", Some(&round.id))
            .unwrap();

        let history = db.get_session_history(&session.id).unwrap();

        // Verify structure
        assert_eq!(history["session"]["topic"], "History test");
        assert_eq!(history["session"]["project"], "proj");
        assert_eq!(history["rounds"].as_array().unwrap().len(), 2);
        assert_eq!(
            history["rounds"][0]["responses"].as_array().unwrap().len(),
            2
        );
        assert_eq!(history["feedback_items"].as_array().unwrap().len(), 1);
        assert_eq!(
            history["feedback_items"][0]["responses"]
                .as_array()
                .unwrap()
                .len(),
            1
        );
        assert_eq!(history["roles"].as_array().unwrap().len(), 1);
        assert!(history["consensus"].is_object());
        assert_eq!(history["consensus"]["version"], 1);
    }

    #[test]
    fn consensus_version_auto_increments() {
        let db = db();
        let session = db.create_session("Consensus test", None).unwrap();

        let c1 = db
            .save_consensus(&session.id, "First consensus", None)
            .unwrap();
        assert_eq!(c1.version, 1);

        let c2 = db
            .save_consensus(&session.id, "Second consensus", None)
            .unwrap();
        assert_eq!(c2.version, 2);

        let latest = db.get_latest_consensus(&session.id).unwrap().unwrap();
        assert_eq!(latest.version, 2);
        assert_eq!(latest.content, "Second consensus");
    }

    #[test]
    fn agent_definition_upsert() {
        let db = db();
        db.upsert_agent_definition(
            "copilot",
            "GitHub Copilot",
            "Code generation",
            "developer",
            "Practical",
            None,
            None,
            None,
            Some(&["coding".to_string()]),
            Some("gpt-4"),
        )
        .unwrap();

        let def = db.get_agent_definition("copilot").unwrap().unwrap();
        assert_eq!(def.display_name, "GitHub Copilot");
        assert_eq!(def.tags, vec!["coding"]);
        assert_eq!(def.backend_hint.as_deref(), Some("gpt-4"));

        // Upsert updates
        db.upsert_agent_definition(
            "copilot",
            "Copilot v2",
            "Better code generation",
            "developer",
            "Practical v2",
            None,
            None,
            None,
            None,
            None,
        )
        .unwrap();

        let def2 = db.get_agent_definition("copilot").unwrap().unwrap();
        assert_eq!(def2.display_name, "Copilot v2");
        // Same ID
        assert_eq!(def2.id, def.id);

        let all = db.list_agent_definitions().unwrap();
        assert_eq!(all.len(), 1);
    }

    #[test]
    fn workflow_template_upsert() {
        let db = db();
        db.upsert_workflow_template("brainstorm_3phase", "Overview", "{}", "Rules", "Format")
            .unwrap();

        let wf = db
            .get_workflow_template("brainstorm_3phase")
            .unwrap()
            .unwrap();
        assert_eq!(wf.version, 1);

        db.upsert_workflow_template(
            "brainstorm_3phase",
            "Updated overview",
            "{}",
            "Rules v2",
            "Format v2",
        )
        .unwrap();

        let wf2 = db
            .get_workflow_template("brainstorm_3phase")
            .unwrap()
            .unwrap();
        assert_eq!(wf2.version, 2);
        assert_eq!(wf2.overview, "Updated overview");
    }

    #[test]
    fn tool_guide_crud() {
        let db = db();
        db.upsert_tool_guide(
            "bs_new_session",
            "setup",
            "Start session",
            "Call with topic",
        )
        .unwrap();
        db.upsert_tool_guide(
            "bs_save_response",
            "analysis",
            "Save response",
            "Call with content",
        )
        .unwrap();

        let guide = db.get_tool_guide("bs_new_session").unwrap().unwrap();
        assert_eq!(guide.phase, "setup");

        let all = db.list_tool_guides(None).unwrap();
        assert_eq!(all.len(), 2);

        let analysis = db.list_tool_guides(Some("analysis")).unwrap();
        assert_eq!(analysis.len(), 1);
        assert_eq!(analysis[0].tool_name, "bs_save_response");
    }

    #[test]
    fn round_participants_sync_barrier() {
        let db = db();
        let session = db.create_session("Participants test", None).unwrap();
        let round = db.create_round(&session.id, None, None).unwrap();

        // Register
        let p = db
            .register_participant(&round.id, "agent-a", "analysis")
            .unwrap();
        assert_eq!(p.status, ParticipantStatus::Pending);
        assert_eq!(p.phase, "analysis");

        // Update status
        db.update_participant_status(
            &round.id,
            "agent-a",
            &ParticipantStatus::Dispatched,
            None,
            None,
        )
        .unwrap();

        db.update_participant_status(
            &round.id,
            "agent-a",
            &ParticipantStatus::Responded,
            Some(&ResponseQuality::Valid),
            None,
        )
        .unwrap();

        let participants = db.get_round_participants(&round.id).unwrap();
        assert_eq!(participants.len(), 1);
        assert_eq!(participants[0].status, ParticipantStatus::Responded);
        assert_eq!(
            participants[0].response_quality,
            Some(ResponseQuality::Valid)
        );
    }

    #[test]
    fn guidelines_crud() {
        let db = db();
        let session = db.create_session("Guidelines test", None).unwrap();

        db.add_guideline(&session.id, "Be concise").unwrap();
        db.add_guideline(&session.id, "Use examples").unwrap();

        let guides = db.list_guidelines(&session.id).unwrap();
        assert_eq!(guides.len(), 2);
        assert_eq!(guides[0].content, "Be concise");
        assert_eq!(guides[1].content, "Use examples");
    }

    #[test]
    fn roles_crud() {
        let db = db();
        let session = db.create_session("Roles test", None).unwrap();

        let role = db
            .set_role(&session.id, "agent-a", "Architect", None)
            .unwrap();
        assert_eq!(role.role, "Architect");

        // Upsert same agent
        let role2 = db
            .set_role(&session.id, "agent-a", "Critic", Some("critic-slug"))
            .unwrap();
        assert_eq!(role2.role, "Critic");

        let fetched = db.get_role(&session.id, "agent-a").unwrap().unwrap();
        assert_eq!(fetched.role, "Critic");
        assert_eq!(fetched.source_slug.as_deref(), Some("critic-slug"));

        // Only one role for this agent
        let roles = db.list_roles(&session.id).unwrap();
        assert_eq!(roles.len(), 1);
    }

    #[test]
    fn update_response_quality_sets_field() {
        let db = db();
        let session = db.create_session("Test", None).unwrap();
        let round = db.create_round(&session.id, Some("obj"), Some("q")).unwrap();
        let response = db.save_response(&round.id, "test-agent", "some content").unwrap();

        // Initially None
        assert!(response.quality.is_none());

        // Update to Valid
        db.update_response_quality(&response.id, &ResponseQuality::Valid).unwrap();
        let fetched = db.get_response(&round.id, "test-agent").unwrap().unwrap();
        assert_eq!(fetched.quality, Some(ResponseQuality::Valid));

        // Update to Suspect
        db.update_response_quality(&response.id, &ResponseQuality::Suspect).unwrap();
        let fetched = db.get_response(&round.id, "test-agent").unwrap().unwrap();
        assert_eq!(fetched.quality, Some(ResponseQuality::Suspect));
    }

    #[test]
    fn get_participant_returns_none_for_missing() {
        let db = db();
        let session = db.create_session("Test", None).unwrap();
        let round = db.create_round(&session.id, None, None).unwrap();
        assert!(db.get_participant(&round.id, "nonexistent").unwrap().is_none());
    }

    #[test]
    fn get_participant_returns_registered() {
        let db = db();
        let session = db.create_session("Test", None).unwrap();
        let round = db.create_round(&session.id, None, None).unwrap();
        db.register_participant(&round.id, "agent-a", "analysis").unwrap();
        let p = db.get_participant(&round.id, "agent-a").unwrap().unwrap();
        assert_eq!(p.agent_name, "agent-a");
        assert_eq!(p.status, ParticipantStatus::Pending);
    }

    #[test]
    fn increment_retry_count_resets_status() {
        let db = db();
        let session = db.create_session("Retry test", None).unwrap();
        let round = db.create_round(&session.id, None, None).unwrap();

        let p = db.register_participant(&round.id, "agent-a", "analysis").unwrap();
        assert_eq!(p.retry_count, 0);

        // Mark as failed
        db.update_participant_status(
            &round.id, "agent-a", &ParticipantStatus::Failed, None, Some("timeout"),
        ).unwrap();

        // Increment retry
        db.increment_retry_count(&round.id, "agent-a").unwrap();

        let p = db.get_participant(&round.id, "agent-a").unwrap().unwrap();
        assert_eq!(p.retry_count, 1);
        assert_eq!(p.status, ParticipantStatus::Pending);
        assert!(p.error_detail.is_none());
    }
}
