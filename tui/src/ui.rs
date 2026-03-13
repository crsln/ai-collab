use ratatui::Frame;
use ratatui::layout::{Constraint, Layout, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, BorderType, Borders, Paragraph, Wrap};

use crate::app::{card_height, AppState};
use crate::colors::DIM;
use crate::db::SessionData;
use crate::render::render_flow_art;

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

    let available_h = area.height as usize;
    let focused_idx = state.focused_idx.unwrap_or(0);

    let mut visible_start = state.scroll_offset;

    // Compute visible range
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

    // Build visible sessions
    let mut y = area.y;
    let visible_sessions: Vec<(usize, &SessionData)> = sessions
        .into_iter()
        .enumerate()
        .skip(visible_start)
        .take_while(|(_, s)| {
            let h = card_height(agent_count(s)) as u16;
            let fits = y + h <= area.y + area.height;
            y += h;
            fits
        })
        .collect();

    if visible_sessions.is_empty() { return; }

    let constraints: Vec<Constraint> = visible_sessions
        .iter()
        .map(|(_, s)| Constraint::Length(card_height(agent_count(s)) as u16))
        .collect();

    let card_areas = Layout::vertical(constraints).split(area);

    for ((global_idx, session), card_area) in visible_sessions.iter().zip(card_areas.iter()) {
        let is_focused = state.focused_idx == Some(*global_idx);
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
    let border_color = if focused {
        Color::Rgb(251, 191, 36) // amber — focused
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
    let mut all_lines = vec![header, meta];
    all_lines.extend(flow_lines);

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
