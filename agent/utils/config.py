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

TOKEN_USAGE_LOG : bool | None = False
TOKEN_USAGE_LOG_FILE: str | None = _resolve_log_path("/home/nishchay/dev-AI/dev-ai/token_usage.log")
