//! Agent-facing MCP server — the simpler server brainstorm agents connect to.
//!
//! Provides only the tools agents need during brainstorm sessions:
//! onboarding, responses, feedback, roles, and read-only session data.

use std::collections::BTreeMap;
use std::sync::{Arc, Mutex};

use rmcp::handler::server::router::tool::ToolRouter;
use rmcp::handler::server::wrapper::Parameters;
use rmcp::model::*;
use rmcp::{ServerHandler, tool, tool_handler, tool_router};
use schemars::JsonSchema;
use serde::Deserialize;

use ai_collab_config::AgentConfig;
use ai_collab_core::*;
use ai_collab_db::BrainstormDb;

fn json_result<T: serde::Serialize>(val: &T) -> String {
    serde_json::to_string_pretty(val).unwrap_or_else(|e| format!("{{\"error\": \"{e}\"}}"))
}

// ---------------------------------------------------------------------------
// Parameter structs
// ---------------------------------------------------------------------------

/// Empty params for tools that take no arguments.
/// Custom JsonSchema impl ensures `"properties": {}` is present
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
            "title": "EmptyParams"
        })
    }
}

#[derive(Debug, Deserialize, JsonSchema)]
pub struct GetOnboardingParams {
    #[schemars(description = "Agent name")]
    pub agent_name: String,
    #[schemars(description = "Session ID (optional)")]
    #[serde(default)]
    pub session_id: Option<String>,
    #[schemars(description = "Round ID (optional)")]
    #[serde(default)]
    pub round_id: Option<String>,
}

#[derive(Debug, Deserialize, JsonSchema)]
pub struct GetBriefingParams {
    #[schemars(description = "Session ID")]
    pub session_id: String,
    #[schemars(description = "Agent name (optional, for agent-specific briefing)")]
    #[serde(default)]
    pub agent_name: Option<String>,
}

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
pub struct BatchRespondParams {
    #[schemars(description = "Round ID for this feedback phase")]
    pub round_id: String,
    #[schemars(description = "Agent name providing the verdicts")]
    pub agent_name: String,
    #[schemars(description = "Array of verdicts, one per feedback item")]
    pub verdicts: Vec<VerdictItem>,
}

#[derive(Debug, Deserialize, JsonSchema)]
pub struct VerdictItem {
    #[schemars(description = "Feedback item ID")]
    pub item_id: String,
    #[schemars(description = "Verdict: accept, reject, modify, abstain")]
    pub verdict: String,
    #[schemars(description = "Reasoning behind the verdict")]
    pub reasoning: String,
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
pub struct ListToolGuidesParams {
    #[schemars(description = "Filter by phase: phase1, phase2, phase3")]
    #[serde(default)]
    pub phase: Option<String>,
}

#[derive(Debug, Deserialize, JsonSchema)]
pub struct GetRoleParams {
    #[schemars(description = "Session ID")]
    pub session_id: String,
    #[schemars(description = "Agent name")]
    pub agent_name: String,
}

#[derive(Debug, Deserialize, JsonSchema)]
pub struct RoundIdParams {
    #[schemars(description = "Round ID")]
    pub round_id: String,
}

#[derive(Debug, Deserialize, JsonSchema)]
pub struct SessionIdParams {
    #[schemars(description = "Session ID")]
    pub session_id: String,
}

#[derive(Debug, Deserialize, JsonSchema)]
pub struct GetResponseParams {
    #[schemars(description = "Round ID")]
    pub round_id: String,
    #[schemars(description = "Agent name")]
    pub agent_name: String,
}

#[derive(Debug, Deserialize, JsonSchema)]
pub struct UpdateQualityParams {
    #[schemars(description = "Response ID to update")]
    pub response_id: String,
    #[schemars(description = "Quality classification: valid, invalid, or empty")]
    pub quality: String,
}

// ---------------------------------------------------------------------------
// AgentServer
// ---------------------------------------------------------------------------

#[derive(Clone)]
pub struct AgentServer {
    db: Arc<Mutex<BrainstormDb>>,
    config: BTreeMap<String, AgentConfig>,
    tool_router: ToolRouter<Self>,
}

#[tool_router]
impl AgentServer {
    // -----------------------------------------------------------------------
    // Onboarding & Briefing
    // -----------------------------------------------------------------------

