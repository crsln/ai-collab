//! Config file discovery and loading with hierarchy:
//! 1. AI_COLLAB_CONFIG env var
//! 2. ./ai-collab.toml (project-local / CWD)
//! 3. Platform config dir (%APPDATA% on Windows, ~/.config on Linux)
//! 4. Built-in defaults

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use ai_collab_core::ConfigError;

use crate::defaults::builtin_agents;
use crate::types::{AgentConfig, AppConfig};

/// Find the config file following the hierarchy.
fn find_config_file() -> Option<PathBuf> {
    // 1. Explicit env var
    if let Ok(path) = std::env::var("AI_COLLAB_CONFIG") {
        let p = PathBuf::from(&path);
        if p.is_file() {
            return Some(p);
        }
    }

    // 2. Project-local (CWD)
    let cwd_config = PathBuf::from("ai-collab.toml");
    if cwd_config.is_file() {
        return Some(cwd_config);
    }

    // 3. Platform config directory
    if let Some(config_dir) = dirs::config_dir() {
        let user_config = config_dir.join("ai-collab").join("config.toml");
        if user_config.is_file() {
            return Some(user_config);
        }
    }

    None
}

/// Parse a TOML config file into AppConfig.
fn parse_toml(path: &Path) -> Result<AppConfig, ConfigError> {
    let content = std::fs::read_to_string(path)?;
    toml::from_str(&content).map_err(|e| ConfigError::Parse(e.to_string()))
}

/// Load configuration: file → built-in defaults.
/// Returns (AppConfig, resolved AgentConfigs).
pub fn load_config() -> Result<(AppConfig, BTreeMap<String, AgentConfig>), ConfigError> {
    let app_config = match find_config_file() {
        Some(path) => parse_toml(&path)?,
        None => {
            // No config file — use built-in defaults
            AppConfig {
                agents: builtin_agents(),
                ..AppConfig::default()
            }
        }
    };

    // Resolve TOML agent definitions → AgentConfig
    let default_timeout = app_config.settings.default_timeout;
    let agents: BTreeMap<String, AgentConfig> = app_config
        .agents
        .iter()
        .map(|(name, toml)| {
            let config = AgentConfig::from_toml(name, toml, default_timeout);
            (name.clone(), config)
        })
        .collect();

    Ok((app_config, agents))
}

/// Get only enabled agents.
pub fn get_enabled_agents(
    agents: &BTreeMap<String, AgentConfig>,
) -> BTreeMap<String, &AgentConfig> {
    agents
        .iter()
        .filter(|(_, a)| a.enabled)
        .map(|(k, v)| (k.clone(), v))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn load_config_without_file_uses_defaults() {
        // Remove env var to ensure we get defaults
        // SAFETY: test runs are single-threaded for this specific test
        unsafe { std::env::remove_var("AI_COLLAB_CONFIG") };
        let (config, agents) = load_config().unwrap();
        assert_eq!(config.settings.default_timeout, 900.0);
        assert!(agents.contains_key("copilot"));
        assert!(agents.contains_key("gemini"));
        assert!(agents.contains_key("codex"));
    }

    #[test]
    fn get_enabled_filters_disabled() {
        let (_, agents) = load_config().unwrap();
        let enabled = get_enabled_agents(&agents);
        // codex is disabled by default
        assert!(!enabled.contains_key("codex"));
        assert!(enabled.contains_key("copilot"));
        assert!(enabled.contains_key("gemini"));
    }

    #[test]
    fn parse_toml_string() {
        let toml_str = r#"
[settings]
db_path = "test.db"
default_timeout = 300

[agents.test_agent]
command = "test-cli"
args = ["-p", "{prompt}"]
display_name = "Test Agent"
enabled = true
"#;
        let config: AppConfig = toml::from_str(toml_str).unwrap();
        assert_eq!(config.settings.default_timeout, 300.0);
        assert!(config.agents.contains_key("test_agent"));
        assert_eq!(config.agents["test_agent"].command, "test-cli");
    }

    #[test]
    fn parse_toml_minimal() {
        let toml_str = r#"
[agents.simple]
command = "simple-cli"
"#;
        let config: AppConfig = toml::from_str(toml_str).unwrap();
        assert!(config.agents.contains_key("simple"));
        // Default args
        assert_eq!(
            config.agents["simple"].args,
            vec!["-p".to_string(), "{prompt}".to_string()]
        );
    }
}
