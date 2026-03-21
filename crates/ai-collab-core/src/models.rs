//! Domain models for all brainstorm entities.
//! These structs represent rows from the SQLite database.

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

use crate::enums::{FeedbackStatus, ParticipantStatus, ResponseQuality, SessionStatus};
use crate::ids::*;

/// A brainstorming session.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Session {
    pub id: SessionId,
    pub topic: String,
    pub project: Option<String>,
    pub context: Option<String>,
    pub status: SessionStatus,
    pub created_at: DateTime<Utc>,
}

/// A round within a session.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Round {
    pub id: RoundId,
    pub session_id: SessionId,
    pub round_number: i32,
    pub objective: Option<String>,
    pub question: Option<String>,
    pub created_at: DateTime<Utc>,
}

/// An agent's response to a round.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Response {
    pub id: ResponseId,
    pub round_id: RoundId,
    pub agent_name: String,
    pub content: String,
    pub quality: Option<ResponseQuality>,
    pub source: Option<String>,
    pub created_at: DateTime<Utc>,
}

/// A consensus document for a session.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Consensus {
    pub id: ConsensusId,
    pub session_id: SessionId,
    pub round_id: Option<RoundId>,
    pub version: i32,
    pub content: String,
    pub created_at: DateTime<Utc>,
}

/// A feedback item extracted from Phase 1 responses.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FeedbackItem {
    pub id: FeedbackId,
    pub session_id: SessionId,
    pub source_round_id: RoundId,
    pub source_agent: String,
    pub title: String,
    pub content: String,
    pub status: FeedbackStatus,
    pub created_at: DateTime<Utc>,
}

/// An agent's verdict on a feedback item.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FeedbackResponse {
    pub id: FeedbackResponseId,
    pub item_id: FeedbackId,
    pub round_id: RoundId,
    pub agent_name: String,
    pub verdict: String,
    pub reasoning: String,
    pub created_at: DateTime<Utc>,
}

/// A session-scoped role assignment for an agent.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentRole {
    pub id: RoleId,
    pub session_id: SessionId,
    pub agent_name: String,
    pub role: String,
    pub source_slug: Option<String>,
    pub created_at: DateTime<Utc>,
}

/// A session guideline.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Guideline {
    pub id: GuidelineId,
    pub session_id: SessionId,
    pub content: String,
    pub created_at: DateTime<Utc>,
}

// --- Global tables (not session-scoped) ---

/// An agent definition stored in the database.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentDefinition {
    pub id: AgentDefinitionId,
    pub agent_name: String,
    pub display_name: String,
    pub capabilities: String,
    pub default_role: String,
    pub approach: String,
    pub vision: Option<String>,
    pub angle: Option<String>,
    pub behavior: Option<String>,
    pub tags: Vec<String>,
    pub backend_hint: Option<String>,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}

/// A workflow template defining the 3-phase process.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WorkflowTemplate {
    pub id: WorkflowId,
    pub name: String,
    pub version: i32,
    pub overview: String,
    pub phases: String,
    pub convergence_rules: String,
    pub response_format: String,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}

/// A tool usage guide stored in the database.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolGuide {
    pub id: ToolGuideId,
    pub tool_name: String,
    pub phase: String,
    pub purpose: String,
    pub usage: String,
    pub created_at: DateTime<Utc>,
}

/// A reusable role template from the library.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RoleTemplate {
    pub id: RoleTemplateId,
    pub slug: String,
    pub display_name: String,
    pub agent_name: Option<String>,
    pub description: String,
    pub role_text: String,
    pub approach: Option<String>,
    pub vision: Option<String>,
    pub angle: Option<String>,
    pub behavior: Option<String>,
    pub mandates: Vec<String>,
    pub tags: Vec<String>,
    pub usage_count: i32,
    pub last_used_at: Option<DateTime<Utc>>,
    pub notes: Option<String>,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}

/// A round participant (sync barrier tracking).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RoundParticipant {
    pub id: ParticipantId,
    pub round_id: RoundId,
    pub agent_name: String,
    pub phase: String,
    pub status: ParticipantStatus,
    pub dispatched_at: Option<DateTime<Utc>>,
    pub responded_at: Option<DateTime<Utc>>,
    pub response_quality: Option<ResponseQuality>,
    pub error_detail: Option<String>,
    pub retry_count: i32,
    pub max_retries: i32,
    pub feedback_items_expected: i32,
    pub feedback_items_completed: i32,
    pub created_at: DateTime<Utc>,
}
