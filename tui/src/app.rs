use std::collections::{HashMap, HashSet};
use std::path::PathBuf;

use crate::db::{complete_session, poll_sessions, SessionData};

pub struct AppState {
    pub db_path: PathBuf,
    pub hours: u32,
    pub limit: usize,
    pub sessions: Vec<SessionData>,
    pub color_map: HashMap<String, usize>,
    pub anim_frame: u32,
    pub focused_idx: Option<usize>,
    pub last_refresh: String,
    pub should_quit: bool,
    pub known_agents: HashSet<String>,
}

impl AppState {
    pub fn new(db_path: PathBuf, hours: u32, limit: usize) -> Self {
        Self {
            db_path, hours, limit,
            sessions: vec![],
            color_map: HashMap::new(),
            anim_frame: 0,
            focused_idx: None,
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
    }

    pub fn focus_prev(&mut self) {
        let ids = self.sorted_ids();
        if ids.is_empty() { return; }
        self.focused_idx = Some(match self.focused_idx {
            None => ids.len() - 1,
            Some(0) => ids.len() - 1,
            Some(i) => i - 1,
        });
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
