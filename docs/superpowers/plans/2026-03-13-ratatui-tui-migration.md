# Ratatui TUI Migration Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate `ai_collab_tui.py` (Python/Textual) to a standalone native Rust binary using Ratatui, producing a single static `.exe` for Windows.

**Architecture:** A single Rust crate at `E:/GitHub/ai-collab/tui/` with five focused source modules: `db` (SQLite polling + data types), `colors` (agent palette + braille), `render` (flow-art line builder), `app` (state + input), `ui` (Ratatui draw pass). The main loop is synchronous — crossterm event polling with 50ms timeout drives both the 2s DB poll and 0.5s animation tick via `Instant` timers.

**Tech Stack:** Rust 1.78+, Ratatui 0.28, crossterm 0.28, rusqlite 0.31 (bundled), clap 4, chrono 0.4

---

## File Map

| File | Responsibility |
|------|---------------|
| `tui/Cargo.toml` | Crate manifest and dependencies |
| `tui/src/main.rs` | CLI args (clap), terminal setup/teardown, event loop |
| `tui/src/db.rs` | `SessionData`, `RoundInfo`, `AgentResponse`; `poll_sessions`, `complete_session`, `parse_dt`, `time_ago` |
| `tui/src/colors.rs` | `AGENT_COLORS`, `BRAILLE`, `hex_to_color`, `color_for_agent`, stable color map |
| `tui/src/render.rs` | `extract_summary`, `render_flow_art` → `Vec<Line<'static>>` |
| `tui/src/app.rs` | `AppState`, `refresh`, `focus_next/prev`, `stop_focused`, scroll model |
| `tui/src/ui.rs` | `draw(frame, state)` — full Ratatui render pass |

---

## Chunk 1: Foundation

### Task 1: Project scaffold

**Files:**
- Create: `tui/Cargo.toml`
- Create: `tui/src/main.rs`
- Create: `tui/src/db.rs`
- Create: `tui/src/colors.rs`
- Create: `tui/src/render.rs`
- Create: `tui/src/app.rs`
- Create: `tui/src/ui.rs`

- [ ] **Step 1: Create `tui/Cargo.toml`**

```toml
[package]
name = "ai-collab-tui"
version = "0.1.0"
edition = "2021"

[[bin]]
name = "ai-collab-tui"
path = "src/main.rs"

[dependencies]
ratatui = "0.28"
crossterm = "0.28"
rusqlite = { version = "0.31", features = ["bundled"] }
clap = { version = "4", features = ["derive"] }
chrono = "0.4"

[profile.release]
opt-level = 3
strip = true
lto = true
codegen-units = 1
```

- [ ] **Step 2: Create stub source files**

`tui/src/main.rs`:
```rust
mod app;
mod colors;
mod db;
mod render;
mod ui;

fn main() {
    println!("ai-collab-tui stub");
}
```

`tui/src/db.rs`, `tui/src/colors.rs`, `tui/src/render.rs`, `tui/src/app.rs`, `tui/src/ui.rs` — each just `// TODO` for now.

- [ ] **Step 3: Verify it compiles**

```bash
cd E:/GitHub/ai-collab/tui
cargo build
```
Expected: `Compiling ai-collab-tui v0.1.0` ... `Finished`

- [ ] **Step 4: Commit**

```bash
git add tui/
git commit -m "feat: scaffold Rust tui crate"
```

---

### Task 2: Data types and DB polling (`db.rs`)

**Files:**
- Modify: `tui/src/db.rs`
- Create: `tui/tests/db_test.rs`

- [ ] **Step 1: Write the failing tests**

Create `tui/tests/db_test.rs`:
```rust
use ai_collab_tui::db::{parse_dt, time_ago, extract_summary_from_content};

#[test]
fn test_parse_dt_valid() {
    let dt = parse_dt("2026-03-13T17:40:15.744394+00:00");
    assert!(dt.is_some());
}

#[test]
fn test_parse_dt_invalid() {
    assert!(parse_dt("not-a-date").is_none());
    assert!(parse_dt("").is_none());
}

#[test]
fn test_time_ago_seconds() {
    // Can't test exact value without mocking time — test format only
    let s = time_ago("2020-01-01T00:00:00+00:00");
    assert!(s.contains("ago"));
}

#[test]
fn test_time_ago_empty() {
    assert_eq!(time_ago(""), "");
}
```

Make `db` module public in `main.rs`: add `pub mod db;` etc. Add to `Cargo.toml`:
```toml
[lib]
name = "ai_collab_tui"
path = "src/lib.rs"
```

Create `tui/src/lib.rs`:
```rust
pub mod app;
pub mod colors;
pub mod db;
pub mod render;
pub mod ui;
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd E:/GitHub/ai-collab/tui
cargo test
```
Expected: compile error — `parse_dt`, `time_ago` not defined yet.

- [ ] **Step 3: Implement `db.rs`**

