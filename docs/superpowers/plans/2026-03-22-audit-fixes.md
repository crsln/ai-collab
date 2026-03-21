# ai-collab Audit Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all 6 issues identified in the 3-agent audit consensus (session bs_eb2bc2b5bfb0).

**Architecture:** Wave-based execution. Wave 1 (independent fixes) runs in parallel. Wave 2 (interdependent refactors) runs sequentially since they touch the same files.

**Tech Stack:** Python 3.11+, SQLite, FastMCP

---

## Wave 1 — Independent Fixes (parallel)

### Task 1: Subprocess Error Handling

**Files:**
- Modify: `providers/generic.py:89-99`
- Reference: `providers/errors.py` (ProviderExecution already exists)

- [ ] **Step 1: Add returncode check and stderr reporting**

In `providers/generic.py`, after `proc.communicate()`, check returncode and raise on failure:

```python
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
```

- [ ] **Step 2: Verify ProviderExecution import exists**

`generic.py` line 14 should already import `ProviderExecution` from `errors.py`. If not, add it.

- [ ] **Step 3: Test manually** — run a round with a broken agent command and verify the error surfaces.

---

### Task 2: Quality Classification Fix

**Files:**
- Modify: `brainstorm_db.py` — `classify_response_quality` function

- [ ] **Step 1: Find and update the function**

Search for `classify_response_quality` in `brainstorm_db.py`. Replace 50-char threshold with word count:

```python
def classify_response_quality(content: str | None) -> str:
    if not content or not content.strip():
        return "empty"
    stripped = content.strip()
    if stripped.startswith("[ERROR]"):
        return "error"
    if len(stripped.split()) < 5:
        return "empty"
    return "valid"
```

5-word minimum is much more reasonable than 50-char — catches garbage but allows concise valid responses.

- [ ] **Step 2: Verify** — confirm no other code depends on the 50-char assumption.

---

### Task 3: Agent Registry Consolidation

**Files:**
- Modify: `ai_collab_cli.py:38-63` — remove `KNOWN_AGENTS`, import from config
- Modify: `config.py:73-95` — export `_BUILTIN_AGENTS` as `BUILTIN_AGENTS` (public)

- [ ] **Step 1: Make config.py the single source**

In `config.py`, rename `_BUILTIN_AGENTS` to `BUILTIN_AGENTS` (remove underscore to make it public API).

- [ ] **Step 2: Update ai_collab_cli.py**

Replace the `KNOWN_AGENTS` dict with an import:

```python
from config import BUILTIN_AGENTS

KNOWN_AGENTS = BUILTIN_AGENTS
```

Keep `KNOWN_AGENTS` as an alias for backward compat within the CLI file, but it now points to the single source.

- [ ] **Step 3: Verify** — check that `ai-collab init` still works correctly with the imported registry.

---

## Wave 2 — Interdependent Refactors (sequential)

### Task 4: BrainstormDB Split — Extract Service Layer

**Files:**
- Create: `brainstorm_service.py` — onboarding builder, phase detection, completion gates, quality classification
- Modify: `brainstorm_db.py` — remove extracted methods, keep CRUD/DDL
- Modify: `mcp_server.py` — import from service instead of db where needed
- Modify: `brainstorm_server.py` — same

- [ ] **Step 1: Create brainstorm_service.py**

Extract these from `brainstorm_db.py` into a new `BrainstormService` class that wraps a `BrainstormDB` instance:

- `get_onboarding_briefing()` — the full onboarding builder
- `get_agent_briefing()` — session briefing helper
- `classify_response_quality()` — standalone function
- `check_round_complete()` — completion gate
- `check_feedback_votes_complete()` — feedback gate
- `check_phase_ready()` — phase readiness check
- `get_agent_session_responses()` — prior work query

The service class takes a `BrainstormDB` instance and delegates CRUD to it:

```python
class BrainstormService:
    def __init__(self, db: BrainstormDB):
        self._db = db

    def get_onboarding_briefing(self, agent_name, session_id=None, round_id=None):
        # ... moved from BrainstormDB
        # calls self._db.get_agent_definition(), self._db.get_round(), etc.
```

- [ ] **Step 2: Update imports in both MCP servers**

Both `mcp_server.py` and `brainstorm_server.py` should create `BrainstormService(_db)` and call service methods instead of DB methods for the extracted functions.

- [ ] **Step 3: Verify** — run a dummy brainstorm to confirm onboarding, phase detection, and gates work.

---

### Task 5: Encapsulation — Remove Direct `_db._conn` Access

**Files:**
- Modify: `brainstorm_db.py` — add proper methods for all raw SQL operations
- Modify: `mcp_server.py` — replace `_db._conn.execute()` with method calls
- Modify: `brainstorm_server.py` — same

- [ ] **Step 1: Identify all _db._conn usages**

Grep for `_db._conn` and `_db\._conn` in both server files. For each, create a proper method in `brainstorm_db.py`.

Expected new methods:
- `mark_participant_status(round_id, agent_name, status, error=None)`
- `mark_participant_validated(round_id, agent_name, quality)`
- `set_round_completed(round_id)`
- `mark_response_quality(round_id, agent_name, quality)`
- `mark_response_source(round_id, agent_name, source)`

- [ ] **Step 2: Replace all direct access in server files**

- [ ] **Step 3: Grep verify** — `grep -n "_db._conn" mcp_server.py brainstorm_server.py` should return 0 results.

---

### Task 6: Shared Tool Module (Server Consolidation)

**Files:**
- Create: `brainstorm_tools.py` — shared tool handler functions
- Modify: `mcp_server.py` — import handlers from shared module
- Modify: `brainstorm_server.py` — import handlers from shared module

- [ ] **Step 1: Identify duplicated tools**

All `bs_*` tools that exist in BOTH servers with identical logic. Extract their handler functions (not the `@mcp.tool()` decorators) into `brainstorm_tools.py`.

Each handler is a plain function that takes args and returns a dict/string:

```python
# brainstorm_tools.py
def handle_new_session(db, topic, project=None):
    return db.create_session(topic, project)

def handle_list_sessions(db):
    return db.list_sessions()
```

- [ ] **Step 2: Update both servers**

Each server keeps its `@mcp.tool()` decorators but calls the shared handler:

```python
# mcp_server.py
@mcp.tool()
def bs_new_session(topic: str, project: str | None = None) -> str:
    return json.dumps(handle_new_session(_db, topic, project), indent=2)
```

- [ ] **Step 3: Fix diverged contracts**

Where contracts differ (e.g., `bs_list_feedback` return shape, `bs_create_feedback` param names), align them to a single shape in the shared handler.

- [ ] **Step 4: Verify** — restart MCP servers, run `bs_get_onboarding` from both.

---

## Verification

After all tasks:
1. Restart MCP server (`/mcp`)
2. Run a 1-round dummy brainstorm to verify full flow
3. Grep for `_db._conn` in server files — should be 0
4. Grep for `KNOWN_AGENTS` definition in `ai_collab_cli.py` — should be import only
5. Check dashboard works
