import os
from typing import Any

from langchain.agents.middleware import ModelCallLimitMiddleware

from .callback_metadata_logger import MetadataLoggerHandler
from .check_message_queue import check_message_queue_before_model
from .docker_cleanup import docker_cleanup_middleware
from .ensure_no_empty_msg import ensure_no_empty_msg
from .exclude_tools import ExcludeToolsMiddleware
from .jira_plan_sync import JiraPlanSyncMiddleware
from .model_fallback import ModelFallbackMiddleware
from .notify_step_limit import notify_step_limit_reached
from .refresh_slack_status import SlackAssistantStatusMiddleware
from .sandbox_circuit_breaker import SandboxCircuitBreakerMiddleware
from .sanitize_thinking_blocks import SanitizeThinkingBlocksMiddleware
from .sanitize_tool_inputs import SanitizeToolInputsMiddleware
from .tool_error_handler import ToolErrorMiddleware
from agent.middleware.multi_repo_clone import MultiRepoCloneMiddleware
from agent.middleware.jira_plan_sync import JiraPlanSyncMiddleware

MODEL_CALL_RECURSION_LIMIT = 5_000

__all__ = [
    "MetadataLoggerHandler",
    "ExcludeToolsMiddleware",
    "JiraPlanSyncMiddleware",
    "ModelFallbackMiddleware",
    "SanitizeThinkingBlocksMiddleware",
    "SanitizeToolInputsMiddleware",
    "ToolErrorMiddleware",
    "SandboxCircuitBreakerMiddleware",
    "SlackAssistantStatusMiddleware",
    "build_middleware_list",
    "check_message_queue_before_model",
    "docker_cleanup_middleware",
    "ensure_no_empty_msg",
    "notify_step_limit_reached",
    "MultiRepoCloneMiddleware",
    "JiraPlanSyncMiddleware",
]


def build_middleware_list(
    fallback_middleware: list[Any],
) -> list[Any]:
    middleware = [
        SanitizeToolInputsMiddleware(),
        ModelCallLimitMiddleware(run_limit=MODEL_CALL_RECURSION_LIMIT, exit_behavior="end"),
        ToolErrorMiddleware(),
        JiraPlanSyncMiddleware(),
        check_message_queue_before_model,
        SlackAssistantStatusMiddleware(),
        ensure_no_empty_msg,
        notify_step_limit_reached,
        SandboxCircuitBreakerMiddleware(),
        *fallback_middleware,
        SanitizeThinkingBlocksMiddleware(),
    ]
    if os.environ.get("SANDBOX_TYPE", "langsmith") == "docker":
        from .docker_cleanup import docker_cleanup_middleware

        middleware.append(docker_cleanup_middleware)
    return middleware
