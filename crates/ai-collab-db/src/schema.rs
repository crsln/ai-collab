//! DDL schema for the brainstorm SQLite database.
//!
//! All CREATE TABLE and CREATE INDEX statements live here as a single
//! constant so they can be executed in one `execute_batch` call.

pub const SCHEMA: &str = r#"
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    topic TEXT NOT NULL,
    project TEXT,
    context TEXT,
    status TEXT DEFAULT 'active' CHECK(status IN ('active','completed','archived')),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rounds (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    round_number INTEGER NOT NULL,
    objective TEXT,
    question TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    UNIQUE (session_id, round_number)
);

CREATE TABLE IF NOT EXISTS responses (
    id TEXT PRIMARY KEY,
    round_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    content TEXT NOT NULL,
    quality TEXT CHECK(quality IS NULL OR quality IN ('valid','invalid','suspect','empty','self_saved')),
    source TEXT DEFAULT 'stdout',
    created_at TEXT NOT NULL,
    FOREIGN KEY (round_id) REFERENCES rounds(id) ON DELETE CASCADE,
    UNIQUE (round_id, agent_name)
);

CREATE TABLE IF NOT EXISTS consensus (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    round_id TEXT,
    version INTEGER DEFAULT 1,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    FOREIGN KEY (round_id) REFERENCES rounds(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS feedback_items (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    source_round_id TEXT NOT NULL,
    source_agent TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    status TEXT DEFAULT 'pending' CHECK(status IN ('pending','accepted','rejected','modified','consolidated')),
    created_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    FOREIGN KEY (source_round_id) REFERENCES rounds(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS feedback_responses (
    id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL,
    round_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    verdict TEXT NOT NULL CHECK(verdict IN ('accept','reject','modify','abstain','agree','disagree','partial')),
    reasoning TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (item_id) REFERENCES feedback_items(id) ON DELETE CASCADE,
    FOREIGN KEY (round_id) REFERENCES rounds(id) ON DELETE CASCADE,
    UNIQUE (item_id, round_id, agent_name)
);

CREATE TABLE IF NOT EXISTS agent_roles (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    role TEXT NOT NULL,
    source_slug TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    UNIQUE (session_id, agent_name)
);

CREATE TABLE IF NOT EXISTS guidelines (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

-- Global tables (not session-scoped) --

CREATE TABLE IF NOT EXISTS agent_definitions (
    id TEXT PRIMARY KEY,
    agent_name TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    capabilities TEXT NOT NULL,
    default_role TEXT NOT NULL,
    approach TEXT NOT NULL,
    vision TEXT,
    angle TEXT,
    behavior TEXT,
    tags TEXT DEFAULT '[]',
    backend_hint TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workflow_templates (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    version INTEGER DEFAULT 1,
    overview TEXT NOT NULL,
    phases TEXT NOT NULL,
    convergence_rules TEXT NOT NULL,
    response_format TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tool_guides (
    id TEXT PRIMARY KEY,
    tool_name TEXT NOT NULL UNIQUE,
    phase TEXT NOT NULL,
    purpose TEXT NOT NULL,
    usage TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS role_library (
    id TEXT PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    agent_name TEXT,
    description TEXT NOT NULL,
    role_text TEXT NOT NULL,
    approach TEXT,
    vision TEXT,
    angle TEXT,
    behavior TEXT,
    mandates TEXT DEFAULT '[]',
    tags TEXT DEFAULT '[]',
    usage_count INTEGER DEFAULT 0,
    last_used_at TEXT,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS round_participants (
    id TEXT PRIMARY KEY,
    round_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    phase TEXT NOT NULL DEFAULT 'analysis' CHECK(phase IN ('analysis','deliberation','consolidation')),
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','dispatched','responded','failed','validated','timed_out')),
    dispatched_at TEXT,
    responded_at TEXT,
    response_quality TEXT,
    error_detail TEXT,
    retry_count INTEGER DEFAULT 0,
    max_retries INTEGER DEFAULT 1,
    feedback_items_expected INTEGER DEFAULT 0,
    feedback_items_completed INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY (round_id) REFERENCES rounds(id) ON DELETE CASCADE,
    UNIQUE (round_id, agent_name)
);

-- Indexes --

CREATE INDEX IF NOT EXISTS idx_role_library_agent ON role_library(agent_name);
CREATE INDEX IF NOT EXISTS idx_role_library_slug ON role_library(slug);
CREATE INDEX IF NOT EXISTS idx_rounds_session ON rounds(session_id);
CREATE INDEX IF NOT EXISTS idx_responses_lookup ON responses(round_id, agent_name);
CREATE INDEX IF NOT EXISTS idx_consensus_session ON consensus(session_id);
CREATE INDEX IF NOT EXISTS idx_feedback_session ON feedback_items(session_id);
CREATE INDEX IF NOT EXISTS idx_feedback_responses_item ON feedback_responses(item_id);
CREATE INDEX IF NOT EXISTS idx_guidelines_session ON guidelines(session_id);
CREATE INDEX IF NOT EXISTS idx_rp_round ON round_participants(round_id);
CREATE INDEX IF NOT EXISTS idx_rp_status ON round_participants(round_id, status);
"#;
