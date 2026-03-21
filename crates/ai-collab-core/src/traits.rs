//! Shared traits for the ai-collab workspace.

use crate::error::ProviderError;
use std::sync::OnceLock;

/// Trait for CLI agent providers.
///
/// Implemented by GenericCLIProvider and specialized variants (Copilot, Gemini).
/// Uses trait objects (`Box<dyn Provider>`) for runtime-configured agent registry.
pub trait Provider: Send + Sync {
    /// Execute a prompt and return the agent's response.
    fn execute(
        &self,
        prompt: &str,
        cwd: Option<&str>,
    ) -> impl std::future::Future<Output = Result<String, ProviderError>> + Send;

    /// Check if the provider's CLI executable is available.
    fn is_ready(&self) -> bool;

    /// Get the agent name this provider handles.
    fn agent_name(&self) -> &str;
}

/// Extension trait for stripping ANSI escape codes from CLI output.
pub trait AnsiStrip {
    fn strip_ansi(&self) -> String;
}

impl AnsiStrip for str {
    fn strip_ansi(&self) -> String {
        static RE: OnceLock<regex::Regex> = OnceLock::new();
        let re = RE.get_or_init(|| {
            regex::Regex::new(r"\x1b\[[0-9;]*[a-zA-Z]|\x1b\].*?\x07|\x1b[()][A-Z0-9]").unwrap()
        });
        re.replace_all(self, "").to_string()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn strip_ansi_removes_color_codes() {
        let input = "\x1b[32mSuccess\x1b[0m: done";
        assert_eq!(input.strip_ansi(), "Success: done");
    }

    #[test]
    fn strip_ansi_preserves_clean_text() {
        let input = "No escape codes here";
        assert_eq!(input.strip_ansi(), "No escape codes here");
    }

    #[test]
    fn strip_ansi_handles_complex_sequences() {
        let input = "\x1b[1;34mBold Blue\x1b[0m \x1b]0;title\x07 text";
        assert_eq!(input.strip_ansi(), "Bold Blue  text");
    }
}
