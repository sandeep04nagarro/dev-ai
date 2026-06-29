# Multi-Repo Routing

DevAIAgent supports multi-repo tasks, allowing a single Jira ticket to trigger an agent run that has access to multiple repositories simultaneously.

## How it works

1. **Mapping**: You configure which repositories are associated with a given Jira project key (e.g. `WEBAPP`).
2. **Selection**: When a ticket is triggered, a fast LLM reads the ticket description and selects only the necessary repositories for the task.
3. **Workspace Initialization**: The agent clones all the selected repositories into its sandbox.
4. **Agent Execution**: The agent is prompted with the list of repositories and can edit files across any of them in a single thread.

## Configuration

To enable the multi-repo selector, set the following environment variables:

```env
MULTI_REPO_SELECTOR_ENABLED="true"
MULTI_REPO_SELECTOR_MODEL_ID="mimo-v2.5-free"
MULTI_REPO_SELECTOR_FALLBACK="all"
```

## Managing Repositories

Use the dashboard API endpoints to add or update the list of repositories for a project:

- `GET /dashboard/api/project-repos/{project_key}`
- `PUT /dashboard/api/project-repos/{project_key}`

The `PUT` endpoint accepts a JSON payload mapping a list of repositories, like so:

```json
{
  "repos": [
    {"owner": "langchain-ai", "name": "frontend", "type": "frontend"},
    {"owner": "langchain-ai", "name": "backend", "type": "backend"},
    {"owner": "langchain-ai", "name": "shared", "type": "shared"}
  ]
}
```

The `type` field helps the LLM decide which repo handles which part of the stack.

## Manual Overrides

If you want to manually specify repositories for a given ticket comment, include them in the comment body:

```
@openswe please update the API. repos: langchain-ai/backend, langchain-ai/shared
```

This bypasses the LLM selection and runs the agent only on the explicitly mentioned repositories.