```rust
use chrono::{DateTime, Duration, Utc};
use rusqlite::{Connection, OpenFlags, Row};
use std::collections::HashSet;
use std::path::Path;

// ── Data types ───────────────────────────────────────────────────────────

#[derive(Debug, Clone)]
pub struct AgentResponse {
    pub agent_name: String,
    pub content: Option<String>,
    pub created_at: Option<String>,
}

#[derive(Debug, Clone)]
pub struct RoundInfo {
    pub round_id: String,
    pub round_number: i64,
    pub total_rounds: i64,
    pub objective: Option<String>,
    pub question: Option<String>,
    pub created_at: String,
    pub responses: Vec<AgentResponse>,
}

#[derive(Debug, Clone)]
pub struct SessionData {
    pub session_id: String,
    pub topic: String,
    pub project: Option<String>,
    pub status: String,
    pub created_at: String,
    pub rounds: Vec<RoundInfo>,
    pub consensus_content: Option<String>,
    pub is_running: bool,
}

// ── Time helpers ─────────────────────────────────────────────────────────

pub fn parse_dt(s: &str) -> Option<DateTime<Utc>> {
    if s.is_empty() {
        return None;
    }
    DateTime::parse_from_rfc3339(s)
        .ok()
        .map(|dt| dt.with_timezone(&Utc))
}

pub fn time_ago(s: &str) -> String {
    match parse_dt(s) {
        None => String::new(),
        Some(dt) => {
            let secs = (Utc::now() - dt).num_seconds().max(0);
            if secs < 60 {
                format!("{}s ago", secs)
            } else if secs < 3600 {
                format!("{}m ago", secs / 60)
            } else {
                format!("{}h {}m ago", secs / 3600, (secs % 3600) / 60)
            }
        }
    }
}

// ── DB polling ───────────────────────────────────────────────────────────

fn open_db(db_path: &Path) -> Option<Connection> {
    if !db_path.exists() {
        return None;
    }
    Connection::open_with_flags(
        db_path,
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )
    .ok()
}

pub fn poll_sessions(
    db_path: &Path,
    hours: u32,
    limit: usize,
    known_agents: &HashSet<String>,
) -> Vec<SessionData> {
    let conn = match open_db(db_path) {
        Some(c) => c,
        None => return vec![],
    };

    let cutoff = (Utc::now() - Duration::hours(hours as i64)).to_rfc3339();
    let mut stmt = match conn.prepare(
        "SELECT id, topic, project, status, created_at \
         FROM sessions WHERE created_at >= ? ORDER BY created_at DESC LIMIT ?",
    ) {
        Ok(s) => s,
        Err(_) => return vec![],
    };

    let session_rows: Vec<(String, String, Option<String>, String, String)> = stmt
        .query_map(rusqlite::params![cutoff, limit as i64], |row| {
            Ok((
                row.get(0)?,
                row.get(1)?,
                row.get(2)?,
                row.get(3)?,
                row.get(4)?,
            ))
        })
        .unwrap_or_else(|_| Box::new(std::iter::empty()))
        .filter_map(|r| r.ok())
        .collect();

    let mut results = vec![];

    for (sid, topic, project, status, created_at) in session_rows {
        let rounds = load_rounds(&conn, &sid);
        let is_running = detect_running(&rounds, &status, known_agents);

        let consensus_content: Option<String> = conn
            .query_row(
                "SELECT content FROM consensus WHERE session_id = ? LIMIT 1",
                rusqlite::params![sid],
                |row| row.get(0),
            )
            .ok();

        results.push(SessionData {
            session_id: sid,
            topic,
            project,
            status,
            created_at,
            rounds,
            consensus_content,
            is_running,
        });
    }

    results
}

fn load_rounds(conn: &Connection, session_id: &str) -> Vec<RoundInfo> {
    let mut stmt = match conn.prepare(
        "SELECT id, round_number, objective, question, created_at \
         FROM rounds WHERE session_id = ? ORDER BY round_number",
    ) {
        Ok(s) => s,
        Err(_) => return vec![],
    };

    let round_rows: Vec<(String, i64, Option<String>, Option<String>, String)> = stmt
        .query_map(rusqlite::params![session_id], |row| {
            Ok((
                row.get(0)?,
                row.get(1)?,
                row.get(2)?,
                row.get(3)?,
                row.get(4)?,
            ))
        })
        .unwrap_or_else(|_| Box::new(std::iter::empty()))
        .filter_map(|r| r.ok())
        .collect();

    let total_rounds = round_rows.len() as i64;
    let mut rounds = vec![];

    for (rid, round_number, objective, question, created_at) in round_rows {
        let responses = load_responses(conn, &rid);
        rounds.push(RoundInfo {
            round_id: rid,
            round_number,
            total_rounds,
            objective,
            question,
            created_at,
            responses,
        });
    }

    rounds
}

fn load_responses(conn: &Connection, round_id: &str) -> Vec<AgentResponse> {
    let mut stmt = match conn.prepare(
        "SELECT agent_name, content, created_at \
         FROM responses WHERE round_id = ? ORDER BY created_at",
    ) {
        Ok(s) => s,
        Err(_) => return vec![],
    };

    stmt.query_map(rusqlite::params![round_id], |row| {
        Ok(AgentResponse {
            agent_name: row.get(0)?,
            content: row.get(1)?,
            created_at: row.get(2)?,
        })
    })
    .unwrap_or_else(|_| Box::new(std::iter::empty()))
    .filter_map(|r| r.ok())
    .collect()
}

fn detect_running(
    rounds: &[RoundInfo],
    status: &str,
    known_agents: &HashSet<String>,
) -> bool {
    if status != "active" || rounds.is_empty() {
        return false;
    }
    let latest = rounds.last().unwrap();
    let round_dt = match parse_dt(&latest.created_at) {
        Some(dt) => dt,
        None => return false,
    };
    let age = (Utc::now() - round_dt).num_seconds();
    if age >= 1800 {
        return false;
    }
    let responded: HashSet<&str> = latest
        .responses
        .iter()
        .filter(|r| r.content.is_some())
        .map(|r| r.agent_name.as_str())
        .collect();

    if !known_agents.is_empty() {
        return !known_agents.iter().all(|a| responded.contains(a.as_str()));
    }

    // Infer from all rounds
    let all_session_agents: HashSet<&str> = rounds
        .iter()
        .flat_map(|r| r.responses.iter())
        .filter(|r| r.content.is_some())
        .map(|r| r.agent_name.as_str())
        .collect();

    all_session_agents.iter().any(|a| !responded.contains(a))
}

pub fn complete_session(db_path: &Path, session_id: &str) {
    if let Ok(conn) = Connection::open(db_path) {
        let _ = conn.execute(
            "UPDATE sessions SET status = 'completed' WHERE id = ?",
            rusqlite::params![session_id],
        );
    }
}
```

- [ ] **Step 4: Run tests**

