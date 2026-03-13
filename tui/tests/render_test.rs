use ai_collab_tui::db::{AgentResponse, RoundInfo, SessionData};
use ai_collab_tui::render::{compute_layout, extract_summary};
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
            vec![]
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
fn test_layout_has_origin_node() {
    let session = make_session(3, 2, true);
    let color_map: HashMap<String, usize> = [
        ("claude".to_string(), 0),
        ("copilot".to_string(), 1),
        ("gemini".to_string(), 2),
    ].into();
    let layout = compute_layout(&session, &color_map, 0);
    // Origin is always first node
    assert!(!layout.nodes.is_empty());
    assert!(layout.nodes[0].x < 15.0); // origin is on the left
}

#[test]
fn test_layout_dot_count_3_agents_2_rounds() {
    let session = make_session(3, 2, true);
    let color_map: HashMap<String, usize> = [
        ("claude".to_string(), 0),
        ("copilot".to_string(), 1),
        ("gemini".to_string(), 2),
    ].into();
    let layout = compute_layout(&session, &color_map, 0);
    // 1 origin + 3 agents * 2 rounds = 7 nodes
    assert_eq!(layout.nodes.len(), 7);
}

#[test]
fn test_layout_has_spinners_when_running() {
    let session = make_session(3, 2, false); // not all responded, is_running=true
    let color_map: HashMap<String, usize> = [
        ("claude".to_string(), 0),
        ("copilot".to_string(), 1),
        ("gemini".to_string(), 2),
    ].into();
    let layout = compute_layout(&session, &color_map, 0);
    // Last round has 0 responses, so 3 spinners expected
    assert_eq!(layout.spinners.len(), 3);
}

#[test]
fn test_extract_summary_strips_prefix() {
    assert_eq!(extract_summary("## My heading\nother"), "My heading");
}

#[test]
fn test_extract_summary_truncates() {
    let long = "a".repeat(80);
    let s = extract_summary(&long);
    assert!(s.len() <= 73);
}
