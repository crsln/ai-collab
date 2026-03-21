//! ai-collab-db: SQLite persistence layer for the brainstorm system.
//!
//! Wraps rusqlite (with bundled SQLite) to provide typed CRUD operations
//! matching the Python `brainstorm_db.py` interface.

pub mod queries;
pub mod schema;
pub mod seeds;

pub use queries::BrainstormDb;
