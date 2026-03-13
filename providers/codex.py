"""Codex CLI provider — adapter for OpenAI Codex CLI."""

from __future__ import annotations

from config import AgentConfig
from providers.generic import GenericCLIProvider


class CodexProvider(GenericCLIProvider):
    """Adapter for OpenAI Codex CLI. Subclass for future output-cleaning."""

    def __init__(self, agent_config: AgentConfig | None = None):
        if agent_config is None:
            agent_config = AgentConfig(
                name="codex", command="codex",
                args=["-p", "{prompt}", "--full-auto"],
                display_name="OpenAI Codex",
                description="Code generation, file editing, spec implementation",
            )
        super().__init__(agent_config)
