"""Google Gemini CLI adapter — direct subprocess mode only."""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import subprocess
import sys

from config import GEMINI_RESPONSE_TIMEOUT
from providers.base import Provider
from providers.errors import ProviderTimeout, ProviderUnavailable

log = logging.getLogger("ai-collab.gemini")

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]|\x1b\].*?\x07|\x1b[()][A-Z0-9]")


class GeminiProvider(Provider):
    """Adapter for Google Gemini CLI (`gemini` command)."""

    name = "gemini"

    def __init__(self):
        self._pending_message: str = ""

    @staticmethod
    def _find_cmd() -> str:
        """Find the gemini executable, preferring .cmd on Windows."""
        if sys.platform == "win32":
            cmd_path = shutil.which("gemini.cmd") or shutil.which("gemini")
            if cmd_path:
                return cmd_path
        found = shutil.which("gemini")
        if found:
            return found
        raise ProviderUnavailable("gemini", "executable not found in PATH")

    async def send(self, message: str) -> None:
        self._pending_message = message

    async def read_response(self, timeout: float | None = None) -> str:
        """Run gemini as a subprocess and return its output."""
        timeout = timeout or GEMINI_RESPONSE_TIMEOUT
        cmd = self._find_cmd()
        proc = await asyncio.create_subprocess_exec(
            cmd, "-p", self._pending_message,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise ProviderTimeout("gemini", timeout)

        output = stdout.decode("utf-8", errors="replace")
        output = _ANSI_RE.sub("", output)
        return output.strip()

    def is_ready(self) -> bool:
        try:
            cmd = self._find_cmd()
            result = subprocess.run(
                [cmd, "--version"],
                capture_output=True, text=True, timeout=5,
            )
            return result.returncode == 0
        except (ProviderUnavailable, FileNotFoundError, subprocess.TimeoutExpired):
            return False
