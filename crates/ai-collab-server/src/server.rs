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
    #[schemars(description = "Verdict: agree, disagree, partial, abstain")]
    pub verdict: String,
    #[schemars(description = "Reasoning behind the verdict")]
    pub reasoning: String,
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
        #[allow(unused_variables)] Parameters(_params): Parameters<()>,
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
        match db.create_session(&params.topic, params.project.as_deref()) {
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
                resp.quality = Some(quality);
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
                let score: f64 = topic_words.iter().filter(|w| text.contains(**w)).count() as f64;
                // Boost by usage_count (less used = more interesting for diversity)
                let usage_penalty = (t.usage_count as f64) * 0.1;
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
        #[allow(unused_variables)] Parameters(_params): Parameters<()>,
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
