# Open SWE Consolidated Setup & Integration Guide

This document provides a comprehensive record of all changes made to enable Open SWE on a local Windows environment with OpenRouter, generic OpenAI-compatible providers, and Jira integration.

---

## 1. Windows Native Compatibility

### Sandbox Path Resolution
**File:** `agent/utils/sandbox_paths.py`
**Goal:** Allow the agent to correctly identify its working directory and verify write permissions using Windows `cmd.exe` conventions.

- **Changes:**
    - Added `import os`.
    - **`_normalize_path`**: Supports drive letters (e.g., `C:\`) and UNC paths. It also preserves the virtual root `/`.
    - **`_is_writable_directory`**: Implemented a `cmd.exe` fallback. Since `del "path/to/file"` fails on Windows (forward slashes are seen as switches), we force backslashes and use double quotes.
    - **`_iter_work_dir_candidates`**: Added `cd` and `echo %cd%` as fallbacks for `pwd`.

### Path Middleware Bypass
**File:** `agent/integrations/local.py`
**Goal:** Prevent the filesystem middleware from rejecting absolute Windows paths.

- **Change:** Set `virtual_mode=True` in `LocalShellBackend`.
- **Snippet:**
  ```python
  return LocalShellBackend(
      root_dir=root_dir,
      inherit_env=True,
      virtual_mode=True, # Critical for Windows local dev
  )
  ```

---

## 2. Model & Provider Flexibility

### Universal OpenAI Adapter
**File:** `agent/utils/model.py`
**Goal:** Support any OpenAI-compatible provider (OpenRouter, OpenCode Zen, etc.).

- **Robust Splitting**: Updated to only split `provider:model` if the prefix is a known LangChain provider (e.g., `openai`, `anthropic`). This fixes colons in model names like `poolside/laguna-m.1:free`.
- **Custom URL Logic**: If `OPENAI_BASE_URL` is custom, the system now automatically uses the `openai` provider and disables the `use_responses_api` feature which is only supported by official OpenAI servers.

### Global `LLM_MODEL_ID` Override
**Files:** `agent/server.py`, `agent/reviewer.py`
**Goal:** Ensure the user's chosen model is used for all agents and sub-agents.

- **Implementation**: Factories now check `os.environ.get("LLM_MODEL_ID")` first. If present, it overrides all other team or profile defaults.

---

## 3. Dynamic GitHub Authentication

### Sandbox-Aware Prompts
**Files:** `agent/prompt.py`, `agent/reviewer.py`, `agent/webapp.py`, `agent/utils/github_comments.py`, `agent/review_style_analyzer.py`
**Goal:** Use the real `GH_TOKEN` for local development instead of the production proxy (`GH_TOKEN=dummy`).

- **Dynamic Prefix**: Introduced `{gh_auth_prefix}` into the system prompts.
- **Logic**:
  ```python
  sandbox_type = os.getenv("SANDBOX_TYPE", "langsmith")
  gh_auth_prefix = "GH_TOKEN=dummy " if sandbox_type == "langsmith" else ""
  ```
- **Instructions**: Updated the "Working Environment" section to tell the agent to use `git` or `curl` as fallbacks if the `gh` CLI is missing on the host machine.

---

## 4. Jira Integration

### API & Tools
**Files:** `agent/utils/jira.py`, `agent/tools/jira_comment.py`, `agent/utils/jira_project_repo_map.py`
**Goal:** Enable end-to-end Jira support.

- **`jira.py`**: Handles authentication (Basic Auth), issue fetching (ADF to text parsing), and comment posting.
- **`jira_comment.py`**: A new LangChain tool enabling the agent to reply to Jira tickets.
- **Mapping**: Added a JSON mapper to link Jira project keys (e.g., `ENG`) to GitHub repos.

### Webhook Handling
**File:** `agent/webapp.py`
**Goal:** Securely trigger runs from Jira comments.

- **Signature Fix**: Fixed the verification logic to strip the `sha256=` prefix sent by Jira.
- **Route**: Added `POST /webhooks/jira` to handle `@openswe` mentions and kick off background tasks.

---

## 5. Allowlist Configuration

### Repository Security
**Issue:** Webhooks were ignored with "Repository not in allowlist".

- **Guidance:** If you have `ALLOWED_GITHUB_ORGS` or `ALLOWED_GITHUB_REPOS` set in your environment, the repository must match them. For local development, if you want to allow everything, ensure these variables are **completely empty** in your `.env` file.

---

## 6. Rate Limit Handling (429 Errors)

**Files:** `agent/utils/model.py`, `agent/middleware/model_fallback.py`
**Goal:** Prevent agent crashes when using free models with tight rate limits.

- **Aggressive Retries:** Increased `DEFAULT_MAX_RETRIES` from 6 to 20. This allows the OpenAI client to retry many more times with exponential backoff.
- **Backoff Delay:** Added a 5-second sleep in `ModelFallbackMiddleware` specifically for 429 errors to give the API quota time to reset before trying the fallback model.

## 7. Reasoning Parameter Fix (TypeError)

**File:** `agent/utils/model.py`
**Goal:** Prevent `TypeError: AsyncCompletions.create() got an unexpected keyword argument 'reasoning'` when using non-reasoning models or 3rd party providers.

- **Selective Reasoning:** Updated `provider_model_kwargs` to only include the `reasoning` parameter for known OpenAI o-series models (`o1`, `o3`). This ensures that standard models like `gpt-4o` and OpenAI-compatible providers (OpenCode Zen, etc.) are not passed experimental arguments they don't support.

## 8. Jira Field Extraction Fix (Missing Title/Description/Comments)

**Files:** `agent/utils/jira.py`, `agent/webapp.py`
**Goal:** Fix the issue where Jira issue title, description, or the triggering comment were missing from the agent's prompt, especially for formatted comments.

- **Recursive ADF Parsing:** Jira Cloud uses Atlassian Document Format (ADF) which heavily nests text (e.g., inside bullet lists). Added a robust `extract_adf_text` recursive function to reliably parse out all plain text from any ADF structure.
- **API Simplification:** Removed unnecessary `expand` parameters from the Jira issue fetch call in `agent/utils/jira.py` to ensure a standard response structure.
- **Robust Fallback:** Updated `process_jira_issue` in `agent/webapp.py` to automatically fall back to the webhook payload's data if the API-fetched issue lacks a summary or description. 
- **Comment Injection:** Updated the webhook handler to pass the author's name and ensures the triggering comment is always included in the `comments` list, even if it hasn't been indexed by the Jira API yet.

## 9. Jira Assignment Triggers

**File:** `agent/webapp.py`
**Goal:** Enable automatic agent triggering when a task is assigned to the bot.

- **Assignment Detection:** Added logic to `jira_webhook` to handle `jira:issue_updated` events. It scans the changelog for `assignee` changes and triggers the agent if the new assignee matches `JIRA_BOT_NAME`.
- **Assignment at Creation:** Added logic to handle `jira:issue_created` events. This ensures that if a ticket is assigned to the bot at the moment of creation, the agent triggers immediately without needing a separate update or comment.
- **Instruction Synthesis:** When triggered by assignment, the system generates a synthetic starting instruction for the agent (e.g., *"I have just been assigned to this ticket..."*), while still correctly fetching the full issue context from the Jira API.

## 10. Reviewer Repo Gating Fallback

**File:** `agent/webapp.py`
**Goal:** Prevent "Repository not enabled for review" errors for repos already allowlisted in `.env`.

- **Smart Fallback:** Modified `_is_repo_enabled_for_review` to fall back to the standard `_is_repo_allowed` check (which uses `ALLOWED_GITHUB_REPOS` and `ALLOWED_GITHUB_ORGS`) if the dashboard's explicit opt-in list is empty. This ensures that repositories configured via environment variables are automatically enabled for auto-reviews and PR comment triggers without requiring manual dashboard configuration.

---
*Documented for future reference. Setup verified working as of June 1, 2026.*
