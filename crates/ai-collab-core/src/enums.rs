//! Status enums for brainstorm entities.

use serde::{Deserialize, Serialize};
use std::fmt;
use std::str::FromStr;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SessionStatus {
    Active,
    Completed,
}

impl fmt::Display for SessionStatus {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Active => write!(f, "active"),
            Self::Completed => write!(f, "completed"),
        }
    }
}

impl FromStr for SessionStatus {
    type Err = String;
    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s {
            "active" => Ok(Self::Active),
            "completed" => Ok(Self::Completed),
            other => Err(format!("invalid session status: {other}")),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FeedbackStatus {
    Pending,
    Accepted,
    Rejected,
    Modified,
    Consolidated,
}

impl fmt::Display for FeedbackStatus {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Pending => write!(f, "pending"),
            Self::Accepted => write!(f, "accepted"),
            Self::Rejected => write!(f, "rejected"),
            Self::Modified => write!(f, "modified"),
            Self::Consolidated => write!(f, "consolidated"),
        }
    }
}

impl FromStr for FeedbackStatus {
    type Err = String;
    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s {
            "pending" => Ok(Self::Pending),
            "accepted" => Ok(Self::Accepted),
            "rejected" => Ok(Self::Rejected),
            "modified" => Ok(Self::Modified),
            "consolidated" => Ok(Self::Consolidated),
            other => Err(format!("invalid feedback status: {other}")),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ParticipantStatus {
    Pending,
    Dispatched,
    Responded,
    Validated,
    Failed,
    TimedOut,
}

impl fmt::Display for ParticipantStatus {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Pending => write!(f, "pending"),
            Self::Dispatched => write!(f, "dispatched"),
            Self::Responded => write!(f, "responded"),
            Self::Validated => write!(f, "validated"),
            Self::Failed => write!(f, "failed"),
            Self::TimedOut => write!(f, "timed_out"),
        }
    }
}

impl FromStr for ParticipantStatus {
    type Err = String;
    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s {
            "pending" => Ok(Self::Pending),
            "dispatched" => Ok(Self::Dispatched),
            "responded" => Ok(Self::Responded),
            "validated" => Ok(Self::Validated),
            "failed" => Ok(Self::Failed),
            "timed_out" => Ok(Self::TimedOut),
            other => Err(format!("invalid participant status: {other}")),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ResponseQuality {
    Valid,
    SelfSaved,
    Empty,
    Invalid,
    Suspect,
}

impl fmt::Display for ResponseQuality {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Valid => write!(f, "valid"),
            Self::SelfSaved => write!(f, "self_saved"),
            Self::Empty => write!(f, "empty"),
            Self::Invalid => write!(f, "invalid"),
            Self::Suspect => write!(f, "suspect"),
        }
    }
}

impl FromStr for ResponseQuality {
    type Err = String;
    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s {
            "valid" => Ok(Self::Valid),
            "self_saved" => Ok(Self::SelfSaved),
            "empty" => Ok(Self::Empty),
            "invalid" => Ok(Self::Invalid),
            "suspect" => Ok(Self::Suspect),
            other => Err(format!("invalid response quality: {other}")),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn session_status_roundtrip() {
        let status = SessionStatus::Active;
        let s = status.to_string();
        assert_eq!(s, "active");
        assert_eq!(s.parse::<SessionStatus>().unwrap(), SessionStatus::Active);
    }

    #[test]
    fn feedback_status_serde() {
        let status = FeedbackStatus::Accepted;
        let json = serde_json::to_string(&status).unwrap();
        assert_eq!(json, "\"accepted\"");
        let parsed: FeedbackStatus = serde_json::from_str(&json).unwrap();
        assert_eq!(parsed, FeedbackStatus::Accepted);
    }

    #[test]
    fn invalid_status_returns_error() {
        assert!("bogus".parse::<SessionStatus>().is_err());
    }

    #[test]
    fn suspect_quality_roundtrip() {
        let q = ResponseQuality::Suspect;
        let s = q.to_string();
        assert_eq!(s, "suspect");
        assert_eq!(s.parse::<ResponseQuality>().unwrap(), ResponseQuality::Suspect);

        let json = serde_json::to_string(&q).unwrap();
        assert_eq!(json, "\"suspect\"");
        let parsed: ResponseQuality = serde_json::from_str(&json).unwrap();
        assert_eq!(parsed, ResponseQuality::Suspect);
    }
}
