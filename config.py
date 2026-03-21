"""Configuration for the AI Collab MCP server and providers.

Loads agent definitions from ai-collab.toml (project-local) with fallback
to ~/.config/ai-collab/config.toml (user-level). Secrets go in .env.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Use stdlib tomllib (3.11+) or fallback to tomli
if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError:
        tomllib = None  # type: ignore[assignment]


# ── Defaults ────────────────────────────────────────────────────────────

CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"
CLAUDE_RESPONSE_TIMEOUT = 120  # seconds
CLAUDE_POLL_INTERVAL = 0.5  # seconds
CLAUDE_IDLE_THRESHOLD = 3.0  # seconds of no new content = response complete

DEFAULT_AGENT_TIMEOUT = 900  # 15 minutes — agents with tool access need time
DEFAULT_DB_PATH = Path(__file__).parent / ".data" / "brainstorm.db"


# ── Agent Config ────────────────────────────────────────────────────────

@dataclass
class AgentConfig:
    """Configuration for a single AI CLI agent."""
    name: str
    command: str
    args: list[str] = field(default_factory=lambda: ["-p", "{prompt}"])
    model: str = ""
    timeout: float = DEFAULT_AGENT_TIMEOUT
    enabled: bool = True
    display_name: str = ""
    description: str = ""

    def __post_init__(self):
        if not self.display_name:
            self.display_name = self.name.title()

    def build_args(self, prompt: str) -> list[str]:
        """Build CLI arguments, replacing {prompt} placeholder."""
        result = []
        for arg in self.args:
            result.append(arg.replace("{prompt}", prompt))
        if self.model:
            result.extend(["--model", self.model])
        return result


@dataclass
class AppConfig:
    """Top-level application configuration."""
    db_path: Path = field(default_factory=lambda: DEFAULT_DB_PATH)
    default_timeout: float = DEFAULT_AGENT_TIMEOUT
    agents: dict[str, AgentConfig] = field(default_factory=dict)


# ── Built-in Agent Defaults ────────────────────────────────────────────

BUILTIN_AGENTS: dict[str, dict] = {
    "copilot": {
        "command": "copilot",
        "args": ["-p", "{prompt}", "--allow-all", "--allow-tool", "brainstorm", "atlas"],
        "display_name": "GitHub Copilot",
        "description": "Code analysis, shell commands, git operations, GitHub CLI",
    },
    "gemini": {
        "command": "gemini",
        "args": ["-p", "{prompt}", "--yolo", "--allowed-mcp-server-names", "brainstorm"],
        "display_name": "Google Gemini",
        "description": "Architecture analysis, research, alternative approaches, documentation",
    },
    "codex": {
        "command": "codex",
        "args": ["exec", "-p", "{prompt}", "--full-auto"],
        "enabled": False,  # opt-in: most users won't have codex installed
        "display_name": "OpenAI Codex",
        "description": "Code generation, file editing, implementing specs from plans",
    },
}


# ── Config Loading ──────────────────────────────────────────────────────

def _find_config_file() -> Path | None:
    """Find ai-collab.toml: check env var, project-local, then user-level."""
    # 1. Explicit env var
    env_path = os.environ.get("AI_COLLAB_CONFIG")
    if env_path:
        p = Path(env_path)
        if p.is_file():
            return p

    # 2. Project-local (same dir as this script)
    local = Path(__file__).parent / "ai-collab.toml"
    if local.is_file():
        return local

    # 3. User-level config
    if sys.platform == "win32":
        user_config = Path.home() / ".config" / "ai-collab" / "config.toml"
    else:
        user_config = Path.home() / ".config" / "ai-collab" / "config.toml"
    if user_config.is_file():
        return user_config

    return None


def load_config() -> AppConfig:
    """Load configuration from TOML file, falling back to built-in defaults."""
    config = AppConfig()

    config_file = _find_config_file()
    if config_file and not tomllib:
        import warnings
        warnings.warn(
            f"Found config file {config_file} but cannot parse TOML. "
            f"Install 'tomli' for Python <3.11: pip install tomli",
            stacklevel=2,
        )
    if config_file and tomllib:
        with open(config_file, "rb") as f:
            data = tomllib.load(f)

        # Load settings
        settings = data.get("settings", {})
        if "db_path" in settings:
            config.db_path = Path(settings["db_path"])
        if "default_timeout" in settings:
            config.default_timeout = float(settings["default_timeout"])

        # Load agents from config
        agents_data = data.get("agents", {})
        for name, agent_data in agents_data.items():
            if not isinstance(agent_data, dict):
                continue
            config.agents[name] = AgentConfig(
                name=name,
                command=agent_data.get("command", name),
                args=agent_data.get("args", ["-p", "{prompt}"]),
                model=agent_data.get("model", ""),
                timeout=float(agent_data.get("timeout", config.default_timeout)),
                enabled=agent_data.get("enabled", True),
                display_name=agent_data.get("display_name", name.title()),
                description=agent_data.get("description", ""),
            )
    elif not config_file:
        # No config file found — use built-in defaults
        for name, defaults in BUILTIN_AGENTS.items():
            config.agents[name] = AgentConfig(name=name, **defaults)

    return config


# ── Module-level singleton ──────────────────────────────────────────────

_config: AppConfig | None = None


def get_config() -> AppConfig:
    """Get the application config (lazy-loaded singleton)."""
    global _config
    if _config is None:
        _config = load_config()
    return _config


def get_enabled_agents() -> dict[str, AgentConfig]:
    """Get only enabled agents from config."""
    return {
        name: agent
        for name, agent in get_config().agents.items()
        if agent.enabled
    }
