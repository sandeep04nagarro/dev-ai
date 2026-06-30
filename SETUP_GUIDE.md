# Setup Guide — dev-ai + Langfuse + ngrok

This document covers the end-to-end setup of the **dev-ai** environment (a modified Open SWE agent), **Langfuse** (tracing/observability backend), and **ngrok** (public tunnel for webhooks). The system supports GitHub and Jira integrations.

> **Steps are ordered to avoid forward references.** Each step only depends on things you've already completed.

---

## Prerequisites

- **Python 3.11 – 3.13** (3.14 is not yet supported)
- [uv](https://docs.astral.sh/uv/) package manager
- [Docker](https://docs.docker.com/engine/install/) & Docker Compose (for Langfuse and local sandbox)
- [Git](https://git-scm.com/)
- [ngrok](https://ngrok.com/) (for local development — exposes webhook endpoints to the internet)
- A **GitHub** account with repository access

---

## 1. Clone and install dev-ai

```bash
git clone <your-dev-ai-repo-url>
cd dev-ai
uv venv
source .venv/bin/activate        # Linux/macOS
# or: .venv\Scripts\activate     # Windows
uv sync --all-extras --link-mode=copy
```

> `--link-mode=copy` is recommended on cross-filesystem setups (e.g. WSL). Omit it on native Linux if you prefer symlinks.

---

## 2. Create a GitHub App

dev-ai authenticates as a [GitHub App](https://docs.github.com/en/apps/creating-github-apps) to clone repos, push branches, and open PRs.

### 2a. Create the app

1. Go to **GitHub Settings → Developer settings → GitHub Apps → New GitHub App**
2. Fill in:
   - **App name**: `dev-ai` (or your preferred name)
   - **Homepage URL**: Any valid URL (e.g. `https://github.com/your-org/dev-ai`)
   - **Callback URL** (for OAuth): `https://smith.langchain.com/host-oauth-callback/<your-provider-id>` — only if using LangSmith OAuth; otherwise leave blank
   - **Request user authorization (OAuth) during installation**: Enable if using per-user OAuth
   - **Webhook URL**: `https://<your-ngrok-url>/webhooks/github` — you'll fill this after starting ngrok (step 7)
   - **Webhook secret**: `openssl rand -hex 32` — save as `GITHUB_WEBHOOK_SECRET`
3. Set **Repository permissions**:
   - Contents: **Read & write**
   - Pull requests: **Read & write**
   - Issues: **Read & write**
   - Metadata: **Read-only**
4. Under **Subscribe to events**, enable:
   - `Issue comment`
   - `Pull request review`
   - `Pull request review comment`
5. Click **Create GitHub App**

### 2b. Collect credentials

- **App ID** — shown at the top of the app's settings page → `GITHUB_APP_ID`
- **Private key** — click **Generate a private key** → save the `.pem` contents as `GITHUB_APP_PRIVATE_KEY`
- **Client ID / Client Secret** — found under **OAuth credentials** (needed only if using per-user OAuth via LangSmith)

### 2c. Install the app

1. From the app's settings page, click **Install App** in the sidebar
2. Select your org or personal account
3. Choose which repositories dev-ai should have access to
4. Click **Install**
5. The URL after installation ends with `/settings/installations/<id>`. Save that numeric ID as `GITHUB_APP_INSTALLATION_ID`

---

## 3. Set up Jira integration

### 3a. Get a Jira API token

1. Log in to [id.atlassian.com/manage-profile/security/api-tokens](https://id.atlassian.com/manage-profile/security/api-tokens)
2. Click **Create API token**, give it a label like `dev-ai`, and copy the token
3. Save it as `JIRA_API_TOKEN`

### 3b. Configure environment variables

```bash
JIRA_API_TOKEN="your-api-token"
JIRA_EMAIL="your-atlassian-email@example.com"
JIRA_DOMAIN="your-org.atlassian.net"
JIRA_WEBHOOK_SECRET="openssl rand -hex 32"

# Map Jira projects to GitHub repos (JSON)
JIRA_PROJECT_TO_REPO='{"PROJ": {"owner": "github-org", "name": "repo-name"}}'
```

### 3c. Create the Jira webhook

1. In Jira, go to **Settings (gear icon) → System → Webhooks**
2. Click **Create a Webhook**
   - **Name**: `dev-ai`
   - **URL**: `https://<your-ngrok-url>/webhooks/jira`
   - **Secret**: same as `JIRA_WEBHOOK_SECRET`
3. Under **Events → Issue**, enable **Comment: created**
4. Click **Create**

---

## 4. Configure the LLM provider (opencode zen)

dev-ai uses [opencode zen](https://opencode.ai/zen) as the LLM provider.

1. Sign up / log in at [opencode.ai](https://opencode.ai)
2. Navigate to the **Zen** section and generate an API key
3. Set these in your `.env`:

```bash
OPENAI_BASE_URL="https://opencode.ai/zen/v1"
OPENAI_API_KEY="sk-your-key-from-opencode-zen"
LLM_MODEL_ID="deepseek-v4-flash-free"
```

> The base URL and model ID can use the current value as a default for opencode zen. Only the API key needs to be freshly generated per installation.

---

## 5. Environment variables — `.env`

Create a `.env` file in the project root with all credentials gathered so far:

```bash
# === LLM Provider (opencode zen) ===
OPENAI_BASE_URL="https://opencode.ai/zen/v1"
OPENAI_API_KEY="sk-your-key-here"
LLM_MODEL_ID="deepseek-v4-flash-free"

# === GitHub App ===
GITHUB_APP_ID=""
GITHUB_APP_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----
...
-----END RSA PRIVATE KEY-----"
GITHUB_APP_INSTALLATION_ID=""
GITHUB_WEBHOOK_SECRET=""

# === GitHub OAuth via LangSmith (optional) ===
GITHUB_OAUTH_PROVIDER_ID=""

# === Repo Access ===
ALLOWED_GITHUB_ORGS=""
ALLOWED_GITHUB_REPOS=""
DEFAULT_REPO_OWNER=""
DEFAULT_REPO_NAME=""

# === Jira Integration ===
JIRA_API_TOKEN=""
JIRA_EMAIL=""
JIRA_DOMAIN=""
JIRA_WEBHOOK_SECRET=""
JIRA_PROJECT_TO_REPO=''

# === Sandbox ===
SANDBOX_TYPE="local"
# Langfuse vars will be added after step 6

# === Token Encryption ===
TOKEN_ENCRYPTION_KEY=""  # openssl rand -base64 32
```

---

## 6. Set up Langfuse (tracing)

Langfuse replaces LangSmith as the tracing and observability backend.

### 6a. Clone and start Langfuse

```bash
git clone https://github.com/langfuse/langfuse.git
cd langfuse
docker compose up
```

Langfuse is now running at `http://localhost:3000`.

### 6b. Create API keys

1. Open `http://localhost:3000` in a browser
2. Sign up with new credentials (first-time setup)
3. Log in, then navigate to **Settings → API Keys**
4. Click **Create new API key** — this generates a **Secret Key** and a **Public Key**

### 6c. Add to `.env`

```bash
LANGFUSE_SECRET_KEY="sk-lf-..."
LANGFUSE_PUBLIC_KEY="pk-lf-..."
LANGFUSE_BASE_URL="http://localhost:3000"
```

---

## 7. Start ngrok

ngrok exposes your local dev-ai server to the internet so GitHub and Jira can deliver webhooks.

```bash
ngrok http 2024
```

Copy the HTTPS URL ngrok provides (e.g. `https://abc123.ngrok.dev`). Use this URL as the **Webhook URL** when configuring:

- GitHub App webhook → `https://<ngrok-url>/webhooks/github`
- Jira webhook → `https://<ngrok-url>/webhooks/jira`

> Keep this terminal open — ngrok stops when the process exits. Use a second terminal for the dev-ai server.

---

## 8. Run the system

You need three terminals (or a process manager like `tmux`/`screen`).

### Terminal 1 — dev-ai agent server

```bash
cd dev-ai
source .venv/bin/activate
uv run langgraph dev --no-reload --no-browser
```

The server starts on `http://localhost:2024`. Endpoints:

| Endpoint | Purpose |
|---|---|
| `POST /webhooks/github` | GitHub issue/PR/comment webhooks |
| `POST /webhooks/jira` | Jira comment webhooks |
| `GET /health` | Health check |

### Terminal 2 — Langfuse

```bash
cd langfuse
docker compose up
```

Dashboard at `http://localhost:3000`.

### Terminal 3 — ngrok

```bash
ngrok http 2024
```

Webhook URL: `https://<ngrok-id>.ngrok.dev`

---

## 9. EC2 / production deployment

When deploying on an **EC2 instance** (or any VM with a public IP):

- **ngrok is not required.** The instance's public IP (or a DNS name) can be used directly.
- The webhook URL becomes `http://<public-ip>:2024/webhooks/github` (or `https://` with a reverse proxy like nginx + certbot).
- All three components (dev-ai, Langfuse, and the app) run on the same machine. Langfuse remains at `http://localhost:3000` — it does not need to be publicly exposed.

### Firewall rules

- Open port `2024` (dev-ai webhooks) to GitHub and Jira IP ranges only, or use a reverse proxy with allow-listing.
- Keep port `3000` (Langfuse) internal — do not expose it publicly.

---

## 10. Verify it works

### GitHub

1. Create or comment on an issue with: `@openswe what files are in this repo?`
2. Expect:
   - A 👀 reaction on your comment within seconds
   - A trace appearing in Langfuse (`http://localhost:3000`)
   - The agent replies with a comment on the issue

### Jira

1. Comment on any issue with: `@openswe what files are in this repo?`
2. Expect:
   - The agent posts an "On it!" acknowledgment comment
   - A trace in Langfuse
   - The agent's final response posted back to the Jira ticket

---

## Troubleshooting

### Webhook not reaching the server

- Verify ngrok is running and the webhook URL in GitHub/Jira matches exactly (including the `/webhooks/github` or `/webhooks/jira` suffix).
- Check the ngrok inspector at `http://localhost:4040` to see incoming requests and server responses.
- Webhook secrets must match between the provider config and your `.env`.

### Server refuses to start

- Ensure the Python version is 3.11–3.13 (`python --version`).
- Run `uv sync --all-extras --link-mode=copy` again if dependencies changed.

### Langfuse not accessible

- Verify `docker compose up` ran without errors in the langfuse directory.
- Check that port `3000` is not already in use.
- Ensure `LANGFUSE_BASE_URL` in `.env` is `http://localhost:3000` (not `https`).

### Agent not responding

- For GitHub: ensure the comment or issue contains `@openswe` (case-insensitive).
- For Jira: ensure the comment contains `@openswe`.
- Check the dev-ai server logs for webhook processing errors.
- Verify the LLM API key is valid and the model ID is correct.
