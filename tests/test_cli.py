"""Smoke tests for ai_collab_cli.py — CLI entry points."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from ai_collab_cli import _VALID_AGENT_NAME, _write_toml, _detect_agents


class TestAgentNameValidation:
    def test_valid_names(self):
        for name in ["copilot", "gemini", "my-agent", "agent_1", "a"]:
            assert _VALID_AGENT_NAME.match(name), f"{name} should be valid"

    def test_invalid_names(self):
        for name in ["", "1agent", "-bad", "UPPER", "has space", "foo]", "a.b", "a\nb"]:
            assert not _VALID_AGENT_NAME.match(name), f"{name} should be invalid"

    def test_toml_injection_blocked(self):
        """Agent names that could inject TOML should be rejected."""
        malicious = 'foo]\ncommand = "rm -rf /"'
        assert not _VALID_AGENT_NAME.match(malicious)


class TestWriteToml:
    def test_writes_valid_toml(self, tmp_path):
        agents = [{
            "name": "test",
            "command": "test-cli",
            "args": ["-p", "{prompt}"],
            "display_name": "Test Agent",
            "description": "A test agent",
            "enabled": True,
        }]
        path = tmp_path / "test.toml"
        _write_toml(agents, path)
        content = path.read_text()
        assert "[agents.test]" in content
        assert 'command = "test-cli"' in content

    def test_rejects_invalid_name(self, tmp_path):
        agents = [{"name": "bad]name", "command": "x", "args": [], "display_name": "X",
                    "description": "X", "enabled": True}]
        with pytest.raises(ValueError, match="Invalid agent name"):
            _write_toml(agents, tmp_path / "bad.toml")


class TestDetectAgents:
    def test_returns_dict(self):
        result = _detect_agents()
        assert isinstance(result, dict)
        assert "claude" in result
