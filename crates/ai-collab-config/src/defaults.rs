//! Built-in agent defaults (used when no config file is found).

use crate::types::AgentToml;
use std::collections::BTreeMap;

/// Returns the built-in agent definitions matching BUILTIN_AGENTS in config.py.
pub fn builtin_agents() -> BTreeMap<String, AgentToml> {
    let mut agents = BTreeMap::new();

    agents.insert(
        "copilot".into(),
        AgentToml {
            command: "copilot".into(),
            args: vec![
                "-p".into(),
                "{prompt}".into(),
                "--allow-all".into(),
                "--allow-tool".into(),
                "brainstorm".into(),
                "atlas".into(),
            ],
            model: None,
            timeout: None,
            enabled: Some(true),
            display_name: Some("GitHub Copilot".into()),
            description: Some("Code analysis, shell commands, git operations, GitHub CLI".into()),
            max_auto_retries: None,
        },
    );

    agents.insert(
        "gemini".into(),
        AgentToml {
            command: "gemini".into(),
            args: vec![
                "-p".into(),
                "{prompt}".into(),
                "--yolo".into(),
                "--allowed-mcp-server-names".into(),
                "brainstorm".into(),
            ],
            model: None,
            timeout: None,
            enabled: Some(true),
            display_name: Some("Google Gemini".into()),
            description: Some(
                "Architecture analysis, research, alternative approaches, documentation".into(),
            ),
            max_auto_retries: None,
        },
    );

    agents.insert(
        "codex".into(),
        AgentToml {
            command: "codex".into(),
            args: vec![
                "exec".into(),
                "-p".into(),
                "{prompt}".into(),
                "--full-auto".into(),
            ],
            model: None,
            timeout: None,
            enabled: Some(false), // opt-in
            display_name: Some("OpenAI Codex".into()),
            description: Some(
                "Code generation, file editing, implementing specs from plans".into(),
            ),
            max_auto_retries: None,
        },
    );

    agents
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn builtin_agents_has_three_entries() {
        let agents = builtin_agents();
        assert_eq!(agents.len(), 3);
        assert!(agents.contains_key("copilot"));
        assert!(agents.contains_key("gemini"));
        assert!(agents.contains_key("codex"));
    }

    #[test]
    fn codex_is_disabled_by_default() {
        let agents = builtin_agents();
        assert_eq!(agents["codex"].enabled, Some(false));
    }

    #[test]
    fn copilot_has_correct_args() {
        let agents = builtin_agents();
        assert!(agents["copilot"].args.contains(&"{prompt}".to_string()));
    }
}
