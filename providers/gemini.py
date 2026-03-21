"""Google Gemini CLI adapter — extends GenericCLIProvider with Gemini-specific handling."""

from __future__ import annotations

from config import AgentConfig
from providers.generic import GenericCLIProvider


class GeminiProvider(GenericCLIProvider):
    """Adapter for Google Gemini CLI.

    Currently identical to GenericCLIProvider but kept as a subclass
    for future Gemini-specific output parsing needs.
    """

    def __init__(self, agent_config: AgentConfig | None = None):
        if agent_config is None:
            agent_config = AgentConfig(
                name="gemini",
                command="gemini",
                args=["-p", "{prompt}", "--yolo"],
                display_name="Google Gemini",
                description="Architecture analysis, research, alternative approaches, documentation",
            )
        super().__init__(agent_config)
