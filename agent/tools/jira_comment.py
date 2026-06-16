import asyncio
from typing import Any

from ..utils.jira import post_jira_comment


def jira_comment(comment_body: str, issue_key: str) -> dict[str, Any]:
    """Post a comment to a Jira issue.

    Use this tool to communicate progress and completion to stakeholders on Jira.

    **When to use:**
    - After opening/updating a draft PR, post a comment on the Jira issue to let
      stakeholders know the task is complete and include the PR link. For example:
      "I've completed the implementation and opened a PR: <pr_url>"
    - When answering a question or sharing an update (no code changes needed).

    Args:
        comment_body: Text of the comment to post to the Jira issue.
        issue_key: The Jira issue ID or key (e.g., PROJ-123) to post the comment to.

    Returns:
        Dictionary with 'success' (bool) key.
    """
    success = asyncio.run(post_jira_comment(issue_key, comment_body))
    return {"success": bool(success)}
