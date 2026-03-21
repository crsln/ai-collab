//! ai-collab: Multi-AI collaboration CLI + MCP servers.

mod agent_server;
mod server;

use std::path::PathBuf;

use clap::{Parser, Subcommand};

use rmcp::ServiceExt;

use ai_collab_config::load_config;
use ai_collab_db::BrainstormDb;

use crate::agent_server::AgentServer;
use crate::server::OrchestratorServer;

#[derive(Parser)]
#[command(name = "ai-collab", about = "Multi-AI collaboration MCP server")]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Start the orchestrator MCP server (stdio transport).
    Serve {
        /// Path to the brainstorm SQLite database.
        #[arg(long, default_value = ".data/brainstorm.db")]
        db: PathBuf,
    },
    /// Start the agent-facing MCP server (stdio transport).
    AgentServe {
        /// Path to the brainstorm SQLite database.
        #[arg(long, default_value = ".data/brainstorm.db")]
        db: PathBuf,
    },
    /// Seed the database with default data.
    SeedDefaults {
        /// Path to the brainstorm SQLite database.
        #[arg(long, default_value = ".data/brainstorm.db")]
        db: PathBuf,
    },
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // All logging goes to stderr — stdout is reserved for the MCP protocol.
    tracing_subscriber::fmt()
        .with_writer(std::io::stderr)
        .with_ansi(false)
        .init();

    let cli = Cli::parse();

    match cli.command {
        Command::Serve { db: db_path } => {
            tracing::info!("Starting orchestrator MCP server");
            let db = BrainstormDb::new(&db_path)?;
            let (_, agents) = load_config().unwrap_or_else(|e| {
                tracing::warn!("Failed to load config, using empty: {e}");
                (
                    ai_collab_config::AppConfig::default(),
                    std::collections::BTreeMap::new(),
                )
            });
            let server = OrchestratorServer::new(db, agents);
            let service = server.serve(rmcp::transport::stdio()).await?;
            service.waiting().await?;
        }
        Command::AgentServe { db: db_path } => {
            tracing::info!("Starting agent-facing MCP server");
            let db = BrainstormDb::new(&db_path)?;
            let server = AgentServer::new(db);
            let service = server.serve(rmcp::transport::stdio()).await?;
            service.waiting().await?;
        }
        Command::SeedDefaults { db: db_path } => {
            let db = BrainstormDb::new(&db_path)?;
            ai_collab_db::seeds::seed_defaults(&db)?;
            eprintln!("Database seeded with defaults.");
        }
    }

    Ok(())
}