```bash
cargo test db_test
```
Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tui/
git commit -m "feat: db polling and data types (db.rs)"
```

---

### Task 3: Color system (`colors.rs`)

**Files:**
- Modify: `tui/src/colors.rs`

- [ ] **Step 1: Write failing tests**

Add to `tui/tests/db_test.rs` (or create `tui/tests/colors_test.rs`):
```rust
use ai_collab_tui::colors::{hex_to_color, color_for_agent, BRAILLE};
use ratatui::style::Color;

#[test]
fn test_hex_to_color_orange() {
    assert_eq!(hex_to_color("#ff9f00"), Color::Rgb(255, 159, 0));
}

#[test]
fn test_hex_to_color_fallback() {
    assert_eq!(hex_to_color("bad"), Color::White);
}

#[test]
fn test_braille_length() {
    assert_eq!(BRAILLE.len(), 10);
}

#[test]
fn test_color_for_agent_stable() {
    // Same agent always gets same color index
    let c1 = color_for_agent("claude", 0);
    let c2 = color_for_agent("claude", 0);
    assert_eq!(c1, c2);
}
```

- [ ] **Step 2: Run to verify failure**

```bash
cargo test colors
```
Expected: compile error.

- [ ] **Step 3: Implement `colors.rs`**

```rust
use ratatui::style::Color;

pub const BRAILLE: &[char] = &['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'];

pub const AGENT_COLORS: &[Color] = &[
    Color::Rgb(255, 159, 0),   // #ff9f00 orange  — claude
    Color::Rgb(167, 139, 250), // #a78bfa purple  — copilot
    Color::Rgb(52, 211, 153),  // #34d399 green   — gemini
    Color::Rgb(244, 114, 182), // #f472b6 pink
    Color::Rgb(96, 165, 250),  // #60a5fa blue
];

pub const CYAN: Color = Color::Rgb(0, 215, 255);   // ◉ start node
pub const MINT: Color = Color::Rgb(0, 255, 159);   // ◆ consensus
pub const DIM: Color = Color::DarkGray;

pub fn hex_to_color(hex: &str) -> Color {
    let h = hex.trim_start_matches('#');
    if h.len() != 6 {
        return Color::White;
    }
    let r = u8::from_str_radix(&h[0..2], 16).unwrap_or(255);
    let g = u8::from_str_radix(&h[2..4], 16).unwrap_or(255);
    let b = u8::from_str_radix(&h[4..6], 16).unwrap_or(255);
    Color::Rgb(r, g, b)
}

/// Return color for agent at position `idx` in sorted agent list.
pub fn color_for_agent(_name: &str, idx: usize) -> Color {
    AGENT_COLORS[idx % AGENT_COLORS.len()]
}

pub fn braille_frame(frame: u32) -> char {
    BRAILLE[(frame as usize) % BRAILLE.len()]
}
```

- [ ] **Step 4: Run tests**

```bash
cargo test colors
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git commit -am "feat: color constants and helpers (colors.rs)"
```

---

## Chunk 2: Rendering

### Task 4: Flow art renderer (`render.rs`)

**Files:**
- Modify: `tui/src/render.rs`
- Create: `tui/tests/render_test.rs`

- [ ] **Step 1: Write failing tests**

`tui/tests/render_test.rs`:
```rust
use ai_collab_tui::db::{AgentResponse, RoundInfo, SessionData};
use ai_collab_tui::render::{extract_summary, render_flow_art};
use std::collections::HashMap;

fn make_session(n_agents: usize, n_rounds: usize, all_responded: bool) -> SessionData {
    let agents = vec!["claude", "copilot", "gemini"];
    let mut rounds = vec![];
    for ri in 0..n_rounds {
        let responses = if all_responded || ri < n_rounds - 1 {
            agents[..n_agents]
                .iter()
                .map(|a| AgentResponse {
                    agent_name: a.to_string(),
                    content: Some(format!("Response from {}", a)),
                    created_at: Some("2026-03-13T17:00:00+00:00".to_string()),
                })
                .collect()
        } else {
            vec![] // last round, no responses yet
        };
        rounds.push(RoundInfo {
            round_id: format!("r_{}", ri),
            round_number: (ri + 1) as i64,
            total_rounds: n_rounds as i64,
            objective: Some("Phase 1".to_string()),
            question: None,
            created_at: "2026-03-13T17:00:00+00:00".to_string(),
            responses,
        });
    }
    SessionData {
        session_id: "test_sid".to_string(),
        topic: "Test session".to_string(),
        project: Some("test".to_string()),
        status: "active".to_string(),
        created_at: "2026-03-13T17:00:00+00:00".to_string(),
        rounds,
        consensus_content: None,
        is_running: !all_responded,
    }
}

#[test]
fn test_flow_art_row_count_3_agents() {
    let session = make_session(3, 2, true);
    let color_map: HashMap<String, usize> = [
        ("claude".to_string(), 0),
        ("copilot".to_string(), 1),
        ("gemini".to_string(), 2),
    ]
    .into();
    let lines = render_flow_art(&session, &color_map, 0);
    // H = 2*3-1 = 5 rows
    assert_eq!(lines.len(), 5);
}

#[test]
fn test_flow_art_row_count_1_agent() {
    let session = make_session(1, 1, true);
    let color_map: HashMap<String, usize> = [("claude".to_string(), 0)].into();
    let lines = render_flow_art(&session, &color_map, 0);
    assert_eq!(lines.len(), 1);
}

#[test]
fn test_extract_summary_strips_prefix() {
    assert_eq!(extract_summary("## My heading\nother"), "My heading");
}

#[test]
fn test_extract_summary_skips_blank() {
    assert_eq!(extract_summary("\n\nHello world"), "Hello world");
}

