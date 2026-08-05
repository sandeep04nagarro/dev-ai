from typing import Any, Dict, List
from collections import defaultdict
from datetime import datetime, UTC
import logging
import os

from agent.utils.thread_ops import langgraph_client
from agent.utils.secrets import SecretsManager
from .thread_api import _DASHBOARD_SOURCE, _run_status_to_agent_status, _metadata_repo

logger = logging.getLogger(__name__)


def _default_model_id() -> str:
    """Label for threads whose metadata predates per-thread model tracking."""
    # return os.environ.get("LLM_MODEL_ID") or "Unspecified"
    return SecretsManager.get("LLM_MODEL_ID") or "Unspecified"


async def get_dashboard_metrics(login: str) -> Dict[str, Any]:
    """Aggregate metrics across all threads for the user."""
    client = langgraph_client()
    # Fetch recent threads (up to 1000 for aggregation)
    threads = await client.threads.search(
        metadata={},
        limit=1000,
    )
    
    total_tasks = len(threads)
    active_threads = 0
    completed_threads = 0
    failed_threads = 0
    
    model_dist: Dict[str, int] = defaultdict(int)
    token_usage_by_hour: Dict[str, int] = defaultdict(int)
    effort_counts: Dict[str, int] = defaultdict(int)
    source_counts: Dict[str, int] = defaultdict(int)
    total_completion_seconds = 0
    completed_with_duration = 0
    
    for t in threads:
        metadata = t.get("metadata", {}) if isinstance(t, dict) else getattr(t, "metadata", {})
        thread_status = t.get("status") if isinstance(t, dict) else getattr(t, "status", "idle")
        latest_run_status = metadata.get("latest_run_status")
        
        status = _run_status_to_agent_status(
            thread_status,
            latest_run_status if isinstance(latest_run_status, str) else None,
        )
        
        if status == "running":
            active_threads += 1
        elif status == "finished" or status == "completed" or (status == "idle" and not getattr(t, "error", None)):
            completed_threads += 1
            
            # Avg Completion Time
            created_at = t.get("created_at") if isinstance(t, dict) else getattr(t, "created_at", None)
            updated_at = t.get("updated_at") if isinstance(t, dict) else getattr(t, "updated_at", None)
            if created_at and updated_at:
                try:
                    c_time = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                    u_time = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                    duration = (u_time - c_time).total_seconds()
                    if duration > 0:
                        total_completion_seconds += duration
                        completed_with_duration += 1
                except Exception:
                    pass
        else:
            failed_threads += 1
            
        # Model
        model = metadata.get("model") or metadata.get("agent_model_id") or _default_model_id()
        model_dist[model] += 1
        
        # Effort
        effort = metadata.get("reasoning_effort") or "high"
        effort_counts[effort] += 1
        
        # Source
        source = metadata.get("source") or "unknown"
        if metadata.get("jira_issue_key"): source = "jira"
        elif metadata.get("slack_channel_id"): source = "slack"
        elif metadata.get("github_issue"): source = "github"
        elif metadata.get("linear_issue"): source = "linear"
        source_counts[source] += 1
        
        # Token usage
        tokens = 0
        if "jira_token_usage" in metadata and isinstance(metadata["jira_token_usage"], dict):
            tokens = metadata["jira_token_usage"].get("total", 0)
        elif "token_usage" in metadata and isinstance(metadata["token_usage"], dict):
            tu = metadata["token_usage"]
            tokens = tu.get("total_tokens", tu.get("total", 0))
            
        if tokens > 0:
            created_at_ms = metadata.get("created_at_ms") or metadata.get("updated_at_ms")
            dt = None
            if created_at_ms:
                try:
                    dt = datetime.fromtimestamp(created_at_ms / 1000.0, tz=UTC)
                except Exception:
                    pass
            else:
                created_at = t.get("created_at") if isinstance(t, dict) else getattr(t, "created_at", None)
                if created_at:
                    try:
                        dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                    except Exception:
                        pass
            
            if dt:
                hour_str = dt.strftime("%H:00")
                token_usage_by_hour[hour_str] += tokens
                    
    return {
        "overview": {
            "total_tasks": total_tasks,
            "active_threads": active_threads,
            "completed_threads": completed_threads,
            "failed_threads": failed_threads,
            "success_rate": (completed_threads / total_tasks * 100) if total_tasks > 0 else 0,
            "average_completion_time_seconds": (total_completion_seconds / completed_with_duration) if completed_with_duration > 0 else 0,
        },
        "model_distribution": [{"name": k, "value": v} for k, v in model_dist.items()],
        "effort_distribution": [{"name": k, "value": v} for k, v in effort_counts.items()],
        "source_distribution": [{"name": k, "value": v} for k, v in source_counts.items()],
        "token_usage": [{"time": k, "tokens": v} for k, v in sorted(token_usage_by_hour.items())],
    }


