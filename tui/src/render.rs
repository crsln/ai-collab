use ratatui::style::Color;
use std::collections::{BTreeSet, HashMap, HashSet};

use crate::colors::{AGENT_COLORS, CYAN, MINT};
use crate::db::SessionData;

// ── Layout types ────────────────────────────────────────────────────────

#[derive(Debug, Clone)]
pub struct GNode {
    pub x: f64,
    pub y: f64,
    pub color: Color,
    pub radius: f64,
}

#[derive(Debug, Clone)]
pub struct GEdge {
    pub x1: f64,
    pub y1: f64,
    pub x2: f64,
    pub y2: f64,
    pub color: Color,
}

#[derive(Debug, Clone)]
pub struct GraphLayout {
    pub glow: Vec<GNode>,
    pub edges: Vec<GEdge>,
    pub nodes: Vec<GNode>,
    pub spinners: Vec<GNode>,
    pub label: Option<(f64, f64, String)>,
}

// ── Helpers ─────────────────────────────────────────────────────────────

pub fn dim_color(c: Color, factor: f64) -> Color {
    match c {
        Color::Rgb(r, g, b) => Color::Rgb(
            (r as f64 * factor) as u8,
            (g as f64 * factor) as u8,
            (b as f64 * factor) as u8,
        ),
        _ => Color::DarkGray,
    }
}

fn simple_hash(a: &str, r: usize, i: usize) -> u64 {
    let mut h: u64 = 5381;
    for b in a.bytes() {
        h = h.wrapping_mul(33).wrapping_add(b as u64);
    }
    h = h.wrapping_mul(33).wrapping_add(r as u64);
    h = h.wrapping_mul(33).wrapping_add(i as u64);
    h
}

fn scatter(agent: &str, round_idx: usize, resp_idx: usize) -> (f64, f64) {
    let h = simple_hash(agent, round_idx, resp_idx);
    let dx = ((h % 1000) as f64 / 1000.0 - 0.5) * 8.0;
    let dy = ((h / 1000 % 1000) as f64 / 1000.0 - 0.5) * 6.0;
    (dx, dy)
}

pub fn extract_summary(content: &str) -> String {
    for raw in content.lines() {
        let ln = raw.trim();
        if ln.is_empty() { continue; }
        let first = ln.chars().next().unwrap_or(' ');
        if "\u{2713}\u{2717}\u{280B}\u{2819}\u{2839}\u{2838}\u{283C}\u{2834}\u{2826}\u{2827}\u{2807}\u{280F}\u{26A0}\u{2718}|┌┐└┘├┤┬┴┼─│[{".contains(first) { continue; }
        if ln.contains("brainstorm-") || ln.contains("atlas-") || ln.contains("skill(") { continue; }
        let mut s = ln;
        for pfx in &["## ", "### ", "# ", "**", "- ", "> ", "* "] {
            if s.starts_with(pfx) { s = s[pfx.len()..].trim(); break; }
        }
        if s.is_empty() { continue; }
        if s.len() > 70 { return format!("{}…", &s[..70]); }
        return s.to_string();
    }
    "(no summary)".to_string()
}

// ── Layout computation ──────────────────────────────────────────────────

