//! Background validator dispatch — spawns a Haiku agent to classify suspect responses.

use std::collections::BTreeMap;
use std::sync::{Arc, Mutex};

use ai_collab_config::AgentConfig;
use ai_collab_core::*;
use ai_collab_db::BrainstormDb;

/// Truncate a string to at most `max_bytes` without splitting a UTF-8 char.
fn truncate_utf8(s: &str, max_bytes: usize) -> &str {
    if s.len() <= max_bytes {
        return s;
    }
    let mut end = max_bytes;
    while end > 0 && !s.is_char_boundary(end) {
        end -= 1;
    }
    &s[..end]
}

/// Dispatch the validator agent in background to classify a suspect response.
pub fn spawn_validator(
    db: Arc<Mutex<BrainstormDb>>,
    config: &BTreeMap<String, AgentConfig>,
    response_id: ResponseId,
    agent_name: String,
    content: String,
) {
    let validator_config = match config.get("validator") {
        Some(cfg) => cfg.clone(),
        None => {
            tracing::warn!("No 'validator' agent configured — skipping Haiku validation");
            return;
        }
    };

    let prompt = format!(
        "You are a response quality validator. Classify the following agent response.\n\n\
         Response ID: {response_id}\n\
         Agent: {agent_name}\n\
         Content:\n---\n{content}\n---\n\n\
         Classify as one of:\n\
         - \"valid\" — genuine analytical response to the question\n\
         - \"invalid\" — error message, failure output, or irrelevant content\n\
         - \"empty\" — no substantive analysis, placeholder, or trivially short\n\n\
         Call bs_update_quality(response_id=\"{response_id}\", quality=\"<your classification>\").",
        response_id = response_id.as_str(),
        agent_name = agent_name,
        content = truncate_utf8(&content, 2000),
    );

    let run_config = ai_collab_provider::AgentRunConfig {
        name: "validator".to_string(),
        command: validator_config.command.clone(),
        args: validator_config.args.clone(),
        model: if validator_config.model.is_empty() {
            None
        } else {
            Some(validator_config.model.clone())
        },
        timeout: validator_config.timeout,
    };

    // The db Arc is passed in but not used directly here — the validator agent
    // calls bs_update_quality via MCP which writes to the DB through the agent server.
    let _ = db;

    tokio::spawn(async move {
        let provider = ai_collab_provider::get_provider(run_config);
        match provider.execute(&prompt, None).await {
            Ok(output) => {
                tracing::info!(
                    "Validator completed for response {}: {}",
                    response_id.as_str(),
                    truncate_utf8(&output, 100),
                );
            }
            Err(e) => {
                tracing::warn!(
                    "Validator failed for response {}: {e}",
                    response_id.as_str()
                );
                // On validator failure, keep as suspect (don't change quality)
                // The orchestrator can decide what to do with suspect responses
            }
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn truncate_utf8_within_limit() {
        assert_eq!(truncate_utf8("hello", 10), "hello");
    }

    #[test]
    fn truncate_utf8_at_boundary() {
        assert_eq!(truncate_utf8("hello world", 5), "hello");
    }

    #[test]
    fn truncate_utf8_multibyte() {
        // Each emoji is 4 bytes
        let s = "\u{1F600}\u{1F601}"; // 8 bytes total
        assert_eq!(truncate_utf8(s, 4).len(), 4); // one emoji
        assert_eq!(truncate_utf8(s, 5).len(), 4); // still one (can't split)
    }

    #[test]
    fn truncate_utf8_empty() {
        assert_eq!(truncate_utf8("", 10), "");
    }
}
