use ai_collab_tui::db::{parse_dt, time_ago};

#[test]
fn test_parse_dt_valid() {
    let dt = parse_dt("2026-03-13T17:40:15.744394+00:00");
    assert!(dt.is_some());
}

#[test]
fn test_parse_dt_invalid() {
    assert!(parse_dt("not-a-date").is_none());
    assert!(parse_dt("").is_none());
}

#[test]
fn test_time_ago_seconds() {
    let s = time_ago("2020-01-01T00:00:00+00:00");
    assert!(s.contains("ago"));
}

#[test]
fn test_time_ago_empty() {
    assert_eq!(time_ago(""), "");
}
