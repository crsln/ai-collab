"""Unit tests for config.py — configuration loading."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from config import AgentConfig, AppConfig, load_config, _BUILTIN_AGENTS


class TestAgentConfig:
    def test_build_args_replaces_prompt(self):
        cfg = AgentConfig(name="test", command="test", args=["-p", "{prompt}"])
        args = cfg.build_args("hello world")
        assert args == ["-p", "hello world"]

    def test_build_args_adds_model(self):
        cfg = AgentConfig(name="test", command="test", args=["-p", "{prompt}"], model="gpt-4")
        args = cfg.build_args("hi")
        assert "--model" in args
        assert "gpt-4" in args

    def test_display_name_defaults_to_title(self):
        cfg = AgentConfig(name="my_agent", command="test")
        assert cfg.display_name == "My_Agent"

    def test_display_name_preserved(self):
        cfg = AgentConfig(name="test", command="test", display_name="Custom Name")
        assert cfg.display_name == "Custom Name"


class TestBuiltinDefaults:
    def test_copilot_defined(self):
        assert "copilot" in _BUILTIN_AGENTS

    def test_gemini_defined(self):
        assert "gemini" in _BUILTIN_AGENTS

    def test_builtin_agents_have_required_fields(self):
        for name, agent in _BUILTIN_AGENTS.items():
            assert "command" in agent
            assert "args" in agent
            assert "{prompt}" in " ".join(agent["args"])
