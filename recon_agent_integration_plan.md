# Recon Agent Integration Plan

## Goal

Upgrade `get_recon_agent()` in `agent/recon_agent.py` from a minimal stub to a fully-functional agent
that mirrors the main `get_agent()` setup — sandbox, full middleware stack, tracing, tools, and a
proper system prompt — while remaining read-only (no writing files, committing, or opening PRs).

## Files Changed

| File | Change |
|---|---|
| `agent/middleware/__init__.py` | Add `build_recon_middleware_list()` function + export in `__all__` |
| `agent/prompt.py` | Add `construct_recon_system_prompt()` function with shared prompt sections |
| `agent/recon_agent.py` | Full rewrite: sandbox, middleware, tools, callbacks, tracing |

---

### 1. `agent/middleware/__init__.py` — New `build_recon_middleware_list()`

Identical in structure to `build_server_middleware_list`. Includes all the same middleware
components so the recon agent has the same error handling, input sanitization, circuit-breaking,
token tracking, message queuing, Slack status, and thinking-block sanitization as the main agent.

```python
def build_recon_middleware_list(
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
```

**Export**: add `"build_recon_middleware_list"` to `__all__`.

---

### 2. `agent/prompt.py` — New `construct_recon_system_prompt()`

Assembles a system prompt from the shared sections of the main `SYSTEM_PROMPT_TEMPLATE`
that are relevant to recon work, plus a read-only directive.

**Sections reused:**

| Section | Reason |
|---|---|
| `WORKING_ENV_SECTION` | Sandbox info, `GH_TOKEN`, working directory, execute timeout |
| Clone-only instructions (inline) | How to clone repos — no branch/commit/PR steps |
| `FILE_MANAGEMENT_SECTION` | Where repos live in the sandbox |
| `TOOL_USAGE_SECTION` | Docs for `execute`, `fetch_url`, `http_request` (omit Linear/Slack/gh PR sections) |
| `TOOL_BEST_PRACTICES_SECTION` | Search, parallel tool calling |
| `CORE_BEHAVIOR_SECTION` | Persistence, accuracy, autonomy |
| Read-only directive (inline) | Explicit constraint: no writing, editing, or creating files |

```python
RECON_SYSTEM_PROMPT_TEMPLATE = (
    WORKING_ENV_SECTION
    + REPO_SETUP_RECON_SECTION
    + FILE_MANAGEMENT_SECTION
    + RECON_TOOL_USAGE_SECTION
    + TOOL_BEST_PRACTICES_SECTION
    + CORE_BEHAVIOR_SECTION
    + READ_ONLY_CONSTRAINT_SECTION
)

def construct_recon_system_prompt(working_dir: str) -> str:
    sandbox_type = os.getenv("SANDBOX_TYPE", "langsmith")
    gh_auth_prefix = "GH_TOKEN=dummy " if sandbox_type == "langsmith" else ""
    return RECON_SYSTEM_PROMPT_TEMPLATE.format(
        working_dir=working_dir,
        gh_auth_prefix=gh_auth_prefix,
    )
```

---

### 3. `agent/recon_agent.py` — Full Rewrite

