use ratatui::style::Style;
use ratatui::text::{Line, Span};
use std::collections::{BTreeSet, HashMap, HashSet};

use crate::colors::{braille_frame, AGENT_COLORS, CYAN, DIM, MINT};
use crate::db::SessionData;

// ── Summary extractor ────────────────────────────────────────────────────

pub fn extract_summary(content: &str) -> String {
    for raw in content.lines() {
        let ln = raw.trim();
        if ln.is_empty() {
            continue;
        }
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

// ── Span helpers ──────────────────────────────────────────────────────────

fn dim(s: &str) -> Span<'static> {
    Span::styled(s.to_string(), Style::default().fg(DIM))
}

fn colored(s: &str, color: ratatui::style::Color) -> Span<'static> {
    Span::styled(s.to_string(), Style::default().fg(color))
}

// ── Flow art ─────────────────────────────────────────────────────────────

/// Render the session as a node graph growing left-to-right.
/// One row per agent (H = N). No box-drawing characters.
///
/// 3 agents, 2 rounds, gemini pending:
///   ◉ ─ ● ─ ●   (claude  — has ◉ start + ─ connectors)
///       ●   ●   (copilot — dots aligned under columns)
///       ●   ⠋  (gemini  — ⠋ spinner for pending)
pub fn render_flow_art(
    session: &SessionData,
    color_map: &HashMap<String, usize>,
    frame: u32,
) -> Vec<Line<'static>> {
    let sp = braille_frame(frame);
    let rounds = &session.rounds;

    let all_agents: Vec<String> = {
        let mut set: BTreeSet<String> = BTreeSet::new();
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

    let responded_sets: Vec<HashSet<&str>> = rounds
        .iter()
        .map(|r| {
            r.responses
                .iter()
                .filter(|resp| resp.content.is_some())
                .map(|resp| resp.agent_name.as_str())
                .collect()
        })
        .collect();

    let has_consensus = session.consensus_content.is_some();

    let mut lines = vec![];

    for (ai, agent) in all_agents.iter().enumerate() {
        let color_idx = color_map.get(agent).copied().unwrap_or(ai);
        let color = AGENT_COLORS[color_idx % AGENT_COLORS.len()];
        let is_first = ai == 0;

        let mut spans: Vec<Span<'static>> = vec![];

        // Start node or indent (1 char)
        if is_first {
            spans.push(colored("◉", CYAN));
        } else {
            spans.push(Span::raw(" "));
        }

        // Round columns
        for (ri, responded) in responded_sets.iter().enumerate() {
            // Connector (3 chars): ` ─ ` on row 0, `   ` on others
            if is_first {
                spans.push(dim(" ─ "));
            } else {
                spans.push(Span::raw("   "));
            }

            // Dot (1 char)
            if responded.contains(agent.as_str()) {
                spans.push(colored("●", color));
            } else if session.is_running && ri == m - 1 {
                spans.push(Span::styled(
                    sp.to_string(),
                    Style::default().fg(color),
                ));
            } else {
                spans.push(colored("○", color));
            }
        }

        // Consensus / running tail (row 0 only)
        if is_first {
            if has_consensus {
                let short =
                    extract_summary(session.consensus_content.as_deref().unwrap_or(""));
                let short = if short.len() > 40 {
                    format!("{}…", &short[..40])
                } else {
                    short
                };
                spans.push(dim(" ─ "));
                spans.push(colored("◆", MINT));
                spans.push(dim(&format!(" {}", short)));
            } else if session.is_running {
                spans.push(dim(&format!(" ─ {}", sp)));
            }
        }

        lines.push(Line::from(spans));
    }

    lines
}
