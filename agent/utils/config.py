from pathlib import Path


class LLMConfig:
    """LLM / Model configuration."""
    FALLBACK_MODEL_ID: str | None = None
    BASE_URL: str = "https://opencode.ai/zen/go/v1"


class SandboxConfig:
    """General sandbox configuration."""
    TYPE: str = "docker"
    DEFAULT_SNAPSHOT_ID: str = ""
    DEFAULT_SNAPSHOT_FS_CAPACITY_BYTES: int = 2_147_483_648
    DEFAULT_VCPUS: int = 2
    DEFAULT_MEM_BYTES: int = 8_589_934_592
    DEFAULT_IDLE_TTL_SECONDS: int = 600
    DEFAULT_DELETE_AFTER_STOP_SECONDS: int = 86_400


class DockerConfig:
    """Docker-specific sandbox configuration."""
    IMAGE: str = "open-swe-sandbox:latest"
    MEM_LIMIT: str = "2g"
    CPU_COUNT: str = "2"
    NETWORK: str = "bridge"
    SECCOMP_PROFILE: str = ""


class LocalSandboxConfig:
    """Local sandbox configuration."""
    ROOT_DIR: str = "."

class JiraConfig:
    """Jira integration configuration."""
    BOT_NAME: str = "Open SWE Agent"
    DOMAIN: str = ""
    PROJECT_TO_REPO: str = '{"JIRA_SPACE_ID":{"owner":"repo_owner_name","name":"repo_name"}}'


class SlackConfig:
    """Slack integration configuration."""
    BOT_USER_ID: str = ""
    BOT_USERNAME: str = ""


class RepoConfig:
    """GitHub / repo defaults configuration."""
    DEFAULT_OWNER: str = ""
    DEFAULT_NAME: str = ""
    SLACK_OWNER: str = ""
    SLACK_NAME: str = ""
    ALLOWED_GITHUB_ORGS: str = ""
    ALLOWED_GITHUB_REPOS: str = ""
    PUBLIC_ORG_GATE: str = ""
    CONFIGURED_ADMINS: str = ""

class ReconConfig:
    """Recon agent configuration."""
    STEP_LIMIT: int = 50

class MiddlewareConfig:
    """Middleware and recursion limit configuration."""
    MODEL_CALL_RECURSION_LIMIT: int = 5000
    RECON_MODEL_CALL_RECURSION_LIMIT: int = 50

class MultiRepoConfig:
    """Multi-repo selector configuration."""
    SELECTOR_MODEL_ID: str = "openai:gpt-5.5"
    SELECTOR_FALLBACK: str = "all"
    SELECTOR_ENABLED: bool = False

class PromptConfig:
    """Prompt configuration."""
    DEFAULT_PATH: str = str(Path(__file__).resolve().parent.parent.parent / "default_prompt.md")

class BuildConfig:
    """Build metadata."""
    LANGCHAIN_REVISION_ID: str = ""

class TokenLogConfig:
    """Token usage logging configuration."""
    USAGE_LOG: bool = False
    USAGE_LOG_FILE: str | None = None
