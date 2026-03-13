use chrono::{DateTime, Duration, Utc};
use rusqlite::{Connection, OpenFlags};
use std::collections::HashSet;
use std::path::Path;

// ── Data types ───────────────────────────────────────────────────────────

#[derive(Debug, Clone)]
pub struct AgentResponse {
    pub agent_name: String,
    pub content: Option<String>,
    pub created_at: Option<String>,
}

#[derive(Debug, Clone)]
pub struct RoundInfo {
    pub round_id: String,
    pub round_number: i64,
    pub total_rounds: i64,
    pub objective: Option<String>,
    pub question: Option<String>,
    pub created_at: String,
    pub responses: Vec<AgentResponse>,
}

#[derive(Debug, Clone)]
pub struct SessionData {
    pub session_id: String,
    pub topic: String,
    pub project: Option<String>,
    pub status: String,
    pub created_at: String,
    pub rounds: Vec<RoundInfo>,
    pub consensus_content: Option<String>,
    pub is_running: bool,
}

// ── Time helpers ─────────────────────────────────────────────────────────

pub fn parse_dt(s: &str) -> Option<DateTime<Utc>> {
    if s.is_empty() {
        return None;
    }
    DateTime::parse_from_rfc3339(s)
        .ok()
        .map(|dt| dt.with_timezone(&Utc))
}

pub fn time_ago(s: &str) -> String {
    match parse_dt(s) {
        None => String::new(),
        Some(dt) => {
            let secs = (Utc::now() - dt).num_seconds().max(0);
            if secs < 60 {
                format!("{}s ago", secs)
            } else if secs < 3600 {
                format!("{}m ago", secs / 60)
            } else {
                format!("{}h {}m ago", secs / 3600, (secs % 3600) / 60)
            }
        }
    }
}

// ── DB polling ───────────────────────────────────────────────────────────

fn open_db(db_path: &Path) -> Option<Connection> {
    if !db_path.exists() {
        return None;
    }
    Connection::open_with_flags(
        db_path,
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )
    .ok()
}

pub fn poll_sessions(
    db_path: &Path,
    hours: u32,
    limit: usize,
    known_agents: &HashSet<String>,
) -> Vec<SessionData> {
    let conn = match open_db(db_path) {
        Some(c) => c,
        None => return vec![],
    };

    let cutoff = (Utc::now() - Duration::hours(hours as i64)).to_rfc3339();
    let mut stmt = match conn.prepare(
        "SELECT id, topic, project, status, created_at \
         FROM sessions WHERE created_at >= ? ORDER BY created_at DESC LIMIT ?",
    ) {
        Ok(s) => s,
        Err(_) => return vec![],
    };

    let session_rows: Vec<(String, String, Option<String>, String, String)> = match stmt
        .query_map(rusqlite::params![cutoff, limit as i64], |row| {
            Ok((
                row.get(0)?,
                row.get(1)?,
                row.get(2)?,
                row.get(3)?,
                row.get(4)?,
            ))
        }) {
        Ok(rows) => rows.filter_map(|r| r.ok()).collect(),
        Err(_) => vec![],
    };

    let mut results = vec![];

    for (sid, topic, project, status, created_at) in session_rows {
        let rounds = load_rounds(&conn, &sid);
        let is_running = detect_running(&rounds, &status, known_agents);

        let consensus_content: Option<String> = conn
            .query_row(
                "SELECT content FROM consensus WHERE session_id = ? LIMIT 1",
                rusqlite::params![sid],
                |row| row.get(0),
            )
            .ok();

        results.push(SessionData {
            session_id: sid,
            topic,
            project,
            status,
            created_at,
            rounds,
            consensus_content,
            is_running,
        });
    }

    results
}

fn load_rounds(conn: &Connection, session_id: &str) -> Vec<RoundInfo> {
    let mut stmt = match conn.prepare(
        "SELECT id, round_number, objective, question, created_at \
         FROM rounds WHERE session_id = ? ORDER BY round_number",
    ) {
        Ok(s) => s,
        Err(_) => return vec![],
    };

    let round_rows: Vec<(String, i64, Option<String>, Option<String>, String)> = match stmt
        .query_map(rusqlite::params![session_id], |row| {
            Ok((
                row.get(0)?,
                row.get(1)?,
                row.get(2)?,
                row.get(3)?,
                row.get(4)?,
            ))
        }) {
        Ok(rows) => rows.filter_map(|r| r.ok()).collect(),
        Err(_) => vec![],
    };

    let total_rounds = round_rows.len() as i64;
    let mut rounds = vec![];

    for (rid, round_number, objective, question, created_at) in round_rows {
        let responses = load_responses(conn, &rid);
        rounds.push(RoundInfo {
            round_id: rid,
            round_number,
            total_rounds,
            objective,
            question,
            created_at,
            responses,
        });
    }

    rounds
}

fn load_responses(conn: &Connection, round_id: &str) -> Vec<AgentResponse> {
    let mut stmt = match conn.prepare(
        "SELECT agent_name, content, created_at \
         FROM responses WHERE round_id = ? ORDER BY created_at",
    ) {
        Ok(s) => s,
        Err(_) => return vec![],
    };

    let result = match stmt.query_map(rusqlite::params![round_id], |row| {
        Ok(AgentResponse {
            agent_name: row.get(0)?,
            content: row.get(1)?,
            created_at: row.get(2)?,
        })
    }) {
        Ok(rows) => rows.filter_map(|r| r.ok()).collect(),
        Err(_) => vec![],
    };
    result
}

fn detect_running(
    rounds: &[RoundInfo],
    status: &str,
    known_agents: &HashSet<String>,
) -> bool {
    if status != "active" || rounds.is_empty() {
        return false;
    }
    let latest = rounds.last().unwrap();
    let round_dt = match parse_dt(&latest.created_at) {
        Some(dt) => dt,
        None => return false,
    };
    let age = (Utc::now() - round_dt).num_seconds();
    if age >= 1800 {
        return false;
    }
    let responded: HashSet<&str> = latest
        .responses
        .iter()
        .filter(|r| r.content.is_some())
        .map(|r| r.agent_name.as_str())
        .collect();

    if !known_agents.is_empty() {
        return !known_agents.iter().all(|a| responded.contains(a.as_str()));
    }

    // Infer from all rounds
    let all_session_agents: HashSet<&str> = rounds
        .iter()
        .flat_map(|r| r.responses.iter())
        .filter(|r| r.content.is_some())
        .map(|r| r.agent_name.as_str())
        .collect();

    all_session_agents.iter().any(|a| !responded.contains(a))
}

pub fn complete_session(db_path: &Path, session_id: &str) {
    if let Ok(conn) = Connection::open(db_path) {
        let _ = conn.execute(
            "UPDATE sessions SET status = 'completed' WHERE id = ?",
            rusqlite::params![session_id],
        );
    }
}
