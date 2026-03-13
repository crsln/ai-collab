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