#[test]
fn test_extract_summary_truncates() {
    let long = "a".repeat(80);
    let s = extract_summary(&long);
    assert!(s.len() <= 73); // 70 chars + "…"
}
```

- [ ] **Step 2: Run to verify failure**

```bash
cargo test render
```
Expected: compile error — render module not defined.

- [ ] **Step 3: Implement `render.rs`**

```rust
use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span};
use std::collections::HashMap;

use crate::colors::{braille_frame, DIM, CYAN, MINT, AGENT_COLORS};
use crate::db::SessionData;

// ── Summary extractor ────────────────────────────────────────────────────

pub fn extract_summary(content: &str) -> String {
    for raw in content.lines() {
        let ln = raw.trim();
        if ln.is_empty() {
            continue;
        }
        // Skip lines starting with special chars
        let first = ln.chars().next().unwrap_or(' ');
        if "✓✗⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏⚠✘|┌┐└┘├┤┬┴┼─│[{".contains(first) {
            continue;
        }
        if ln.contains("brainstorm-") || ln.contains("atlas-") || ln.contains("skill(") {
            continue;
        }
        let mut s = ln;
        for pfx in &["## ", "### ", "# ", "**", "- ", "> ", "* "] {
            if s.starts_with(pfx) {
                s = s[pfx.len()..].trim();
                break;
            }
        }
        if s.is_empty() {
            continue;
        }
        if s.len() > 70 {
            return format!("{}…", &s[..70]);
        }
        return s.to_string();
    }
    "(no summary)".to_string()
}

// ── Flow art ─────────────────────────────────────────────────────────────

fn dim(s: &str) -> Span<'static> {
    Span::styled(s.to_string(), Style::default().fg(DIM))
}

fn colored(s: &str, color: ratatui::style::Color) -> Span<'static> {
    Span::styled(s.to_string(), Style::default().fg(color))
}

/// Render the branching dot-graph for one session.
/// Returns one `Line` per row (H = 2*N-1 for N agents).
///
/// Layout (3 agents, 2 rounds):
///   ╭── ● ── ● ──╮
///   │             │
/// ◉─┼── ● ── ⠋ ──┼── ◆ summary
///   │             │
///   ╰── ● ── ● ──╯
pub fn render_flow_art(
    session: &SessionData,
    color_map: &HashMap<String, usize>, // agent_name → color index
    frame: u32,
) -> Vec<Line<'static>> {
    let sp = braille_frame(frame);
    let rounds = &session.rounds;

    let mut all_agents: Vec<String> = {
        let mut set: std::collections::BTreeSet<String> = std::collections::BTreeSet::new();
        for (name, _) in color_map {
            set.insert(name.clone());
        }
        for r in rounds {
            for resp in &r.responses {
                set.insert(resp.agent_name.clone());
            }
        }
        set.into_iter().collect()
    };

    let n = all_agents.len();
    let m = rounds.len();

    if n == 0 || m == 0 {
        return vec![Line::from(vec![
            colored("◉", CYAN),
            dim(&format!("  {} waiting…", sp)),
        ])];
    }

    let h = 2 * n - 1;
    let center = n - 1; // index of middle row (H rows, 0-indexed)
    let has_consensus = session.consensus_content.is_some();

    // Precompute responded sets per round
    let responded_sets: Vec<std::collections::HashSet<&str>> = rounds
        .iter()
        .map(|r| {
            r.responses
                .iter()
                .filter(|resp| resp.content.is_some())
                .map(|resp| resp.agent_name.as_str())
                .collect()
        })
        .collect();

    let mid_width = m + (m - 1) * 4; // M dots + (M-1) separators of " ── " (4 chars)

    let mut lines = vec![];

    for row in 0..h {
        let is_agent_row = row % 2 == 0;
        let ai = row / 2;
        let mut spans: Vec<Span<'static>> = vec![Span::raw("  ")]; // 2-space indent

        // ── LEFT (6 rendered chars) ──────────────────────────────────────
        if n == 1 {
            spans.push(colored("◉", CYAN));
            spans.push(dim("── "));
        } else if row == 0 {
            spans.push(dim("  ╭── "));
        } else if row == h - 1 {
            spans.push(dim("  ╰── "));
        } else if is_agent_row && row == center {
            spans.push(colored("◉", CYAN));
            spans.push(dim("─┼── "));
        } else if !is_agent_row && row == center {
            spans.push(colored("◉", CYAN));
            spans.push(dim("─┤   "));
        } else if is_agent_row {
            spans.push(dim("  ├── "));
        } else {
            spans.push(dim("  │   "));
        }

        // ── MIDDLE (rounds as columns) ───────────────────────────────────
        if is_agent_row {
            let agent = &all_agents[ai];
            let color_idx = color_map.get(agent).copied().unwrap_or(0);
            let color = AGENT_COLORS[color_idx % AGENT_COLORS.len()];

            for (ri, responded) in responded_sets.iter().enumerate() {
                let dot = if responded.contains(agent.as_str()) {
                    "●"
                } else if session.is_running && ri == m - 1 {
                    // Safety: sp is a char, format it
                    // We need a &'static str — use a match on braille index
                    // Actually we'll push a dynamic span here
                    spans.push(Span::styled(
                        sp.to_string(),
                        Style::default().fg(color),
                    ));
                    if ri < m - 1 {
                        spans.push(dim(" ── "));
                    }
                    continue;
                } else {
                    "○"
                };
                spans.push(colored(dot, color));
                if ri < m - 1 {
                    spans.push(dim(" ── "));
                }
            }
        } else {
            spans.push(Span::raw(" ".repeat(mid_width)));
        }

        // ── RIGHT (fan-in + consensus) ───────────────────────────────────
        if n == 1 {
            if has_consensus {
                let short = extract_summary(session.consensus_content.as_deref().unwrap_or(""));
                let short = if short.len() > 40 { format!("{}…", &short[..40]) } else { short };
                spans.push(dim(" ── "));
                spans.push(colored("◆", MINT));
                spans.push(dim(&format!(" {}", short)));
            } else if session.is_running {
                spans.push(dim(&format!(" ── {}", sp)));
            }
        } else if row == 0 {
            spans.push(dim(" ──╮"));
        } else if row == h - 1 {
            spans.push(dim(" ──╯"));
        } else if is_agent_row && row == center {
            if has_consensus {
                let short = extract_summary(session.consensus_content.as_deref().unwrap_or(""));
                let short = if short.len() > 40 { format!("{}…", &short[..40]) } else { short };
                spans.push(dim(" ──┼── "));
                spans.push(colored("◆", MINT));
                spans.push(dim(&format!(" {}", short)));
            } else if session.is_running {
                spans.push(dim(&format!(" ──┼── {}", sp)));
            } else {
                spans.push(dim(" ──┤"));
            }
        } else if !is_agent_row && row == center {
            if has_consensus {
                let short = extract_summary(session.consensus_content.as_deref().unwrap_or(""));
                let short = if short.len() > 40 { format!("{}…", &short[..40]) } else { short };
                spans.push(dim(" ──┤ "));
                spans.push(colored("◆", MINT));
                spans.push(dim(&format!(" {}", short)));
            } else if session.is_running {
                spans.push(dim(&format!(" ──┤ {}", sp)));
            } else {
                spans.push(dim(" ──┤"));
            }
        } else if is_agent_row {
            spans.push(dim(" ──┤"));
        } else {
            spans.push(dim("   │"));
        }

        lines.push(Line::from(spans));
    }

    lines
}
```

- [ ] **Step 4: Run tests**

```bash
cargo test render
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git commit -am "feat: flow art renderer (render.rs)"
```

---

## Chunk 3: App State and UI

### Task 5: App state (`app.rs`)

**Files:**
- Modify: `tui/src/app.rs`
- Create: `tui/tests/app_test.rs`

- [ ] **Step 1: Write failing tests**

`tui/tests/app_test.rs`:
```rust
use ai_collab_tui::app::AppState;
use ai_collab_tui::db::SessionData;
use std::path::PathBuf;

