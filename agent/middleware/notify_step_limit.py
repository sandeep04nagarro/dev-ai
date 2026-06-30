"""After-agent middleware that notifies users when the agent stops.

Detects two kinds of stop from the final ASSistant message:

1. ``_LIMIT_MARKER`` — injected by ``ModelCallLimitMiddleware``
2. ``_CONSECUTIVE_FAILURE_MARKER`` — injected by
   ``ConsecutiveFailureBreakerMiddleware``

Posts the notification to ALL configured channels (Slack thread,
Linear issue, GitHub PR/issue) so the user sees it regardless of
where the run was triggered from.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from langchain.agents.middleware import AgentState, after_agent
from langgraph.config import get_config
from langgraph.runtime import Runtime

from agent.utils.github_app import get_github_app_installation_token
from agent.utils.github_comments import post_github_comment
from agent.utils.github_token import get_github_token
from agent.utils.linear import comment_on_linear_issue
from agent.utils.slack import post_slack_thread_reply

logger = logging.getLogger(__name__)

_LIMIT_MARKER = "Model call limits exceeded"
_CONSECUTIVE_FAILURE_MARKER = "Consecutive tool failures"


def _content_to_text(content: object) -> str:
    """Flatten a message content value (str, list of blocks, or other) to a plain string."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)
    parts: list[str] = []
    for block in content:
        if isinstance(block, Mapping):
            text = block.get("text", "")
            parts.append(text if isinstance(text, str) else str(text))
        else:
            parts.append(str(block))
    return " ".join(parts)


def _get_slack_target(configurable: Mapping[str, Any]) -> tuple[str, str] | None:
    """Extract the Slack ``(channel_id, thread_ts)`` from thread metadata.

    Returns ``None`` when the thread was not triggered from Slack or the
    required fields are absent.
    """
    slack_thread = configurable.get("slack_thread")
    if not isinstance(slack_thread, Mapping):
        return None
    channel_id = slack_thread.get("channel_id")
    thread_ts = slack_thread.get("thread_ts")
    if not isinstance(channel_id, str) or not isinstance(thread_ts, str):
        return None
    if not channel_id or not thread_ts:
        return None
    return channel_id, thread_ts


def _get_linear_issue_id(configurable: Mapping[str, Any]) -> str | None:
    """Extract the Linear issue ``id`` from thread metadata.

    Returns ``None`` when the thread was not triggered from Linear or the
    id field is absent.
    """
    linear_issue = configurable.get("linear_issue")
    if not isinstance(linear_issue, Mapping):
        return None
    issue_id = linear_issue.get("id")
    return issue_id if isinstance(issue_id, str) and issue_id else None


def _coerce_issue_number(value: object) -> int | None:
    """Coerce a GitHub/LangChain issue number to ``int`` or ``None``.

    Handles raw integers as well as digit strings that may have been
    deserialized from JSON.
    """
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _get_github_target(configurable: Mapping[str, Any]) -> tuple[dict[str, str], int] | None:
    """Resolve a GitHub ``(repo, issue_number)`` tuple from thread metadata.

    Checks ``github_pr_or_issue``, ``github_issue``, and ``pr_number`` in
    that order so PR-comment identities are preserved when the thread was
    triggered from a PR comment event.
    """
    repo_config = configurable.get("repo")
    if not isinstance(repo_config, Mapping):
        return None
    owner = repo_config.get("owner")
    name = repo_config.get("name")
    if not isinstance(owner, str) or not isinstance(name, str) or not owner or not name:
        return None
    repo = {"owner": owner, "name": name}

    github_pr_or_issue = configurable.get("github_pr_or_issue")
    if isinstance(github_pr_or_issue, Mapping):
        number = _coerce_issue_number(github_pr_or_issue.get("number"))
        target_repo = github_pr_or_issue.get("repo")
        if isinstance(target_repo, Mapping):
            target_owner = target_repo.get("owner")
            target_name = target_repo.get("name")
            if (
                isinstance(target_owner, str)
                and isinstance(target_name, str)
                and target_owner
                and target_name
            ):
                repo = {"owner": target_owner, "name": target_name}
        if number is not None:
            return repo, number

    github_issue = configurable.get("github_issue")
    if isinstance(github_issue, Mapping):
        number = _coerce_issue_number(github_issue.get("number"))
        if number is not None:
            return repo, number

    pr_number = _coerce_issue_number(configurable.get("pr_number"))
    if pr_number is not None:
        return repo, pr_number

    return None


