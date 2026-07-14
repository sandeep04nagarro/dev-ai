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
    ensure_sandbox_for_thread,
    graph_loaded_for_execution,
)
from agent.tools import fetch_url, http_request, web_search
from agent.utils import config as cfg
from agent.utils.model import fallback_model_id_for, make_model, provider_model_kwargs
from agent.utils.sandbox_paths import aresolve_sandbox_work_dir
from agent.utils.tracing import get_langfuse_handler
from agent.utils.tracing_diagnostics import _AttrsStore

logger = logging.getLogger(__name__)

if os.environ.get("DEBUG_MODE",False):
    logger.setLevel(logging.DEBUG)


async def get_recon_agent(config: RunnableConfig) -> Pregel:
    """Reconnaissance agent for scope analysis before main agent run."""
    thread_id = config["configurable"].get("thread_id", None)

    if thread_id is None or not graph_loaded_for_execution(config):
        return create_deep_agent(
            model=make_model("openai:gpt-4o-mini"),
            system_prompt="",
            tools=[],
        ).with_config(config)

    sandbox_backend = await ensure_sandbox_for_thread(thread_id)
    work_dir = await aresolve_sandbox_work_dir(sandbox_backend)

    model_id = os.environ.get("RECON_MODEL_ID","deepseek-v4-flash")
    model_kwargs = provider_model_kwargs(model_id, None, max_tokens=4000)
    model = make_model(model_id, **model_kwargs)

    fallback_model_id = fallback_model_id_for(model_id)
    fallback_middleware: list[Any] = []
    if fallback_model_id and fallback_model_id != model_id:
        fallback_kwargs: dict[str, Any] = {"max_tokens": 4000}
        fallback_middleware.append(
            ModelFallbackMiddleware(make_model(fallback_model_id, **fallback_kwargs))
        )

    system_prompt = construct_recon_system_prompt(working_dir=work_dir)

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
        user_id=metadata.get("langfuse_user_id") or configurable.get("langfuse_user_id", "unknown"),
        trace_name=metadata.get("langfuse_trace_name") or configurable.get("langfuse_trace_name"),
    )

    # recon_step_limit = config["configurable"].get("recon_step_limit", 20)
    return create_deep_agent(
        model=model,
        system_prompt=system_prompt,
        tools=[http_request, fetch_url, web_search],
        backend=sandbox_backend,
        middleware=[
            ExcludeToolsMiddleware(excluded=frozenset({"write_file", "edit_file", "write_todos"})),
            *build_recon_middleware_list(fallback_middleware),
        ],
    ).with_config(config)
