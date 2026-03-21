//! Shared error types for the ai-collab workspace.

use thiserror::Error;

/// Errors from the database layer.
#[derive(Debug, Error)]
pub enum DbError {
    #[error("session not found: {id}")]
    SessionNotFound { id: String },

    #[error("round not found: {id}")]
    RoundNotFound { id: String },

    #[error("feedback item not found: {id}")]
    FeedbackNotFound { id: String },

    #[error("response not found: round={round_id}, agent={agent}")]
    ResponseNotFound { round_id: String, agent: String },

    #[error("agent '{agent}' not found in definitions")]
    AgentNotFound { agent: String },

    #[error("role template not found: {slug}")]
    RoleNotFound { slug: String },

    #[error("duplicate entry: {entity} with key {key}")]
    Duplicate { entity: &'static str, key: String },

    #[error("constraint violation: {0}")]
    Constraint(String),

    #[error("migration failed: {0}")]
    Migration(String),

    #[error("sqlite error: {0}")]
    Sqlite(String),

    #[error("json error: {0}")]
    Json(String),
}

/// Errors from the provider (subprocess) layer.
#[derive(Debug, Error)]
pub enum ProviderError {
    #[error("agent '{agent}' not available: {reason}")]
    Unavailable { agent: String, reason: String },

    #[error("agent '{agent}' timed out after {seconds}s")]
    Timeout { agent: String, seconds: f64 },

    #[error("agent '{agent}' exited with code {code}: {stderr}")]
    Execution {
        agent: String,
        code: i32,
        stderr: String,
    },

    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),

    #[error("internal error: {0}")]
    Internal(String),
}

/// Errors from the config layer.
#[derive(Debug, Error)]
pub enum ConfigError {
    #[error("config file not found")]
    NotFound,

    #[error("invalid TOML: {0}")]
    Parse(String),

    #[error("invalid config value: {field}: {reason}")]
    Validation { field: String, reason: String },

    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),
}

/// Unified error type for MCP tool handlers.
#[derive(Debug, Error)]
pub enum ToolError {
    #[error(transparent)]
    Db(#[from] DbError),

    #[error(transparent)]
    Provider(#[from] ProviderError),

    #[error(transparent)]
    Config(#[from] ConfigError),

    #[error("invalid parameter: {0}")]
    BadParam(String),
}
