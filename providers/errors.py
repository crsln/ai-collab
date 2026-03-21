"""Typed exception hierarchy for AI provider errors."""


class ProviderError(Exception):
    """Base class for all provider errors."""

    def __init__(self, provider: str, message: str):
        self.provider = provider
        super().__init__(message)


class ProviderUnavailable(ProviderError):
    """CLI tool is not installed or not found in PATH."""


class ProviderTimeout(ProviderError):
    """CLI tool did not respond within the timeout."""

    def __init__(self, provider: str, timeout: float):
        self.timeout = timeout
        super().__init__(provider, f"timed out after {timeout}s")


class ProviderExecution(ProviderError):
    """CLI tool exited with a non-zero return code."""

    def __init__(self, provider: str, returncode: int, stderr: str = ""):
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(provider, f"exited with code {returncode}: {stderr}")