fn dummy_state() -> AppState {
    AppState::new(PathBuf::from("/tmp/test.db"), 24, 6)
}

fn make_session(id: &str, is_running: bool, created_at: &str) -> SessionData {
    SessionData {
        session_id: id.to_string(),
        topic: format!("Session {}", id),
        project: None,
        status: if is_running { "active".to_string() } else { "completed".to_string() },
        created_at: created_at.to_string(),
        rounds: vec![],
        consensus_content: None,
        is_running,
    }
}

#[test]
fn test_sorted_ids_running_first() {
    let mut state = dummy_state();
    state.sessions = vec![
        make_session("old", false, "2026-01-01T00:00:00+00:00"),
        make_session("running", true, "2026-01-02T00:00:00+00:00"),
        make_session("new", false, "2026-01-03T00:00:00+00:00"),
    ];
    let ids = state.sorted_ids();
    assert_eq!(ids[0], "running");
}

#[test]
fn test_focus_next_wraps() {
    let mut state = dummy_state();
    state.sessions = vec![
        make_session("a", false, "2026-01-01T00:00:00+00:00"),
        make_session("b", false, "2026-01-02T00:00:00+00:00"),
    ];
    state.focused_idx = Some(1);
    state.focus_next();
    assert_eq!(state.focused_idx, Some(0));
}

#[test]
fn test_card_height_3_agents() {
    use ai_collab_tui::app::card_height;
    // 3 agents: H = 5 flow rows + 4 fixed = 9 total
    assert_eq!(card_height(3), 9);
}

#[test]
fn test_card_height_1_agent() {
    use ai_collab_tui::app::card_height;
    // 1 agent: H = 1 flow row + 4 fixed = 5 total
    assert_eq!(card_height(1), 5);
}
```

- [ ] **Step 2: Run to verify failure**

```bash
cargo test app
```
Expected: compile error.

- [ ] **Step 3: Implement `app.rs`**

```rust
use ratatui::style::Color;
use std::collections::{HashMap, HashSet};
use std::path::PathBuf;

use crate::colors::AGENT_COLORS;
use crate::db::{complete_session, poll_sessions, SessionData};

/// Card height in terminal rows (including borders).
/// = 2 (borders) + 1 (header) + 1 (meta) + 1 (blank) + (2*N-1) (flow) + 1 (blank)
/// = 2*N + 5  where N = max(1, agent_count)
pub fn card_height(agent_count: usize) -> usize {
    let n = agent_count.max(1);
    2 * n + 5
}

pub struct AppState {
    pub db_path: PathBuf,
    pub hours: u32,
    pub limit: usize,
    pub sessions: Vec<SessionData>,
    /// agent_name → index into AGENT_COLORS
    pub color_map: HashMap<String, usize>,
    pub anim_frame: u32,
    /// Index into sorted_ids()
    pub focused_idx: Option<usize>,
    /// First visible card (card index, not row)
    pub scroll_offset: usize,
    pub last_refresh: String,
    pub should_quit: bool,
    pub known_agents: HashSet<String>,
}

impl AppState {
    pub fn new(db_path: PathBuf, hours: u32, limit: usize) -> Self {
        Self {
            db_path,
            hours,
            limit,
            sessions: vec![],
            color_map: HashMap::new(),
            anim_frame: 0,
            focused_idx: None,
            scroll_offset: 0,
            last_refresh: "—".to_string(),
            should_quit: false,
            known_agents: HashSet::new(),
        }
    }

    pub fn sorted_ids(&self) -> Vec<String> {
        let mut sorted = self.sessions.clone();
        sorted.sort_by(|a, b| {
            let a_key = (if a.is_running { 1i32 } else { 0 }, a.created_at.clone());
            let b_key = (if b.is_running { 1i32 } else { 0 }, b.created_at.clone());
            b_key.cmp(&a_key)
        });
        sorted.into_iter().map(|s| s.session_id).collect()
    }

