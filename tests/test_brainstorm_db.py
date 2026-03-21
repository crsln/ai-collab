"""Unit tests for brainstorm_db.py — core data layer."""

import sys
from pathlib import Path

# Add project root to path so imports work
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from brainstorm_db import BrainstormDB


@pytest.fixture
def db(tmp_path):
    """Create a fresh in-memory-like DB for each test."""
    return BrainstormDB(tmp_path / "test.db")


class TestSessions:
    def test_create_session(self, db):
        s = db.create_session("Test topic", project="test-proj")
        assert s["topic"] == "Test topic"
        assert s["project"] == "test-proj"
        assert s["id"].startswith("bs_")

    def test_get_session(self, db):
        s = db.create_session("Topic A")
        fetched = db.get_session(s["id"])
        assert fetched is not None
        assert fetched["topic"] == "Topic A"

    def test_complete_session(self, db):
        s = db.create_session("Topic B")
        db.complete_session(s["id"])
        fetched = db.get_session(s["id"])
        assert fetched["status"] == "completed"

    def test_list_sessions(self, db):
        db.create_session("One")
        db.create_session("Two")
        sessions = db.list_sessions()
        assert len(sessions) >= 2


class TestRounds:
    def test_create_round(self, db):
        s = db.create_session("Round test")
        r = db.create_round(s["id"], objective="Phase 1")
        assert r["round_number"] == 1
        assert r["id"].startswith("r_")

    def test_round_auto_increments(self, db):
        s = db.create_session("Increment test")
        r1 = db.create_round(s["id"])
        r2 = db.create_round(s["id"])
        assert r2["round_number"] == r1["round_number"] + 1


class TestResponses:
    def test_save_and_get_response(self, db):
        s = db.create_session("Resp test")
        r = db.create_round(s["id"])
        resp = db.save_response(r["id"], "copilot", "Analysis here")
        assert resp["agent_name"] == "copilot"

        responses = db.get_round_responses(r["id"])
        assert len(responses) == 1
        assert responses[0]["content"] == "Analysis here"

    def test_response_replaces_existing(self, db):
        s = db.create_session("Replace test")
        r = db.create_round(s["id"])
        db.save_response(r["id"], "copilot", "First")
        db.save_response(r["id"], "copilot", "Second")
        responses = db.get_round_responses(r["id"])
        copilot_responses = [x for x in responses if x["agent_name"] == "copilot"]
        assert len(copilot_responses) == 1
        assert copilot_responses[0]["content"] == "Second"


class TestFeedback:
    def test_create_and_list_feedback(self, db):
        s = db.create_session("Feedback test")
        r = db.create_round(s["id"])
        fb = db.create_feedback_item(s["id"], r["id"], "copilot", "Bug found", "Details here")
        assert fb["id"].startswith("fb_")

        items = db.list_feedback_items(s["id"])
        assert len(items) == 1
        assert items[0]["title"] == "Bug found"

    def test_feedback_verdicts(self, db):
        s = db.create_session("Verdict test")
        r = db.create_round(s["id"])
        fb = db.create_feedback_item(s["id"], r["id"], "copilot", "Issue", "Content")

        db.save_feedback_response(fb["id"], r["id"], "claude", "accept", "Agree")
        db.save_feedback_response(fb["id"], r["id"], "gemini", "accept", "Also agree")

        item = db.get_feedback_item(fb["id"])
        assert len(item["responses"]) == 2

    def test_update_feedback_status(self, db):
        s = db.create_session("Status test")
        r = db.create_round(s["id"])
        fb = db.create_feedback_item(s["id"], r["id"], "copilot", "Issue", "Content")
        db.update_feedback_status(fb["id"], "accepted")

        item = db.get_feedback_item(fb["id"])
        assert item["status"] == "accepted"


class TestConsensus:
    def test_save_consensus(self, db):
        s = db.create_session("Consensus test")
        c = db.save_consensus(s["id"], "Final document here")
        assert c["version"] == 1

    def test_consensus_version_increments(self, db):
        s = db.create_session("Version test")
        c1 = db.save_consensus(s["id"], "V1")
        c2 = db.save_consensus(s["id"], "V2")
        assert c2["version"] == c1["version"] + 1


class TestContext:
    def test_set_and_get_context(self, db):
        s = db.create_session("Context test")
        db.set_context(s["id"], "Codebase summary here")
        ctx = db.get_context(s["id"])
        assert ctx == "Codebase summary here"


class TestRoles:
    def test_set_and_get_role(self, db):
        s = db.create_session("Role test")
        result = db.set_role(s["id"], "copilot", "Security reviewer")
        assert result["agent_name"] == "copilot"
        assert result["id"].startswith("role_")
