//! Type-safe newtype IDs to prevent mixing session/round/feedback/response IDs.

use serde::{Deserialize, Serialize};
use std::fmt;
use uuid::Uuid;

macro_rules! define_id {
    ($name:ident, $prefix:expr) => {
        #[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
        #[serde(transparent)]
        pub struct $name(String);

        impl $name {
            /// Generate a new random ID with the appropriate prefix.
            pub fn new() -> Self {
                Self(format!("{}_{}", $prefix, &Uuid::new_v4().to_string()[..12]))
            }

            /// Create from an existing string (e.g., loaded from DB).
            pub fn from_str(s: impl Into<String>) -> Self {
                Self(s.into())
            }

            pub fn as_str(&self) -> &str {
                &self.0
            }
        }

        impl Default for $name {
            fn default() -> Self {
                Self::new()
            }
        }

        impl fmt::Display for $name {
            fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                f.write_str(&self.0)
            }
        }

        impl AsRef<str> for $name {
            fn as_ref(&self) -> &str {
                &self.0
            }
        }

        impl From<String> for $name {
            fn from(s: String) -> Self {
                Self(s)
            }
        }

        impl From<&str> for $name {
            fn from(s: &str) -> Self {
                Self(s.to_string())
            }
        }
    };
}

define_id!(SessionId, "bs");
define_id!(RoundId, "r");
define_id!(ResponseId, "resp");
define_id!(ConsensusId, "con");
define_id!(FeedbackId, "fb");
define_id!(FeedbackResponseId, "fbr");
define_id!(RoleId, "role");
define_id!(GuidelineId, "gl");
define_id!(ParticipantId, "part");
define_id!(RoleTemplateId, "rl");
define_id!(AgentDefinitionId, "adef");
define_id!(WorkflowId, "wf");
define_id!(ToolGuideId, "tg");

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn session_id_has_correct_prefix() {
        let id = SessionId::new();
        assert!(id.as_str().starts_with("bs_"));
    }

    #[test]
    fn round_id_has_correct_prefix() {
        let id = RoundId::new();
        assert!(id.as_str().starts_with("r_"));
    }

    #[test]
    fn ids_are_unique() {
        let a = SessionId::new();
        let b = SessionId::new();
        assert_ne!(a, b);
    }

    #[test]
    fn id_from_string() {
        let id = SessionId::from_str("bs_test123");
        assert_eq!(id.as_str(), "bs_test123");
    }

    #[test]
    fn id_serializes_as_string() {
        let id = SessionId::from_str("bs_abc123");
        let json = serde_json::to_string(&id).unwrap();
        assert_eq!(json, "\"bs_abc123\"");
    }

    #[test]
    fn id_deserializes_from_string() {
        let id: SessionId = serde_json::from_str("\"bs_abc123\"").unwrap();
        assert_eq!(id.as_str(), "bs_abc123");
    }
}