    pub fn sorted_sessions(&self) -> Vec<&SessionData> {
        let ids = self.sorted_ids();
        ids.iter()
            .filter_map(|id| self.sessions.iter().find(|s| &s.session_id == id))
            .collect()
    }

    pub fn refresh(&mut self) {
        self.sessions = poll_sessions(&self.db_path, self.hours, self.limit, &self.known_agents);
        self.update_color_map();
        self.clamp_focus();
        let now = chrono::Local::now();
        self.last_refresh = now.format("%H:%M:%S").to_string();
    }

    fn update_color_map(&mut self) {
        let mut all_names: std::collections::BTreeSet<String> = std::collections::BTreeSet::new();
        for s in &self.sessions {
            for r in &s.rounds {
                for resp in &r.responses {
                    all_names.insert(resp.agent_name.clone());
                }
            }
        }
        let next_idx = self.color_map.len();
        for (i, name) in all_names.iter().enumerate() {
            self.color_map.entry(name.clone()).or_insert(next_idx + i);
        }
    }

    fn clamp_focus(&mut self) {
        let ids = self.sorted_ids();
        if ids.is_empty() {
            self.focused_idx = None;
            return;
        }
        if self.focused_idx.is_none() {
            self.focused_idx = Some(0);
        }
        if let Some(idx) = self.focused_idx {
            if idx >= ids.len() {
                self.focused_idx = Some(ids.len() - 1);
            }
        }
    }

    pub fn focus_next(&mut self) {
        let ids = self.sorted_ids();
        if ids.is_empty() { return; }
        self.focused_idx = Some(match self.focused_idx {
            None => 0,
            Some(i) => (i + 1) % ids.len(),
        });
        self.scroll_to_focused();
    }

    pub fn focus_prev(&mut self) {
        let ids = self.sorted_ids();
        if ids.is_empty() { return; }
        self.focused_idx = Some(match self.focused_idx {
            None => ids.len() - 1,
            Some(0) => ids.len() - 1,
            Some(i) => i - 1,
        });
        self.scroll_to_focused();
    }

    /// Ensure focused card is visible; adjust scroll_offset if needed.
    pub fn scroll_to_focused(&mut self, ) {
        // Implemented in ui.rs draw pass — scroll_offset is adjusted there
        // For now just clamp scroll_offset to focused card
        if let Some(idx) = self.focused_idx {
            if idx < self.scroll_offset {
                self.scroll_offset = idx;
            }
        }
    }

    pub fn stop_focused(&mut self) {
        let ids = self.sorted_ids();
        if let Some(idx) = self.focused_idx {
            if let Some(sid) = ids.get(idx) {
                let session = self.sessions.iter().find(|s| &s.session_id == sid);
                if session.map(|s| s.is_running).unwrap_or(false) {
                    complete_session(&self.db_path, sid);
                    self.refresh();
                }
            }
        }
    }
}
```

- [ ] **Step 4: Run tests**

Add `chrono` usage — update `Cargo.toml` if needed (already there). Run:
```bash
cargo test app
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git commit -am "feat: app state and input handling (app.rs)"
```

---

### Task 6: Full UI render pass (`ui.rs`)

**Files:**
- Modify: `tui/src/ui.rs`

No unit tests for the draw function (integration tested visually). Implement and verify by running the app.

- [ ] **Step 1: Implement `ui.rs`**

```rust
use ratatui::Frame;
use ratatui::layout::{Constraint, Layout, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, BorderType, Borders, Paragraph, Wrap};

use crate::app::{card_height, AppState};
use crate::colors::{DIM, CYAN, MINT};
use crate::db::SessionData;
use crate::render::{extract_summary, render_flow_art};

pub fn draw(frame: &mut Frame, state: &AppState) {
    let area = frame.area();

    // Split into main content + status bar
    let chunks = Layout::vertical([
        Constraint::Fill(1),
        Constraint::Length(1),
    ])
    .split(area);

    draw_sessions(frame, chunks[0], state);
    draw_statusbar(frame, chunks[1], state);
}

fn draw_sessions(frame: &mut Frame, area: Rect, state: &AppState) {
    let sessions = state.sorted_sessions();

    if sessions.is_empty() {
        let msg = Paragraph::new("No sessions in the last 24h.\nRun /multi-ai-brainstorm in any Claude Code window to start one.")
            .style(Style::default().fg(DIM))
            .wrap(Wrap { trim: true });
        frame.render_widget(msg, area);
        return;
    }

    // Compute which cards are visible given scroll_offset and area height
    let available_h = area.height as usize;
    let focused_idx = state.focused_idx.unwrap_or(0);

    // Build layout constraints for visible cards
    let mut constraints = vec![];
    let mut visible_start = state.scroll_offset;

    // Auto-scroll: ensure focused card is visible
    // (recompute visible range)
    let mut row = 0;
    let mut visible_range = (visible_start, visible_start);
    for (i, s) in sessions.iter().enumerate().skip(visible_start) {
        let n_agents = agent_count(s);
        let h = card_height(n_agents);
        if row + h > available_h { break; }
        row += h;
        visible_range.1 = i;
    }

    // If focused card is below visible range, scroll down
    if focused_idx > visible_range.1 {
        visible_start = focused_idx;
    }

    // Build constraints for visible cards
    let mut y = area.y;
    let visible_sessions: Vec<&SessionData> = sessions
        .into_iter()
        .enumerate()
        .skip(visible_start)
        .take_while(|(_, s)| {
            let h = card_height(agent_count(s)) as u16;
            let fits = y + h <= area.y + area.height;
            y += h;
            fits
        })
        .map(|(_, s)| s)
        .collect();

    if visible_sessions.is_empty() { return; }

    let constraints: Vec<Constraint> = visible_sessions
        .iter()
        .map(|s| Constraint::Length(card_height(agent_count(s)) as u16))
        .collect();

    let card_areas = Layout::vertical(constraints).split(area);

    for (i, (session, card_area)) in visible_sessions.iter().zip(card_areas.iter()).enumerate() {
        let global_idx = visible_start + i;
        let is_focused = state.focused_idx == Some(global_idx);
        draw_card(frame, *card_area, session, state, is_focused);
    }
}

