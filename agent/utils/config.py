from pathlib import Path

# ── LangGraph ──
LANGGRAPH_URL: str = "http://localhost:2024"

# ── LLM / Model ──
LLM_MODEL_ID: str = "deepseek-v4-pro"
LLM_FALLBACK_MODEL_ID: str | None = None
OPENAI_BASE_URL: str = "https://opencode.ai/zen/go/v1"

# ── Sandbox ──
SANDBOX_TYPE: str = "docker"
DEFAULT_SANDBOX_SNAPSHOT_ID: str = ""
DEFAULT_SANDBOX_SNAPSHOT_FS_CAPACITY_BYTES: int = 2_147_483_648
DEFAULT_SANDBOX_VCPUS: int = 2
DEFAULT_SANDBOX_MEM_BYTES: int = 8_589_934_592
DEFAULT_SANDBOX_IDLE_TTL_SECONDS: int = 600
DEFAULT_SANDBOX_DELETE_AFTER_STOP_SECONDS: int = 86_400

# ── Docker Sandbox ──
DOCKER_SANDBOX_IMAGE: str = "open-swe-sandbox:latest"
DOCKER_SANDBOX_MEM_LIMIT: str = "2g"
DOCKER_SANDBOX_CPU_COUNT: str = "2"
DOCKER_SANDBOX_NETWORK: str = "bridge"
DOCKER_SANDBOX_SECCOMP_PROFILE: str = ""

# ── Local Sandbox ──
LOCAL_SANDBOX_ROOT_DIR: str = "."

# ── Modal Sandbox ──
MODAL_APP_NAME: str = "open-swe"

# ── Daytona Sandbox ──
DAYTONA_SANDBOX_SNAPSHOT: str = "daytonaio/sandbox:0.6.0"

# ── LangSmith (non-secret) ──
# LANGSMITH_URL_PROD: str = "https://smith.langchain.com"
# LANGSMITH_ENDPOINT: str = "https://api.smith.langchain.com"
# LANGSMITH_ENDPOINT_PROD: str = "https://api.smith.langchain.com"
# LANGSMITH_HOST_API_URL: str = "https://api.host.langchain.com"
# LANGSMITH_TENANT_ID_PROD: str = ""
# LANGSMITH_TRACING_PROJECT_ID_PROD: str = ""

# ── Jira (non-secret) ──
JIRA_BOT_NAME: str = "Open SWE Agent"
JIRA_DOMAIN: str = "nagarro-team-u6vtl9j6.atlassian.net"
JIRA_PROJECT_TO_REPO: str = '{"DT":{"owner":"NishchayGuptaNagarro","name":"DSD-TEST"}}'

# ── Slack (non-secret) ──
SLACK_BOT_USER_ID: str = ""
SLACK_BOT_USERNAME: str = ""

# ── GitHub / Repo Defaults ──
DEFAULT_REPO_OWNER: str = ""
DEFAULT_REPO_NAME: str = ""
SLACK_REPO_OWNER: str = ""
SLACK_REPO_NAME: str = ""
ALLOWED_GITHUB_ORGS: str = ""
ALLOWED_GITHUB_REPOS: str = ""
PUBLIC_REPO_ORG_GATE: str = ""
CONFIGURED_ADMINS: str = ""

# ── Dashboard ──
DASHBOARD_API_BASE_URL: str = ""
DASHBOARD_BASE_URL: str = ""
DASHBOARD_ALLOWED_ORIGINS: str = ""

# ── Recon Agent ──
RECON_MODEL_ID: str = "deepseek-v4-flash"
RECON_ENABLED: bool = True
RECON_STEP_LIMIT: int = 50

# ── Middleware ──
MODEL_CALL_RECURSION_LIMIT: int = 5000
RECON_MODEL_CALL_RECURSION_LIMIT: int = 50

# ── Multi-Repo Selector ──
MULTI_REPO_SELECTOR_MODEL_ID: str = "openai:gpt-5.5"
MULTI_REPO_SELECTOR_FALLBACK: str = "all"
MULTI_REPO_SELECTOR_ENABLED: bool = False

# ── Debug ──
DEBUG_MODE: bool = True

# ── Prompt ──
DEFAULT_PROMPT_PATH: str = str(Path(__file__).resolve().parent.parent.parent / "default_prompt.md")

# ── Langfuse URL (key/secret stay in .env) ──
LANGFUSE_BASE_URL: str = "http://localhost:3000"

# ── Build Metadata ──
LANGCHAIN_REVISION_ID: str = ""

# ── Token Usage Logging ──
TOKEN_USAGE_LOG: bool = False
TOKEN_USAGE_LOG_FILE: str | None = None
