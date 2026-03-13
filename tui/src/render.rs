use ratatui::style::{Style};
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

    let all_agents: Vec<String> = {
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
                    // Push dynamic braille spinner span and continue
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
