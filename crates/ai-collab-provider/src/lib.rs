//! ai-collab-provider: CLI subprocess adapters for AI agents.
//!
//! Provides GenericCLIProvider (config-driven) with specialized subclasses
//! for Copilot, Gemini, etc. Uses tokio::process for async subprocess management.

pub mod generic;
pub mod registry;

pub use generic::{CopilotProvider, GeminiProvider, GenericCLIProvider};
pub use registry::{get_provider, AgentRunConfig, ProviderInstance};
