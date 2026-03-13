"""Provider registry — maps agent names to appropriate provider classes."""

from __future__ import annotations

from config import AgentConfig, get_enabled_agents
from .base import Provider
from .codex import CodexProvider
from .copilot import CopilotProvider
from .errors import ProviderError, ProviderExecution, ProviderTimeout, ProviderUnavailable
from .gemini import GeminiProvider
from .generic import GenericCLIProvider

# Known providers with CLI-specific quirks
_KNOWN_PROVIDERS: dict[str, type[GenericCLIProvider]] = {
    "copilot": CopilotProvider,
    "codex": CodexProvider,
    "gemini": GeminiProvider,
}


def get_provider(agent_config: AgentConfig) -> GenericCLIProvider:
    """Get the appropriate provider for an agent config.

    Uses specialized subclass for known CLIs (copilot, gemini),
    falls back to GenericCLIProvider for everything else.
    """
    provider_class = _KNOWN_PROVIDERS.get(agent_config.name, GenericCLIProvider)
    return provider_class(agent_config)


def get_all_providers() -> dict[str, GenericCLIProvider]:
    """Get providers for all enabled agents from config."""
    return {
        name: get_provider(agent_config)
        for name, agent_config in get_enabled_agents().items()
    }


__all__ = [
    "Provider",
    "GenericCLIProvider",
    "CodexProvider",
    "CopilotProvider",
    "GeminiProvider",
    "ProviderError",
    "ProviderExecution",
    "ProviderTimeout",
    "ProviderUnavailable",
    "get_provider",
    "get_all_providers",
]
