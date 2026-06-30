"""Middleware to clone multiple repos into the sandbox and update the system prompt."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage
from langgraph.config import get_config

from agent.utils.sandbox_state import ensure_sandbox_for_thread

logger = logging.getLogger(__name__)

async def _clone_repos_and_update_prompt(request: ModelRequest) -> None:
    config = get_config()
    configurable = config.get("configurable", {})
    metadata = config.get("metadata", {})
    selected_repos = metadata.get("selected_repos") or configurable.get("selected_repos")
    
    if not selected_repos:
        return
        
    has_cloned = metadata.get("has_cloned_multi_repos", False)
    if has_cloned:
        return
        
    logger.info("MultiRepoCloneMiddleware: Initializing %d repos in sandbox", len(selected_repos))
    
    # Ensure sandbox
    try:
        thread_id = configurable.get("thread_id")
        if thread_id:
            sandbox = await ensure_sandbox_for_thread(thread_id)
            
            # Clone each repo
            for repo_config in selected_repos:
                owner = repo_config["owner"]
                name = repo_config["name"]
                clone_path = f"/workspace/{name}"
                
                try:
                    res = sandbox.execute(f"GH_TOKEN=dummy gh repo clone {owner}/{name} {clone_path}")
                    if res.exit_code != 0:
                        logger.warning("Failed to clone %s/%s: %s", owner, name, res.output)
                    else:
                        logger.info("Successfully cloned %s/%s to %s", owner, name, clone_path)
                except Exception as e:
                    logger.exception("Error cloning %s/%s: %s", owner, name, e)
    except Exception as e:
         logger.exception("Failed to initialize sandbox in multi-repo middleware: %s", e)
    
    # Inject REPOS_CONTEXT into system prompt
    if request.messages and isinstance(request.messages[0], SystemMessage):
        original_sys = request.messages[0].content
        
        repo_context = "\\n\\n## Multi-Repository Workspace\\n"
        repo_context += "You have access to multiple repositories in this shared workspace:\\n"
        for repo in selected_repos:
            repo_context += f"- **{repo['name']}** (Type: {repo.get('type', 'unknown')}): Located at `/workspace/{repo['name']}`\\n"
        
        repo_context += "\\nMake sure to navigate to the correct directory when running commands or editing files."
        
        new_sys_content = f"{original_sys}{repo_context}"
        # We replace the content
        request.messages[0].content = new_sys_content
        
    metadata["has_cloned_multi_repos"] = True
    config["metadata"] = metadata

def _clone_repos_and_update_prompt_sync(request: ModelRequest) -> None:
    import asyncio
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(_clone_repos_and_update_prompt(request))
        return
    
    loop = asyncio.get_running_loop()
    loop.create_task(_clone_repos_and_update_prompt(request))

class MultiRepoCloneMiddleware(AgentMiddleware):
    """Middleware to clone selected repos into the sandbox and inject them into the system prompt."""

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        _clone_repos_and_update_prompt_sync(request)
        return handler(request)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> Any:
        await _clone_repos_and_update_prompt(request)
        return await handler(request)
