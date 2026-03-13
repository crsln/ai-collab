use ratatui::Frame;
use ratatui::layout::{Constraint, Layout, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::symbols::Marker;
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, Borders, Paragraph};
use ratatui::widgets::canvas::{Canvas, Circle, Line as CanvasLine};

use crate::app::AppState;
use crate::colors::DIM;
use crate::db::SessionData;
use crate::render::compute_layout;

pub fn draw(frame: &mut Frame, state: &AppState) {
    let area = frame.area();

    let chunks = Layout::vertical([
        Constraint::Length(2), // header (title + meta)
        Constraint::Fill(1),  // canvas
        Constraint::Length(1), // status bar
    ])
    .split(area);

    let sessions = state.sorted_sessions();

    if sessions.is_empty() {
        let msg = Paragraph::new("No sessions found.\nRun /multi-ai-brainstorm to start one.")
            .style(Style::default().fg(DIM));
        frame.render_widget(msg, chunks[1]);
        draw_statusbar(frame, chunks[2], state, 0, 0);
        return;
    }

    let idx = state.focused_idx.unwrap_or(0).min(sessions.len() - 1);
    let session = sessions[idx];

    draw_header(frame, chunks[0], session);
    draw_canvas(frame, chunks[1], state, session);
    draw_statusbar(frame, chunks[2], state, idx + 1, sessions.len());
}

fn draw_header(frame: &mut Frame, area: Rect, session: &SessionData) {
    let badge = if session.is_running {
        Span::styled("◌ RUNNING", Style::default().fg(Color::Yellow).add_modifier(Modifier::BOLD))
    } else if session.status == "completed" {
        Span::styled("✓ DONE", Style::default().fg(Color::Green).add_modifier(Modifier::BOLD))
    } else {
        Span::styled("● IDLE", Style::default().fg(DIM))
    };

    let topic = if session.topic.len() > 60 {
        format!("{}…", &session.topic[..59])
    } else {
        session.topic.clone()
    };
    let sid = &session.session_id[session.session_id.len().saturating_sub(8)..];

    let title = Line::from(vec![
        Span::raw(" "),
        badge,
        Span::raw("  "),
        Span::styled(topic, Style::default().add_modifier(Modifier::BOLD)),
        Span::raw("  "),
        Span::styled(sid.to_string(), Style::default().fg(DIM)),
    ]);

    // Meta line
    let mut meta_parts: Vec<Span> = vec![Span::raw(" ")];
    if let Some(proj) = &session.project {
        meta_parts.push(Span::styled("project: ", Style::default().fg(DIM)));
        meta_parts.push(Span::raw(proj.clone()));
        meta_parts.push(Span::styled("  ·  ", Style::default().fg(DIM)));
    }
    meta_parts.push(Span::raw(crate::db::time_ago(&session.created_at)));
    if let Some(r) = session.rounds.last() {
        let obj = r.objective.as_deref().or(r.question.as_deref()).unwrap_or("").trim();
        let label = if obj.len() > 50 {
            format!("round {}/{} · {}…", r.round_number, r.total_rounds, &obj[..50])
        } else if obj.is_empty() {
            format!("round {}/{}", r.round_number, r.total_rounds)
        } else {
            format!("round {}/{} · {}", r.round_number, r.total_rounds, obj)
        };
        meta_parts.push(Span::styled("  ·  ", Style::default().fg(DIM)));
        meta_parts.push(Span::styled(label, Style::default().fg(DIM)));
    }
    let meta = Line::from(meta_parts);

    let header = Paragraph::new(vec![title, meta]);
    frame.render_widget(header, area);
}

fn draw_canvas(frame: &mut Frame, area: Rect, state: &AppState, session: &SessionData) {
    let layout = compute_layout(session, &state.color_map, state.anim_frame);

    let canvas = Canvas::default()
        .block(Block::default().borders(Borders::NONE))
        .background_color(Color::Rgb(8, 12, 20))
        .x_bounds([0.0, 100.0])
        .y_bounds([0.0, 50.0])
        .marker(Marker::Braille)
        .paint(|ctx| {
            // Layer 1: glow halos (dimest, drawn first)
            for g in &layout.glow {
                ctx.draw(&Circle {
                    x: g.x, y: g.y, radius: g.radius, color: g.color,
                });
            }

            ctx.layer();

            // Layer 2: edges and connection lines
            for e in &layout.edges {
                ctx.draw(&CanvasLine {
                    x1: e.x1, y1: e.y1, x2: e.x2, y2: e.y2, color: e.color,
                });
            }

            ctx.layer();

            // Layer 3: bright nodes
            for n in &layout.nodes {
                ctx.draw(&Circle {
                    x: n.x, y: n.y, radius: n.radius, color: n.color,
                });
            }

            // Spinners (pulsing)
            for s in &layout.spinners {
                ctx.draw(&Circle {
                    x: s.x, y: s.y, radius: s.radius, color: s.color,
                });
            }

            // Labels
            if let Some((x, y, ref text)) = layout.label {
                ctx.print(x, y, Span::styled(
                    text.clone(),
                    Style::default().fg(DIM),
                ));
            }
        });

    frame.render_widget(canvas, area);
}

fn draw_statusbar(frame: &mut Frame, area: Rect, state: &AppState, current: usize, total: usize) {
    let running = state.sessions.iter().filter(|s| s.is_running).count();

    let mut parts: Vec<Span> = vec![Span::raw(" ")];

    if running > 0 {
        parts.push(Span::styled(
            format!("◌ {} running", running),
            Style::default().fg(Color::Yellow).add_modifier(Modifier::BOLD),
        ));
        parts.push(Span::styled("  |  ", Style::default().fg(DIM)));
    }
    if total > 0 {
        parts.push(Span::raw(format!("{}/{}", current, total)));
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
        .style(Style::default().bg(Color::Rgb(20, 20, 30)));
    frame.render_widget(bar, area);
}
