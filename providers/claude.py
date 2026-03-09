"""Claude Code CLI adapter — reads responses via JSONL session polling."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

from config import (
    CLAUDE_IDLE_THRESHOLD,
    CLAUDE_POLL_INTERVAL,
    CLAUDE_PROJECTS_DIR,
    CLAUDE_RESPONSE_TIMEOUT,
)
from providers.base import Provider

log = logging.getLogger("ai-collab.claude")


class ClaudeProvider(Provider):
    """Adapter for Claude Code CLI sessions."""

    name = "claude"

    def __init__(self, project_dir: str):
        self.project_dir = project_dir
        self._jsonl_path: Path | None = None
        self._file_offset: int = 0

    def _discover_session_file(self) -> Path | None:
        """Find the latest JSONL session file for the current project directory."""
        if not CLAUDE_PROJECTS_DIR.exists():
            return None

        candidates: list[Path] = []
        for project_hash_dir in CLAUDE_PROJECTS_DIR.iterdir():
            if not project_hash_dir.is_dir():
                continue
            for jsonl_file in project_hash_dir.glob("*.jsonl"):
                candidates.append(jsonl_file)

        if not candidates:
            return None

        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return candidates[0]

    def _seek_to_end(self) -> None:
        """Set the file offset to the current end of the JSONL file."""
        if self._jsonl_path and self._jsonl_path.exists():
            self._file_offset = self._jsonl_path.stat().st_size

    def _read_new_lines(self) -> list[dict]:
        """Read new complete lines from the JSONL file since last offset.

        Only advances the offset past lines that end with a newline,
        preventing data loss from partial writes.
        """
        if not self._jsonl_path or not self._jsonl_path.exists():
            return []

        entries = []
        with open(self._jsonl_path, "r", encoding="utf-8") as f:
            f.seek(self._file_offset)
            while True:
                line = f.readline()
                if not line:
                    break
                # Don't advance past incomplete lines (no trailing newline)
                if not line.endswith("\n"):
                    break
                line = line.strip()
                if not line:
                    self._file_offset = f.tell()
                    continue
                try:
                    entries.append(json.loads(line))
                    self._file_offset = f.tell()
                except json.JSONDecodeError:
                    log.warning("Skipping malformed JSON line at offset %d", self._file_offset)
                    self._file_offset = f.tell()
        return entries

    def _extract_assistant_text(self, entries: list[dict]) -> str:
        """Extract assistant message text from JSONL entries."""
        texts = []
        for entry in entries:
            if entry.get("type") == "assistant":
                msg = entry.get("message", {})
                content = msg.get("content", [])
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        texts.append(block["text"])
                    elif isinstance(block, str):
                        texts.append(block)
        return "\n".join(texts)

    async def send(self, message: str) -> None:
        """Prepare to read Claude's response (JSONL polling mode)."""
        self._jsonl_path = self._discover_session_file()
        self._seek_to_end()

    async def read_response(self, timeout: float | None = None) -> str:
        """Poll JSONL for Claude's response until idle or timeout."""
        timeout = timeout or CLAUDE_RESPONSE_TIMEOUT
        start = time.monotonic()
        last_content_time = None
        accumulated_text = ""

        while True:
            elapsed = time.monotonic() - start
            if elapsed > timeout:
                if accumulated_text:
                    return accumulated_text
                raise TimeoutError(
                    f"Claude did not respond within {timeout}s"
                )

            new_entries = self._read_new_lines()
            new_text = self._extract_assistant_text(new_entries)

            if new_text:
                if accumulated_text:
                    accumulated_text += "\n"
                accumulated_text += new_text
                last_content_time = time.monotonic()

            if last_content_time is not None:
                idle_duration = time.monotonic() - last_content_time
                if idle_duration >= CLAUDE_IDLE_THRESHOLD:
                    return accumulated_text

            await asyncio.sleep(CLAUDE_POLL_INTERVAL)

    def is_ready(self) -> bool:
        """Check if a Claude session file exists."""
        self._jsonl_path = self._discover_session_file()
        return self._jsonl_path is not None
