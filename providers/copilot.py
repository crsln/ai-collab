"""GitHub Copilot CLI adapter — direct subprocess mode only."""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import subprocess
import sys

from config import COPILOT_RESPONSE_TIMEOUT
from providers.base import Provider
from providers.errors import ProviderTimeout, ProviderUnavailable

log = logging.getLogger("ai-collab.copilot")

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]|\x1b\].*?\x07|\x1b[()][A-Z0-9]")


class CopilotProvider(Provider):
    """Adapter for GitHub Copilot CLI (`copilot` command)."""

    name = "copilot"

    def __init__(self):
        self._pending_message: str = ""

    @staticmethod
    def _find_cmd() -> str:
        """Find the copilot executable, preferring .cmd on Windows."""
        if sys.platform == "win32":
            cmd_path = shutil.which("copilot.cmd") or shutil.which("copilot")
            if cmd_path:
                return cmd_path
        found = shutil.which("copilot")
        if found:
            return found
        raise ProviderUnavailable("copilot", "executable not found in PATH")

    async def send(self, message: str) -> None:
        self._pending_message = message

    async def read_response(self, timeout: float | None = None) -> str:
        """Run copilot as a subprocess and return its output."""
        timeout = timeout or COPILOT_RESPONSE_TIMEOUT
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
            raise ProviderTimeout("copilot", timeout)

        output = stdout.decode("utf-8", errors="replace")
        output = _ANSI_RE.sub("", output)
        lines = output.strip().split("\n")
        content_lines = []
        for line in lines:
            if line.strip().startswith("Total usage est:"):
                break
            content_lines.append(line)
        return "\n".join(content_lines).strip()

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
