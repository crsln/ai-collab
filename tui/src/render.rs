use ratatui::style::Style;
use ratatui::text::{Line, Span};
use std::collections::{BTreeSet, HashMap, HashSet};

use crate::colors::{braille_frame, AGENT_COLORS, CYAN, DIM, MINT};
use crate::db::SessionData;

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

fn dim(s: &str) -> Span<'static> {
    Span::styled(s.to_string(), Style::default().fg(DIM))
}

fn colored(s: &str, color: ratatui::style::Color) -> Span<'static> {
    Span::styled(s.to_string(), Style::default().fg(color))
}

/// Render a single-line dot chain: one ● per response record, colored by agent.
/// Chain grows left-to-right as records land in the DB.
///
/// Examples:
///   ◉ ─ ●(orange) ─ ●(purple) ─ ●(green) ─ ◆ summary
///   ◉ ─ ● ─ ● ─ ⠋ ─ ⠋  (running, 2 agents pending)
///   ◉  ⠋ waiting…         (no rounds yet)
pub fn render_flow_art(
    session: &SessionData,
    color_map: &HashMap<String, usize>,
    frame: u32,
) -> Vec<Line<'static>> {
    let sp = braille_frame(frame);
    let rounds = &session.rounds;

    let mut spans: Vec<Span<'static>> = vec![colored("◉", CYAN)];

    if rounds.is_empty() {
        if session.is_running {
            spans.push(dim(&format!("  {} waiting…", sp)));
        }
        return vec![Line::from(spans)];
    }

    // One dot per response record, in round order
    for (ri, round) in rounds.iter().enumerate() {
        let is_last = ri == rounds.len() - 1;

        // Responded agents (have content)
        for resp in &round.responses {
            if resp.content.is_some() {
                let color_idx = color_map.get(&resp.agent_name).copied().unwrap_or(0);
                let color = AGENT_COLORS[color_idx % AGENT_COLORS.len()];
                spans.push(dim(" ─ "));
                spans.push(colored("●", color));
            }
        }

        // Pending agents on the last round (show spinner)
        if session.is_running && is_last {
            let responded: HashSet<&str> = round
                .responses
                .iter()
                .filter(|r| r.content.is_some())
                .map(|r| r.agent_name.as_str())
                .collect();

            // All known agents (from color_map + past responses)
            let mut all_known: BTreeSet<String> = color_map.keys().cloned().collect();
            for r in rounds {
                for resp in &r.responses {
                    all_known.insert(resp.agent_name.clone());
                }
            }

            for agent in &all_known {
                if !responded.contains(agent.as_str()) {
                    let color_idx = color_map.get(agent.as_str()).copied().unwrap_or(0);
                    let color = AGENT_COLORS[color_idx % AGENT_COLORS.len()];
                    spans.push(dim(" ─ "));
                    spans.push(Span::styled(sp.to_string(), Style::default().fg(color)));
                }
            }
        }
    }

    // Consensus node
    if let Some(content) = &session.consensus_content {
        let short = extract_summary(content);
        let short = if short.len() > 40 {
            format!("{}…", &short[..40])
        } else {
            short
        };
        spans.push(dim(" ─ "));
        spans.push(colored("◆", MINT));
        spans.push(dim(&format!(" {}", short)));
    }

    vec![Line::from(spans)]
}
