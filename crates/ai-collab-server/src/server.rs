//! Orchestrator MCP server — the main server Claude Code connects to.
//!
//! Exposes all brainstorm tools + delegation tools over MCP stdio.

use std::collections::BTreeMap;
use std::sync::{Arc, Mutex};

use rmcp::handler::server::router::tool::ToolRouter;
use rmcp::handler::server::wrapper::Parameters;
use rmcp::model::*;
use rmcp::{ErrorData as McpError, ServerHandler, tool, tool_handler, tool_router};
use schemars::JsonSchema;
use serde::Deserialize;

use ai_collab_config::AgentConfig;
use ai_collab_core::*;
use ai_collab_db::BrainstormDb;
use ai_collab_provider::{AgentRunConfig, get_provider};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn json_result<T: serde::Serialize>(val: &T) -> String {
    serde_json::to_string_pretty(val).unwrap_or_else(|e| format!("{{\"error\": \"{e}\"}}"))
}

// ---------------------------------------------------------------------------
// Parameter structs
// ---------------------------------------------------------------------------

/// Empty params for tools that take no arguments.
/// Using `()` produces `"type": "null"` which violates the MCP spec
/// (inputSchema.type must be "object").
/// Custom JsonSchema impl ensures `"properties": {}` is always present
/// (OpenAI/Copilot rejects schemas without it).
#[derive(Debug, Deserialize)]
pub struct EmptyParams {}

impl schemars::JsonSchema for EmptyParams {
    fn schema_name() -> std::borrow::Cow<'static, str> {
        "EmptyParams".into()
    }
    fn schema_id() -> std::borrow::Cow<'static, str> {
        concat!(module_path!(), "::EmptyParams").into()
    }
    fn json_schema(_gen: &mut schemars::SchemaGenerator) -> schemars::Schema {
        schemars::json_schema!({
            "type": "object",
            "properties": {},
            "title": "EmptyParams",
            "description": "Empty params for tools that take no arguments."
        })
    }
}

// -- Delegation --

#[derive(Debug, Deserialize, JsonSchema)]
pub struct AskAgentParams {
    #[schemars(description = "Name of the agent to ask")]
    pub agent_name: String,
    #[schemars(description = "Question or prompt to send")]
    pub question: String,
    #[schemars(description = "Working directory for the agent subprocess")]
    #[serde(default)]
    pub cwd: Option<String>,
}

// -- Session --

#[derive(Debug, Deserialize, JsonSchema)]
pub struct NewSessionParams {
    #[schemars(description = "Topic for the brainstorm session")]
    pub topic: String,
    #[schemars(description = "Project name (optional)")]
    #[serde(default)]
    pub project: Option<String>,
    #[schemars(
        description = "Session complexity mode: 'quick' (1 round, no deliberation), 'standard' (2 phase, default), 'deep' (3 phase with extended deliberation)"
    )]
    #[serde(default = "default_session_mode")]
    pub mode: String,
}

fn default_session_mode() -> String {
    "standard".to_string()
}

#[derive(Debug, Deserialize, JsonSchema)]
pub struct ListSessionsParams {
    #[schemars(description = "Filter by status: active or completed")]
    #[serde(default)]
    pub status: Option<String>,
    #[schemars(description = "Maximum number of sessions to return")]
    #[serde(default = "default_limit")]
    pub limit: i64,
}

fn default_limit() -> i64 {
    20
}

#[derive(Debug, Deserialize, JsonSchema)]
pub struct SetContextParams {
    #[schemars(description = "Session ID")]
    pub session_id: String,
    #[schemars(description = "Context text to attach to the session")]
    pub context: String,
}

#[derive(Debug, Deserialize, JsonSchema)]
pub struct SessionIdParams {
    #[schemars(description = "Session ID")]
    pub session_id: String,
}

// -- Rounds --

#[derive(Debug, Deserialize, JsonSchema)]
pub struct NewRoundParams {
    #[schemars(description = "Session ID")]
    pub session_id: String,
    #[schemars(description = "Objective for this round")]
    #[serde(default)]
    pub objective: Option<String>,
    #[schemars(description = "Question for agents to answer")]
    #[serde(default)]
    pub question: Option<String>,
}

#[derive(Debug, Deserialize, JsonSchema)]
pub struct RoundIdParams {
    #[schemars(description = "Round ID")]
    pub round_id: String,
}

#[derive(Debug, Deserialize, JsonSchema)]
pub struct RunRoundParams {
    #[schemars(description = "Session ID")]
    pub session_id: String,
    #[schemars(description = "Objective for this round")]
    pub objective: String,
    #[schemars(description = "Question for agents to answer")]
    pub question: String,
    #[schemars(description = "Working directory for agent subprocesses (optional)")]
    #[serde(default)]
    pub cwd: Option<String>,
    #[schemars(
        description = "Comma-separated agent names to dispatch (optional, defaults to all enabled non-validator agents)"
    )]
    #[serde(default)]
    pub agents: Option<String>,
    #[schemars(
        description = "Gate mode: 'strict' (all must succeed, default), 'quorum' (majority must succeed), 'best_effort' (any success = proceed)"
    )]
    #[serde(default = "default_gate_mode")]
    pub gate_mode: String,
}

fn default_gate_mode() -> String {
    "strict".to_string()
}

#[derive(Debug, Deserialize, JsonSchema)]
pub struct RetryAgentParams {
    #[schemars(description = "Round ID")]
    pub round_id: String,
    #[schemars(description = "Agent name to retry")]
    pub agent_name: String,
    #[schemars(description = "Working directory for the agent subprocess (optional)")]
    #[serde(default)]
    pub cwd: Option<String>,
}

// -- Responses --

#[derive(Debug, Deserialize, JsonSchema)]
pub struct SaveResponseParams {
    #[schemars(description = "Round ID")]
    pub round_id: String,
    #[schemars(description = "Agent name")]
    pub agent_name: String,
    #[schemars(description = "Response content")]
    pub content: String,
}

#[derive(Debug, Deserialize, JsonSchema)]
pub struct GetResponseParams {
    #[schemars(description = "Round ID")]
    pub round_id: String,
    #[schemars(description = "Agent name")]
    pub agent_name: String,
}

// -- Feedback --

#[derive(Debug, Deserialize, JsonSchema)]
pub struct CreateFeedbackParams {
    #[schemars(description = "Session ID")]
    pub session_id: String,
    #[schemars(description = "Source round ID where feedback originated")]
    pub source_round_id: String,
    #[schemars(description = "Agent that provided the source content")]
    pub source_agent: String,
    #[schemars(description = "Feedback item title")]
    pub title: String,
    #[schemars(description = "Feedback content")]
    pub content: String,
}

#[derive(Debug, Deserialize, JsonSchema)]
pub struct ListFeedbackParams {
    #[schemars(description = "Session ID")]
    pub session_id: String,
    #[schemars(
        description = "Filter by status: pending, accepted, rejected, modified, consolidated"
    )]
    #[serde(default)]
    pub status: Option<String>,
}

#[derive(Debug, Deserialize, JsonSchema)]
pub struct FeedbackIdParams {
    #[schemars(description = "Feedback item ID")]
    pub item_id: String,
}

#[derive(Debug, Deserialize, JsonSchema)]
pub struct UpdateFeedbackStatusParams {
    #[schemars(description = "Feedback item ID")]
    pub item_id: String,
    #[schemars(description = "New status: pending, accepted, rejected, modified, consolidated")]
    pub status: String,
}

