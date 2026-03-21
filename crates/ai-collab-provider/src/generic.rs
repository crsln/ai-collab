//! Generic CLI provider — config-driven subprocess adapter for any AI CLI tool.

use std::process::Stdio;
use std::time::Duration;

use ai_collab_core::traits::AnsiStrip;
use ai_collab_core::ProviderError;
use tokio::io::AsyncWriteExt;
use tokio::process::Command;
use tokio::time::timeout;

use crate::registry::AgentRunConfig;

/// Config-driven adapter for any AI CLI tool.
///
/// Reads agent configuration (command, args, timeout) and executes
/// the CLI as a subprocess. Works with any CLI that accepts a prompt
/// via command-line arguments or stdin.
pub struct GenericCLIProvider {
    config: AgentRunConfig,
}

impl GenericCLIProvider {
    pub fn new(config: AgentRunConfig) -> Self {
        Self { config }
    }

    /// Find the CLI executable, preferring .cmd shims on Windows.
    fn find_cmd(&self) -> Result<String, ProviderError> {
        let name = &self.config.command;

        // On Windows, check for .cmd shim first
        if cfg!(windows) {
            let cmd_name = format!("{name}.cmd");
            if let Ok(path) = which::which(&cmd_name) {
                return Ok(path.to_string_lossy().to_string());
            }
        }

        which::which(name)
            .map(|p| p.to_string_lossy().to_string())
            .map_err(|_| ProviderError::Unavailable {
                agent: name.clone(),
                reason: format!("executable '{name}' not found in PATH"),
            })
    }

    /// Strip ANSI codes from output. Override in specialized providers.
    fn clean_output(&self, output: &str) -> String {
        output.strip_ansi().trim().to_string()
    }

    /// Run the CLI as a subprocess and return cleaned output.
    pub async fn execute(
        &self,
        prompt: &str,
        cwd: Option<&str>,
    ) -> Result<String, ProviderError> {
        let cmd = self.find_cmd()?;
        let timeout_secs = self.config.timeout;

        let mut args = self.config.build_args(prompt);

        // Pipe long prompts via stdin on Windows to avoid 8191-char limit
        let stdin_input = if cfg!(windows) && prompt.len() > 7000 {
            // Rebuild args without the prompt
            args = Vec::new();
            for arg in &self.config.args {
                if arg.contains("{prompt}") {
                    continue; // skip prompt arg, will pipe via stdin
                }
                args.push(arg.clone());
            }
            if let Some(ref model) = self.config.model {
                if !model.is_empty() {
                    args.extend(["--model".to_string(), model.clone()]);
                }
            }
            Some(prompt.as_bytes().to_vec())
        } else {
            None
        };

        let mut command = Command::new(&cmd);
        command
            .args(&args)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true); // Critical for Windows cleanup

        if stdin_input.is_some() {
            command.stdin(Stdio::piped());
        } else {
            command.stdin(Stdio::null());
        }

        if let Some(dir) = cwd {
            command.current_dir(dir);
        }

        let mut child = command.spawn().map_err(|e| ProviderError::Unavailable {
            agent: self.config.name.clone(),
            reason: format!("failed to spawn: {e}"),
        })?;

        // Write stdin if needed, then close it
        if let Some(input) = stdin_input {
            if let Some(mut stdin) = child.stdin.take() {
                stdin.write_all(&input).await?;
                drop(stdin); // Close stdin to signal EOF
            }
        }

        // Wait with timeout
        let result = timeout(
            Duration::from_secs_f64(timeout_secs),
            child.wait_with_output(),
        )
        .await;

        match result {
            Ok(Ok(output)) if output.status.success() => {
                let text = String::from_utf8_lossy(&output.stdout);
                Ok(self.clean_output(&text))
            }
            Ok(Ok(output)) => {
                let stderr = String::from_utf8_lossy(&output.stderr);
                Err(ProviderError::Execution {
                    agent: self.config.name.clone(),
                    code: output.status.code().unwrap_or(-1),
                    stderr: stderr.trim().to_string(),
                })
            }
            Ok(Err(e)) => Err(ProviderError::Execution {
                agent: self.config.name.clone(),
                code: -1,
                stderr: e.to_string(),
            }),
            Err(_) => {
                // Timeout — child is killed on drop
                Err(ProviderError::Timeout {
                    agent: self.config.name.clone(),
                    seconds: timeout_secs,
                })
            }
        }
    }

    /// Check if the provider's CLI executable is available.
    pub fn is_ready(&self) -> bool {
        self.find_cmd().is_ok()
    }

    pub fn agent_name(&self) -> &str {
        &self.config.name
    }
}

/// Copilot-specific provider — strips "Total usage est:" footer.
pub struct CopilotProvider {
    inner: GenericCLIProvider,
}

impl CopilotProvider {
    pub fn new(config: AgentRunConfig) -> Self {
        Self {
            inner: GenericCLIProvider::new(config),
        }
    }

    pub async fn execute(
        &self,
        prompt: &str,
        cwd: Option<&str>,
    ) -> Result<String, ProviderError> {
        let output = self.inner.execute(prompt, cwd).await?;
        // Strip Copilot's usage footer
        let lines: Vec<&str> = output.lines().collect();
        let mut content_lines = Vec::new();
        for line in lines {
            if line.trim().starts_with("Total usage est:") {
                break;
            }
            content_lines.push(line);
        }
        Ok(content_lines.join("\n").trim().to_string())
    }

    pub fn is_ready(&self) -> bool {
        self.inner.is_ready()
    }

    pub fn agent_name(&self) -> &str {
        self.inner.agent_name()
    }
}

/// Gemini-specific provider — currently identical to generic.
pub struct GeminiProvider {
    inner: GenericCLIProvider,
}

impl GeminiProvider {
    pub fn new(config: AgentRunConfig) -> Self {
        Self {
            inner: GenericCLIProvider::new(config),
        }
    }

    pub async fn execute(
        &self,
        prompt: &str,
        cwd: Option<&str>,
    ) -> Result<String, ProviderError> {
        self.inner.execute(prompt, cwd).await
    }

    pub fn is_ready(&self) -> bool {
        self.inner.is_ready()
    }

    pub fn agent_name(&self) -> &str {
        self.inner.agent_name()
    }
}
