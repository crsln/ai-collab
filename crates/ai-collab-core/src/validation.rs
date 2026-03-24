//! Heuristic validation for agent brainstorm responses.

use crate::enums::ResponseQuality;

/// Error patterns that indicate a failed or broken response.
/// These are checked with context-awareness: patterns ending with ':' only match
/// when followed by whitespace or at end of line, not inside analytical text.
const ERROR_PATTERNS_STRICT: &[&str] = &[
    "could not connect",
    "connection refused",
    "econnrefused",
    "no tools available",
    "traceback",
    "panic:",
    "command not found",
    "permission denied",
    "timed out",
    "module not found",
    "cannot find module",
    "segmentation fault",
    "stack overflow",
    "out of memory",
    "killed",
];

/// Patterns that need contextual checking — they commonly appear in legitimate analysis.
const ERROR_PATTERNS_CONTEXTUAL: &[&str] = &[
    "error:",
    "exception:",
    "failed to",
    "unable to",
    "not available",
];

/// Check if a contextual error pattern appears in a truly error-like context
/// (at line start or preceded by a newline) rather than inside analytical text.
fn is_real_error_context(lower: &str, pattern: &str) -> bool {
    let mut search_from = 0;
    while let Some(pos) = lower[search_from..].find(pattern) {
        let abs_pos = search_from + pos;

        // Pattern at very start of content → likely a real error
        if abs_pos == 0 {
            return true;
        }

        // Check what's before the match
        let before = &lower[..abs_pos];
        let prev_char = before.chars().last().unwrap_or(' ');

        // Real error: at line start, after a newline, or after "> " (shell output)
        if prev_char == '\n' || prev_char == '\r' {
            return true;
        }

        // Check if it's the start of a line after trimming whitespace
        let line_start = before.rfind('\n').map(|p| p + 1).unwrap_or(0);
        let line_prefix = before[line_start..].trim();
        // If the only thing before the pattern on this line is whitespace or "> ", it's a real error
        if line_prefix.is_empty() || line_prefix == ">" || line_prefix == "$" {
            return true;
        }

        search_from = abs_pos + pattern.len();
    }
    false
}

/// Check if content has sentence-like structure (indicates real analysis, not garbage).
fn has_sentence_structure(text: &str) -> bool {
    let trimmed = text.trim();
    // Has at least one period, question mark, or colon suggesting structured text
    let has_punctuation = trimmed.contains('.') || trimmed.contains('?') || trimmed.contains(':');
    // Has at least 3 words
    let word_count = trimmed.split_whitespace().count();
    // Contains at least one uppercase letter (start of sentence)
    let has_uppercase = trimmed.chars().any(|c| c.is_uppercase());

    has_punctuation && word_count >= 5 && has_uppercase
}