#[derive(Debug, Deserialize, JsonSchema)]
pub struct RespondToFeedbackParams {
    #[schemars(description = "Feedback item ID")]
    pub item_id: String,
    #[schemars(description = "Round ID for this feedback phase")]
    pub round_id: String,
    #[schemars(description = "Agent name providing the verdict")]
    pub agent_name: String,
    #[schemars(description = "Verdict: accept, reject, modify, abstain")]
    pub verdict: String,
    #[schemars(description = "Reasoning behind the verdict")]
    pub reasoning: String,
}

#[derive(Debug, Deserialize, JsonSchema)]
pub struct AutoResolveParams {
    #[schemars(description = "Session ID")]
    pub session_id: String,
    #[schemars(description = "Round ID (to determine which agents voted)")]
    pub round_id: String,
}

#[derive(Debug, Deserialize, JsonSchema)]
pub struct CheckFeedbackStatusParams {
    #[schemars(description = "Round ID")]
    pub round_id: String,
    #[schemars(description = "Session ID")]
    pub session_id: String,
}

// -- Roles --

#[derive(Debug, Deserialize, JsonSchema)]
pub struct SetRoleParams {
    #[schemars(description = "Session ID")]
    pub session_id: String,
    #[schemars(description = "Agent name")]
    pub agent_name: String,
    #[schemars(description = "Role text to assign")]
    pub role: String,
}

#[derive(Debug, Deserialize, JsonSchema)]
pub struct GetRoleParams {
    #[schemars(description = "Session ID")]
    pub session_id: String,
    #[schemars(description = "Agent name")]
    pub agent_name: String,
}

#[derive(Debug, Deserialize, JsonSchema)]
pub struct CreateRoleTemplateParams {
    #[schemars(description = "Unique slug identifier")]
    pub slug: String,
    #[schemars(description = "Display name")]
    pub display_name: String,
    #[schemars(description = "Description of the role")]
    pub description: String,
    #[schemars(description = "Full role text")]
    pub role_text: String,
    #[schemars(description = "Agent name (optional, for agent-specific roles)")]
    #[serde(default)]
    pub agent_name: Option<String>,
    #[schemars(description = "Approach description")]
    #[serde(default)]
    pub approach: Option<String>,
    #[schemars(description = "Tags for categorization")]
    #[serde(default)]
    pub tags: Option<Vec<String>>,
    #[schemars(description = "Additional notes")]
    #[serde(default)]
    pub notes: Option<String>,
    #[schemars(description = "Vision description")]
    #[serde(default)]
    pub vision: Option<String>,
    #[schemars(description = "Angle description")]
    #[serde(default)]
    pub angle: Option<String>,
    #[schemars(description = "Behavior description")]
    #[serde(default)]
    pub behavior: Option<String>,
    #[schemars(description = "Non-negotiable mandates")]
    #[serde(default)]
    pub mandates: Option<Vec<String>>,
}

#[derive(Debug, Deserialize, JsonSchema)]
pub struct ListRoleTemplatesParams {
    #[schemars(description = "Filter by agent name")]
    #[serde(default)]
    pub agent_name: Option<String>,
    #[schemars(description = "Filter by tag")]
    #[serde(default)]
    pub tag: Option<String>,
}

#[derive(Debug, Deserialize, JsonSchema)]
pub struct RoleTemplateSlugParams {
    #[schemars(description = "Role template slug or ID")]
    pub slug: String,
}

#[derive(Debug, Deserialize, JsonSchema)]
pub struct ApplyRoleParams {
    #[schemars(description = "Session ID")]
    pub session_id: String,
    #[schemars(description = "Agent name")]
    pub agent_name: String,
    #[schemars(description = "Role template slug")]
    pub slug: String,
}

#[derive(Debug, Deserialize, JsonSchema)]
pub struct SuggestRolesParams {
    #[schemars(description = "Topic to suggest roles for")]
    pub topic: String,
    #[schemars(description = "List of agent names to consider")]
    #[serde(default)]
    pub agents: Option<Vec<String>>,
    #[schemars(description = "Number of suggestions to return")]
    #[serde(default)]
    pub top_n: Option<usize>,
}

// -- Guidelines --

#[derive(Debug, Deserialize, JsonSchema)]
pub struct AddGuidelineParams {
    #[schemars(description = "Session ID")]
    pub session_id: String,
    #[schemars(description = "Guideline content")]
    pub content: String,
}

// -- Meta / Onboarding --

#[derive(Debug, Deserialize, JsonSchema)]
pub struct GetOnboardingParams {
    #[schemars(description = "Agent name")]
    pub agent_name: String,
    #[schemars(description = "Session ID (optional, for session-specific onboarding)")]
    #[serde(default)]
    pub session_id: Option<String>,
}

#[derive(Debug, Deserialize, JsonSchema)]
pub struct ListToolGuidesParams {
    #[schemars(description = "Filter by phase: phase1, phase2, phase3")]
    #[serde(default)]
    pub phase: Option<String>,
}

// -- Consensus --

#[derive(Debug, Deserialize, JsonSchema)]
pub struct SaveConsensusParams {
    #[schemars(description = "Session ID")]
    pub session_id: String,
    #[schemars(description = "Consensus document content")]
    pub content: String,
}

// ---------------------------------------------------------------------------
// OrchestratorServer
// ---------------------------------------------------------------------------

#[derive(Clone)]
pub struct OrchestratorServer {
    db: Arc<Mutex<BrainstormDb>>,
    config: BTreeMap<String, AgentConfig>,
    tool_router: ToolRouter<Self>,
}

#[tool_router]
impl OrchestratorServer {
    // -----------------------------------------------------------------------
    // Delegation tools
    // -----------------------------------------------------------------------

    #[tool(description = "List all enabled agents from configuration")]
    fn list_agents(
        &self,
        #[allow(unused_variables)] Parameters(_params): Parameters<EmptyParams>,
    ) -> String {
        let enabled = ai_collab_config::get_enabled_agents(&self.config);
        let agents: Vec<serde_json::Value> = enabled
            .iter()
            .map(|(name, cfg)| {
                serde_json::json!({
                    "name": name,
                    "display_name": cfg.display_name,
                    "command": cfg.command,
                    "model": cfg.model,
                    "enabled": cfg.enabled,
                    "description": cfg.description,
                })
            })
            .collect();
        json_result(&agents)
    }

