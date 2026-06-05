# Jira Integration Guide

This guide walks you through setting up the Jira integration for Open SWE. Once configured, you can trigger the agent by tagging `@openswe` in any Jira issue comment.

## Prerequisites

- A **Jira Cloud** instance (Atlassian).
- Administrator access to Jira (to configure webhooks).
- An [ngrok](https://ngrok.com/) tunnel running (for local development).

---

## 1. Get your Jira API Token

Open SWE needs an API token to fetch issue details and post comments back to Jira.

1.  Log in to [id.atlassian.com](https://id.atlassian.com/manage-profile/security/api-tokens).
2.  Click **Create API token**.
3.  Give it a label like `open-swe` and click **Create**.
4.  **Copy the token immediately** and save it as `JIRA_API_TOKEN`.

---

## 2. Configure Environment Variables

Add the following to your `.env` file:

```bash
# === Jira Integration ===
JIRA_API_TOKEN="your-api-token-from-step-1"
JIRA_EMAIL="your-atlassian-email@example.com"
JIRA_DOMAIN="your-org.atlassian.net"  # The domain of your Jira instance

# Webhook secret (for security) - generate a random string
JIRA_WEBHOOK_SECRET="generate-a-random-hex-string" # e.g. openssl rand -hex 32

# Map Jira Project Keys to GitHub Repositories (JSON format)
# Format: {"PROJECT_KEY": {"owner": "github-org", "name": "repo-name"}}
JIRA_PROJECT_TO_REPO='{"PROJ": {"owner": "langchain-ai", "name": "open-swe"}}'
```

---

## 3. Set up the Jira Webhook

Jira will send a notification to Open SWE whenever a new comment is created.

1.  In Jira, go to **Settings (gear icon) > System**.
2.  In the left sidebar, scroll down to **Webhooks** (under the "Automation" or "Advanced" section).
3.  Click **Create a Webhook**.
4.  Fill in the details:
    *   **Name**: `Open SWE Trigger`
    *   **Status**: `Enabled`
    *   **URL**: `https://<your-ngrok-url>/webhooks/jira` — use your ngrok URL.
    *   **Secret**: Enter the exact same string you used for `JIRA_WEBHOOK_SECRET` in step 2.
5.  Under **Events**, scroll down to **Issue** and check:
    *   **Comment: created**
6.  Click **Create** at the bottom of the page.

---

## 4. Verify it works

1.  Ensure your Open SWE server is running: `uv run langgraph dev`.
2.  Go to any issue in a Jira project you mapped in `JIRA_PROJECT_TO_REPO`.
3.  Add a comment: `@openswe what files are in this repository?`
4.  **Expectations:**
    *   The agent will acknowledge the request by posting a "On it!" comment (including a link to the LangSmith trace if enabled).
    *   The agent will spin up a sandbox, clone the repo, and perform the task.
    *   Once finished, the agent will post its final response back to the Jira ticket.

---

## Troubleshooting

### Webhook not reaching the server
- Verify ngrok is running and the URL in Jira matches exactly (including the `/webhooks/jira` suffix).
- Check the ngrok inspector (`http://localhost:4040`) to see if Jira is sending payloads and if the server is returning `200 OK` or `401 Unauthorized`.
- If you see `401 Unauthorized`, double-check that your `JIRA_WEBHOOK_SECRET` matches between Jira and your `.env` file.

### Repository not found
- Ensure the **Project Key** (e.g., `PROJ` from `PROJ-123`) is correctly mapped in the `JIRA_PROJECT_TO_REPO` environment variable.
- Ensure the GitHub App is installed on the repository you mapped to that project.

### Atlassian Document Format (ADF) errors
- Jira Cloud uses a complex JSON format for comments (ADF). Open SWE automatically attempts to parse this into plain text. If comments appear garbled or empty, check the server logs for "Failed to parse Jira ADF".
