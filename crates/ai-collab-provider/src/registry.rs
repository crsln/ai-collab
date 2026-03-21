//! Provider registry — maps agent names to appropriate provider implementations.

use ai_collab_core::ProviderError;

use crate::generic::{CopilotProvider, GeminiProvider, GenericCLIProvider};

/// Simplified agent config for provider construction.
/// Extracted from ai-collab-config's AgentConfig to avoid circular dependency.
#[derive(Debug, Clone)]
pub struct AgentRunConfig {
    pub name: String,
    pub command: String,
    pub args: Vec<String>,
    pub model: Option<String>,
    pub timeout: f64,
}

impl AgentRunConfig {
    /// Build CLI arguments, replacing {prompt} placeholder.
    pub fn build_args(&self, prompt: &str) -> Vec<String> {
        let mut result: Vec<String> = self
            .args
            .iter()
            .map(|a| a.replace("{prompt}", prompt))
            .collect();
        if let Some(ref model) = self.model
            && !model.is_empty() {
                result.extend(["--model".to_string(), model.clone()]);
            }
        result
    }
}

/// Runtime-dispatched provider that wraps specialized implementations.
pub enum ProviderInstance {
    Generic(GenericCLIProvider),
    Copilot(CopilotProvider),
    Gemini(GeminiProvider),
}

impl ProviderInstance {
    /// Execute a prompt and return the agent's response.
    pub async fn execute(
        &self,
        prompt: &str,
        cwd: Option<&str>,
    ) -> Result<String, ProviderError> {
        match self {
            Self::Generic(p) => p.execute(prompt, cwd).await,
            Self::Copilot(p) => p.execute(prompt, cwd).await,
            Self::Gemini(p) => p.execute(prompt, cwd).await,
        }
    }

    /// Check if the provider's CLI executable is available.
    pub fn is_ready(&self) -> bool {
        match self {
            Self::Generic(p) => p.is_ready(),
            Self::Copilot(p) => p.is_ready(),
            Self::Gemini(p) => p.is_ready(),
        }
    }

    /// Get the agent name this provider handles.
    pub fn agent_name(&self) -> &str {
        match self {
            Self::Generic(p) => p.agent_name(),
            Self::Copilot(p) => p.agent_name(),
            Self::Gemini(p) => p.agent_name(),
        }
    }
}

/// Get the appropriate provider for an agent config.
///
/// Uses specialized implementations for known CLIs (copilot, gemini),
/// falls back to GenericCLIProvider for everything else.
pub fn get_provider(config: AgentRunConfig) -> ProviderInstance {
    match config.name.as_str() {
        "copilot" => ProviderInstance::Copilot(CopilotProvider::new(config)),
        "gemini" => ProviderInstance::Gemini(GeminiProvider::new(config)),
        _ => ProviderInstance::Generic(GenericCLIProvider::new(config)),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn test_config(name: &str) -> AgentRunConfig {
        AgentRunConfig {
            name: name.to_string(),
            command: name.to_string(),
            args: vec!["-p".into(), "{prompt}".into()],
            model: None,
            timeout: 60.0,
        }
    }

    #[test]
    fn build_args_replaces_prompt() {
        let config = test_config("test");
        let args = config.build_args("hello world");
        assert_eq!(args, vec!["-p", "hello world"]);
    }

    #[test]
    fn build_args_adds_model() {
        let config = AgentRunConfig {
            model: Some("gpt-4".into()),
            ..test_config("test")
        };
        let args = config.build_args("hi");
        assert_eq!(args, vec!["-p", "hi", "--model", "gpt-4"]);
    }

    #[test]
    fn get_provider_returns_copilot() {
        let p = get_provider(test_config("copilot"));
        assert!(matches!(p, ProviderInstance::Copilot(_)));
        assert_eq!(p.agent_name(), "copilot");
    }

    #[test]
    fn get_provider_returns_gemini() {
        let p = get_provider(test_config("gemini"));
        assert!(matches!(p, ProviderInstance::Gemini(_)));
    }

    #[test]
    fn get_provider_returns_generic_for_unknown() {
        let p = get_provider(test_config("aider"));
        assert!(matches!(p, ProviderInstance::Generic(_)));
        assert_eq!(p.agent_name(), "aider");
    }
}