    #[tool(
        description = "Ask a specific agent a question. Runs the agent CLI subprocess and returns its response."
    )]
    async fn ask_agent(
        &self,
        Parameters(params): Parameters<AskAgentParams>,
    ) -> Result<CallToolResult, McpError> {
        let cfg = self.config.get(&params.agent_name).ok_or_else(|| {
            McpError::invalid_params(
                format!("Agent '{}' not found in config", params.agent_name),
                None,
            )
        })?;

        let run_config = AgentRunConfig {
            name: cfg.name.clone(),
            command: cfg.command.clone(),
            args: cfg.args.clone(),
            model: if cfg.model.is_empty() {
                None
            } else {
                Some(cfg.model.clone())
            },
            timeout: cfg.timeout,
        };

        let provider = get_provider(run_config);
        let result = provider
            .execute(&params.question, params.cwd.as_deref())
            .await
            .map_err(|e| McpError::internal_error(e.to_string(), None))?;

        Ok(CallToolResult::success(vec![Content::text(result)]))
    }

    // -----------------------------------------------------------------------
    // Session tools
    // -----------------------------------------------------------------------

    #[tool(description = "Create a new brainstorming session")]
    fn bs_new_session(&self, Parameters(params): Parameters<NewSessionParams>) -> String {
        let db = self.db.lock().unwrap();
        match db.create_session_with_mode(
            &params.topic,
            params.project.as_deref(),
            &params.mode,
        ) {
            Ok(session) => json_result(&session),
            Err(e) => json_result(&serde_json::json!({"error": e.to_string()})),
        }
    }

    #[tool(description = "List brainstorming sessions, optionally filtered by status")]
    fn bs_list_sessions(&self, Parameters(params): Parameters<ListSessionsParams>) -> String {
        let db = self.db.lock().unwrap();
        let status = params
            .status
            .as_deref()
            .and_then(|s| s.parse::<SessionStatus>().ok());
        match db.list_sessions(status.as_ref(), params.limit) {
            Ok(sessions) => json_result(&sessions),
            Err(e) => json_result(&serde_json::json!({"error": e.to_string()})),
        }
    }

    #[tool(description = "Attach context (codebase info, background) to a session")]
    fn bs_set_context(&self, Parameters(params): Parameters<SetContextParams>) -> String {
        let db = self.db.lock().unwrap();
        let sid = SessionId::from(params.session_id.as_str());
        match db.set_context(&sid, &params.context) {
            Ok(()) => {
                json_result(&serde_json::json!({"status": "ok", "session_id": params.session_id}))
            }
            Err(e) => json_result(&serde_json::json!({"error": e.to_string()})),
        }
    }

    #[tool(description = "Mark a session as completed")]
    fn bs_complete_session(&self, Parameters(params): Parameters<SessionIdParams>) -> String {
        let db = self.db.lock().unwrap();
        let sid = SessionId::from(params.session_id.as_str());
        match db.complete_session(&sid) {
            Ok(()) => json_result(
                &serde_json::json!({"status": "completed", "session_id": params.session_id}),
            ),
            Err(e) => json_result(&serde_json::json!({"error": e.to_string()})),
        }
    }

    #[tool(
        description = "Get full session history including rounds, responses, feedback, roles, and consensus"
    )]
    fn bs_session_history(&self, Parameters(params): Parameters<SessionIdParams>) -> String {
        let db = self.db.lock().unwrap();
        let sid = SessionId::from(params.session_id.as_str());
        match db.get_session_history(&sid) {
            Ok(history) => json_result(&history),
            Err(e) => json_result(&serde_json::json!({"error": e.to_string()})),
        }
    }

    // -----------------------------------------------------------------------
    // Round tools
    // -----------------------------------------------------------------------

    #[tool(description = "Create a new round within a session")]
    fn bs_new_round(&self, Parameters(params): Parameters<NewRoundParams>) -> String {
        let db = self.db.lock().unwrap();
        let sid = SessionId::from(params.session_id.as_str());
        match db.create_round(
            &sid,
            params.objective.as_deref(),
            params.question.as_deref(),
        ) {
            Ok(round) => json_result(&round),
            Err(e) => json_result(&serde_json::json!({"error": e.to_string()})),
        }
    }

    #[tool(description = "List all rounds in a session")]
    fn bs_list_rounds(&self, Parameters(params): Parameters<SessionIdParams>) -> String {
        let db = self.db.lock().unwrap();
        let sid = SessionId::from(params.session_id.as_str());
        match db.list_rounds(&sid) {
            Ok(rounds) => json_result(&rounds),
            Err(e) => json_result(&serde_json::json!({"error": e.to_string()})),
        }
    }

    #[tool(
        description = "Check round completion status — returns participants and whether all have responded"
    )]
    fn bs_check_round_status(&self, Parameters(params): Parameters<RoundIdParams>) -> String {
        let db = self.db.lock().unwrap();
        let rid = RoundId::from(params.round_id.as_str());
        match db.get_round_participants(&rid) {
            Ok(participants) => {
                let total = participants.len();
                let responded = participants
                    .iter()
                    .filter(|p| {
                        p.status == ParticipantStatus::Responded
                            || p.status == ParticipantStatus::Validated
                    })
                    .count();
                let failed = participants
                    .iter()
                    .filter(|p| p.status == ParticipantStatus::Failed)
                    .count();
                let all_done = total > 0 && responded == total;
                let has_failures = failed > 0;

                json_result(&serde_json::json!({
                    "round_id": params.round_id,
                    "total_participants": total,
                    "responded": responded,
                    "failed": failed,
                    "all_done": all_done,
                    "has_failures": has_failures,
                    "gate_passed": all_done && !has_failures,
                    "participants": participants,
                }))
            }
            Err(e) => json_result(&serde_json::json!({"error": e.to_string()})),
        }
    }

    // -----------------------------------------------------------------------
    // Round dispatch tools
    // -----------------------------------------------------------------------

    #[tool(
        description = "Run a full round: create round, dispatch ALL agents in parallel, wait for completion, return results. FAIL-FAST: if any agent fails, returns [ROUND FAILED]."
    )]
    async fn bs_run_round(
        &self,
        Parameters(params): Parameters<RunRoundParams>,
    ) -> Result<CallToolResult, McpError> {
        let sid = SessionId::from(params.session_id.as_str());
        let db = Arc::clone(&self.db);
        let config = self.config.clone();

        // 1. Determine which agents to dispatch
        let agent_names: Vec<String> = if let Some(ref agents_str) = params.agents {
            agents_str
                .split(',')
                .map(|s| s.trim().to_string())
                .filter(|s| !s.is_empty())
                .collect()
        } else {
            // All enabled agents except "validator"
            ai_collab_config::get_enabled_agents(&config)
                .keys()
                .filter(|name| *name != "validator")
                .cloned()
                .collect()
        };

        if agent_names.is_empty() {
            return Err(McpError::invalid_params(
                "No agents to dispatch. Check config or agents parameter.",
                None,
            ));
        }

        // 2. Validate all agents exist in config
        for name in &agent_names {
            if !config.contains_key(name) {
                return Err(McpError::invalid_params(
                    format!("Agent '{}' not found in config", name),
                    None,
                ));
            }
        }

        // 3. Create round + register participants (single lock acquisition)
        let round = {
            let db_lock = db.lock().unwrap();
            let round = db_lock
                .create_round(&sid, Some(&params.objective), Some(&params.question))
                .map_err(|e| McpError::internal_error(e.to_string(), None))?;

            for name in &agent_names {
                db_lock
                    .register_participant(&round.id, name, "analysis")
                    .map_err(|e| McpError::internal_error(e.to_string(), None))?;
            }
            round
        };

        // 4. Spawn all agent dispatches in parallel
        let mut handles = Vec::new();
        for agent_name in &agent_names {
            let db_clone = Arc::clone(&db);
            let config_clone = config.clone();
            let rid = round.id.clone();
            let sid_clone = sid.clone();
            let name = agent_name.clone();
            let cwd = params.cwd.clone();

            handles.push(tokio::spawn(async move {
                dispatch_single_agent(db_clone, config_clone, rid, sid_clone, name, cwd).await
            }));
        }

        // 5. Await all results
        let mut results: Vec<AgentDispatchResult> = Vec::new();
        for handle in handles {
            match handle.await {
                Ok(result) => results.push(result),
                Err(e) => {
                    results.push(AgentDispatchResult {
                        agent_name: "unknown".into(),
                        success: false,
                        quality: None,
                        error: Some(format!("Task panic: {e}")),
                        duration_secs: 0.0,
                    });
                }
            }
        }

        // 6. Compute gate status based on gate_mode
        let total = results.len();
        let succeeded = results.iter().filter(|r| r.success).count();
        let failed = total - succeeded;

        let gate_passed = match params.gate_mode.as_str() {
            "quorum" => succeeded > total / 2,    // majority must succeed
            "best_effort" => succeeded > 0,        // any success = proceed
            _ => succeeded == total,               // "strict" (default): all must succeed
        };

        // 7. Build response
        if gate_passed {
            let status = if succeeded == total {
                "complete"
            } else {
                "partial_success"
            };
            let body = serde_json::to_string_pretty(&serde_json::json!({
                "status": status,
                "round_id": round.id.as_str(),
                "round_number": round.round_number,
                "session_id": params.session_id,
                "total_agents": total,
                "succeeded": succeeded,
                "failed": failed,
                "gate_passed": true,
                "gate_mode": params.gate_mode,
                "results": results,
            }))
            .unwrap_or_default();
            Ok(CallToolResult::success(vec![Content::text(body)]))
        } else {
            let failed_agents: Vec<&AgentDispatchResult> =
                results.iter().filter(|r| !r.success).collect();
            let body = serde_json::to_string_pretty(&serde_json::json!({
                "status": "[ROUND FAILED]",
                "round_id": round.id.as_str(),
                "round_number": round.round_number,
                "session_id": params.session_id,
                "total_agents": total,
                "succeeded": succeeded,
                "failed": failed,
                "gate_passed": false,
                "gate_mode": params.gate_mode,
                "results": results,
                "failed_agents": failed_agents,
                "hint": "Use bs_retry_agent to retry failed agents, then bs_check_round_status to verify."
            }))
            .unwrap_or_default();
            Ok(CallToolResult::success(vec![Content::text(body)]))
        }
    }

    #[tool(
        description = "Retry a failed or timed-out agent in a round. Increments retry count, re-dispatches the agent, returns updated status."
    )]
    async fn bs_retry_agent(
        &self,
        Parameters(params): Parameters<RetryAgentParams>,
    ) -> Result<CallToolResult, McpError> {
        let rid = RoundId::from(params.round_id.as_str());
        let db = Arc::clone(&self.db);
        let config = self.config.clone();

        // 1. Fetch participant + round
        let (participant, round) = {
            let db_lock = db.lock().unwrap();
            let participant = db_lock
                .get_participant(&rid, &params.agent_name)
                .map_err(|e| McpError::internal_error(e.to_string(), None))?
                .ok_or_else(|| {
                    McpError::invalid_params(
                        format!(
                            "No participant record for agent '{}' in round '{}'",
                            params.agent_name, params.round_id
                        ),
                        None,
                    )
                })?;

            let round = db_lock
                .get_round(&rid)
                .map_err(|e| McpError::internal_error(e.to_string(), None))?
                .ok_or_else(|| {
                    McpError::invalid_params(
                        format!("Round '{}' not found", params.round_id),
                        None,
                    )
                })?;

            (participant, round)
        };

        // 2. Check status is retryable
        if participant.status != ParticipantStatus::Failed
            && participant.status != ParticipantStatus::TimedOut
        {
            return Err(McpError::invalid_params(
                format!(
                    "Agent '{}' status is '{}' — only failed or timed_out agents can be retried",
                    params.agent_name, participant.status
                ),
                None,
            ));
        }

        // 3. Check retry limit
        if participant.retry_count >= participant.max_retries {
            return Err(McpError::invalid_params(
                format!(
                    "Agent '{}' has exhausted retries ({}/{})",
                    params.agent_name, participant.retry_count, participant.max_retries
                ),
                None,
            ));
        }

        // 4. Increment retry count and reset status
        {
            let db_lock = db.lock().unwrap();
            db_lock
                .increment_retry_count(&rid, &params.agent_name)
                .map_err(|e| McpError::internal_error(e.to_string(), None))?;
        }

        // 5. Re-dispatch the agent
        let result = dispatch_single_agent(
            db,
            config,
            rid,
            round.session_id,
            params.agent_name.clone(),
            params.cwd,
        )
        .await;

        // 6. Return result
        let body = serde_json::to_string_pretty(&serde_json::json!({
            "agent": result.agent_name,
            "success": result.success,
            "quality": result.quality,
            "error": result.error,
            "duration_secs": result.duration_secs,
            "retry_count": participant.retry_count + 1,
            "hint": if result.success {
                "Agent succeeded. Use bs_check_round_status to verify all agents are done."
            } else {
                "Agent still failing. Check error details, fix config, or try again."
            },
        }))
        .unwrap_or_default();

        Ok(CallToolResult::success(vec![Content::text(body)]))
    }

    // -----------------------------------------------------------------------
    // Response tools
    // -----------------------------------------------------------------------

    #[tool(description = "Save an agent's response to a round")]
    fn bs_save_response(&self, Parameters(params): Parameters<SaveResponseParams>) -> String {
        let db = self.db.lock().unwrap();
        let rid = RoundId::from(params.round_id.as_str());
        match db.save_response(&rid, &params.agent_name, &params.content) {
            Ok(response) => {
                // Auto-validate response quality
                let quality = validate_heuristic(&params.content);
                let _ = db.update_response_quality(&response.id, &quality);

                let mut resp = response;
                resp.quality = Some(quality.clone());

                // Spawn background Haiku validator for suspect responses
                if quality == ResponseQuality::Suspect {
                    drop(db); // Release mutex before spawning background task
                    crate::validator::spawn_validator(
                        Arc::clone(&self.db),
                        &self.config,
                        resp.id.clone(),
                        params.agent_name.clone(),
                        params.content.clone(),
                    );
                }

                json_result(&resp)
            }
            Err(e) => json_result(&serde_json::json!({"error": e.to_string()})),
        }
    }

    #[tool(description = "Get a specific agent's response for a round")]
    fn bs_get_response(&self, Parameters(params): Parameters<GetResponseParams>) -> String {
        let db = self.db.lock().unwrap();
        let rid = RoundId::from(params.round_id.as_str());
        match db.get_response(&rid, &params.agent_name) {
            Ok(Some(response)) => json_result(&response),
            Ok(None) => json_result(&serde_json::json!({
                "error": format!("No response found for agent '{}' in round '{}'", params.agent_name, params.round_id)
            })),
            Err(e) => json_result(&serde_json::json!({"error": e.to_string()})),
        }
    }

    #[tool(description = "Get all responses for a round")]
    fn bs_get_round_responses(&self, Parameters(params): Parameters<RoundIdParams>) -> String {
        let db = self.db.lock().unwrap();
        let rid = RoundId::from(params.round_id.as_str());
        match db.get_round_responses(&rid) {
            Ok(responses) => json_result(&responses),
            Err(e) => json_result(&serde_json::json!({"error": e.to_string()})),
        }
    }

    // -----------------------------------------------------------------------
    // Feedback tools
    // -----------------------------------------------------------------------

    #[tool(description = "Create a feedback item from Phase 1 analysis")]
    fn bs_create_feedback(&self, Parameters(params): Parameters<CreateFeedbackParams>) -> String {
        let db = self.db.lock().unwrap();
        let sid = SessionId::from(params.session_id.as_str());
        let rid = RoundId::from(params.source_round_id.as_str());
        match db.create_feedback_item(
            &sid,
            &rid,
            &params.source_agent,
            &params.title,
            &params.content,
        ) {
            Ok(item) => json_result(&item),
            Err(e) => json_result(&serde_json::json!({"error": e.to_string()})),
        }
    }

    #[tool(description = "List feedback items for a session, optionally filtered by status")]
    fn bs_list_feedback(&self, Parameters(params): Parameters<ListFeedbackParams>) -> String {
        let db = self.db.lock().unwrap();
        let sid = SessionId::from(params.session_id.as_str());
        let status = params
            .status
            .as_deref()
            .and_then(|s| s.parse::<FeedbackStatus>().ok());
        match db.list_feedback_items(&sid, status.as_ref()) {
            Ok(items) => json_result(&items),
            Err(e) => json_result(&serde_json::json!({"error": e.to_string()})),
        }
    }

    #[tool(description = "Get a feedback item with all its responses")]
    fn bs_get_feedback(&self, Parameters(params): Parameters<FeedbackIdParams>) -> String {
        let db = self.db.lock().unwrap();
        let fid = FeedbackId::from(params.item_id.as_str());
        match db.get_feedback_item(&fid) {
            Ok(Some((item, responses))) => json_result(&serde_json::json!({
                "item": item,
                "responses": responses,
            })),
            Ok(None) => json_result(&serde_json::json!({
                "error": format!("Feedback item '{}' not found", params.item_id)
            })),
            Err(e) => json_result(&serde_json::json!({"error": e.to_string()})),
        }
    }

    #[tool(
        description = "Auto-resolve feedback items based on agent verdicts. Unanimous accept→accepted, unanimous reject→rejected, majority wins. Only processes pending items."
    )]
    fn bs_auto_resolve(
        &self,
        Parameters(params): Parameters<AutoResolveParams>,
    ) -> String {
        let db = self.db.lock().unwrap();
        let sid = SessionId::from(params.session_id.as_str());
        let rid = RoundId::from(params.round_id.as_str());

        // Get all pending feedback items
        let items = match db.list_feedback_items(&sid, Some(&FeedbackStatus::Pending)) {
            Ok(items) => items,
            Err(e) => return json_result(&serde_json::json!({"error": e.to_string()})),
        };

        let mut resolved = 0u32;
        let mut contested = 0u32;
        let mut details = Vec::new();

        for item in &items {
            // Get verdicts for this item, filtered by round
            let fid = &item.id;
            let all_responses = match db.get_feedback_responses(fid) {
                Ok(r) => r,
                Err(_) => continue,
            };
            // Filter to only the specified round's votes
            let responses: Vec<_> = all_responses
                .into_iter()
                .filter(|r| r.round_id == rid)
                .collect();

            if responses.is_empty() {
                contested += 1;
                details.push(serde_json::json!({
                    "item_id": fid.as_str(),
                    "title": item.title,
                    "result": "no_votes",
                }));
                continue;
            }

            let total = responses.len();
            let accepts = responses.iter().filter(|r| r.verdict == "accept").count();
            let rejects = responses.iter().filter(|r| r.verdict == "reject").count();
            let modifies = responses.iter().filter(|r| r.verdict == "modify").count();
            let abstains = responses.iter().filter(|r| r.verdict == "abstain").count();

            // Exclude abstain votes from effective total so "no opinion" doesn't
            // inflate the denominator and block majorities.
            let effective_total = total - abstains;

            let resolution = if effective_total == 0 {
                None // all abstained — contested
            } else if accepts == effective_total {
                Some(FeedbackStatus::Accepted)
            } else if rejects == effective_total {
                Some(FeedbackStatus::Rejected)
            } else if modifies == effective_total {
                Some(FeedbackStatus::Modified)
            } else if accepts > effective_total / 2 {
                Some(FeedbackStatus::Accepted)
            } else if rejects > effective_total / 2 {
                Some(FeedbackStatus::Rejected)
            } else if modifies > effective_total / 2 {
                Some(FeedbackStatus::Modified)
            } else {
                None // contested — no majority
            };

            if let Some(status) = resolution {
                let _ = db.update_feedback_status(fid, &status);
                resolved += 1;
                details.push(serde_json::json!({
                    "item_id": fid.as_str(),
                    "title": item.title,
                    "result": status.to_string(),
                    "votes": {"accept": accepts, "reject": rejects, "modify": modifies, "abstain": abstains, "total": total, "effective_total": effective_total},
                }));
            } else {
                contested += 1;
                details.push(serde_json::json!({
                    "item_id": fid.as_str(),
                    "title": item.title,
                    "result": "contested",
                    "votes": {"accept": accepts, "reject": rejects, "modify": modifies, "abstain": abstains, "total": total, "effective_total": effective_total},
                }));
            }
        }

        json_result(&serde_json::json!({
            "resolved": resolved,
            "contested": contested,
            "total_pending": items.len(),
            "details": details,
        }))
    }

    #[tool(
        description = "Get contested feedback items with all conflicting verdicts pre-loaded. Use this to surface dissenting reasoning before follow-up deliberation rounds."
    )]
    fn bs_get_contested_items(
        &self,
        Parameters(params): Parameters<SessionIdParams>,
    ) -> String {
        let db = self.db.lock().unwrap();
        let sid = SessionId::from(params.session_id.as_str());

        // Get all pending (contested) feedback items
        let items = match db.list_feedback_items(&sid, Some(&FeedbackStatus::Pending)) {
            Ok(items) => items,
            Err(e) => return json_result(&serde_json::json!({"error": e.to_string()})),
        };

        let mut contested = Vec::new();
        for item in &items {
            let responses = match db.get_feedback_responses(&item.id) {
                Ok(r) => r,
                Err(_) => continue,
            };

            if responses.is_empty() {
                continue;
            }

            // Group verdicts
            let verdicts: Vec<serde_json::Value> = responses
                .iter()
                .map(|r| {
                    serde_json::json!({
                        "agent": r.agent_name,
                        "verdict": r.verdict,
                        "reasoning": r.reasoning,
                    })
                })
                .collect();

            let accept_count = responses.iter().filter(|r| r.verdict == "accept").count();
            let reject_count = responses.iter().filter(|r| r.verdict == "reject").count();
            let modify_count = responses.iter().filter(|r| r.verdict == "modify").count();

            contested.push(serde_json::json!({
                "item_id": item.id.as_str(),
                "title": item.title,
                "content": item.content,
                "source_agent": item.source_agent,
                "vote_summary": {
                    "accept": accept_count,
                    "reject": reject_count,
                    "modify": modify_count,
                },
                "verdicts": verdicts,
            }));
        }

        json_result(&serde_json::json!({
            "session_id": params.session_id,
            "contested_count": contested.len(),
            "items": contested,
        }))
    }

    #[tool(description = "Update the status of a feedback item")]
    fn bs_update_feedback_status(
        &self,
        Parameters(params): Parameters<UpdateFeedbackStatusParams>,
    ) -> String {
        let db = self.db.lock().unwrap();
        let fid = FeedbackId::from(params.item_id.as_str());
        let status: FeedbackStatus = match params.status.parse() {
            Ok(s) => s,
            Err(e) => return json_result(&serde_json::json!({"error": e})),
        };
        match db.update_feedback_status(&fid, &status) {
            Ok(()) => json_result(&serde_json::json!({
                "status": "ok",
                "item_id": params.item_id,
                "new_status": params.status,
            })),
            Err(e) => json_result(&serde_json::json!({"error": e.to_string()})),
        }
    }

    #[tool(description = "Submit a verdict on a feedback item (Phase 2 deliberation)")]
    fn bs_respond_to_feedback(
        &self,
        Parameters(params): Parameters<RespondToFeedbackParams>,
    ) -> String {
        // Validate verdict is a known value
        let valid_verdicts = ["accept", "reject", "modify", "abstain"];
        if !valid_verdicts.contains(&params.verdict.as_str()) {
            return json_result(&serde_json::json!({
                "error": format!("Invalid verdict '{}'. Must be one of: accept, reject, modify, abstain", params.verdict)
            }));
        }

        // Reasoning quality gate: minimum 50 chars to prevent rubber-stamp verdicts
        if params.reasoning.trim().len() < 50 {
            return json_result(&serde_json::json!({
                "error": format!(
                    "Reasoning too short ({} chars). Minimum 50 characters required to ensure substantive deliberation.",
                    params.reasoning.trim().len()
                )
            }));
        }

        let db = self.db.lock().unwrap();
        let fid = FeedbackId::from(params.item_id.as_str());
        let rid = RoundId::from(params.round_id.as_str());
        match db.save_feedback_response(
            &fid,
            &rid,
            &params.agent_name,
            &params.verdict,
            &params.reasoning,
        ) {
            Ok(response) => json_result(&response),
            Err(e) => json_result(&serde_json::json!({"error": e.to_string()})),
        }
    }

    #[tool(
        description = "Check feedback vote completeness — shows which agents have voted on which items"
    )]
    fn bs_check_feedback_status(
        &self,
        Parameters(params): Parameters<CheckFeedbackStatusParams>,
    ) -> String {
        let db = self.db.lock().unwrap();
        let sid = SessionId::from(params.session_id.as_str());
        let rid = RoundId::from(params.round_id.as_str());

        // Get all feedback items for the session
        let items = match db.list_feedback_items(&sid, None) {
            Ok(items) => items,
            Err(e) => return json_result(&serde_json::json!({"error": e.to_string()})),
        };

        // Get participants for the round
        let participants = match db.get_round_participants(&rid) {
            Ok(p) => p,
            Err(e) => return json_result(&serde_json::json!({"error": e.to_string()})),
        };

        let agent_names: Vec<&str> = participants.iter().map(|p| p.agent_name.as_str()).collect();
        let total_items = items.len();
        let total_agents = agent_names.len();
        let total_votes_needed = total_items * total_agents;

        // Build vote matrix
        let mut votes_received = 0u64;
        let mut matrix: Vec<serde_json::Value> = Vec::new();
        for item in &items {
            let responses = match db.get_feedback_item(&item.id) {
                Ok(Some((_, r))) => r,
                _ => vec![],
            };
            let voted_agents: Vec<&str> = responses.iter().map(|r| r.agent_name.as_str()).collect();
            let missing: Vec<&str> = agent_names
                .iter()
                .filter(|a| !voted_agents.contains(a))
                .copied()
                .collect();
            votes_received += voted_agents.len() as u64;
            matrix.push(serde_json::json!({
                "item_id": item.id.as_str(),
                "title": item.title,
                "voted": voted_agents,
                "missing": missing,
                "complete": missing.is_empty(),
            }));
        }

        let all_complete = votes_received == total_votes_needed as u64 && total_votes_needed > 0;

        json_result(&serde_json::json!({
            "session_id": params.session_id,
            "round_id": params.round_id,
            "total_feedback_items": total_items,
            "total_agents": total_agents,
            "total_votes_needed": total_votes_needed,
            "votes_received": votes_received,
            "all_complete": all_complete,
            "matrix": matrix,
        }))
    }

    // -----------------------------------------------------------------------
    // Role tools
    // -----------------------------------------------------------------------

    #[tool(description = "Assign a role to an agent for a session")]
    fn bs_set_role(&self, Parameters(params): Parameters<SetRoleParams>) -> String {
        let db = self.db.lock().unwrap();
        let sid = SessionId::from(params.session_id.as_str());
        match db.set_role(&sid, &params.agent_name, &params.role, None) {
            Ok(role) => json_result(&role),
            Err(e) => json_result(&serde_json::json!({"error": e.to_string()})),
        }
    }

    #[tool(description = "Get the current role assigned to an agent in a session")]
    fn bs_get_role(&self, Parameters(params): Parameters<GetRoleParams>) -> String {
        let db = self.db.lock().unwrap();
        let sid = SessionId::from(params.session_id.as_str());
        match db.get_role(&sid, &params.agent_name) {
            Ok(Some(role)) => json_result(&role),
            Ok(None) => json_result(&serde_json::json!({
                "info": format!("No role assigned for agent '{}' in session '{}'", params.agent_name, params.session_id)
            })),
            Err(e) => json_result(&serde_json::json!({"error": e.to_string()})),
        }
    }

    #[tool(description = "Create a reusable role template in the library")]
    fn bs_create_role(&self, Parameters(params): Parameters<CreateRoleTemplateParams>) -> String {
        let db = self.db.lock().unwrap();
        let tags_ref: Option<Vec<String>> = params.tags;
        let mandates_ref: Option<Vec<String>> = params.mandates;
        match db.create_role_template(
            &params.slug,
            &params.display_name,
            &params.description,
            &params.role_text,
            params.agent_name.as_deref(),
            params.approach.as_deref(),
            tags_ref.as_deref().map(|v| v as &[String]),
            params.notes.as_deref(),
            params.vision.as_deref(),
            params.angle.as_deref(),
            params.behavior.as_deref(),
            mandates_ref.as_deref().map(|v| v as &[String]),
        ) {
            Ok(template) => json_result(&template),
            Err(e) => json_result(&serde_json::json!({"error": e.to_string()})),
        }
    }

    #[tool(
        description = "List role templates from the library, optionally filtered by agent or tag"
    )]
    fn bs_list_roles(&self, Parameters(params): Parameters<ListRoleTemplatesParams>) -> String {
        let db = self.db.lock().unwrap();
        match db.list_role_templates(params.agent_name.as_deref(), params.tag.as_deref()) {
            Ok(templates) => json_result(&templates),
            Err(e) => json_result(&serde_json::json!({"error": e.to_string()})),
        }
    }

    #[tool(description = "Get a specific role template by slug or ID")]
    fn bs_get_role_template(
        &self,
        Parameters(params): Parameters<RoleTemplateSlugParams>,
    ) -> String {
        let db = self.db.lock().unwrap();
        match db.get_role_template(&params.slug) {
            Ok(Some(template)) => json_result(&template),
            Ok(None) => json_result(&serde_json::json!({
                "error": format!("Role template '{}' not found", params.slug)
            })),
            Err(e) => json_result(&serde_json::json!({"error": e.to_string()})),
        }
    }

    #[tool(description = "Apply a role template to an agent in a session")]
    fn bs_apply_role(&self, Parameters(params): Parameters<ApplyRoleParams>) -> String {
        let db = self.db.lock().unwrap();
        let sid = SessionId::from(params.session_id.as_str());
        match db.apply_role_template(&sid, &params.agent_name, &params.slug) {
            Ok(Some((template, role))) => json_result(&serde_json::json!({
                "template": template,
                "role": role,
            })),
            Ok(None) => json_result(&serde_json::json!({
                "error": format!("Role template '{}' not found", params.slug)
            })),
            Err(e) => json_result(&serde_json::json!({"error": e.to_string()})),
        }
    }

    #[tool(description = "Suggest roles for a topic based on available role templates")]
    fn bs_suggest_roles(&self, Parameters(params): Parameters<SuggestRolesParams>) -> String {
        let db = self.db.lock().unwrap();
        let top_n = params.top_n.unwrap_or(5);

        // Get all role templates and score them by keyword overlap with the topic
        let mut templates = match db.list_role_templates(None, None) {
            Ok(t) => t,
            Err(e) => return json_result(&serde_json::json!({"error": e.to_string()})),
        };

        // Filter by agent names if provided (keep only templates assignable to those agents)
        if let Some(ref agents) = params.agents {
            templates.retain(|t| {
                t.agent_name.is_none() || t.agent_name.as_ref().is_some_and(|a| agents.contains(a))
            });
        }

        let topic_lower = params.topic.to_lowercase();
        let topic_words: Vec<&str> = topic_lower.split_whitespace().collect();

        let mut scored: Vec<(f64, &RoleTemplate)> = templates
            .iter()
            .map(|t| {
                let text = format!(
                    "{} {} {} {}",
                    t.display_name,
                    t.description,
                    t.role_text,
                    t.tags.join(" ")
                )
                .to_lowercase();
                let doc_words: Vec<&str> = text.split_whitespace().collect();
                let doc_len = doc_words.len() as f64;

                // TF-IDF-inspired scoring: weight by inverse document frequency within the text
                let score: f64 = topic_words
                    .iter()
                    .map(|w| {
                        // Term frequency: count occurrences, normalized by doc length
                        let tf = doc_words.iter().filter(|dw| dw.contains(w)).count() as f64
                            / doc_len.max(1.0);
                        // Boost partial matches less than exact matches
                        let exact_matches = doc_words.iter().filter(|dw| *dw == w).count() as f64;
                        tf + exact_matches * 0.5
                    })
                    .sum();

                // Logarithmic usage penalty: log2(usage_count + 1) * 0.5
                // Much stronger diversity pressure than the old linear 0.1 * count
                let usage_penalty = ((t.usage_count as f64) + 1.0).log2() * 0.5;
                (score - usage_penalty, t)
            })
            .collect();

        scored.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal));
        scored.truncate(top_n);

        let suggestions: Vec<serde_json::Value> = scored
            .iter()
            .map(|(score, t)| {
                serde_json::json!({
                    "slug": t.slug,
                    "display_name": t.display_name,
                    "description": t.description,
                    "score": score,
                    "usage_count": t.usage_count,
                    "tags": t.tags,
                })
            })
            .collect();

        json_result(&serde_json::json!({
            "topic": params.topic,
            "suggestions": suggestions,
        }))
    }

    // -----------------------------------------------------------------------
    // Guideline tools
    // -----------------------------------------------------------------------

    #[tool(description = "Add a guideline to a session")]
    fn bs_add_guideline(&self, Parameters(params): Parameters<AddGuidelineParams>) -> String {
        let db = self.db.lock().unwrap();
        let sid = SessionId::from(params.session_id.as_str());
        match db.add_guideline(&sid, &params.content) {
            Ok(guideline) => json_result(&guideline),
            Err(e) => json_result(&serde_json::json!({"error": e.to_string()})),
        }
    }

    #[tool(description = "List all guidelines for a session")]
    fn bs_list_guidelines(&self, Parameters(params): Parameters<SessionIdParams>) -> String {
        let db = self.db.lock().unwrap();
        let sid = SessionId::from(params.session_id.as_str());
        match db.list_guidelines(&sid) {
            Ok(guidelines) => json_result(&guidelines),
            Err(e) => json_result(&serde_json::json!({"error": e.to_string()})),
        }
    }

    // -----------------------------------------------------------------------
    // Meta / Onboarding tools
    // -----------------------------------------------------------------------

    #[tool(
        description = "Get onboarding information for an agent — agent definition, workflow, tools, session context"
    )]
    fn bs_get_onboarding(&self, Parameters(params): Parameters<GetOnboardingParams>) -> String {
        let db = self.db.lock().unwrap();

        // Agent definition from DB
        let agent_def = db.get_agent_definition(&params.agent_name).ok().flatten();

        // Workflow template
        let workflow = db
            .get_workflow_template("multi-ai-brainstorm")
            .ok()
            .flatten();

        // Tool guides
        let tool_guides = db.list_tool_guides(None).unwrap_or_default();

        // Session-specific data
        let mut session_data = serde_json::json!(null);
        if let Some(ref sid_str) = params.session_id {
            let sid = SessionId::from(sid_str.as_str());
            let session = db.get_session(&sid).ok().flatten();
            let role = db.get_role(&sid, &params.agent_name).ok().flatten();
            let guidelines = db.list_guidelines(&sid).unwrap_or_default();
            let rounds = db.list_rounds(&sid).unwrap_or_default();
            let latest_round = rounds.last();
            session_data = serde_json::json!({
                "session": session,
                "role": role,
                "guidelines": guidelines,
                "round_count": rounds.len(),
                "latest_round": latest_round,
            });
        }

        json_result(&serde_json::json!({
            "agent_name": params.agent_name,
            "agent_definition": agent_def,
            "workflow": workflow,
            "tool_guides": tool_guides,
            "session": session_data,
        }))
    }

    #[tool(description = "Get the workflow template defining the 3-phase brainstorm process")]
    fn bs_get_workflow(
        &self,
        #[allow(unused_variables)] Parameters(_params): Parameters<EmptyParams>,
    ) -> String {
        let db = self.db.lock().unwrap();
        match db.get_workflow_template("multi-ai-brainstorm") {
            Ok(Some(workflow)) => json_result(&workflow),
            Ok(None) => json_result(&serde_json::json!({
                "info": "No workflow template found. Run seed-defaults to populate."
            })),
            Err(e) => json_result(&serde_json::json!({"error": e.to_string()})),
        }
    }

    #[tool(description = "List tool usage guides, optionally filtered by phase")]
    fn bs_list_tool_guides(&self, Parameters(params): Parameters<ListToolGuidesParams>) -> String {
        let db = self.db.lock().unwrap();
        match db.list_tool_guides(params.phase.as_deref()) {
            Ok(guides) => json_result(&guides),
            Err(e) => json_result(&serde_json::json!({"error": e.to_string()})),
        }
    }

    // -----------------------------------------------------------------------
    // Consensus tools
    // -----------------------------------------------------------------------

    #[tool(description = "Save a consensus document for a session")]
    fn bs_save_consensus(&self, Parameters(params): Parameters<SaveConsensusParams>) -> String {
        let db = self.db.lock().unwrap();
        let sid = SessionId::from(params.session_id.as_str());
        match db.save_consensus(&sid, &params.content, None) {
            Ok(consensus) => json_result(&consensus),
            Err(e) => json_result(&serde_json::json!({"error": e.to_string()})),
        }
    }

    #[tool(description = "Get the latest consensus document for a session")]
    fn bs_get_consensus(&self, Parameters(params): Parameters<SessionIdParams>) -> String {
        let db = self.db.lock().unwrap();
        let sid = SessionId::from(params.session_id.as_str());
        match db.get_latest_consensus(&sid) {
            Ok(Some(consensus)) => json_result(&consensus),
            Ok(None) => json_result(&serde_json::json!({
                "info": format!("No consensus found for session '{}'", params.session_id)
            })),
            Err(e) => json_result(&serde_json::json!({"error": e.to_string()})),
        }
    }
}