fn agent_count(session: &SessionData) -> usize {
    let mut agents: std::collections::BTreeSet<&str> = std::collections::BTreeSet::new();
    for r in &session.rounds {
        for resp in &r.responses {
            agents.insert(&resp.agent_name);
        }
    }
    agents.len().max(1)
}

fn draw_card(frame: &mut Frame, area: Rect, session: &SessionData, state: &AppState, focused: bool) {
    // Border color
    let border_color = if focused {
        Color::Rgb(251, 191, 36) // warning orange
    } else if session.is_running {
        Color::Yellow
    } else if session.status == "completed" {
        Color::Green
    } else {
        Color::DarkGray
    };

    let block = Block::default()
        .borders(Borders::ALL)
        .border_type(BorderType::Rounded)
        .border_style(Style::default().fg(border_color));

    let inner = block.inner(area);
    frame.render_widget(block, area);

    // Header line
    let badge = if session.is_running {
        Span::styled("◌ RUNNING", Style::default().fg(Color::Yellow).add_modifier(Modifier::BOLD))
    } else if session.status == "completed" {
        Span::styled("✓ DONE", Style::default().fg(Color::Green).add_modifier(Modifier::BOLD))
    } else {
        Span::styled("● IDLE", Style::default().fg(DIM))
    };

    let topic = if session.topic.len() > 52 {
        format!("{}…", &session.topic[..51])
    } else {
        session.topic.clone()
    };
    let sid = &session.session_id[session.session_id.len().saturating_sub(8)..];

    let header = Line::from(vec![
        badge,
        Span::raw("  "),
        Span::styled(topic, Style::default().add_modifier(Modifier::BOLD)),
        Span::raw("  "),
        Span::styled(sid.to_string(), Style::default().fg(DIM)),
    ]);

    // Meta line
    let mut meta_parts: Vec<Span> = vec![];
    if let Some(proj) = &session.project {
        meta_parts.push(Span::styled("project: ", Style::default().fg(DIM)));
        meta_parts.push(Span::raw(proj.clone()));
        meta_parts.push(Span::styled("  ·  ", Style::default().fg(DIM)));
    }
    meta_parts.push(Span::raw(crate::db::time_ago(&session.created_at)));
    if let Some(r) = session.rounds.last() {
        let obj = r.objective.as_deref()
            .or(r.question.as_deref())
            .unwrap_or("")
            .trim();
        let label = if obj.len() > 40 {
            format!("round {}/{} · {}…", r.round_number, r.total_rounds, &obj[..40])
        } else if obj.is_empty() {
            format!("round {}/{}", r.round_number, r.total_rounds)
        } else {
            format!("round {}/{} · {}", r.round_number, r.total_rounds, obj)
        };
        meta_parts.push(Span::styled("  ·  ", Style::default().fg(DIM)));
        meta_parts.push(Span::styled(label, Style::default().fg(DIM)));
    }
    let meta = Line::from(meta_parts);

    // Flow art lines
    let flow_lines = render_flow_art(session, &state.color_map, state.anim_frame);

    // Assemble all lines
    let mut all_lines = vec![header, meta, Line::raw("")];
    all_lines.extend(flow_lines);
    all_lines.push(Line::raw(""));

    let para = Paragraph::new(all_lines);
    frame.render_widget(para, inner);
}

fn draw_statusbar(frame: &mut Frame, area: Rect, state: &AppState) {
    let running = state.sessions.iter().filter(|s| s.is_running).count();
    let total = state.sessions.len();

    let mut parts: Vec<Span> = vec![];

    if running > 0 {
        parts.push(Span::styled(
            format!("◌ {} running", running),
            Style::default().fg(Color::Yellow).add_modifier(Modifier::BOLD),
        ));
        parts.push(Span::styled("  |  ", Style::default().fg(DIM)));
    }
    if total > 0 {
        parts.push(Span::raw(format!("{} session(s)", total)));
        parts.push(Span::styled("  |  ", Style::default().fg(DIM)));
    }
    parts.push(Span::raw(format!("refreshed {}", state.last_refresh)));
    parts.push(Span::styled("  |  ", Style::default().fg(DIM)));
    parts.push(Span::styled("j/k", Style::default().add_modifier(Modifier::BOLD)));
    parts.push(Span::raw(" nav  "));
    parts.push(Span::styled("x", Style::default().add_modifier(Modifier::BOLD)));
    parts.push(Span::raw(" stop  "));
    parts.push(Span::styled("r", Style::default().add_modifier(Modifier::BOLD)));
    parts.push(Span::raw(" refresh  "));
    parts.push(Span::styled("q", Style::default().add_modifier(Modifier::BOLD)));
    parts.push(Span::raw(" quit"));

    let bar = Paragraph::new(Line::from(parts))
        .style(Style::default().bg(Color::Rgb(30, 30, 30)));
    frame.render_widget(bar, area);
}
```

- [ ] **Step 2: Build to verify**

```bash
cargo build
```
Expected: compiles without errors.

- [ ] **Step 3: Commit**

```bash
git commit -am "feat: full Ratatui draw pass (ui.rs)"
```

---

## Chunk 4: Main Loop and Release

### Task 7: Main entry point (`main.rs`)

**Files:**
- Modify: `tui/src/main.rs`

- [ ] **Step 1: Implement `main.rs`**

```rust
mod app;
mod colors;
mod db;
mod render;
mod ui;

use clap::Parser;
use crossterm::{
    event::{self, Event, KeyCode, KeyEventKind},
    execute,
    terminal::{disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen},
};
use ratatui::{backend::CrosstermBackend, Terminal};
use std::{
    io,
    path::PathBuf,
    time::{Duration, Instant},
};