async def _post_unified_limit_notification(
    config: Mapping[str, Any],
    stop_reason: str,
) -> None:
    """Post a stop notification to all configured channels.

    Builds a user-facing message from *stop_reason* (distinguishing
    call-limit and consecutive-failure markers) then fans out to every
    configured channel in parallel via :func:`SafeGather.gather`.

    Failures on one channel are logged but do not suppress notifications
    on the others.
    """
    configurable = config.get("configurable", {})
    if not isinstance(configurable, Mapping):
        logger.info("No runtime configurable found for limit notification")
        return

    if _LIMIT_MARKER in stop_reason:
        user_message = (
            "I've reached my maximum execution limit and had to stop. "
            "The task may be incomplete. You can retry with a more focused "
            "request, or ask me to continue from where I left off."
        )
    elif _CONSECUTIVE_FAILURE_MARKER in stop_reason:
        user_message = (
            "I detected a repeating tool failure and stopped to avoid "
            "wasting more calls. "
            "'{}'. You can retry with a more focused request, or ask me "
            "to continue from where I left off.".format(stop_reason)
        )
    else:
        user_message = (
            "I've had to stop for an internal reason. "
            "The task may be incomplete. You can retry with a more focused "
            "request, or ask me to continue from where I left off."
        )

    tasks: list[tuple[str, Any]] = []

    slack_target = _get_slack_target(configurable)
    if slack_target is not None:
        channel_id, thread_ts = slack_target
        tasks.append(
            (
                "slack",
                post_slack_thread_reply(channel_id, thread_ts, user_message),
            )
        )

    linear_issue_id = _get_linear_issue_id(configurable)
    if linear_issue_id is not None:
        tasks.append(
            (
                "linear",
                comment_on_linear_issue(linear_issue_id, user_message),
            )
        )

    github_target = _get_github_target(configurable)
    if github_target is not None:
        token = get_github_token(config) or await get_github_app_installation_token()
        if not token:
            logger.info(
                "No GitHub token available — cannot post limit notification on GitHub"
            )
        else:
            repo, issue_number = github_target
            tasks.append(
                (
                    "github",
                    post_github_comment(
                        repo,
                        issue_number,
                        user_message,
                        token=token,
                    ),
                )
            )

    if not tasks:
        logger.info("No user-facing target found for limit notification")
        return

    results = await SafeGather.gather(tasks)
    for channel, task_result in results:
        if task_result is None:
            logger.info("Sent limit notification to %s", channel)
        else:
            logger.warning(
                "Failed to send limit notification to %s: %s",
                channel,
                task_result,
            )


@after_agent
async def notify_step_limit_reached(
    state: AgentState,
    runtime: Runtime,  # noqa: ARG002
) -> dict[str, Any] | None:
    """Detect a model-call or failure-breaker stop and notify the user.

    Runs after the agent exits. Checks whether the last AI message
    contains a known stop marker from ``ModelCallLimitMiddleware`` or
    ``ConsecutiveFailureBreakerMiddleware`` and posts a human-readable
    notification to all configured channels (Slack, Linear, GitHub).
    """
    messages = state.get("messages", [])
    if not messages:
        return None

    last_msg = messages[-1]
    content = _content_to_text(getattr(last_msg, "content", "") or "")

    if _LIMIT_MARKER not in content and _CONSECUTIVE_FAILURE_MARKER not in content:
        return None

    try:
        config = get_config()
    except Exception:
        logger.exception(
            "Failed to read runtime config while posting limit notification"
        )
        return None

    try:
        await _post_unified_limit_notification(config, content)
    except Exception:
        logger.exception("Failed to send limit notification")

    return None


class SafeGather:
    """Trivial parallel executor that tolerates individual task failures."""

    @staticmethod
    async def gather(
        tasks: list[tuple[str, Any]],
    ) -> list[tuple[str, Exception | None]]:
        import asyncio

        async def _run(label: str, coro: Any) -> tuple[str, Exception | None]:
            try:
                await coro
                return label, None
            except Exception as exc:  # noqa: BLE001
                return label, exc

        return list(
            await asyncio.gather(*(_run(label, coro) for label, coro in tasks))
        )