// ---------------------------------------------------------------------------
// ServerHandler (MCP protocol)
// ---------------------------------------------------------------------------

#[tool_handler]
impl ServerHandler for OrchestratorServer {
    fn get_info(&self) -> ServerInfo {
        ServerInfo::new(ServerCapabilities::builder().enable_tools().build())
            .with_server_info(Implementation::new("ai-collab", "0.1.0"))
            .with_instructions("ai-collab orchestrator: multi-AI brainstorm + delegation tools")
    }
}

impl OrchestratorServer {
    pub fn new(db: BrainstormDb, config: BTreeMap<String, AgentConfig>) -> Self {
        Self {
            db: Arc::new(Mutex::new(db)),
            config,
            tool_router: Self::tool_router(),
        }
    }
}

// ---------------------------------------------------------------------------
// Agent dispatch helpers (used by bs_run_round and bs_retry_agent)
// ---------------------------------------------------------------------------

/// Result of dispatching a single agent subprocess.
#[derive(Debug, Clone, serde::Serialize)]
struct AgentDispatchResult {
    agent_name: String,
    success: bool,
    quality: Option<String>,
    error: Option<String>,
    duration_secs: f64,
}

/// Dispatch a single agent: Pending → Dispatched → (execute) → Responded/Failed.
///
/// Standalone async fn (not a method) so it can be moved into `tokio::spawn`.
async fn dispatch_single_agent(
    db: Arc<Mutex<BrainstormDb>>,
    config: BTreeMap<String, AgentConfig>,
    round_id: RoundId,
    session_id: SessionId,
    agent_name: String,
    cwd: Option<String>,
) -> AgentDispatchResult {
    let start = std::time::Instant::now();

    // 1. Look up agent config
    let cfg = match config.get(&agent_name) {
        Some(c) => c.clone(),
        None => {
            return AgentDispatchResult {
                agent_name,
                success: false,
                quality: None,
                error: Some("Agent not found in config".into()),
                duration_secs: 0.0,
            };
        }
    };

    // 2. Mark as Dispatched
    {
        let db_lock = db.lock().unwrap();
        let _ = db_lock.update_participant_status(
            &round_id,
            &agent_name,
            &ParticipantStatus::Dispatched,
            None,
            None,
        );
    }

    // 3. Build enriched bootstrap prompt with topic + urgency hint
    let session_topic = {
        let db_lock = db.lock().unwrap();
        db_lock
            .get_session(&session_id)
            .ok()
            .flatten()
            .map(|s| s.topic)
            .unwrap_or_default()
    };
    let prompt = format!(
        "You are '{}'. Topic: \"{}\". Your FIRST and ONLY action: call bs_get_onboarding(agent_name=\"{}\", session_id=\"{}\", round_id=\"{}\") immediately. Do NOT explore files or do anything else before calling this tool.",
        agent_name,
        session_topic,
        agent_name,
        session_id.as_str(),
        round_id.as_str(),
    );

    // 4. Build provider and execute with auto-retry
    let max_retries = cfg.max_auto_retries;
    let mut attempt = 0u32;
    let mut result;

    loop {
        let run_config = AgentRunConfig {
            name: cfg.name.clone(),
            command: cfg.command.clone(),
            args: cfg.args.clone(),
            model: if cfg.model.is_empty() {
                None
            } else {
                Some(cfg.model.clone())
            },
            timeout: cfg.timeout,
        };

        let provider = get_provider(run_config);
        result = provider.execute(&prompt, cwd.as_deref()).await;

        if result.is_ok() || attempt >= max_retries {
            break;
        }

        // Log retry and apply exponential backoff (2^attempt seconds)
        attempt += 1;
        let backoff = std::time::Duration::from_secs(1u64 << attempt);
        eprintln!(
            "[auto-retry] Agent '{}' failed (attempt {}), retrying in {:?}...",
            agent_name, attempt, backoff
        );
        tokio::time::sleep(backoff).await;
    }
    let elapsed = start.elapsed().as_secs_f64();

    match result {
        Ok(output) => {
            // 5a. Check if agent already self-saved via its own MCP server
            let already_saved = {
                let db_lock = db.lock().unwrap();
                db_lock
                    .get_response(&round_id, &agent_name)
                    .ok()
                    .flatten()
                    .is_some()
            };

            if already_saved {
                // Agent saved via bs_save_response on the agent-facing server.
                let db_lock = db.lock().unwrap();
                let _ = db_lock.update_participant_status(
                    &round_id,
                    &agent_name,
                    &ParticipantStatus::Responded,
                    Some(&ResponseQuality::SelfSaved),
                    None,
                );
                return AgentDispatchResult {
                    agent_name,
                    success: true,
                    quality: Some("self_saved".into()),
                    error: None,
                    duration_secs: elapsed,
                };
            }

            // 5b. Save stdout capture as fallback response
            let quality = validate_heuristic(&output);
            let db_lock = db.lock().unwrap();
            match db_lock.save_response(&round_id, &agent_name, &output) {
                Ok(response) => {
                    let _ = db_lock.update_response_quality(&response.id, &quality);
                    let _ = db_lock.update_participant_status(
                        &round_id,
                        &agent_name,
                        &ParticipantStatus::Responded,
                        Some(&quality),
                        None,
                    );

                    // Spawn Haiku validator for suspect responses
                    if quality == ResponseQuality::Suspect {
                        let quality_str = quality.to_string();
                        drop(db_lock);
                        crate::validator::spawn_validator(
                            Arc::clone(&db),
                            &config,
                            response.id,
                            agent_name.clone(),
                            output,
                        );
                        return AgentDispatchResult {
                            agent_name,
                            success: true,
                            quality: Some(quality_str),
                            error: None,
                            duration_secs: elapsed,
                        };
                    }

                    AgentDispatchResult {
                        agent_name,
                        success: true,
                        quality: Some(quality.to_string()),
                        error: None,
                        duration_secs: elapsed,
                    }
                }
                Err(e) => {
                    let _ = db_lock.update_participant_status(
                        &round_id,
                        &agent_name,
                        &ParticipantStatus::Failed,
                        None,
                        Some(&e.to_string()),
                    );
                    AgentDispatchResult {
                        agent_name,
                        success: false,
                        quality: None,
                        error: Some(e.to_string()),
                        duration_secs: elapsed,
                    }
                }
            }
        }
        Err(e) => {
            // 5c. Handle provider error
            let status = if matches!(e, ProviderError::Timeout { .. }) {
                ParticipantStatus::TimedOut
            } else {
                ParticipantStatus::Failed
            };
            {
                let db_lock = db.lock().unwrap();
                let _ = db_lock.update_participant_status(
                    &round_id,
                    &agent_name,
                    &status,
                    None,
                    Some(&e.to_string()),
                );
            }
            AgentDispatchResult {
                agent_name,
                success: false,
                quality: None,
                error: Some(e.to_string()),
                duration_secs: elapsed,
            }
        }
    }
}
