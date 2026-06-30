# Multi-Repo Setup & Architecture Guide

This guide explains how to set up and use the Multi-Repo Intelligence feature in Open SWE, and details how data flows through the system to provide cross-repository context to the AI agent.

## 1. Example Setup

To enable Multi-Repo routing, you must configure the environment variables and map repositories to Jira projects.

### Step 1: Environment Variables
Add the following to your `.env` file to enable the feature and configure the LLM used for selecting repos:

```env
MULTI_REPO_SELECTOR_ENABLED="true"
MULTI_REPO_SELECTOR_MODEL_ID="mimo-v2.5-free"
MULTI_REPO_SELECTOR_FALLBACK="all"  # Options: "all" or "none"
```

### Step 2: Mapping Repositories to a Jira Project (Example with "OSJ")
Before the agent can work on a multi-repo ticket, it needs to know which repositories are associated with your Jira project. 

Let's say your Jira Project Key is `OSJ`. You want to tell Open SWE that tickets starting with `OSJ-` (like `OSJ-123`) can potentially touch a frontend UI repository, a backend API repository, and a shared types repository.

You do this by sending a `PUT` request to the Admin Dashboard API at `/project-repos/OSJ` (relative to your Open SWE dashboard URL).

Here is how you would register the repositories using a `curl` command:

```bash
curl -X PUT "http://localhost:8000/dashboard/api/project-repos/OSJ" \
  -H "Content-Type: application/json" \
  -d '{
    "repos": [
      {
        "owner": "my-org",
        "name": "osj-frontend",
        "type": "frontend"
      },
      {
        "owner": "my-org",
        "name": "osj-backend",
        "type": "backend"
      },
      {
        "owner": "my-org",
        "name": "osj-shared",
        "type": "shared"
      }
    ]
  }'
```

#### What do these fields mean?
- **`owner`**: The GitHub organization or username that owns the repo (e.g., `my-org`).
- **`name`**: The exact name of the repository on GitHub (e.g., `osj-frontend`).
- **`type`**: This is **crucial for the LLM selector**. When the fast LLM evaluates a Jira ticket, it reads the `type` to decide if the repo is needed.
  - `frontend`: Included if the ticket mentions UI/UX changes.
  - `backend`: Included if the ticket mentions APIs, databases, or core logic.
  - `shared`: (Always included automatically if any other repo is selected).

Behind the scenes, this API call saves your mapping directly into the persistent **LangGraph Store** under the `multi_repo_registry` namespace. Now, whenever an `OSJ-*` ticket is triggered, Open SWE instantly retrieves these 3 repositories to begin the evaluation phase.

### Step 3: Triggering a Run
- **Automatic Selection:** When a ticket is assigned or commented on, the fast LLM (`mimo-v2.5-free`) reads the ticket description. If the ticket says "Fix the UI button color", it will select `frontend-react` and `shared-types` (shared repos are always included).
- **Manual Override:** You can force specific repos by commenting on the ticket:
  > `@openswe please fix the API endpoints. repos: my-org/backend-api, my-org/shared-types`

---

## 2. How the Flow Works (File by File)

Here is the exact lifecycle of a multi-repo task, from the moment a Jira webhook is received, to the moment the agent starts coding in the sandbox.

### Phase 1: Webhook & Selection (`agent/webapp.py`)
1. **Webhook Reception:** Jira sends a payload to `jira_webhook()` in `agent/webapp.py`.
2. **Text Extraction (`agent/utils/repo.py`):** The system first checks if the user manually specified repos (e.g., `repos: org/repo1, org/repo2`) in the comment using `extract_repos_from_text()`.
3. **LLM Selection (`agent/utils/repo_selector.py`):** If no manual repos are specified, `select_repos_for_ticket()` is called. This function:
   - Fetches the mapped repositories for the project from the **LangGraph Store** via `agent/utils/multi_repo_registry.py`.
   - Prompts the LLM with the ticket details and repo types.
   - The LLM returns a JSON list of repos needed for the task.
4. **Task Dispatch:** `process_jira_issue()` is called in the background with the `selected_repos`.

### Phase 2: State Initialization (`agent/webapp.py`)
1. **Prompt Building:** `build_jira_issue_prompt()` renders a new `## Repositories` section in the AI's user prompt, listing out the selected repos and their types.
2. **Thread Metadata:** The `selected_repos` list is saved into the LangGraph thread's `metadata`, persisting it for the lifecycle of the run.

### Phase 3: Agent Creation (`agent/server.py`)
1. **Middleware Stack:** In `get_agent()`, the `MultiRepoCloneMiddleware` is attached to the agent's middleware stack.
2. **Graph Construction:** The `deepagents.create_deep_agent` graph is built and execution begins.

### Phase 4: Sandbox Cloning (`agent/middleware/multi_repo_clone.py`)
1. **Intercepting the Call:** Right before the agent makes its first LLM call, `MultiRepoCloneMiddleware.wrap_model_call()` (and async counterpart) intercepts it.
2. **Parallel Cloning:** The middleware reads `selected_repos` from the thread metadata. It uses the `sandbox.execute()` tool to run `GH_TOKEN=dummy gh repo clone <owner>/<name> /workspace/<name>` for every required repository.
3. **Prompt Injection:** It dynamically modifies the initial `SystemMessage`, explicitly telling the agent that it is in a multi-repo workspace and listing the paths (e.g., `/workspace/backend-api`).
4. **Execution:** The modified system prompt is sent to the primary coding LLM, which now knows it can navigate between the cloned directories to write code across the entire stack.
