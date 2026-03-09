"""Configuration for the AI Collab MCP server and providers."""

from pathlib import Path

# Claude
CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"
CLAUDE_RESPONSE_TIMEOUT = 120  # seconds
CLAUDE_POLL_INTERVAL = 0.5  # seconds
CLAUDE_IDLE_THRESHOLD = 3.0  # seconds of no new content = response complete

# Copilot
COPILOT_RESPONSE_TIMEOUT = 600  # seconds

# Gemini
GEMINI_RESPONSE_TIMEOUT = 600  # seconds