    #[tool(
        description = "Get onboarding information — agent definition, workflow, tools, session context, and current task"
    )]
    fn bs_get_onboarding(&self, Parameters(params): Parameters<GetOnboardingParams>) -> String {
        let db = self.db.lock().unwrap();

        let agent_def = db.get_agent_definition(&params.agent_name).ok().flatten();
        let workflow = db
            .get_workflow_template("multi-ai-brainstorm")
            .ok()
            .flatten();
        let tool_guides = db.list_tool_guides(None).unwrap_or_default();

        let mut session_data = serde_json::json!(null);
        let mut task_data = serde_json::json!(null);

        if let Some(ref sid_str) = params.session_id {
            let sid = SessionId::from(sid_str.as_str());
            let session = db.get_session(&sid).ok().flatten();
            let role = db.get_role(&sid, &params.agent_name).ok().flatten();
            let guidelines = db.list_guidelines(&sid).unwrap_or_default();
            let rounds = db.list_rounds(&sid).unwrap_or_default();
            let latest_round = rounds.last();

            // If a round_id is provided, get the question from that round
            if let Some(ref rid_str) = params.round_id {
                let rid = RoundId::from(rid_str.as_str());
                if let Ok(Some(round)) = db.get_round(&rid) {
                    task_data = serde_json::json!({
                        "round_id": round.id.as_str(),
                        "round_number": round.round_number,
                        "question": round.question,
                        "objective": round.objective,
                    });
                }
            }

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
            "task": task_data,
        }))
    }

    #[tool(
        description = "Get a session briefing — session info, context, rounds summary, and roles"
    )]
    fn bs_get_briefing(&self, Parameters(params): Parameters<GetBriefingParams>) -> String {
        let db = self.db.lock().unwrap();
        let sid = SessionId::from(params.session_id.as_str());

        let session = match db.get_session(&sid) {
            Ok(Some(s)) => s,
            Ok(None) => {
                return json_result(&serde_json::json!({
                    "error": format!("Session '{}' not found", params.session_id)
                }));
            }
            Err(e) => return json_result(&serde_json::json!({"error": e.to_string()})),
        };

        let rounds = db.list_rounds(&sid).unwrap_or_default();
        let roles = db.list_roles(&sid).unwrap_or_default();
        let guidelines = db.list_guidelines(&sid).unwrap_or_default();

        let mut agent_role = serde_json::json!(null);
        if let Some(ref agent) = params.agent_name {
            agent_role = db
                .get_role(&sid, agent)
                .ok()
                .flatten()
                .map(|r| serde_json::json!(r))
                .unwrap_or(serde_json::json!(null));
        }

        json_result(&serde_json::json!({
            "session": session,
            "rounds_count": rounds.len(),
            "rounds": rounds,
            "all_roles": roles,
            "agent_role": agent_role,
            "guidelines": guidelines,
        }))
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

    #[tool(description = "Get all responses for a round")]
    fn bs_get_round_responses(&self, Parameters(params): Parameters<RoundIdParams>) -> String {
        let db = self.db.lock().unwrap();
        let rid = RoundId::from(params.round_id.as_str());
        match db.get_round_responses(&rid) {
            Ok(responses) => json_result(&responses),
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

    #[tool(description = "Update quality classification for a response (used by validator agent)")]
    fn bs_update_quality(&self, Parameters(params): Parameters<UpdateQualityParams>) -> String {
        let quality = match params.quality.parse::<ResponseQuality>() {
            Ok(q) => q,
            Err(e) => return json_result(&serde_json::json!({"error": e})),
        };
        let db = self.db.lock().unwrap();
        let rid = ResponseId::from(params.response_id.as_str());
        match db.update_response_quality(&rid, &quality) {
            Ok(()) => json_result(&serde_json::json!({
                "updated": true,
                "response_id": params.response_id,
                "quality": params.quality,
            })),
            Err(e) => json_result(&serde_json::json!({"error": e.to_string()})),
        }
    }

    // -----------------------------------------------------------------------
    // Feedback tools
    // -----------------------------------------------------------------------

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
        description = "Submit verdicts on ALL feedback items in one call. Preferred over calling bs_respond_to_feedback per item."
    )]
    fn bs_batch_respond(
        &self,
        Parameters(params): Parameters<BatchRespondParams>,
    ) -> String {
        let db = self.db.lock().unwrap();
        let rid = RoundId::from(params.round_id.as_str());
        let mut saved = 0u32;
        let mut errors = Vec::new();

        for v in &params.verdicts {
            let fid = FeedbackId::from(v.item_id.as_str());
            match db.save_feedback_response(&fid, &rid, &params.agent_name, &v.verdict, &v.reasoning)
            {
                Ok(_) => saved += 1,
                Err(e) => errors.push(serde_json::json!({
                    "item_id": v.item_id,
                    "error": e.to_string(),
                })),
            }
        }

        json_result(&serde_json::json!({
            "saved": saved,
            "errors": errors,
            "total": params.verdicts.len(),
        }))
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

    // -----------------------------------------------------------------------
    // Read-only session data
    // -----------------------------------------------------------------------

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

    #[tool(description = "List all rounds in a session")]
    fn bs_list_rounds(&self, Parameters(params): Parameters<SessionIdParams>) -> String {
        let db = self.db.lock().unwrap();
        let sid = SessionId::from(params.session_id.as_str());
        match db.list_rounds(&sid) {
            Ok(rounds) => json_result(&rounds),
            Err(e) => json_result(&serde_json::json!({"error": e.to_string()})),
        }
    }
}

// ---------------------------------------------------------------------------
// ServerHandler (MCP protocol)
// ---------------------------------------------------------------------------

#[tool_handler]
impl ServerHandler for AgentServer {
    fn get_info(&self) -> ServerInfo {
        ServerInfo::new(ServerCapabilities::builder().enable_tools().build())
            .with_server_info(Implementation::new("ai-collab-agent", "0.1.0"))
            .with_instructions("ai-collab agent server: brainstorm tools for participating agents")
    }
}

impl AgentServer {
    pub fn new(db: BrainstormDb, config: BTreeMap<String, AgentConfig>) -> Self {
        Self {
            db: Arc::new(Mutex::new(db)),
            config,
            tool_router: Self::tool_router(),
        }
    }
}
