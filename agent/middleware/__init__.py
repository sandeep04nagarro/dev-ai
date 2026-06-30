import os
from typing import Any

from langchain.agents.middleware import ModelCallLimitMiddleware

from agent.middleware.callback_metadata_logger import MetadataLoggerHandler
from agent.middleware.check_message_queue import check_message_queue_before_model
from agent.middleware.consecutive_failure_breaker import ConsecutiveFailureBreakerMiddleware
from agent.middleware.docker_cleanup import docker_cleanup_middleware
from agent.middleware.ensure_no_empty_msg import ensure_no_empty_msg
from agent.middleware.exclude_tools import ExcludeToolsMiddleware
from agent.middleware.jira_plan_sync import JiraPlanSyncMiddleware
from agent.middleware.model_fallback import ModelFallbackMiddleware
from agent.middleware.notify_step_limit import notify_step_limit_reached
from agent.middleware.refresh_slack_status import SlackAssistantStatusMiddleware
from agent.middleware.sandbox_circuit_breaker import SandboxCircuitBreakerMiddleware
from agent.middleware.sanitize_thinking_blocks import SanitizeThinkingBlocksMiddleware
from agent.middleware.sanitize_tool_inputs import SanitizeToolInputsMiddleware
from agent.middleware.ticket_token_usage import TicketTokenUsageMiddleware
from agent.middleware.tool_error_handler import ToolErrorMiddleware
from agent.middleware.multi_repo_clone import MultiRepoCloneMiddleware

MODEL_CALL_RECURSION_LIMIT = 5_000

CONSECUTIVE_FAILURE_THRESHOLDS: dict[str, int] = {
    "execute": 5,
    "ls": 20,
    "read_file": 50,
}
CONSECUTIVE_FAILURE_DEFAULT_THRESHOLD = 5

__all__ = [
    "MetadataLoggerHandler",
    "ConsecutiveFailureBreakerMiddleware",
    "ExcludeToolsMiddleware",
    "JiraPlanSyncMiddleware",
    "ModelFallbackMiddleware",
    "SanitizeThinkingBlocksMiddleware",
    "SanitizeToolInputsMiddleware",
    "TicketTokenUsageMiddleware",
    "ToolErrorMiddleware",
    "SandboxCircuitBreakerMiddleware",
    "SlackAssistantStatusMiddleware",
    "build_reviewer_middleware_list",
    "build_server_middleware_list",
    "check_message_queue_before_model",
    "docker_cleanup_middleware",
    "ensure_no_empty_msg",
    "notify_step_limit_reached",
    "MultiRepoCloneMiddleware",
    "JiraPlanSyncMiddleware",
]


def build_server_middleware_list(
    fallback_middleware: list[Any],
) -> list[Any]:
    middleware = [
        SanitizeToolInputsMiddleware(),
        MultiRepoCloneMiddleware(),
        ConsecutiveFailureBreakerMiddleware(
            thresholds=CONSECUTIVE_FAILURE_THRESHOLDS,
            default_threshold=CONSECUTIVE_FAILURE_DEFAULT_THRESHOLD,
        ),
        ModelCallLimitMiddleware(run_limit=MODEL_CALL_RECURSION_LIMIT, exit_behavior="end"),
        ToolErrorMiddleware(),
        TicketTokenUsageMiddleware(),
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


def build_reviewer_middleware_list() -> list[Any]:
    return [
        SanitizeToolInputsMiddleware(),
        ConsecutiveFailureBreakerMiddleware(
            thresholds=CONSECUTIVE_FAILURE_THRESHOLDS,
            default_threshold=CONSECUTIVE_FAILURE_DEFAULT_THRESHOLD,
        ),
        ModelCallLimitMiddleware(run_limit=MODEL_CALL_RECURSION_LIMIT, exit_behavior="end"),
        ToolErrorMiddleware(),
        check_message_queue_before_model,
        SlackAssistantStatusMiddleware(),
        SanitizeThinkingBlocksMiddleware(),
    ]