use app::AppState;

#[derive(Parser)]
#[command(name = "ai-collab-tui", about = "Live monitor for ai-collab brainstorm sessions")]
struct Cli {
    /// Hours of history to show
    #[arg(long, default_value_t = 24)]
    hours: u32,

    /// Max sessions to display
    #[arg(long, default_value_t = 6)]
    limit: usize,

    /// Path to brainstorm.db (overrides BRAINSTORM_DB env var)
    #[arg(long)]
    db: Option<PathBuf>,
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let cli = Cli::parse();

    let db_path = cli
        .db
        .or_else(|| std::env::var("BRAINSTORM_DB").ok().map(PathBuf::from))
        .unwrap_or_else(default_db_path);

    let mut state = AppState::new(db_path, cli.hours, cli.limit);
    state.refresh();

    // Terminal setup
    enable_raw_mode()?;
    let mut stdout = io::stdout();
    execute!(stdout, EnterAlternateScreen)?;
    let backend = CrosstermBackend::new(stdout);
    let mut terminal = Terminal::new(backend)?;

    let result = run_loop(&mut terminal, &mut state);

    // Always restore terminal
    disable_raw_mode()?;
    execute!(terminal.backend_mut(), LeaveAlternateScreen)?;
    terminal.show_cursor()?;

    result
}

fn run_loop(
    terminal: &mut Terminal<CrosstermBackend<io::Stdout>>,
    state: &mut AppState,
) -> Result<(), Box<dyn std::error::Error>> {
    let mut poll_timer = Instant::now();
    let mut anim_timer = Instant::now();

    loop {
        terminal.draw(|f| ui::draw(f, state))?;

        // Poll for events with 50ms timeout (keeps animation smooth)
        if event::poll(Duration::from_millis(50))? {
            if let Event::Key(key) = event::read()? {
                if key.kind == KeyEventKind::Press {
                    match key.code {
                        KeyCode::Char('q') | KeyCode::Esc => {
                            state.should_quit = true;
                        }
                        KeyCode::Char('j') | KeyCode::Down => state.focus_next(),
                        KeyCode::Char('k') | KeyCode::Up => state.focus_prev(),
                        KeyCode::Char('x') => state.stop_focused(),
                        KeyCode::Char('r') => state.refresh(),
                        _ => {}
                    }
                }
            }
        }

        if state.should_quit {
            break;
        }

        // 2s DB poll
        if poll_timer.elapsed() >= Duration::from_secs(2) {
            state.refresh();
            poll_timer = Instant::now();
        }

        // 0.5s animation tick
        if anim_timer.elapsed() >= Duration::from_millis(500) {
            state.anim_frame = state.anim_frame.wrapping_add(1);
            anim_timer = Instant::now();
        }
    }

    Ok(())
}

fn default_db_path() -> PathBuf {
    // Look for .data/brainstorm.db relative to binary location, then cwd
    let exe_dir = std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|p| p.to_path_buf()));

    if let Some(dir) = exe_dir {
        let candidate = dir.join(".data").join("brainstorm.db");
        if candidate.exists() {
            return candidate;
        }
    }

    std::env::current_dir()
        .unwrap_or_else(|_| PathBuf::from("."))
        .join(".data")
        .join("brainstorm.db")
}
```

- [ ] **Step 2: Build and run**

```bash
cd E:/GitHub/ai-collab/tui
cargo build
./target/debug/ai-collab-tui --help
```
Expected: help text printed, no crash.

- [ ] **Step 3: Smoke test against real DB**

```bash
BRAINSTORM_DB=E:/GitHub/ai-collab/.data/brainstorm.db ./target/debug/ai-collab-tui
```
Expected: TUI launches, sessions visible, animation running, `q` exits cleanly.

- [ ] **Step 4: Commit**

```bash
git commit -am "feat: main event loop and CLI (main.rs)"
```

---

### Task 8: Release build and integration

**Files:**
- Create: `tui/README.md` (brief)
- Modify: `E:/GitHub/ai-collab/ai_collab_cli.py` — add `tui-native` subcommand

- [ ] **Step 1: Release build**

```bash
cd E:/GitHub/ai-collab/tui
cargo build --release
ls -lh target/release/ai-collab-tui.exe
```
Expected: single `.exe`, size ~3-5MB.

- [ ] **Step 2: Test release binary**

```bash
BRAINSTORM_DB=E:/GitHub/ai-collab/.data/brainstorm.db ./target/release/ai-collab-tui.exe
```
Expected: same as debug, but noticeably snappier.

- [ ] **Step 3: Verify all tests pass**

```bash
cargo test
```
Expected: all tests PASS.

- [ ] **Step 4: Add binary to `.gitignore`**

Add to `E:/GitHub/ai-collab/.gitignore` (or create `tui/.gitignore`):
```
/target/
```

- [ ] **Step 5: Final commit**

```bash
git add tui/
git commit -m "feat: Rust+Ratatui native TUI — release build verified"
```

---

## Notes for Implementer

**Windows console Unicode:** crossterm on Windows uses the Console API which handles Unicode correctly. No special configuration needed for braille chars or box-drawing.

**Scroll logic:** The current `draw_sessions` uses a simple greedy layout — cards fill from `scroll_offset` until they exceed terminal height. `focus_next`/`focus_prev` update `focused_idx`; `draw_sessions` auto-scrolls if focused card would be off-screen.

**Color map stability:** Agent colors are assigned by alphabetical order (BTreeSet), matching the Python behavior. `claude` always gets orange, `copilot` purple, `gemini` green.

**`rusqlite` bundled feature:** Compiles SQLite directly into the binary. Adds ~700KB but eliminates the `sqlite3.dll` dependency on Windows. Required for a truly standalone `.exe`.

**`lib.rs` + `main.rs` pattern:** Exposes all modules as a library for integration tests, while `main.rs` provides the binary entry point. `cargo test` compiles both.
