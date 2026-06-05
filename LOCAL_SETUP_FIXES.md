# Open SWE Local Windows Setup - Fixes Applied

This document summarizes the changes made to enable Open SWE to run locally on a Windows environment using OpenRouter.

## 1. Sandbox Initialization Fixes (Windows Compatibility)
**File:** `agent/utils/sandbox_paths.py`
- **Issue:** The agent could not find a writable working directory on Windows because the path normalization logic strictly required forward-slashes and POSIX-style absolute paths.
- **Fix:** 
    - Updated `_normalize_path` to support Windows drive letters (e.g., `C:\`) and backslashes.
    - Updated `_iter_work_dir_candidates` to include Windows command fallbacks like `cd` and `echo %cd%` to resolve the current directory.
    - Fixed `_is_writable_directory` to use Windows-specific commands (`echo` and `del`) with backslashes and double-quotes, as `cmd.exe` does not support `shlex.quote` (single quotes) or forward-slashes in the `del` command.

## 2. OpenRouter & Model Configuration
**File:** `agent/utils/model.py`, `agent/server.py`, `agent/reviewer.py`
- **Issue:** OpenRouter model IDs (like `poolside/laguna-m.1:free`) were being incorrectly split at the colon, causing "Unsupported Provider" errors. Also, subagents were not respecting the custom model override.
- **Fix:**
    - **Smart Provider Resolution:** Updated `make_model` in `agent/utils/model.py` to only treat a prefix as a provider if it's in a known list (e.g., `openai`, `anthropic`). If no prefix is found but an OpenRouter URL is used, it defaults to the `openai` provider.
    - **Global Override:** Updated the agent factories to check for `LLM_MODEL_ID` at the start. This ensures your chosen model is used for both the **Main Agent** and all **Subagents**, preventing accidental usage of paid or default models.
    - **Base URL Routing:** Ensured that when `OPENAI_BASE_URL` is set to OpenRouter, the "Responses API" is disabled to prevent compatibility issues.

## 3. General Stability & Cleanup
- **Missing Imports:** Fixed a `NameError` in `agent/reviewer.py` by adding the missing `import os`.
- **Deprecation Warnings:** 
    - Explicitly set `virtual_mode=False` in `agent/integrations/local.py` for the local shell backend.
    - Provided an explicit model ID for introspection paths in `agent/server.py`, `agent/reviewer.py`, and `agent/review_style_analyzer.py` to silence LangChain warnings.

## 4. Operational Recommendations
- **Telemetry:** If you see "401 Unauthorized" warnings for `api.smith.langchain.com`, these are non-fatal telemetry traces. You can disable them by setting `LANGSMITH_TRACING=false` in your environment.

---
*Changes applied on June 1, 2026.*