/// Compute 2D graph layout from session data.
/// Canvas coordinate space: 100.0 x 50.0
pub fn compute_layout(
    session: &SessionData,
    color_map: &HashMap<String, usize>,
    frame: u32,
) -> GraphLayout {
    let rounds = &session.rounds;

    let all_agents: Vec<String> = {
        let mut set: BTreeSet<String> = BTreeSet::new();
        for (name, _) in color_map { set.insert(name.clone()); }
        for r in rounds { for resp in &r.responses { set.insert(resp.agent_name.clone()); } }
        set.into_iter().collect()
    };

    let n = all_agents.len().max(1);
    let m = rounds.len();

    let mut glow = vec![];
    let mut edges = vec![];
    let mut nodes = vec![];
    let mut spinners = vec![];

    // Origin at left-center
    let ox = 8.0;
    let oy = 25.0;

    // Origin glow rings (outer to inner)
    glow.push(GNode { x: ox, y: oy, radius: 4.0, color: dim_color(CYAN, 0.08) });
    glow.push(GNode { x: ox, y: oy, radius: 3.0, color: dim_color(CYAN, 0.15) });
    glow.push(GNode { x: ox, y: oy, radius: 2.0, color: dim_color(CYAN, 0.3) });
    // Origin bright center
    nodes.push(GNode { x: ox, y: oy, radius: 1.2, color: CYAN });

    if m == 0 {
        // No rounds yet — just show pulsing spinner near origin
        let pulse = ((frame as f64 * 0.4).sin() * 0.3 + 0.5).max(0.1);
        spinners.push(GNode { x: ox + 6.0, y: oy, radius: 0.8, color: dim_color(CYAN, pulse) });
        return GraphLayout { glow, edges, nodes, spinners, label: None };
    }

    // Agent vertical positions (spread evenly)
    let v_spacing = (40.0 / n as f64).min(14.0);
    let agent_ys: Vec<f64> = (0..n).map(|i| {
        oy + (i as f64 - (n - 1) as f64 / 2.0) * v_spacing
    }).collect();

    // Precompute responded sets
    let responded_sets: Vec<HashSet<&str>> = rounds.iter().map(|r| {
        r.responses.iter()
            .filter(|resp| resp.content.is_some())
            .map(|resp| resp.agent_name.as_str())
            .collect()
    }).collect();

    // Track last dot position per agent for chaining
    let mut last_pos: HashMap<String, (f64, f64)> = HashMap::new();

    for (ai, agent) in all_agents.iter().enumerate() {
        let color_idx = color_map.get(agent).copied().unwrap_or(ai);
        let color = AGENT_COLORS[color_idx % AGENT_COLORS.len()];
        let agent_y = agent_ys[ai];

        // Branch line from origin to agent zone entry point
        let entry_x = 20.0;
        edges.push(GEdge {
            x1: ox, y1: oy,
            x2: entry_x, y2: agent_y,
            color: dim_color(color, 0.3),
        });

        let mut dot_count = 0usize;

        for (ri, responded) in responded_sets.iter().enumerate() {
            let has_resp = responded.contains(agent.as_str());
            let is_last = ri == m - 1;

            if has_resp {
                // Compute scattered position
                let base_x = 28.0 + ri as f64 * (60.0 / m.max(1) as f64);
                let (dx, dy) = scatter(agent, ri, dot_count);
                let x = base_x + dx;
                let y = agent_y + dy;

                // Glow halo
                glow.push(GNode { x, y, radius: 2.2, color: dim_color(color, 0.1) });
                glow.push(GNode { x, y, radius: 1.5, color: dim_color(color, 0.2) });

                // Bright dot
                nodes.push(GNode { x, y, radius: 0.8, color });

                // Connection line from previous dot (or entry point)
                let (px, py) = last_pos.get(agent).copied()
                    .unwrap_or((entry_x, agent_y));
                edges.push(GEdge {
                    x1: px, y1: py, x2: x, y2: y,
                    color: dim_color(color, 0.25),
                });

                last_pos.insert(agent.clone(), (x, y));
                dot_count += 1;
            } else if session.is_running && is_last {
                // Pulsing spinner for pending agent
                let base_x = 28.0 + ri as f64 * (60.0 / m.max(1) as f64);
                let pulse = ((frame as f64 * 0.4 + ai as f64 * 1.5).sin() * 0.35 + 0.5).max(0.1);
                let (px, py) = last_pos.get(agent).copied()
                    .unwrap_or((entry_x, agent_y));

                // Dim connection to spinner
                edges.push(GEdge {
                    x1: px, y1: py, x2: base_x, y2: agent_y,
                    color: dim_color(color, 0.12),
                });

                spinners.push(GNode {
                    x: base_x, y: agent_y, radius: 0.8,
                    color: dim_color(color, pulse),
                });
            }
        }
    }

    // Consensus node
    let label = if let Some(content) = &session.consensus_content {
        let cx = 92.0;
        let cy = oy;

        // Glow
        glow.push(GNode { x: cx, y: cy, radius: 5.0, color: dim_color(MINT, 0.06) });
        glow.push(GNode { x: cx, y: cy, radius: 3.5, color: dim_color(MINT, 0.12) });
        glow.push(GNode { x: cx, y: cy, radius: 2.5, color: dim_color(MINT, 0.25) });

        // Lines from last agent positions to consensus
        for (_, (lx, ly)) in &last_pos {
            edges.push(GEdge {
                x1: *lx, y1: *ly, x2: cx, y2: cy,
                color: dim_color(MINT, 0.15),
            });
        }

        // Bright consensus dot
        nodes.push(GNode { x: cx, y: cy, radius: 1.8, color: MINT });

        let short = extract_summary(content);
        let short = if short.len() > 35 { format!("{}…", &short[..35]) } else { short };
        Some((cx - 2.0, cy - 5.0, short))
    } else {
        None
    };

    GraphLayout { glow, edges, nodes, spinners, label }
}
