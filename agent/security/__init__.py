"""Security middleware primitives for the Open SWE agent.

This package contains framework-agnostic, pure-Python building blocks used by
the security middlewares in :mod:`agent.middleware`:

* :mod:`agent.security.input_sanitizer` -- neutralises prompt-injection
  payloads carried inside the first user prompt (Jira / GitHub issue content)
  before the model ever sees them.
* :mod:`agent.security.command_guard`    -- classifies shell commands
  destined for the Docker sandbox and decides whether they may run.

Both modules depend only on the Python standard library so that they can be
unit-tested in isolation and reused by other layers (e.g. the Docker
integration) for defence in depth.
"""

from agent.security.command_guard import (
    BLOCKED_COMMAND_MESSAGE,
    CommandDecision,
    CommandGuard,
    evaluate_command,
)
from agent.security.input_sanitizer import (
    SanitizationResult,
    sanitize_prompt_text,
)

__all__ = [
    "BLOCKED_COMMAND_MESSAGE",
    "CommandDecision",
    "CommandGuard",
    "SanitizationResult",
    "evaluate_command",
    "sanitize_prompt_text",
]