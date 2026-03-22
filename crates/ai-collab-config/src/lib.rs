//! ai-collab-config: TOML config loading with hierarchy.
//!
//! Loads agent definitions from ai-collab.toml with fallback to built-in defaults.
//! Config hierarchy: env var → project dir → user config dir → defaults.

pub mod defaults;
pub mod loader;
pub mod types;

pub use loader::{get_enabled_agents, load_config};
pub use types::{AgentConfig, AppConfig};
