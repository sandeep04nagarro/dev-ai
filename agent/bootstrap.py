"""Load ``.env`` before any module reads ``os.environ`` at import time.

``langgraph dev`` populates the environment from ``langgraph.json``'s ``env``
key, but a bare ``uvicorn agent.webapp:app`` (the ``make run`` target) does not
— so under that launcher every module-level ``os.environ.get`` silently
resolved to ``None``, taking the dashboard's CORS origins and model defaults
with it. Importing this module first makes both launchers behave identically.

``override=False`` so a real environment variable still beats the file, which
keeps container and CI overrides working.
"""

from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
