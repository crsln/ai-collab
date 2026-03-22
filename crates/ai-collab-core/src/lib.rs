//! ai-collab-core: Domain types, IDs, enums, errors, and traits.
//!
//! This crate has ZERO I/O dependencies — only serde, chrono, uuid, thiserror, regex.
//! All other crates in the workspace depend on this one.

pub mod enums;
pub mod error;
pub mod ids;
pub mod models;
pub mod traits;
pub mod validation;

// Re-export commonly used types at crate root.
pub use enums::*;
pub use error::*;
pub use ids::*;
pub use models::*;
pub use validation::validate_heuristic;