```python
import logging
import os
from typing import Any

from deepagents import create_deep_agent
from langchain_core.runnables import RunnableConfig
from langgraph.pregel import Pregel

from agent.middleware import (
    MetadataLoggerHandler,
    ModelFallbackMiddleware,
    build_recon_middleware_list,
)
from agent.middleware.exclude_tools import ExcludeToolsMiddleware
from agent.prompt import construct_recon_system_prompt
from agent.server import (
    DEFAULT_RECURSION_LIMIT,
    ensure_sandbox_for_thread,
    graph_loaded_for_execution,
)
from agent.tools import fetch_url, http_request, web_search
from agent.utils.model import fallback_model_id_for, make_model, provider_model_kwargs
from agent.utils.sandbox_paths import aresolve_sandbox_work_dir
from agent.utils.tracing import get_langfuse_handler
from agent.utils.tracing_diagnostics import _AttrsStore

logger = logging.getLogger(__name__)

if os.getenv("DEBUG_MODE", "").lower() in ("on", "1", "true"):
    logger.setLevel(logging.DEBUG)


async def get_recon_agent(config: RunnableConfig) -> Pregel:
    thread_id = config["configurable"].get("thread_id", None)
    config["recursion_limit"] = DEFAULT_RECURSION_LIMIT

    if thread_id is None or not graph_loaded_for_execution(config):
        return create_deep_agent(
            model=make_model("openai:gpt-4o-mini"),
            system_prompt="",
            tools=[],
        ).with_config(config)

    # 1. Sandbox lifecycle
    sandbox_backend = await ensure_sandbox_for_thread(thread_id)
    work_dir = await aresolve_sandbox_work_dir(sandbox_backend)

    # 2. Model resolution
    model_id = os.environ.get("RECON_MODEL_ID", "openai:gpt-4o-mini")
    model_kwargs = provider_model_kwargs(model_id, None, max_tokens=4000)
    model = make_model(model_id, **model_kwargs)

    fallback_model_id = fallback_model_id_for(model_id)
    fallback_middleware: list[Any] = []
    if fallback_model_id and fallback_model_id != model_id:
        fallback_kwargs: dict[str, Any] = {"max_tokens": 4000}
        fallback_middleware.append(
            ModelFallbackMiddleware(make_model(fallback_model_id, **fallback_kwargs))
        )

    # 3. System prompt
    system_prompt = construct_recon_system_prompt(working_dir=work_dir)

    # 4. Callbacks + tracing
    metadata_logger = MetadataLoggerHandler()
    callbacks = config.get("callbacks")
    if callbacks is None:
        config["callbacks"] = [metadata_logger]
    elif isinstance(callbacks, list):
        callbacks.append(metadata_logger)

    langfuse_handler = get_langfuse_handler()
    if langfuse_handler:
        callbacks = config.get("callbacks")
        if callbacks is None:
            config["callbacks"] = [langfuse_handler]
        elif isinstance(callbacks, list):
            callbacks.append(langfuse_handler)

    metadata = config.get("metadata", {}) or {}
    configurable = config.get("configurable", {}) or {}
    _AttrsStore.set(
        thread_id=thread_id,
        session_id=metadata.get("langfuse_session_id")
        or configurable.get("langfuse_session_id", thread_id),
        user_id=metadata.get("langfuse_user_id")
        or configurable.get("langfuse_user_id", "unknown"),
        trace_name=metadata.get("langfuse_trace_name")
        or configurable.get("langfuse_trace_name"),
    )

    # 5. Build and return agent
    recon_step_limit = config["configurable"].get("recon_step_limit", 20)
    return create_deep_agent(
        model=model,
        system_prompt=system_prompt,
        tools=[http_request, fetch_url, web_search],
        backend=sandbox_backend,
        middleware=[
            ExcludeToolsMiddleware(excluded=frozenset({"write_file", "edit_file", "write_todos"})),
            *build_recon_middleware_list(fallback_middleware),
        ],
    ).with_config({**config, "recursion_limit": recon_step_limit + 10})
```

### Key Design Decisions

1. **`backend=sandbox_backend` directly** — Unlike `get_agent()` which uses a factory closure
   (`_get_cached_sandbox_backend`), we pass the sandbox directly (same pattern as `reviewer.py`).
   The recon agent is short-lived and runs synchronously via `runs.wait`, so reconnection logic
   is unnecessary.

2. **`ExcludeToolsMiddleware` first in middleware list** — Strips `write_file`, `edit_file`,
   `write_todos` before any other middleware processes the tool list. This ensures the model
   never sees write tools, even though `tools=[]` doesn't include them — the deepagent always
   adds its built-in tools.

3. **No subagents** — Recon is a focused, sequential exploration task. Subagent dispatch
   (`task` tool) adds unnecessary complexity.

4. **Minimal custom tools** — Only `http_request`, `fetch_url`, `web_search`. All file
   exploration uses deepagent's built-in tools (`ls`, `glob`, `grep`, `read_file`, `execute`).

5. **Full middleware parity with server** — All 14 server middlewares included, plus
   `ExcludeToolsMiddleware` on top. The recon agent has the same safety nets as the main agent.
