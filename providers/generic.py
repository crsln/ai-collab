"""Generic CLI provider — config-driven subprocess adapter for any AI CLI tool."""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import subprocess
import sys

from config import AgentConfig, DEFAULT_AGENT_TIMEOUT
from providers.base import Provider
from providers.errors import ProviderExecution, ProviderTimeout, ProviderUnavailable

log = logging.getLogger("ai-collab.generic")

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]|\x1b\].*?\x07|\x1b[()][A-Z0-9]")


class GenericCLIProvider(Provider):
    """Config-driven adapter for any AI CLI tool.

    Reads agent configuration (command, args, timeout) and executes
    the CLI as a subprocess. Works with any CLI that accepts a prompt
    via command-line arguments or stdin.
    """

    def __init__(self, agent_config: AgentConfig):
        self._config = agent_config
        self.name = agent_config.name
        self._pending_message: str = ""

    @property
    def config(self) -> AgentConfig:
        return self._config

    def _find_cmd(self) -> str:
        """Find the CLI executable, preferring .cmd shims on Windows."""
        name = self._config.command
        if sys.platform == "win32":
            cmd = shutil.which(f"{name}.cmd") or shutil.which(name)
            if cmd:
                return cmd
        found = shutil.which(name)
        if found:
            return found
        raise ProviderUnavailable(name, f"executable '{name}' not found in PATH")

    def _clean_output(self, output: str) -> str:
        """Strip ANSI codes from output. Subclasses can override for CLI-specific cleanup."""
        return _ANSI_RE.sub("", output).strip()

    async def send(self, message: str) -> None:
        self._pending_message = message

    async def read_response(self, timeout: float | None = None, cwd: str | None = None) -> str:
        """Run the CLI as a subprocess and return cleaned output.

        Uses asyncio.create_subprocess_exec (not shell) to avoid injection.
        All arguments are passed as a list, never through a shell.
        """
        timeout = timeout or self._config.timeout or DEFAULT_AGENT_TIMEOUT
        cmd = self._find_cmd()

        args = [cmd] + self._config.build_args(self._pending_message)

        # Pipe long prompts via stdin on Windows to avoid 8191-char limit
        stdin_input = None
        if sys.platform == "win32" and len(self._pending_message) > 7000:
            # Rebuild args without the prompt (replace {prompt} with empty)
            args = [cmd]
            for arg in self._config.args:
                if "{prompt}" in arg:
                    continue  # skip prompt arg, will pipe via stdin
                args.append(arg)
            if self._config.model:
                args.extend(["--model", self._config.model])
            stdin_input = self._pending_message.encode("utf-8")

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE if stdin_input else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=stdin_input), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise ProviderTimeout(self.name, timeout)

        if proc.returncode != 0:
            err_text = stderr.decode("utf-8", errors="replace").strip() if stderr else ""
            raise ProviderExecution(self.name, proc.returncode, err_text)

        output = stdout.decode("utf-8", errors="replace")
        return self._clean_output(output)

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
