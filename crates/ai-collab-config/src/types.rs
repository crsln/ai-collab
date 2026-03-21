//! Configuration types for ai-collab.

use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::path::PathBuf;

/// Top-level TOML configuration (deserialized from ai-collab.toml).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[derive(Default)]
pub struct AppConfig {
    #[serde(default)]
    pub settings: Settings,
    #[serde(default)]
    pub agents: BTreeMap<String, AgentToml>,
}


/// Global settings section.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Settings {
    /// Path to brainstorm SQLite database.
    #[serde(default = "default_db_path")]
    pub db_path: PathBuf,
    /// Default timeout for agent subprocesses (seconds).
    #[serde(default = "default_timeout")]
    pub default_timeout: f64,
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            db_path: default_db_path(),
            default_timeout: default_timeout(),
        }
    }
}

fn default_db_path() -> PathBuf {
    PathBuf::from(".data/brainstorm.db")
}

fn default_timeout() -> f64 {
    900.0
}

/// Agent definition as it appears in TOML.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentToml {
    pub command: String,
    #[serde(default = "default_args")]
    pub args: Vec<String>,
    #[serde(default)]
    pub model: Option<String>,
    #[serde(default)]
    pub timeout: Option<f64>,
    #[serde(default = "default_true")]
    pub enabled: Option<bool>,
    #[serde(default)]
    pub display_name: Option<String>,
    #[serde(default)]
    pub description: Option<String>,
}

fn default_args() -> Vec<String> {
    vec!["-p".into(), "{prompt}".into()]
}

fn default_true() -> Option<bool> {
    Some(true)
}

/// Resolved agent configuration (after merging TOML + defaults).
#[derive(Debug, Clone)]
pub struct AgentConfig {
    pub name: String,
    pub command: String,
    pub args: Vec<String>,
    pub model: String,
    pub timeout: f64,
    pub enabled: bool,
    pub display_name: String,
    pub description: String,
}

impl AgentConfig {
    /// Build CLI arguments, replacing {prompt} placeholder.
    pub fn build_args(&self, prompt: &str) -> Vec<String> {
        let mut result: Vec<String> = self
            .args
            .iter()
            .map(|a| a.replace("{prompt}", prompt))
            .collect();
        if !self.model.is_empty() {
            result.extend(["--model".into(), self.model.clone()]);
        }
        result
    }

    /// Resolve from TOML agent definition with defaults.
    pub fn from_toml(name: &str, toml: &AgentToml, default_timeout: f64) -> Self {
        Self {
            name: name.to_string(),
            command: toml.command.clone(),
            args: toml.args.clone(),
            model: toml.model.clone().unwrap_or_default(),
            timeout: toml.timeout.unwrap_or(default_timeout),
            enabled: toml.enabled.unwrap_or(true),
            display_name: toml
                .display_name
                .clone()
                .unwrap_or_else(|| capitalize(name)),
            description: toml.description.clone().unwrap_or_default(),
        }
    }
}

fn capitalize(s: &str) -> String {
    let mut c = s.chars();
    match c.next() {
        None => String::new(),
        Some(f) => f.to_uppercase().to_string() + c.as_str(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn build_args_replaces_prompt() {
        let config = AgentConfig {
            name: "test".into(),
            command: "test-cli".into(),
            args: vec!["-p".into(), "{prompt}".into(), "--flag".into()],
            model: String::new(),
            timeout: 900.0,
            enabled: true,
            display_name: "Test".into(),
            description: String::new(),
        };
        let args = config.build_args("hello world");
        assert_eq!(args, vec!["-p", "hello world", "--flag"]);
    }

    #[test]
    fn build_args_adds_model_flag() {
        let config = AgentConfig {
            name: "test".into(),
            command: "test-cli".into(),
            args: vec!["-p".into(), "{prompt}".into()],
            model: "gpt-4".into(),
            timeout: 900.0,
            enabled: true,
            display_name: "Test".into(),
            description: String::new(),
        };
        let args = config.build_args("hi");
        assert_eq!(args, vec!["-p", "hi", "--model", "gpt-4"]);
    }

    #[test]
    fn from_toml_with_defaults() {
        let toml = AgentToml {
            command: "copilot".into(),
            args: vec!["-p".into(), "{prompt}".into()],
            model: None,
            timeout: None,
            enabled: None,
            display_name: None,
            description: None,
        };
        let config = AgentConfig::from_toml("copilot", &toml, 900.0);
        assert_eq!(config.name, "copilot");
        assert_eq!(config.display_name, "Copilot");
        assert!(config.enabled);
        assert_eq!(config.timeout, 900.0);
    }

    #[test]
    fn capitalize_works() {
        assert_eq!(capitalize("copilot"), "Copilot");
        assert_eq!(capitalize("gemini"), "Gemini");
        assert_eq!(capitalize(""), "");
    }
}