async def list_dashboard_sandboxes(login: str) -> List[Dict[str, Any]]:
    """Aggregate unique sandboxes from user threads."""
    client = langgraph_client()
    threads = await client.threads.search(
        metadata={},
        limit=200,
        sort_by="updated_at",
        sort_order="desc",
    )
    
    sandboxes = {}
    for t in threads:
        metadata = t.get("metadata", {}) if isinstance(t, dict) else getattr(t, "metadata", {})
        sandbox_id = metadata.get("sandbox_id")
        if not sandbox_id or sandbox_id == "__creating__":
            continue
            
        if sandbox_id not in sandboxes:
            # Parse repo from various thread sources (Dashboard vs Jira vs Slack)
            full_name = "unknown"
            if "selected_repos" in metadata and isinstance(metadata["selected_repos"], list) and len(metadata["selected_repos"]) > 0:
                repo_info = metadata["selected_repos"][0]
                if isinstance(repo_info, dict) and "owner" in repo_info and "name" in repo_info:
                    full_name = f"{repo_info['owner']}/{repo_info['name']}"
            else:
                _, _, extracted_full_name = _metadata_repo(metadata)
                if extracted_full_name:
                    full_name = extracted_full_name
            
            thread_status = t.get("status") if isinstance(t, dict) else getattr(t, "status", "idle")
            latest_run_status = metadata.get("latest_run_status")
            status = _run_status_to_agent_status(
                thread_status,
                latest_run_status if isinstance(latest_run_status, str) else None,
            )
            
            # Root thread usually has 'updated_at' string
            last_active = t.get("updated_at") if isinstance(t, dict) else getattr(t, "updated_at", None)
            if not last_active:
                updated_ms = metadata.get("updated_at_ms")
                if updated_ms:
                    last_active = datetime.fromtimestamp(updated_ms / 1000.0, tz=UTC).isoformat()
            
            sandboxes[sandbox_id] = {
                "sandbox_id": sandbox_id,
                "thread_id": t.get("thread_id") if isinstance(t, dict) else getattr(t, "thread_id", ""),
                "repo": full_name,
                "status": status, # usually if thread is running, sandbox is active
                "last_active": last_active,
            }
            
    return list(sandboxes.values())


async def list_dashboard_agents(login: str) -> List[Dict[str, Any]]:
    """Aggregate agents data. Open SWE primarily uses a single main agent, but users can have profiles."""
    from .profiles import get_profile
    profile = await get_profile(login) or {}
    
    return [
        {
            "agent_id": "dev-agent",
            "name": "Dev Agent",
            "type": "Primary",
            "model": profile.get("default_model") or _default_model_id(),
            "effort": profile.get("reasoning_effort") or "high",
            "status": "active"
        },
        {
            "agent_id": "reviewer-agent",
            "name": "Reviewer Agent",
            "type": "Reviewer",
            "model": _default_model_id(),
            "effort": "low",
            "status": "active"
        }
    ]
