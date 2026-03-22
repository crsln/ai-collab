//! Heuristic validation for agent brainstorm responses.

use crate::enums::ResponseQuality;

/// Error patterns that indicate a failed or broken response (case-insensitive match).
const ERROR_PATTERNS: &[&str] = &[
    "error:",
    "failed to",
    "could not connect",
    "connection refused",
    "econnrefused",
    "no tools available",
    "not available",
    "traceback",
    "exception:",
    "panic:",
    "command not found",
    "permission denied",
    "timed out",
    "unable to",
    "module not found",
    "cannot find module",
    "segmentation fault",
    "stack overflow",
    "out of memory",
    "killed",
];

/// Strict heuristic validation of agent response content.
///
/// Classification rules (strict — errs on the side of `Suspect`):
/// - `len < 30` -> `Empty`
/// - Contains error keywords AND `len < 200` -> `Invalid`
/// - `len >= 200` AND no error keywords -> `Valid`
/// - Everything else -> `Suspect` (needs Haiku validation)
pub fn validate_heuristic(content: &str) -> ResponseQuality {
    let trimmed = content.trim();
    let len = trimmed.len();

    if len < 30 {
        return ResponseQuality::Empty;
    }

    let lower = trimmed.to_lowercase();
    let has_error = ERROR_PATTERNS.iter().any(|p| lower.contains(p));

    if has_error && len < 200 {
        return ResponseQuality::Invalid;
    }

    if len >= 200 && !has_error {
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
    fn suspect_long_with_errors() {
        let analysis = "When comparing CLI frameworks, error handling is important. \
            Tools that failed to provide good error messages lose users. \
            The error: handling in Rust's clap is superior to most alternatives \
            because it validates at compile time rather than runtime. This gives \
            developers confidence that their CLI won't crash unexpectedly.";
        assert!(analysis.len() >= 200);
        assert_eq!(validate_heuristic(analysis), ResponseQuality::Suspect);
    }

    #[test]
    fn suspect_medium_clean() {
        let medium = "Go is good for CLI tools because of single binaries and fast compilation. Rust is better for performance.";
        assert!(medium.len() >= 30 && medium.len() < 200);
        assert_eq!(validate_heuristic(medium), ResponseQuality::Suspect);
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
}
