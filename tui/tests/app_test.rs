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
fn test_focus_prev_wraps() {
    let mut state = dummy_state();
    state.sessions = vec![
        make_session("a", false, "2026-01-01T00:00:00+00:00"),
        make_session("b", false, "2026-01-02T00:00:00+00:00"),
    ];
    state.focused_idx = Some(0);
    state.focus_prev();
    assert_eq!(state.focused_idx, Some(1));
}