/// Strict heuristic validation of agent response content.
///
/// Classification rules (improved — context-aware error detection):
/// - `len < 30` -> `Empty`
/// - Contains strict error keywords AND `len < 200` -> `Invalid`
/// - Contains contextual error keywords in error context AND `len < 200` -> `Invalid`
/// - `len >= 200` AND no real errors -> `Valid`
/// - `len 30-200` AND has sentence structure AND no real errors -> `Valid`
/// - Everything else -> `Suspect` (needs Haiku validation)
pub fn validate_heuristic(content: &str) -> ResponseQuality {
    let trimmed = content.trim();
    let len = trimmed.len();

    if len < 30 {
        return ResponseQuality::Empty;
    }

    let lower = trimmed.to_lowercase();

    // Check strict patterns (always indicate errors regardless of context)
    let has_strict_error = ERROR_PATTERNS_STRICT.iter().any(|p| lower.contains(p));

    // Check contextual patterns (only count if they appear in error-like context)
    let has_contextual_error = ERROR_PATTERNS_CONTEXTUAL
        .iter()
        .any(|p| is_real_error_context(&lower, p));

    let has_real_error = has_strict_error || has_contextual_error;

    if has_real_error && len < 200 {
        return ResponseQuality::Invalid;
    }

    if len >= 200 && !has_real_error {
        return ResponseQuality::Valid;
    }

    // For 30-200 range without real errors: check sentence structure
    if !has_real_error && has_sentence_structure(trimmed) {
        return ResponseQuality::Valid;
    }

    ResponseQuality::Suspect
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_content() {
        assert_eq!(validate_heuristic(""), ResponseQuality::Empty);
        assert_eq!(validate_heuristic("   "), ResponseQuality::Empty);
        assert_eq!(validate_heuristic("short"), ResponseQuality::Empty);
        assert_eq!(validate_heuristic("less than thirty chars"), ResponseQuality::Empty);
    }

    #[test]
    fn clearly_invalid_short_error() {
        assert_eq!(
            validate_heuristic("Error: connection refused to MCP server"),
            ResponseQuality::Invalid,
        );
        assert_eq!(
            validate_heuristic("failed to spawn process: command not found"),
            ResponseQuality::Invalid,
        );
        assert_eq!(
            validate_heuristic("Traceback (most recent call last):\n  File ..."),
            ResponseQuality::Invalid,
        );
    }

    #[test]
    fn valid_long_clean_response() {
        let good = "Go is the pragmatic choice for CLI tools. Its simple toolchain, \
            rapid compilation, and single-binary output make it ideal for most use cases. \
            The cobra/viper ecosystem is battle-tested and widely adopted. \
            Cross-platform support is excellent with GOOS/GOARCH.";
        assert!(good.len() >= 200);
        assert_eq!(validate_heuristic(good), ResponseQuality::Valid);
    }

    #[test]
    fn valid_long_with_analytical_error_mentions() {
        // "error handling", "failed to" in analytical context should NOT trigger Invalid
        let analysis = "When comparing CLI frameworks, error handling is important. \
            Tools that failed to provide good error messages lose users. \
            The error: handling in Rust's clap is superior to most alternatives \
            because it validates at compile time rather than runtime. This gives \
            developers confidence that their CLI won't crash unexpectedly.";
        assert!(analysis.len() >= 200);
        // Now Valid instead of Suspect — contextual patterns in mid-sentence are analytical
        assert_eq!(validate_heuristic(analysis), ResponseQuality::Valid);
    }

    #[test]
    fn valid_medium_clean_with_sentence_structure() {
        // Medium-length text with sentence structure should be Valid, not Suspect
        let medium = "Go is a great choice for CLI tools. It compiles to a single binary \
            and has excellent cross-platform support.";
        assert!(medium.len() >= 30 && medium.len() < 200);
        assert_eq!(validate_heuristic(medium), ResponseQuality::Valid);
    }

    #[test]
    fn suspect_medium_no_structure() {
        // Medium-length text without sentence structure remains Suspect
        let gibberish = "aaaa bbbb cccc dddd eeee ffff gggg hhhh iiii jjjj kkkk llll";
        assert!(gibberish.len() >= 30 && gibberish.len() < 200);
        assert_eq!(validate_heuristic(gibberish), ResponseQuality::Suspect);
    }

    #[test]
    fn boundary_at_200_chars() {
        let at_199 = "x".repeat(199);
        assert_eq!(validate_heuristic(&at_199), ResponseQuality::Suspect);

        let at_200 = "x".repeat(200);
        assert_eq!(validate_heuristic(&at_200), ResponseQuality::Valid);
    }

    #[test]
    fn boundary_at_30_chars() {
        let at_29 = "x".repeat(29);
        assert_eq!(validate_heuristic(&at_29), ResponseQuality::Empty);

        let at_30 = "x".repeat(30);
        assert_eq!(validate_heuristic(&at_30), ResponseQuality::Suspect);
    }

    #[test]
    fn real_error_at_line_start() {
        let output = "Some preamble text\nerror: connection failed\nmore text after";
        assert!(output.len() >= 30);
        // error: at line start = real error context
        assert!(is_real_error_context(&output.to_lowercase(), "error:"));
    }

    #[test]
    fn analytical_error_mention_not_flagged() {
        let text = "Good error handling is essential for robust applications.";
        assert!(!is_real_error_context(&text.to_lowercase(), "error:"));
    }

    #[test]
    fn contextual_error_in_code_output() {
        let output = "> failed to connect to database\nRetrying...";
        assert!(is_real_error_context(&output.to_lowercase(), "failed to"));
    }
}
