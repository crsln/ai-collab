"""Abstract base class for AI provider adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod


class Provider(ABC):
    """Interface for AI CLI providers (Claude, Copilot, Gemini, etc.)."""

    name: str
    pane_id: int | None = None

    @abstractmethod
    async def send(self, message: str) -> None:
        """Send a message/prompt to the provider's terminal pane."""

    @abstractmethod
    async def read_response(self, timeout: float = 60) -> str:
        """Read the provider's response. Blocks until complete or timeout."""

    @abstractmethod
    def is_ready(self) -> bool:
        """Check if the provider pane is active and ready for input."""
