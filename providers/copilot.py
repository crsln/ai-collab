"""GitHub Copilot CLI adapter — extends GenericCLIProvider with Copilot-specific output parsing."""

from __future__ import annotations

from config import AgentConfig
from providers.generic import GenericCLIProvider


class CopilotProvider(GenericCLIProvider):
    """Adapter for GitHub Copilot CLI with custom output cleanup.

    Copilot CLI appends a "Total usage est:" footer that needs stripping.
    """

    def __init__(self, agent_config: AgentConfig | None = None):
        if agent_config is None:
            agent_config = AgentConfig(
                name="copilot",
                command="copilot",
                args=["-p", "{prompt}", "--allow-all", "--allow-tool", "brainstorm", "atlas"],
                display_name="GitHub Copilot",
                description="Code analysis, shell commands, git operations, GitHub CLI",
            )
        super().__init__(agent_config)

    def _clean_output(self, output: str) -> str:
        """Strip ANSI codes and Copilot's usage footer."""
        output = super()._clean_output(output)
        lines = output.split("\n")
        content_lines = []
        for line in lines:
            if line.strip().startswith("Total usage est:"):
                break
            content_lines.append(line)
        return "\n".join(content_lines).strip()
