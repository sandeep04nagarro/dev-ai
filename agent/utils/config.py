import os
from pathlib import Path


def _resolve_log_path(log_file: str | None) -> str | None:
    """Resolve the token-usage log file path.

    Precedence:
    1. TOKEN_USAGE_LOG_FILE env var (absolute or relative path).
    2. TOKEN_USAGE_LOG=trueish -> logs go to <cwd>/token_usage.log.
    3. Neither set -> logging is disabled (returns None).
    """
    if log_file is not None:
        return log_file
    if TOKEN_USAGE_LOG:
        return str(Path.cwd() / "token_usage.log")
    return None


TOKEN_USAGE_LOG: bool | None = False
TOKEN_USAGE_LOG_FILE: str | None = None

# TOKEN_USAGE_LOG_FILE: str | None = _resolve_log_path("/home/nishchay/dev-AI/dev-ai/token_usage.log")

# ---------------------------------------------------------------------------
# Phase-based token profiling configuration
# ---------------------------------------------------------------------------

# File that receives a JSON-Lines stream of per-phase token profiling events.
# Each line is a JSON object; final summary rows are written when a run ends.
#
# Override via the TOKEN_PROFILING_LOG_FILE environment variable.
# If not set, the file is auto-placed next to TOKEN_USAGE_LOG_FILE (if
# configured) or in the process working directory.
TOKEN_PROFILING_LOG_FILE: str | None = os.environ.get("TOKEN_PROFILING_LOG_FILE") or None
